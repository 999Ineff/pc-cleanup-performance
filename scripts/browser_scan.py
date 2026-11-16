#!/usr/bin/env python3
"""READ-ONLY browser RAM audit (changes nothing).

Finds the installed browsers by their user-data folders (Chrome, Edge, Brave, Chromium, Vivaldi,
Opera on Windows, macOS and Linux, plus Firefox) and prints, per browser:
  - browser-wide settings that drive RAM (Chromium "Local State"): keep running in the background
    after closing, Memory Saver state and level, hardware acceleration
  - profiles, and per profile its extensions: on/off, runs a background worker, injects into every
    page (costs RAM in every tab), broad access (all site data, cookies, history), and antivirus
    add-ons whose desktop app is no longer installed
  - optional disk items: the on-device AI model folder and each profile's caches
  - (default) RAM of the running browser processes, grouped by kind: tabs, extensions, GPU,
    utility, main. Windows: private working set; Linux: PSS; macOS: RSS (overstates shared memory)

Privacy: reads only settings and extension manifests. Never opens cookies, history, saved
passwords or session files, and never prints account e-mails or tokens.
Which browser to look at is the owner's answer: pass --browser chrome,edge,... (default: all found).
Usage: python3 -I browser_scan.py [--browser NAMES] [--no-processes] [--no-disk] [--out DIR]
"""
import argparse
import configparser
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

AGGRESSIVENESS = {0: "Moderate", 1: "Balanced", 2: "Maximum"}
BROAD_HOSTS = {"<all_urls>", "*://*/*", "http://*/*", "https://*/*", "*://*/", "http://*/", "https://*/"}
AV_VENDORS = ["McAfee", "Norton", "Avast", "AVG", "Avira", "Bitdefender", "Kaspersky", "ESET", "Trend Micro",
              "Malwarebytes", "Sophos", "Webroot", "Panda", "F-Secure", "Total ?AV", "Total ?Defense"]
CHROMIUM_EXE = {"chrome": ("chrome", "google chrome"), "edge": ("msedge", "microsoft edge"), "brave": ("brave",),
                "chromium": ("chromium",), "vivaldi": ("vivaldi", "vivaldi-bin"), "opera": ("opera",)}


def chromium_dirs():
    """(key, label, user-data dir) for each Chromium-family browser on this OS."""
    H, j = C.HOME, os.path.join
    if C.IS_WIN:
        L = os.environ.get("LOCALAPPDATA", j(H, "AppData", "Local"))
        R = os.environ.get("APPDATA", j(H, "AppData", "Roaming"))
        cands = [("chrome", "Chrome", j(L, "Google", "Chrome", "User Data")),
                 ("edge", "Edge", j(L, "Microsoft", "Edge", "User Data")),
                 ("brave", "Brave", j(L, "BraveSoftware", "Brave-Browser", "User Data")),
                 ("chromium", "Chromium", j(L, "Chromium", "User Data")),
                 ("vivaldi", "Vivaldi", j(L, "Vivaldi", "User Data")),
                 ("opera", "Opera", j(R, "Opera Software", "Opera Stable")),
                 ("opera", "Opera GX", j(R, "Opera Software", "Opera GX Stable"))]
    elif C.IS_MAC:
        S = j(H, "Library", "Application Support")
        cands = [("chrome", "Chrome", j(S, "Google", "Chrome")), ("edge", "Edge", j(S, "Microsoft Edge")),
                 ("brave", "Brave", j(S, "BraveSoftware", "Brave-Browser")), ("chromium", "Chromium", j(S, "Chromium")),
                 ("vivaldi", "Vivaldi", j(S, "Vivaldi")), ("opera", "Opera", j(S, "com.operasoftware.Opera"))]
    else:
        X = C.xdg("XDG_CONFIG_HOME", ".config")
        cands = [("chrome", "Chrome", j(X, "google-chrome")), ("edge", "Edge", j(X, "microsoft-edge")),
                 ("brave", "Brave", j(X, "BraveSoftware", "Brave-Browser")), ("chromium", "Chromium", j(X, "chromium")),
                 ("chromium", "Chromium (snap)", j(H, "snap", "chromium", "common", "chromium")),
                 ("vivaldi", "Vivaldi", j(X, "vivaldi")), ("opera", "Opera", j(X, "opera"))]
    return [c for c in cands if os.path.isfile(os.path.join(c[2], "Local State"))]


