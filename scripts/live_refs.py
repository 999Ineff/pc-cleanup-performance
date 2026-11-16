#!/usr/bin/env python3
"""READ-ONLY: list every folder a live tool depends on, so no cleanup ever touches it.

Run this FIRST, every time, and pass its live-refs.txt to the other scripts with --protect.
Agents' own "safe to delete" lists have included the node_modules of running MCP servers;
this list is what catches that.

Sources (whatever exists on this machine):
  - MCP servers and hooks in coding-agent configs: Claude Code (~/.claude.json, settings.json,
    project .mcp.json), Claude Desktop, Cursor, Windsurf, Gemini CLI, Codex, Continue, Zed,
    VS Code / Insiders / VSCodium (mcp.json, settings "mcp", Cline, Roo Code)
  - running processes (executable + command line; on Linux also the working directory)
  - Windows: scheduled tasks, services, Run keys, Startup-folder shortcuts, user/system PATH
  - macOS: LaunchAgents / LaunchDaemons plists, crontab
  - Linux: systemd unit files, ~/.config/autostart, crontab
  - PATH of this process

A file reference protects its whole project: the nearest folder above it with a project marker
(package.json, pyproject.toml, .git, pyvenv.cfg ...). A reference inside node_modules / a venv
protects the project that owns those dependencies.

Output (--out): live-refs.txt (one folder per line, for --protect), live-refs.json, summary.txt
Usage: python3 -I live_refs.py [--out DIR]
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

PROJECT_MARKERS = ("package.json", "pyproject.toml", "setup.py", "Cargo.toml", "go.mod", ".git", "pyvenv.cfg",
                   "deno.json", "deno.jsonc", "Gemfile", "composer.json", "pom.xml", "build.gradle", "build.gradle.kts")
DEP_DIRS = ("node_modules", ".venv", "venv", "site-packages")
# One-off shells and core utilities: their command lines are the agent's own commands, not tools.
TRANSIENT = {"bash", "sh", "zsh", "fish", "dash", "ksh", "tcsh", "cmd", "powershell", "pwsh", "conhost", "git", "wsl",
             "wslhost", "tr", "grep", "find", "sort", "timeout", "ps", "sed", "awk", "cat", "head", "tail", "xargs",
             "less", "more", "sudo", "login", "sshd", "tmux", "screen", "env", "su", "top", "htop", "watch"}

WIN_START = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")
POSIX_START = re.compile(r"(?:^|(?<=[\s\"'=:,(\[]))~?/")
STOP = re.compile(r"[\"'<>|\r\n\t\0*?]")


def candidates(text):
    """Yield the longest existing path prefix of every absolute path in text. Every possible start
    is tried, so a second unquoted path on the same command line is not swallowed by the first."""
    if not text:
        return
    text = os.path.expandvars(text)
    for m in (WIN_START if C.IS_WIN else POSIX_START).finditer(text):
        s = text[m.start():]
        stop = STOP.search(s)
        if stop:
            s = s[:stop.start()]
        s = s.strip().rstrip(" ,;)]}")
        if C.IS_WIN:
            s = s.replace("/", "\\")
        else:
            if s.startswith("//"):
                continue  # URL remainder, e.g. https://...
            s = os.path.expanduser(s)
        while s and not os.path.exists(s):
            cut = max(s.rfind(" "), s.rfind(";"), s.rfind(","), s.rfind("="), -1 if C.IS_WIN else s.rfind(":"))
            if cut <= (2 if C.IS_WIN else 0):
                s = ""
                break
            s = s[:cut].rstrip()
        if s:
            yield os.path.normpath(s)


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                yield k
            for s in strings(v):
                yield s
    elif isinstance(obj, list):
        for v in obj:
            for s in strings(v):
                yield s


def find_key(node, wanted):
    """Yield every value stored under one of the wanted keys, at any depth."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k in wanted:
                yield k, v
            else:
                for x in find_key(v, wanted):
                    yield x
    elif isinstance(node, list):
        for v in node:
            for x in find_key(v, wanted):
                yield x


