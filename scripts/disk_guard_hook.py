#!/usr/bin/env python3
"""OPTIONAL Claude Code PreToolUse hook: stop agents from filling the disk.

Why: an overnight render or a burst of installs can fill a drive completely, and a full drive can
corrupt what lives on it (a WSL distro's disk file, databases, open documents).

  free < WARN_GB (default 50) -> deny heavy commands: renders, encodes, downloads, installs,
                                 clones, big copies, archive extraction, image pulls
  free < CRIT_GB (default 20) -> deny every shell command except inspection and cleanup

Free space = the lowest of: the home folder's drive, and inside WSL also the Windows system drive
(/mnt/c), where the distro's disk file lives. Fails open: any error inside the hook lets the
command run. Install: references/regrowth.md. Settings (environment variables of the hook):
  PCM_GUARD_WARN_GB, PCM_GUARD_CRIT_GB   thresholds
  PCM_GUARD_PATHS                        paths whose drives are checked (os.pathsep-separated)
  PCM_GUARD_FAKE_FREE_GB                 testing only: pretend this much is free
Pause for 2 hours (the owner, not the agent): create the file ~/.claude/disk-guard-off
"""
import json
import os
import re
import shutil
import sys
import time

WARN_GB = float(os.environ.get("PCM_GUARD_WARN_GB") or 50)
CRIT_GB = float(os.environ.get("PCM_GUARD_CRIT_GB") or 20)
OFF_FLAG = os.path.join(os.path.expanduser("~"), ".claude", "disk-guard-off")
OFF_FLAG_MAX_AGE_S = 2 * 3600

HEAVY = re.compile(r"""(?ix)
    \bffmpeg\b | \bremotion\b.*\brender\b | \b(npm|pnpm|yarn|bun)\s+(run\s+)?render\b | \bblender\b
  | \b(yt-dlp|youtube-dl|gallery-dl)\b
  | \b(npm|pnpm|yarn|bun)\s+(install|i|ci|add)\b
  | \bplaywright\b.*\binstall\b | \bpuppeteer\b.*\binstall\b
  | \bpip3?\s+install\b | \bpython3?\s+-m\s+pip\s+install\b
  | \buv\s+(pip\s+install|sync|add|tool\s+install)\b | \bconda\s+(install|create)\b
  | \bcargo\s+(build|install)\b | \bgo\s+(build|install|mod\s+download)\b | \b(mvn|gradle|gradlew)\b
  | \bgit\s+(clone|lfs\s+(pull|fetch))\b
  | \bdocker\s+(pull|build|run|load|compose\s+(up|pull|build))\b | \bpodman\s+(pull|build|run)\b
  | \bollama\s+(pull|run|create)\b | \b(huggingface-cli|hf)\s+download\b
  | \bcurl\b.*\s(-o|-O|--output|--remote-name)(\s|$)
  | \bwget\b | \b(invoke-webrequest|iwr|invoke-restmethod|irm)\b.*-outfile\b | \bstart-bitstransfer\b
  | \b(robocopy|xcopy|rsync|ditto)\b | \bcp\s+-\w*[rRa] | \bcopy-item\b.*-recurse\b
  | \b(7z|7za)\s+[ax]\b | \btar\s+-?\w*[cx] | \b(unzip|zip)\b | \b(expand-archive|compress-archive)\b
  | \bwsl(\.exe)?\b.*--(export|import)\b | \bhdiutil\s+create\b
""")

# Below CRIT_GB, every segment of a command must start with one of these.
SAFE_FIRST = {
    "ls", "dir", "du", "df", "find", "stat", "wc", "cat", "head", "tail", "less", "more", "grep", "rg", "echo", "printf",
    "pwd", "cd", "pushd", "popd", "which", "where", "whereis", "command", "type", "file", "tree", "date", "sleep", "true",
    "false", "exit", "test", "[", "sort", "uniq", "cut", "tr", "jq", "ps", "tasklist", "free", "uptime", "whoami",
    "hostname", "env", "printenv", "fsutil", "vm_stat",
    "rm", "rmdir", "del", "erase", "rd", "unlink", "trash", "trash-put",
    "get-childitem", "gci", "get-psdrive", "get-volume", "get-item", "gi", "get-itemproperty", "get-content", "gc",
    "measure-object", "measure", "select-object", "select", "sort-object", "where-object", "foreach-object", "%", "?",
    "format-table", "ft", "format-list", "fl", "test-path", "write-output", "write-host", "get-process", "get-date",
    "start-sleep", "remove-item", "ri", "clear-recyclebin", "set-location", "sl", "get-location", "resolve-path",
    "split-path", "join-path", "out-string", "get-ciminstance", "get-service",
}
# Tools allowed below CRIT_GB only for inspection / cleanup subcommands.
SAFE_TOOL_USE = re.compile(r"""(?ix)^(
    git\s+(status|log|diff|show|branch|remote|rev-parse|count-objects|gc|clean|prune
           |worktree\s+(list|prune|remove)|reflog\s+expire|stash\s+(list|drop|clear))
  | npm\s+(cache\s+(clean|verify|ls)|ls|prune|config\s+get)
  | pnpm\s+(store\s+(prune|status|path)|prune|ls)
  | yarn\s+cache\s+(clean|list|dir)
  | (pip3?|python3?\s+-m\s+pip)\s+(cache\s+(purge|remove|list|dir|info)|list|show|uninstall)
  | uv\s+cache\s+(clean|prune|dir) | go\s+clean | cargo\s+clean | brew\s+cleanup | gio\s+trash
  | tmutil\s+(listlocalsnapshots|thinlocalsnapshots|deletelocalsnapshots) | diskutil\s+(info|list|apfs\s+list)
  | docker\s+(system\s+(df|prune)|images?|ps|rmi|rm|(image|builder|container|volume|network)\s+(ls|prune|rm))
  | ollama\s+(rm|list|ls|ps)
  | (python3?|py)\s+(-\w+\s+)*\S*(delete_paths|scan_\w+|live_refs|sysinfo)\.py
  | journalctl\s+--vacuum | apt(-get)?\s+(clean|autoclean|autoremove) | snap\s+(list|remove)
  | wsl(\.exe)?\s+(--shutdown|-l|--list|--status|--terminate|-t)
)\b""")
SHELL_KEYWORDS = {"do", "then", "else", "elif", "if", "while", "until", "time", "sudo", "nohup", "&", "."}