def firefox_roots():
    H, j = C.HOME, os.path.join
    if C.IS_WIN:
        roots = [j(os.environ.get("APPDATA", j(H, "AppData", "Roaming")), "Mozilla", "Firefox")]
    elif C.IS_MAC:
        roots = [j(H, "Library", "Application Support", "Firefox")]
    else:
        roots = [j(H, ".mozilla", "firefox"), j(H, "snap", "firefox", "common", ".mozilla", "firefox"),
                 j(H, ".var", "app", "org.mozilla.firefox", ".mozilla", "firefox")]
    return [r for r in roots if os.path.isfile(os.path.join(r, "profiles.ini"))]


def load_json(p):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get(d, path, default=None):
    for k in path.split("."):
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


def installed_app_names():
    """Lower-case names of installed desktop apps (for spotting orphaned antivirus add-ons)."""
    names = []
    if C.IS_WIN:
        try:
            import winreg
            for hive, path in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                               (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                               (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")):
                try:
                    with winreg.OpenKey(hive, path) as k:
                        i = 0
                        while True:
                            try:
                                sub = winreg.EnumKey(k, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                with winreg.OpenKey(k, sub) as ak:
                                    names.append(str(winreg.QueryValueEx(ak, "DisplayName")[0]).lower())
                            except OSError:
                                continue
                except OSError:
                    continue
        except ImportError:
            pass
    elif C.IS_MAC:
        for d in ("/Applications", os.path.join(C.HOME, "Applications")):
            try:
                names += [n.lower() for n in os.listdir(d)]
            except OSError:
                pass
    return names


def access_flags(perms, hosts, content_matches):
    flags = []
    broad = bool((set(hosts) | set(perms)) & BROAD_HOSTS)
    if broad:
        flags.append("can read and change data on all sites")
    if "cookies" in perms and broad:
        flags.append("can read cookies of all sites")
    if "history" in perms:
        flags.append("can read browsing history")
    if "<all_urls>" in content_matches or set(content_matches) & BROAD_HOSTS:
        flags.append("injects into every page")
    return flags


def av_flag(name, apps):
    for v in AV_VENDORS:
        if re.search(r"\b" + v + r"\b", name, re.I):
            if C.IS_LINUX:
                return "antivirus add-on (check its desktop app is still installed)"
            vendor = v.replace(" ?", " ").lower()
            if not any(vendor.split()[0] in a for a in apps):
                return "antivirus add-on, but no %s desktop app is installed (likely left behind)" % v.replace(" ?", " ")
            return ""
    return ""


# ---------------------------------------------------------------- Chromium family

def manifest_for(pdir, eid, setting):
    """The extension's manifest (dict) and the folder it came from."""
    m = setting.get("manifest") if isinstance(setting.get("manifest"), dict) else None
    folder = ""
    path = setting.get("path") or ""
    if path and os.path.isabs(path) and os.path.isfile(os.path.join(path, "manifest.json")):
        folder = path  # unpacked extension
    else:
        base = os.path.join(pdir, "Extensions", eid)
        cand = [path] if path and not os.path.isabs(path) else []
        try:
            cand += sorted(os.listdir(base))
        except OSError:
            pass
        for v in reversed(cand):
            if os.path.isfile(os.path.join(base, v, "manifest.json")):
                folder = os.path.join(base, v)
                break
    if folder:
        m = load_json(os.path.join(folder, "manifest.json")) or m
    return m or {}, folder


def resolve_name(name, manifest, folder):
    if not name.startswith("__MSG_") or not folder:
        return name
    key = name[6:].rstrip("_").lower()
    for loc in (manifest.get("default_locale"), "en", "en_US"):
        if not loc:
            continue
        msgs = load_json(os.path.join(folder, "_locales", loc, "messages.json"))
        for k, v in msgs.items():
            if k.lower() == key and isinstance(v, dict):
                return v.get("message", name)
    return name


def chromium_profile(pdir, apps):
    prefs = load_json(os.path.join(pdir, "Preferences"))
    sec = load_json(os.path.join(pdir, "Secure Preferences"))
    settings = dict(get(prefs, "extensions.settings", {}) or {})
    settings.update(get(sec, "extensions.settings", {}) or {})
    exts = []
    try:
        folders = set(n for n in os.listdir(os.path.join(pdir, "Extensions")) if len(n) == 32)
    except OSError:
        folders = set()
    leftovers = len(folders - set(settings))  # folders the browser doesn't register: they never load
    for eid in sorted(settings):
        s = settings.get(eid) if isinstance(settings.get(eid), dict) else None
        if not s:
            continue
        if s.get("location") in (5, 10):
            continue  # component extensions that ship with the browser
        manifest, folder = manifest_for(pdir, eid, s)
        if not manifest:
            continue
        if manifest.get("theme") or "app" in manifest:
            continue  # themes and legacy Chrome apps don't run in pages
        name = resolve_name(str(manifest.get("name", "?")), manifest, folder)
        reasons = s.get("disable_reasons")
        enabled = s.get("state", 1) != 0 and not reasons
        bg = manifest.get("background") or {}
        worker = bool(isinstance(bg, dict) and (bg.get("service_worker") or bg.get("scripts") or bg.get("page")))
        perms = [p for p in manifest.get("permissions", []) if isinstance(p, str)]
        hosts = [h for h in manifest.get("host_permissions", []) if isinstance(h, str)]
        matches = []
        for cs in manifest.get("content_scripts", []) or []:
            if isinstance(cs, dict):
                matches += [m for m in cs.get("matches", []) if isinstance(m, str)]
        flags = access_flags(perms, hosts, matches)
        av = av_flag(name, apps)
        if av:
            flags.append(av)
        exts.append({"name": name, "enabled": enabled, "background_worker": worker, "flags": flags,
                     "site_access": "on click / specific sites" if s.get("withholding_permissions") else "as installed",
                     "unpacked": s.get("location") == 4})
    restore = get(prefs, "session.restore_on_startup")
    return {"on_startup": {1: "continue where you left off", 4: "open specific pages", 5: "new tab page"}.get(restore, "browser default"),
            "extensions": sorted(exts, key=lambda e: (not e["enabled"], e["name"].lower())), "leftover_folders": leftovers}


def scan_chromium(key, label, ud, apps, disk):
    ls = load_json(os.path.join(ud, "Local State"))
    pt = get(ls, "performance_tuning", {}) or {}
    state = get(pt, "high_efficiency_mode.state")
    aggr = get(pt, "high_efficiency_mode.aggressiveness")
    if state is None:
        saver = "not set (browser default)"
    else:
        saver = ("on, %s" % AGGRESSIVENESS.get(aggr, "level %s" % aggr)) if state == 2 else "off (state %s)" % state
    bgm = get(ls, "background_mode.enabled")
    hw = get(ls, "hardware_acceleration_mode.enabled")
    info = get(ls, "profile.info_cache", {}) or {}
    profiles = []
    if info:
        for pid, meta in info.items():
            pdir = os.path.join(ud, pid)
            if os.path.isdir(pdir):
                p = chromium_profile(pdir, apps)
                p.update(folder=pid, name=(meta or {}).get("name", pid) if isinstance(meta, dict) else pid)
                profiles.append(p)
    elif os.path.isfile(os.path.join(ud, "Preferences")):  # Opera: the user-data folder is the profile
        p = chromium_profile(ud, apps)
        p.update(folder=".", name="(single profile)")
        profiles.append(p)
    out = {"browser": label, "key": key, "user_data": ud,
           "keep_running_in_background": "not set (browser default)" if bgm is None else bool(bgm),
           "memory_saver": saver, "hardware_acceleration": "not set (on)" if hw is None else bool(hw),
           "profiles": profiles}
    if disk:
        items = []
        model = os.path.join(ud, "OptGuideOnDeviceModel")
        if os.path.isdir(model):
            items.append({"item": "on-device AI model", "bytes": C.tree_stats(model)["bytes"], "path": model})
        total = 0
        for p in profiles:
            pdir = ud if p["folder"] == "." else os.path.join(ud, p["folder"])
            for sub in ("Cache", "Code Cache", "GPUCache", os.path.join("Service Worker", "CacheStorage")):
                d = os.path.join(pdir, sub)
                if os.path.isdir(d):
                    total += C.tree_stats(d)["bytes"]
        items.append({"item": "caches (all profiles)", "bytes": total, "path": ud})
        out["disk"] = items
    return out


# ---------------------------------------------------------------- Firefox

def read_prefs_js(pdir, keys):
    vals = {}
    try:
        with open(os.path.join(pdir, "prefs.js"), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r'user_pref\("([^"]+)",\s*(.+)\);', line.strip())
                if m and m.group(1) in keys:
                    vals[m.group(1)] = m.group(2).strip().strip('"')
    except OSError:
        pass
    return vals


def scan_firefox(root, apps, disk):
    cp = configparser.RawConfigParser()
    try:
        cp.read(os.path.join(root, "profiles.ini"), encoding="utf-8")
    except (configparser.Error, OSError):
        return None
    profiles = []
    for sec in cp.sections():
        if not sec.startswith("Profile") or not cp.has_option(sec, "Path"):
            continue
        rel = cp.get(sec, "IsRelative", fallback="1") == "1"
        pdir = os.path.join(root, cp.get(sec, "Path")) if rel else cp.get(sec, "Path")
        if not os.path.isdir(pdir):
            continue
        data = load_json(os.path.join(pdir, "extensions.json"))
        exts = []
        for a in data.get("addons", []) or []:
            if a.get("type") != "extension" or a.get("location") in ("app-builtin", "app-system-defaults", "app-system-addons"):
                continue
            name = get(a, "defaultLocale.name") or a.get("id", "?")
            up = a.get("userPermissions") or {}
            perms = [p for p in up.get("permissions", []) if isinstance(p, str)]
            origins = [o for o in up.get("origins", []) if isinstance(o, str)]
            flags = access_flags(perms, origins, [])
            av = av_flag(name, apps)
            if av:
                flags.append(av)
            exts.append({"name": name, "enabled": bool(a.get("active")) and not a.get("userDisabled"),
                         "background_worker": None, "flags": flags, "site_access": "as installed", "unpacked": False})
        prefs = read_prefs_js(pdir, {"browser.tabs.unloadOnLowMemory", "browser.startup.page", "layers.acceleration.disabled"})
        profiles.append({"folder": os.path.basename(pdir), "name": cp.get(sec, "Name", fallback="?"),
                         "on_startup": "continue where you left off" if prefs.get("browser.startup.page") == "3" else "browser default",
                         "unload_tabs_on_low_memory": prefs.get("browser.tabs.unloadOnLowMemory", "not set (default: on)"),
                         "extensions": sorted(exts, key=lambda e: (not e["enabled"], e["name"].lower()))})
    out = {"browser": "Firefox", "key": "firefox", "user_data": root, "keep_running_in_background": "n/a",
           "memory_saver": "automatic tab unloading (per profile, below)", "hardware_acceleration": "see Settings > Performance",
           "profiles": profiles}
    if disk:
        out["disk"] = [{"item": "profile caches live under the OS cache folder; clear from Settings > Privacy > Cookies and Site Data",
                        "bytes": 0, "path": root}]
    return out


# ---------------------------------------------------------------- processes

def chromium_kind(cmd):
    m = re.search(r"--type=([\w-]+)", cmd)
    if not m:
        return "main (browser)"
    t = m.group(1)
    if t == "renderer":
        return "extensions" if "--extension-process" in cmd else "tabs (renderer)"
    if t == "gpu-process":
        return "gpu"
    if t == "utility":
        sub = re.search(r"--utility-sub-type=([\w.]+)", cmd)
        return "utility (%s)" % sub.group(1).split(".")[-1] if sub else "utility"
    return t


def firefox_kind(cmd):
    if "-contentproc" not in cmd:
        return "main (browser)"
    toks = cmd.split()
    for t in reversed(toks):
        if t in ("tab", "gpu", "rdd", "socket", "utility", "forkserver"):
            return {"tab": "tabs (content)", "rdd": "media decoding"}.get(t, t)
    return "content"


# Shells and tools whose command line merely MENTIONS a browser path are not browser processes.
NOT_BROWSERS = {"bash", "sh", "zsh", "dash", "fish", "ksh", "tcsh", "cmd", "powershell", "pwsh", "python", "python3",
                "perl", "node", "grep", "ps", "sudo", "env", "timeout", "tee", "less", "wsl", "ssh"}


def which_browser(text):
    argv0 = os.path.basename(text.strip().split(" ", 1)[0].replace("\\", "/")).lower()
    if os.path.splitext(argv0)[0] in NOT_BROWSERS or argv0.split(".")[0] in NOT_BROWSERS:
        return ""
    low = text.lower().replace("\\", "/")
    if re.search(r"(^|/)firefox(-bin|-esr)?(\.exe)?( |$)", low) or "/firefox.app/" in low:
        return "Firefox"
    for key, label in (("edge", "Edge"), ("brave", "Brave"), ("vivaldi", "Vivaldi"), ("opera", "Opera"), ("chromium", "Chromium"),
                       ("chrome", "Chrome")):
        for pat in CHROMIUM_EXE[key]:
            if re.search(r"(^|/)" + re.escape(pat) + r"(\.exe)?( |$|/|\.app)", low) or (pat + " helper") in low:
                return label
    if "com.apple.webkit.webcontent" in low or "/safari.app/" in low:
        return "Safari / WebKit"
    return ""


def processes():
    """{browser: {kind: bytes}} plus a note on what the numbers mean."""
    rows = []
    if C.IS_WIN:
        script = (r"$p=@{}; Get-CimInstance Win32_PerfFormattedData_PerfProc_Process | ForEach-Object { $p[[int]$_.IDProcess]=[double]$_.WorkingSetPrivate };"
                  r" Get-CimInstance Win32_Process -Filter \"Name='chrome.exe' OR Name='msedge.exe' OR Name='brave.exe' OR Name='vivaldi.exe' OR Name='opera.exe' OR Name='firefox.exe' OR Name='chromium.exe'\" |"
                  r" ForEach-Object { \"$($p[[int]$_.ProcessId])`t$($_.ExecutablePath) $($_.CommandLine)\" }")
        rc, out = C.powershell(script.replace('\\"', '"'), timeout=120)
        for line in out.splitlines():
            b, _, cmd = line.partition("\t")
            try:
                rows.append((int(float(b or 0)), cmd))
            except ValueError:
                continue
        note = "private working set (memory only this process uses)"
    elif C.IS_LINUX:
        for pid in [x for x in os.listdir("/proc") if x.isdigit()]:
            try:
                with open("/proc/%s/cmdline" % pid, "rb") as f:
                    cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
                if not which_browser(cmd):
                    continue
                kb = 0
                try:
                    with open("/proc/%s/smaps_rollup" % pid) as f:
                        for line in f:
                            if line.startswith("Pss:"):
                                kb = int(line.split()[1])
                except OSError:
                    with open("/proc/%s/status" % pid) as f:
                        for line in f:
                            if line.startswith("VmRSS:"):
                                kb = int(line.split()[1])
                rows.append((kb * 1024, cmd))
            except (OSError, ValueError):
                continue
        note = "PSS (shared memory split fairly between processes)"
    else:
        rc, out = C.run(["ps", "-axo", "rss=,args="], timeout=30)
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                rows.append((int(parts[0]) * 1024, parts[1]))
        note = "RSS (counts shared memory in every process, so totals run high; compare before/after the same way)"
    groups = {}
    for b, cmd in rows:
        br = which_browser(cmd)
        if not br:
            continue
        kind = firefox_kind(cmd) if br == "Firefox" else ("process" if br.startswith("Safari") else chromium_kind(cmd))
        g = groups.setdefault(br, {})
        k = g.setdefault(kind, [0, 0])
        k[0] += b
        k[1] += 1
    return groups, note


def main():
    ap = argparse.ArgumentParser(description="Read-only browser RAM audit.")
    ap.add_argument("--browser", default="", help="comma list: chrome,edge,brave,chromium,vivaldi,opera,firefox (default: all found)")
    ap.add_argument("--no-processes", action="store_true")
    ap.add_argument("--no-disk", action="store_true")
    ap.add_argument("--out", default=C.default_out("browsers"))
    a = ap.parse_args()
    want = set(x.strip().lower() for x in a.browser.split(",") if x.strip())
    apps = installed_app_names()
    results = []
    for key, label, ud in chromium_dirs():
        if not want or key in want:
            results.append(scan_chromium(key, label, ud, apps, not a.no_disk))
    if not want or "firefox" in want:
        for root in firefox_roots():
            r = scan_firefox(root, apps, not a.no_disk)
            if r:
                results.append(r)
    groups, note = ({}, "") if a.no_processes else processes()

    lines = ["browser_scan on %s: %d browser(s) found%s" % (C.os_label(), len(results),
                                                           " (filter: %s)" % ",".join(sorted(want)) if want else "")]
    if C.IS_MAC:
        lines.append("Safari: no settings file to read; tabs are managed by macOS. Use Activity Monitor (references/browsers.md).")
    for r in results:
        lines.append("")
        lines.append("=== %s  (%s)" % (r["browser"], C.tilde(r["user_data"])))
        lines.append("  keep running in background when closed: %s" % r["keep_running_in_background"])
        lines.append("  memory saver / tab sleeping: %s" % r["memory_saver"])
        lines.append("  hardware acceleration: %s" % r["hardware_acceleration"])
        lines.append("  profiles: %d (extensions are per profile; the settings above are browser-wide)" % len(r["profiles"]))
        for i, p in enumerate(r["profiles"], 1):
            on = [e for e in p["extensions"] if e["enabled"]]
            every = [e for e in on if "injects into every page" in e["flags"]]
            extra = ""
            if "unload_tabs_on_low_memory" in p:
                extra = ", unload tabs on low memory: %s" % p["unload_tabs_on_low_memory"]
            if p.get("leftover_folders"):
                extra += "; %d leftover extension folders (not registered, never load)" % p["leftover_folders"]
            lines.append("  profile %d [%s]: on startup: %s%s; extensions %d installed, %d on, %d injecting into every page" % (
                i, p["name"], p["on_startup"], extra, len(p["extensions"]), len(on), len(every)))
            for e in p["extensions"]:
                tags = []
                if e["enabled"] and e.get("background_worker"):
                    tags.append("background worker")
                if e["enabled"] and e.get("site_access", "").startswith("on click"):
                    tags.append("site access: on click")
                if e.get("unpacked"):
                    tags.append("unpacked")
                tags += e["flags"]
                lines.append("    [%s] %s%s" % ("ON " if e["enabled"] else "off", e["name"], ("  (" + "; ".join(tags) + ")") if tags else ""))
        for d in r.get("disk", []):
            if d["bytes"]:
                lines.append("  disk: %s %s" % (d["item"], C.human(d["bytes"])))
    if groups:
        lines.append("")
        lines.append("=== RAM of running browser processes (%s)" % note)
        for br, kinds in sorted(groups.items()):
            total = sum(v[0] for v in kinds.values())
            n = sum(v[1] for v in kinds.values())
            lines.append("  %s: %s in %d processes" % (br, C.human(total), n))
            for kind, (b, cnt) in sorted(kinds.items(), key=lambda kv: -kv[1][0]):
                lines.append("    %8s  x%-3d %s" % (C.human(b), cnt, kind))
    elif not a.no_processes:
        lines.append("")
        lines.append("no browser processes running right now (so nothing stays in the background after closing)")
    lines.append("")
    lines.append("Next: ask the owner which browser they use daily, then follow references/browsers.md for that one.")
    data = {"browsers": results, "processes": dict((b, dict((k, {"bytes": v[0], "count": v[1]}) for k, v in g.items()))
                                                   for b, g in groups.items()), "process_note": note}
    C.finish(a.out, "browsers", data, lines)


if __name__ == "__main__":
    main()