def toml_mcp_strings(path):
    """Strings inside [mcp_servers.*] tables of a TOML file (tomllib on 3.11+, a plain scan before)."""
    try:
        import tomllib  # 3.11+
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return list(strings(data.get("mcp_servers", {})))
    except ImportError:
        pass
    out, inside = [], False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            s = line.strip()
            if s.startswith("["):
                inside = s.startswith("[mcp_servers") or s.startswith("[[mcp_servers")
                continue
            if inside:
                for dq, sq in re.findall(r'"((?:[^"\\]|\\.)*)"|\'([^\']*)\'', s):
                    out.append((dq or sq).replace("\\\\", "\\"))
    return out


def agent_refs(found, notes):
    projects = []
    for label, path, keys in C.agent_config_files():
        if not os.path.isfile(path):
            continue
        try:
            if path.endswith(".toml"):
                texts = toml_mcp_strings(path)
            elif "*raw*" in keys:
                with open(path, encoding="utf-8", errors="replace") as f:
                    texts = [f.read()]
            else:
                data = C.load_jsonc(path)
                texts = []
                for k, v in find_key(data, set(keys)):
                    texts += list(strings(v))
                if label == "claude-code" and isinstance(data, dict):
                    projects = [p for p in (data.get("projects") or {}) if isinstance(p, str)]
        except (OSError, ValueError) as e:
            notes.append("config %s unreadable (%s): scanned as raw text, so every path in it is protected" % (path, e))
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    texts = [f.read()]
            except OSError:
                continue
        for t in texts:
            for p in candidates(t):
                found.append(("agent:" + label, p))
    # Project-scoped MCP servers (.mcp.json) of every project Claude Code knows about
    for proj in projects:
        mj = os.path.join(proj, ".mcp.json")
        if os.path.isfile(mj):
            try:
                data = C.load_jsonc(mj)
                for _, v in find_key(data, {"mcpServers"}):
                    for t in strings(v):
                        for p in candidates(t):
                            found.append(("agent:project-mcp", p))
            except (OSError, ValueError):
                notes.append("project config %s unreadable: its folder is protected" % mj)
                found.append(("agent:project-mcp", proj))


def path_env_refs(found):
    for part in (os.environ.get("PATH") or "").split(os.pathsep):
        part = part.strip().strip('"')
        if part and os.path.isdir(part):
            found.append(("path", os.path.normpath(part)))