def lowest_free_gb():
    fake = os.environ.get("PCM_GUARD_FAKE_FREE_GB")
    if fake:
        return float(fake), "test"
    paths = [p for p in (os.environ.get("PCM_GUARD_PATHS") or "").split(os.pathsep) if p]
    if not paths:
        paths = [os.path.expanduser("~")]
        if sys.platform.startswith("linux") and os.path.isdir("/mnt/c"):
            paths.append("/mnt/c")  # WSL: the distro's disk file lives on the Windows drive
    best = None
    for p in paths:
        try:
            free = shutil.disk_usage(p).free / 1024.0 ** 3
        except OSError:
            continue
        if best is None or free < best[0]:
            best = (free, p)
    return best if best else (None, "")


def segment_is_safe(seg):
    seg = seg.strip().strip("{}()").strip()
    if not seg or seg in {"done", "fi", "esac", "}", "{"}:
        return True
    words = seg.split()
    while words and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]) or words[0].lower() in SHELL_KEYWORDS):
        words.pop(0)
    if not words:
        return True
    rest = " ".join(words)
    if rest.startswith("$"):
        m = re.match(r"^\$[\w:.]+\s*=\s*(.*)$", rest)
        return segment_is_safe(m.group(1)) if m else True
    if rest[0] in "[(\"'":
        return True
    first = re.split(r"[\\/]", words[0].strip("\"'"))[-1].lower()
    if first.endswith(".exe"):
        first = first[:-4]
    if first in ("for", "foreach"):
        return segment_is_safe(rest.split("{", 1)[1] if "{" in rest else "")
    if first in SAFE_FIRST:
        return True
    return bool(SAFE_TOOL_USE.match(" ".join([first] + words[1:])))


def deny(reason, user_msg):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}, "systemMessage": user_msg}))
    sys.exit(0)


def main():
    data = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    if data.get("tool_name") not in ("Bash", "PowerShell"):
        return
    command = (data.get("tool_input") or {}).get("command") or ""
    if not command.strip():
        return
    try:
        if time.time() - os.path.getmtime(OFF_FLAG) < OFF_FLAG_MAX_AGE_S:
            return
    except OSError:
        pass
    free, where = lowest_free_gb()
    if free is None or free >= WARN_GB:
        return
    if free < CRIT_GB:
        segments = [s for s in re.split(r"&&|\|\||[;|\n]", command) if s.strip()]
        blocked = next((s.strip() for s in segments if not segment_is_safe(s)), None)
        if blocked:
            deny("Disk guard: the drive holding %s is critically low (%.1f GB free, limit %.0f GB). Only inspection and "
                 "cleanup commands may run. Blocked part: %r. Stop the current task and tell the user how full the drive is. "
                 "Do not try to work around this guard." % (where, free, CRIT_GB, blocked[:150]),
                 "Disk guard: blocked a command. %.1f GB free on the drive holding %s." % (free, where))
        return
    m = HEAVY.search(command)
    if m:
        deny("Disk guard: only %.0f GB free on the drive holding %s (limit %.0f GB) and this command looks heavy (matched %r: "
             "render, encode, download, install, clone or big copy). A full drive can corrupt what lives on it. Do not retry or "
             "work around this. Free space first, or stop and tell the user how much space the task needs."
             % (free, where, WARN_GB, m.group(0).strip()),
             "Disk guard: blocked a heavy command. %.0f GB free (limit %.0f GB)." % (free, WARN_GB))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # fail open: never break the agent because of the guard itself