def windows_refs(found, notes):
    import winreg
    for hive, sub, hname in ((winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKCU"),
                             (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Run", "HKLM"),
                             (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "HKLM32"),
                             (winreg.HKEY_CURRENT_USER, r"Environment", "HKCU-env"),
                             (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "HKLM-env")):
        try:
            with winreg.OpenKey(hive, sub) as k:
                i = 0
                while True:
                    try:
                        n, v, _ = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if sub.endswith("Environment") and n.lower() != "path":
                        continue
                    parts = str(v).split(";") if n.lower() == "path" else [str(v)]
                    for part in parts:
                        for p in candidates(part):
                            found.append(("run-key:%s:%s" % (hname, n) if "env" not in hname else "path:" + hname, p))
        except OSError:
            pass
    script = r"""
$sh = New-Object -ComObject WScript.Shell
foreach ($d in @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\Startup")) {
  Get-ChildItem -LiteralPath $d -File | ForEach-Object {
    $t = ''; if ($_.Extension -eq '.lnk') { $l = $sh.CreateShortcut($_.FullName); $t = "$($l.TargetPath) $($l.Arguments) $($l.WorkingDirectory)" }
    "STARTUP`t$($_.Name)`t$($_.FullName) $t" } }
Get-ScheduledTask | Where-Object { $_.State -ne 'Disabled' -and $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
  $n = $_.TaskPath + $_.TaskName; foreach ($a in $_.Actions) { if ($a.Execute) { "TASK`t$n`t$($a.Execute) $($a.Arguments) $($a.WorkingDirectory)" } } }
Get-CimInstance Win32_Service | Where-Object { $_.StartMode -ne 'Disabled' -and $_.PathName } | ForEach-Object { "SERVICE`t$($_.Name)`t$($_.PathName)" }
Get-CimInstance Win32_Process | ForEach-Object { "PROC`t$($_.Name)`t$($_.ProcessId)`t$($_.ExecutablePath) $($_.CommandLine)" }
"""
    rc, out = C.powershell(script, timeout=180)
    if rc == -1:
        notes.append("PowerShell unavailable: startup items, tasks, services and processes NOT checked")
        return
    me = os.getpid()
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        kind, name = parts[0], parts[1]
        if kind == "PROC":
            if len(parts) < 4:
                continue
            base = os.path.splitext(name)[0].lower()
            if base in TRANSIENT or parts[2] == str(me):
                continue
            text = parts[3]
            src = "process:" + name
        else:
            text = parts[2]
            src = {"STARTUP": "startup-folder", "TASK": "scheduled-task", "SERVICE": "service"}.get(kind, kind) + ":" + name
        for p in candidates(text):
            found.append((src, p))


def mac_refs(found, notes):
    import plistlib
    for d in (os.path.join(C.HOME, "Library", "LaunchAgents"), "/Library/LaunchAgents", "/Library/LaunchDaemons"):
        try:
            names = sorted(os.listdir(d))
        except OSError:
            continue
        for n in names:
            if not n.endswith(".plist"):
                continue
            p = os.path.join(d, n)
            try:
                with open(p, "rb") as f:
                    data = plistlib.load(f)
            except Exception:
                continue
            if data.get("Disabled") is True:
                continue
            vals = [data.get("Program") or "", data.get("WorkingDirectory") or ""] + list(data.get("ProgramArguments") or [])
            vals += [str(v) for v in (data.get("EnvironmentVariables") or {}).values()]
            for v in vals:
                for c in candidates(str(v)):
                    found.append(("launchd:" + n, c))
    rc, out = C.run(["ps", "-axww", "-o", "pid=", "-o", "args="], timeout=30)
    me = str(os.getpid())
    for line in out.splitlines():
        line = line.strip()
        pid, _, args = line.partition(" ")
        if not args or pid == me:
            continue
        exe = os.path.basename(args.split(" ", 1)[0]).lower().lstrip("-")
        if exe in TRANSIENT:
            continue
        for c in candidates(args):
            found.append(("process:" + exe, c))
    cron_refs(found)


def linux_refs(found, notes):
    me = os.getpid()
    try:
        pids = [int(x) for x in os.listdir("/proc") if x.isdigit()]
    except OSError:
        pids = []
    for pid in pids:
        if pid == me:
            continue
        try:
            with open("/proc/%d/comm" % pid) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name.lower() in TRANSIENT:
            continue
        texts = []
        try:
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                texts += [a.decode("utf-8", "replace") for a in f.read().split(b"\0") if a]
        except OSError:
            pass
        for link in ("exe", "cwd"):
            try:
                texts.append(os.readlink("/proc/%d/%s" % (pid, link)))
            except OSError:
                pass
        for t in texts:
            for c in candidates(t):
                found.append(("process:" + name, c))
    unit_dirs = [os.path.join(C.xdg("XDG_CONFIG_HOME", ".config"), "systemd", "user"), "/etc/systemd/system", "/etc/systemd/user"]
    for d in unit_dirs:
        for root, _, files in os.walk(d):
            for n in files:
                if not n.endswith((".service", ".timer", ".socket", ".path")):
                    continue
                try:
                    with open(os.path.join(root, n), encoding="utf-8", errors="replace") as f:
                        for line in f:
                            if re.match(r"\s*(Exec\w*|WorkingDirectory|Environment\w*)\s*=", line):
                                for c in candidates(line.split("=", 1)[1]):
                                    found.append(("systemd:" + n, c))
                except OSError:
                    continue
    auto = os.path.join(C.xdg("XDG_CONFIG_HOME", ".config"), "autostart")
    try:
        for n in os.listdir(auto):
            if n.endswith(".desktop"):
                with open(os.path.join(auto, n), encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith(("Exec=", "Path=", "TryExec=")):
                            for c in candidates(line.split("=", 1)[1]):
                                found.append(("autostart:" + n, c))
    except OSError:
        pass
    cron_refs(found)


def cron_refs(found):
    if C.which("crontab"):
        rc, out = C.run(["crontab", "-l"], timeout=15)
        if rc == 0:
            for line in out.splitlines():
                if line.strip() and not line.strip().startswith("#"):
                    for c in candidates(line):
                        found.append(("crontab", c))


def protect_root(p):
    """The folder to protect for a live reference (see module docstring)."""
    d = p if os.path.isdir(p) else os.path.dirname(p)
    parts = d.split(os.sep)
    for i, part in enumerate(parts):
        if part in DEP_DIRS and i > 0:
            d = os.sep.join(parts[:i]) or os.sep
            break
    home_k = C.key(C.HOME)
    cur = d
    while True:
        ck = C.key(cur)
        if ck == home_k or not C.is_under(ck, home_k) and C.is_under(home_k, ck):
            break  # reached home or one of its parents: no project root found
        try:
            names = set(os.listdir(cur))
        except OSError:
            names = set()
        if any(m in names for m in PROJECT_MARKERS):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=C.default_out("live-refs"))
    a = ap.parse_args()
    found, notes = [], []
    agent_refs(found, notes)
    path_env_refs(found)
    if C.IS_WIN:
        windows_refs(found, notes)
    elif C.IS_MAC:
        mac_refs(found, notes)
    else:
        linux_refs(found, notes)

    home_k = C.key(C.HOME)
    keep = {}
    for src, p in found:
        root = protect_root(p)
        for cand in {root, os.path.realpath(root)}:
            k = C.key(cand)
            if k == home_k or C.is_under(home_k, k):
                continue  # home itself or a parent of it would block everything: skip
            if os.path.dirname(cand) == cand or C.is_system_path(cand):
                continue  # drive roots and OS folders: delete_paths never touches those anyway
            if k not in keep:
                keep[k] = {"path": cand, "sources": [], "examples": []}
            if src not in keep[k]["sources"]:
                keep[k]["sources"].append(src)
            if len(keep[k]["examples"]) < 3 and p not in keep[k]["examples"]:
                keep[k]["examples"].append(p)
    entries = sorted(keep.values(), key=lambda x: x["path"].lower())
    sensitive = [C.key(s) for s in C.sensitive_paths()]

    def is_broad(e):
        """A whole top-level home folder that is NOT itself a project (e.g. ~/Documents, ~/dev)."""
        k = C.key(e["path"])
        if C.depth_below(k, home_k) != 1 or any(C.is_under(k, s) for s in sensitive):
            return False
        try:
            return not any(m in os.listdir(e["path"]) for m in PROJECT_MARKERS)
        except OSError:
            return True

    broad = [e for e in entries if is_broad(e)]
    C.ensure_dir(a.out)
    with open(os.path.join(a.out, "live-refs.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("# live-refs: folders a running tool, agent config, startup item or scheduled job uses.\n")
        f.write("# generated %s on %s. Re-run before every delete.\n" % (C.now_stamp(), C.os_label()))
        for e in entries:
            f.write(e["path"] + "\n")
    by_src = {}
    for e in entries:
        for s in e["sources"]:
            k = s.split(":")[0]
            by_src[k] = by_src.get(k, 0) + 1
    in_home = [e for e in entries if C.is_under(C.key(e["path"]), home_k)]
    lines = ["live_refs: %d protected folders (%d under home)  by source: %s" % (
        len(entries), len(in_home), ", ".join("%s %d" % kv for kv in sorted(by_src.items())))]
    if broad:
        lines.append("BROAD: %d whole top-level home folders are in use, so nothing inside them will be cleaned:" % len(broad))
        lines += ["  %s  <- %s" % (C.tilde(e["path"]), ", ".join(e["sources"][:3])) for e in broad]
        lines.append("  (close that app and re-run, or ask the owner)")
    lines += ["NOTE: " + n for n in notes]
    lines.append("protect list: " + os.path.join(a.out, "live-refs.txt"))
    C.finish(a.out, "live-refs", {"entries": entries, "broad": [e["path"] for e in broad], "notes": notes}, lines)


if __name__ == "__main__":
    main()
