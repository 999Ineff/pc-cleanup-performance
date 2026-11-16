#!/usr/bin/env python3
"""READ-ONLY disk map (deletes nothing). One walk of the chosen roots, plus OS-specific extras.

Sections (all in disk.json; TSVs for the big tables):
  volumes   free / used of each local drive or mounted file system
  folders   biggest folders under each root down to --depth, hard-link aware: each hard-linked
            file counts once (macOS/Linux: every file; Windows: files of 1 MB or more plus
            everything inside uv/pnpm stores and pnpm node_modules; --exact-links = every file)
  files     biggest single files (disk images, VM disks, videos, installers)
  cloud     cloud-sync folders: bytes stored locally vs cloud-only placeholders
  known     known space hogs for this OS: size, class (SAFE / REVIEW / KEEP / INFO) and the
            official way to clear each one
  extras    read-only OS commands: restore points, hibernation, WSL/Docker disks, installed apps,
            Time Machine local snapshots, Homebrew, journal, snaps, apt autoremove, docker system df

On macOS, privacy-protected folders (Mail, Messages, app containers, Photos library, iCloud) are
skipped so the scan doesn't trigger permission prompts; System Settings > General > Storage
shows those.
Usage: python3 -I scan_disk.py [--root DIR ...] [--depth 3] [--top 40] [--exact-links]
                               [--no-extras] [--max-minutes N] [--out DIR]
"""
import argparse
import heapq
import os
import re
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

MAC_SKIP = ["Library/Containers", "Library/Group Containers", "Library/Mail", "Library/Messages",
            "Library/Mobile Documents", "Library/CloudStorage", "Library/Application Support/MobileSync",
            "Library/Safari", "Library/Cookies", "Library/Calendars", "Library/Metadata", "Library/Biome",
            "Library/Suggestions", "Library/Accounts", "Library/HomeKit", "Library/IdentityServices",
            "Library/Autosave Information", "Library/Daemon Containers", "Library/Reminders",
            "Pictures/Photos Library.photoslibrary", ".Trash"]


def volumes():
    out = []
    if C.IS_WIN:
        roots = C.fixed_drives()
    elif C.IS_LINUX:
        roots, seen = [], set()
        real_fs = {"ext2", "ext3", "ext4", "xfs", "btrfs", "zfs", "f2fs", "jfs", "reiserfs", "bcachefs",
                   "vfat", "exfat", "ntfs", "ntfs3", "fuseblk", "9p", "drvfs", "virtiofs"}
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    dev, mnt, fs = line.split()[:3]
                    mnt = mnt.replace("\\040", " ")
                    if fs in real_fs and mnt not in seen and not mnt.startswith(("/snap", "/boot/efi", "/usr/lib/wsl")):
                        seen.add(mnt)
                        roots.append(mnt)
        except OSError:
            roots = ["/"]
    else:
        # macOS: local file systems only (a disconnected network share in /Volumes can hang a stat)
        roots = ["/", "/System/Volumes/Data"]
        rc, out = C.run(["mount"], timeout=15)
        for line in out.splitlines():
            m = re.match(r".+? on (.+) \(([^)]*)\)$", line)
            if m and "local" in m.group(2) and m.group(2).split(",")[0].strip() in ("apfs", "hfs", "exfat", "msdos", "ntfs") \
                    and m.group(1).startswith("/Volumes/") and m.group(1) not in roots:
                roots.append(m.group(1))
    seen_dev = set()
    for r in roots:
        try:
            u = shutil.disk_usage(r)
        except OSError:
            continue
        sig = (u.total, u.free) if not C.IS_WIN else r
        if sig in seen_dev:
            continue
        seen_dev.add(sig)
        out.append({"mount": r, "total": u.total, "used": u.used, "free": u.free,
                    "free_pct": round(100.0 * u.free / u.total, 1) if u.total else 0})
    return out


def link_heavy(path_key, names):
    """Windows: subtrees where hard links are common, so exact (per-file) link counting is worth it."""
    if names is not None and ".pnpm" in names:
        return True
    tail = path_key.replace("\\", "/").rstrip("/")
    return tail.endswith(("/uv/cache", "/pnpm/store", "/.pnpm-store", "/.pnpm", "/pnpm-cache"))


def walk_map(roots, depth, top_n, exact_all, deadline):
    tracker = C.LinkTracker(exact=exact_all)
    folders, top_files = {}, []
    stats = {"files": 0, "unreadable": 0, "skipped": [], "partial": False}
    skip_keys = set()
    if C.IS_MAC:
        skip_keys = set(C.key(os.path.join(C.HOME, *s.split("/"))) for s in MAC_SKIP)
    for root in roots:
        try:
            root_dev = os.lstat(root).st_dev
        except OSError:
            continue
        rk = C.key(root)
        folders[rk] = {"path": root, "depth": 0, "bytes": 0, "cloud_bytes": 0, "files": 0, "newest": 0.0}
        stack = [(C.lp(root), (rk,), exact_all or not C.IS_WIN, 0)]
        while stack:
            if deadline and time.time() > deadline:
                stats["partial"] = True
                break
            d, chain, exact, lvl = stack.pop()
            items = C.list_dir(d)
            if items is None:
                stats["unreadable"] += 1
                continue
            names = None
            if C.IS_WIN and not exact:
                names = set(e.name for e, _, _ in items)
                exact = link_heavy(C.key(d), names)
            for e, kind, st in items:
                if kind in ("dir", "cloud_dir"):
                    if not C.IS_WIN and st.st_dev != root_dev:
                        continue  # another file system (mounts, /proc, network shares)
                    pk = C.key(e.path)
                    if pk in skip_keys:
                        stats["skipped"].append(C.sp(e.path))
                        continue
                    sub_exact = exact or (C.IS_WIN and link_heavy(pk, None))
                    new_chain = chain
                    if lvl + 1 <= depth:
                        folders[pk] = {"path": C.sp(e.path), "depth": lvl + 1, "bytes": 0, "cloud_bytes": 0,
                                       "files": 0, "newest": 0.0, "cloud": kind == "cloud_dir"}
                        new_chain = chain + (pk,)
                    stack.append((e.path, new_chain, sub_exact, lvl + 1))
                elif kind == "file":
                    stats["files"] += 1
                    if stats["files"] % 250000 == 0:
                        C.progress("scan_disk: %d files..." % stats["files"])
                    cloud = C.is_cloud_only(st)
                    if cloud:
                        size, counted = st.st_size, True
                    else:
                        size = C.local_bytes(st)
                        counted = tracker.first_time(e.path, st, force_exact=exact)
                    nw = C.newest_ts(st)
                    for fk in chain:
                        f = folders[fk]
                        f["files"] += 1
                        if nw > f["newest"]:
                            f["newest"] = nw
                        if cloud:
                            f["cloud_bytes"] += size
                        elif counted:
                            f["bytes"] += size
                    if not cloud and counted:
                        if len(top_files) < top_n:
                            heapq.heappush(top_files, (size, C.sp(e.path), nw))
                        elif size > top_files[0][0]:
                            heapq.heapreplace(top_files, (size, C.sp(e.path), nw))
    stats["linked_files"] = tracker.linked_files
    return folders, sorted(top_files, reverse=True), stats


# ---------------------------------------------------------------- known locations

def env(name, default):
    return os.environ.get(name) or default


def known_items():
    """(name, [paths], class, how to clear, reference) for this OS."""
    H = C.HOME
    if C.IS_WIN:
        L = env("LOCALAPPDATA", os.path.join(H, "AppData", "Local"))
        sysd = env("SystemDrive", "C:") + "\\"
        win = env("SystemRoot", sysd + "Windows")
        pd = env("ProgramData", sysd + "ProgramData")
        items = [
            ("Recycle Bin", [os.path.join(d, "$Recycle.Bin") for d in C.fixed_drives()], "SAFE",
             "Empty the Recycle Bin after a last look (Storage Sense can do it on a schedule)", "windows.md"),
            ("Temp files (user)", [env("TEMP", os.path.join(L, "Temp"))], "SAFE",
             "Settings > System > Storage > Temporary files (skips files in use)", "windows.md"),
            ("Temp files (Windows)", [os.path.join(win, "Temp")], "SAFE", "Disk Cleanup as admin / Storage > Temporary files", "windows.md"),
            ("Windows Update downloads", [os.path.join(win, "SoftwareDistribution", "Download")], "SAFE",
             "Storage > Temporary files > Windows Update Cleanup (admin)", "windows.md"),
            ("Delivery Optimization cache", [os.path.join(win, "ServiceProfiles", "NetworkService", "AppData", "Local", "Microsoft", "Windows", "DeliveryOptimization", "Cache")],
             "SAFE", "Storage > Temporary files > Delivery Optimization Files", "windows.md"),
            ("Previous Windows install", [sysd + "Windows.old"], "REVIEW",
             "Storage > Temporary files > Previous Windows installation(s): removes the option to roll back", "windows.md"),
            ("Crash dumps", [os.path.join(L, "CrashDumps"), os.path.join(win, "Minidump"), os.path.join(win, "MEMORY.DMP")], "SAFE",
             "Delete after any crash has been looked at (Storage > Temporary files)", "windows.md"),
            ("Error reports (WER)", [os.path.join(L, "Microsoft", "Windows", "WER"), os.path.join(pd, "Microsoft", "Windows", "WER")], "SAFE",
             "Storage > Temporary files > Windows error reports", "windows.md"),
            ("Chrome caches", [os.path.join(L, "Google", "Chrome", "User Data")], "INFO",
             "Clear from the browser (Delete browsing data > Cached images and files); size shown is the whole profile", "windows.md"),
            ("Chrome on-device AI model", [os.path.join(L, "Google", "Chrome", "User Data", "OptGuideOnDeviceModel")], "REVIEW",
             "Comes back after Chrome updates unless turned off in chrome://flags (optimization-guide-on-device-model); "
             "the owner removes any leftover by hand with Chrome closed (browser folders are never deleted by script)", "windows.md"),
            ("Edge profile", [os.path.join(L, "Microsoft", "Edge", "User Data")], "INFO", "Clear caches from the browser", "windows.md"),
            ("VirtualBox VMs", [os.path.join(H, "VirtualBox VMs")], "REVIEW", "Delete unused VMs from VirtualBox itself", "windows.md"),
            ("Hyper-V disks", [os.path.join(pd, "Microsoft", "Windows", "Virtual Hard Disks")], "REVIEW", "Remove unused VMs in Hyper-V Manager", "windows.md"),
            ("Installer cache (keep)", [os.path.join(win, "Installer")], "KEEP",
             "Never delete by hand: needed to repair and uninstall apps", "windows.md"),
            ("Package cache (keep)", [os.path.join(pd, "Package Cache")], "KEEP", "Needed by installers; leave it", "windows.md"),
        ]
        return items
    if C.IS_MAC:
        return [
            ("Trash", [os.path.join(H, ".Trash")], "SAFE", "Empty the Trash in Finder after a last look", "macos.md"),
            ("User caches", [os.path.join(H, "Library", "Caches")], "SAFE",
             "Per app, with that app quit (apps rebuild caches). Never the whole folder at once", "macos.md"),
            ("User logs", [os.path.join(H, "Library", "Logs")], "SAFE", "Old logs and crash reports can go", "macos.md"),
            ("System caches", ["/Library/Caches"], "INFO", "Leave to macOS (needs admin)", "macos.md"),
            ("iOS device backups", [os.path.join(H, "Library", "Application Support", "MobileSync", "Backup")], "REVIEW",
             "Finder > (device) > Manage Backups: delete old ones there (sizing needs Full Disk Access)", "macos.md"),
            ("iOS/iPadOS update files", [os.path.join(H, "Library", "iTunes", "iPhone Software Updates"),
                                         os.path.join(H, "Library", "iTunes", "iPad Software Updates")], "SAFE",
             "Old .ipsw files: re-downloaded when needed", "macos.md"),
            ("macOS installers", [os.path.join("/Applications", n) for n in _ls("/Applications") if n.startswith("Install macOS")],
             "REVIEW", "Drag to Trash if you don't need a bootable installer", "macos.md"),
            ("Sleep image and swap", ["/private/var/vm"], "INFO", "Managed by macOS; shrinks after a restart", "macos.md"),
            ("Messages attachments", [os.path.join(H, "Library", "Messages", "Attachments")], "REVIEW",
             "Messages > Settings > Keep messages, or Storage > Messages (needs Full Disk Access to size)", "macos.md"),
            ("GarageBand / Logic sounds", ["/Library/Application Support/GarageBand", "/Library/Application Support/Logic",
                                           "/Library/Audio/Apple Loops"], "REVIEW", "Remove from the app's Sound Library menu", "macos.md"),
            ("Photos library (keep)", [os.path.join(H, "Pictures", "Photos Library.photoslibrary")], "KEEP",
             "Not sized (privacy). Use iCloud Photos + Optimize Mac Storage to shrink it", "macos.md"),
        ]
    cache = C.xdg("XDG_CACHE_HOME", ".cache")
    return [
        ("Trash", [os.path.join(C.xdg("XDG_DATA_HOME", os.path.join(".local", "share")), "Trash")], "SAFE",
         "Empty the Trash after a last look", "linux.md"),
        ("User cache", [cache], "SAFE", "Per app, with that app closed; never the whole folder at once", "linux.md"),
        ("Thumbnails", [os.path.join(cache, "thumbnails")], "SAFE", "Rebuilt on demand", "linux.md"),
        ("apt package cache", ["/var/cache/apt/archives"], "SAFE", "sudo apt-get clean", "linux.md"),
        ("dnf/yum cache", ["/var/cache/dnf", "/var/cache/yum"], "SAFE", "sudo dnf clean all", "linux.md"),
        ("pacman cache", ["/var/cache/pacman/pkg"], "SAFE", "sudo paccache -rk2 (pacman-contrib) or sudo pacman -Sc", "linux.md"),
        ("zypper cache", ["/var/cache/zypp/packages"], "SAFE", "sudo zypper clean --all", "linux.md"),
        ("systemd journal", ["/var/log/journal"], "SAFE", "sudo journalctl --vacuum-size=200M", "linux.md"),
        ("Logs", ["/var/log"], "INFO", "Rotated by logrotate; the journal is the usual big one", "linux.md"),
        ("Snap packages", ["/var/lib/snapd/snaps"], "SAFE", "Old (disabled) revisions: see extras.snap_disabled", "linux.md"),
        ("Flatpak (system)", ["/var/lib/flatpak"], "SAFE", "flatpak uninstall --unused", "linux.md"),
        ("Flatpak (user)", [os.path.join(C.HOME, ".local", "share", "flatpak")], "SAFE", "flatpak uninstall --user --unused", "linux.md"),
        ("Core dumps", ["/var/lib/systemd/coredump", "/var/crash"], "SAFE", "sudo rm the old dumps after a look (coredumpctl list)", "linux.md"),
        ("Temp", ["/tmp", "/var/tmp"], "INFO", "Cleared at boot / by systemd-tmpfiles", "linux.md"),
    ]


def _ls(d):
    try:
        return sorted(os.listdir(d))
    except OSError:
        return []


def measure_known(items, exact):
    rows = []
    for name, paths, cls, how, ref in items:
        total, partial, present, sized = 0, False, [], True
        for p in paths:
            if not os.path.lexists(p):
                continue
            present.append(p)
            if (cls == "KEEP" and C.IS_WIN) or "Photos Library" in p:
                sized = False  # huge, never cleaned by hand (or privacy-protected): not worth the walk
                continue
            if os.path.isfile(p):
                try:
                    total += C.local_bytes(os.lstat(p))
                except OSError:
                    partial = True
                continue
            s = C.tree_stats(p, exact_links=exact)
            total += s["bytes"]
            partial = partial or s["unreadable"] > 0
        if present:
            rows.append({"name": name, "class": cls, "bytes": total, "sized": sized, "partial": partial, "paths": present,
                         "how": how, "ref": ref})
    return sorted(rows, key=lambda r: -r["bytes"])


# ---------------------------------------------------------------- extras (read-only OS commands)

def docker_df():
    if not C.which("docker"):
        return None
    rc, out = C.run(["docker", "system", "df"], timeout=30)
    return out.strip() if rc == 0 else "docker installed but not running (start it to see image/volume sizes)"


def extras_windows():
    import winreg
    ex = {}

    def reg(hive, path, name):
        try:
            with winreg.OpenKey(hive, path) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            return None
    ex["storage_sense_on"] = reg(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy", "01") == 1
    ex["developer_mode_on"] = reg(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock", "AllowDevelopmentWithoutDevLicense") == 1
    sysd = os.environ.get("SystemDrive", "C:") + "\\"
    sysfiles = {}
    try:
        for e in os.scandir(sysd):
            if e.name.lower() in ("hiberfil.sys", "pagefile.sys", "swapfile.sys"):
                try:
                    sysfiles[e.name] = e.stat(follow_symlinks=False).st_size
                except OSError:
                    pass
    except OSError:
        pass
    ex["system_files"] = sysfiles
    rc, out = C.run(["vssadmin", "list", "shadowstorage", "/for=" + sysd.rstrip("\\")], timeout=30)
    used = re.search(r"Used Shadow Copy Storage space:\s*(.+)", out)
    mx = re.search(r"Maximum Shadow Copy Storage space:\s*(.+)", out)
    ex["restore_points"] = ({"used": used.group(1).strip(), "max": mx.group(1).strip() if mx else "?"} if used
                            else "needs an elevated shell to read (or no shadow storage)")
    distros = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Lxss") as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                with winreg.OpenKey(k, sub) as dk:
                    try:
                        name = winreg.QueryValueEx(dk, "DistributionName")[0]
                        base = C.sp(winreg.QueryValueEx(dk, "BasePath")[0])
                    except OSError:
                        continue
                vhd = os.path.join(base, "ext4.vhdx")
                distros.append({"name": name, "vhdx": vhd, "bytes": os.path.getsize(vhd) if os.path.isfile(vhd) else None})
    except OSError:
        pass
    ex["wsl_distros"] = distros
    L = os.environ.get("LOCALAPPDATA", os.path.join(C.HOME, "AppData", "Local"))
    dvhd, known_vhd = [], set(C.key(d["vhdx"]) for d in distros)
    for root, _, files in os.walk(os.path.join(L, "Docker", "wsl")):
        for f in files:
            p = os.path.join(root, f)
            if f.lower().endswith(".vhdx") and C.key(p) not in known_vhd:
                dvhd.append({"vhdx": p, "bytes": os.path.getsize(p)})
    ex["docker_disks"] = dvhd
    apps = []
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
                            name = winreg.QueryValueEx(ak, "DisplayName")[0]
                            size = winreg.QueryValueEx(ak, "EstimatedSize")[0] * 1024
                    except (OSError, TypeError):
                        continue
                    if reg(hive, path + "\\" + sub, "SystemComponent") == 1:
                        continue
                    apps.append({"name": name, "bytes": size})
        except OSError:
            pass
    dedup = {}
    for a in apps:
        if a["bytes"] > dedup.get(a["name"], 0):
            dedup[a["name"]] = a["bytes"]
    ex["installed_apps_top"] = [{"name": n, "bytes": b} for n, b in sorted(dedup.items(), key=lambda x: -x[1])[:25]]
    ex["docker_df"] = docker_df()
    return ex


def extras_macos():
    ex = {}
    rc, out = C.run(["tmutil", "listlocalsnapshots", "/"], timeout=30)
    ex["time_machine_local_snapshots"] = len([l for l in out.splitlines() if "com.apple" in l]) if rc == 0 else "unavailable"
    if C.which("brew"):
        rc, cache = C.run(["brew", "--cache"], timeout=30)
        cache = cache.strip()
        ex["homebrew_cache_bytes"] = C.tree_stats(cache)["bytes"] if cache and os.path.isdir(cache) else 0
        rc, out = C.run(["brew", "cleanup", "-n", "-s"], timeout=180)
        m = re.search(r"would free approximately ([\d.]+\s*\w+)", out)
        ex["homebrew_cleanup_would_free"] = m.group(1) if m else "nothing (or brew cleanup -n printed no total)"
    ex["docker_df"] = docker_df()
    return ex


def extras_linux():
    ex = {}
    if C.which("journalctl"):
        rc, out = C.run(["journalctl", "--disk-usage"], timeout=30)
        m = re.search(r"take up ([\d.]+\s*\w+)", out)
        ex["journal"] = m.group(1) if m else (out.strip()[:120] or "unreadable (needs the systemd-journal group or sudo)")
    if C.which("snap"):
        rc, out = C.run(["snap", "list", "--all"], timeout=30)
        dis = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and "disabled" in line:
                name, rev = parts[0], parts[2]
                f = "/var/lib/snapd/snaps/%s_%s.snap" % (name, rev)
                dis.append({"name": name, "revision": rev, "bytes": os.path.getsize(f) if os.path.isfile(f) else None,
                            "remove": "sudo snap remove %s --revision=%s" % (name, rev)})
        ex["snap_disabled"] = dis
    if C.which("apt-get"):
        rc, out = C.run(["apt-get", "-s", "autoremove"], timeout=60)
        ex["apt_autoremove_packages"] = len([l for l in out.splitlines() if l.startswith("Remv ")]) if rc == 0 else "unavailable"
    if C.which("flatpak"):
        rc, out = C.run(["flatpak", "list", "--columns=application"], timeout=30)
        ex["flatpak_installed"] = len(out.splitlines()) if rc == 0 else "unavailable"
    if C.is_wsl():
        rc, src = C.run(["findmnt", "-no", "SOURCE", "/"], timeout=10)
        dev = os.path.basename(src.strip())
        try:
            with open("/sys/fs/ext4/%s/lifetime_write_kbytes" % dev) as f:
                ex["wsl_ext4_lifetime_writes_gb"] = round(int(f.read().strip()) / 1048576.0, 1)
        except (OSError, ValueError):
            pass
        ex["wsl_note"] = "This distro's disk is a VHDX file on Windows: deleting here frees Windows space only after compaction (references/wsl.md)"
    ex["docker_df"] = docker_df()
    return ex


def main():
    ap = argparse.ArgumentParser(description="Read-only disk map.")
    ap.add_argument("--root", action="append", default=[], help="folder to map (default: home). Repeatable")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--exact-links", action="store_true", help="Windows: count hard links exactly everywhere (slower)")
    ap.add_argument("--no-extras", action="store_true", help="skip the OS command section")
    ap.add_argument("--max-minutes", type=float, default=0, help="stop the walk after N minutes (results marked partial)")
    ap.add_argument("--out", default=C.default_out("disk"))
    a = ap.parse_args()
    t0 = time.time()
    roots = [os.path.abspath(os.path.expanduser(r)) for r in a.root] or [C.HOME]
    vols = volumes()
    C.progress("scan_disk: walking %s (depth %d)..." % (", ".join(C.tilde(r) for r in roots), a.depth))
    deadline = t0 + a.max_minutes * 60 if a.max_minutes else 0
    folders, top_files, stats = walk_map(roots, a.depth, a.top, a.exact_links, deadline)
    C.progress("scan_disk: %d files walked (%s); measuring known locations..." % (stats["files"], C.elapsed(t0)))
    known = measure_known(known_items(), a.exact_links)
    cloud = []
    for r in C.cloud_roots():
        if C.IS_MAC:
            cloud.append({"path": r, "note": "not sized (privacy-protected); see System Settings > Apple Account > iCloud"})
            continue
        s = C.tree_stats(r)
        cloud.append({"path": r, "local_bytes": s["bytes"], "cloud_only_bytes": s["cloud_bytes"], "cloud_only_files": s["cloud_files"]})
    extras = {}
    if not a.no_extras:
        try:
            extras = extras_windows() if C.IS_WIN else extras_macos() if C.IS_MAC else extras_linux()
        except Exception as e:  # extras are best-effort; the map above is the main result
            extras = {"error": str(e)}
    frows = sorted(folders.values(), key=lambda f: -f["bytes"])
    C.write_tsv(os.path.join(a.out, "folders.tsv"), ["gb", "cloud_only_gb", "files", "newest", "depth", "path"],
                [(C.gb(f["bytes"]), C.gb(f["cloud_bytes"]), f["files"], C.day(f["newest"]), f["depth"], f["path"]) for f in frows])
    C.write_tsv(os.path.join(a.out, "files.tsv"), ["gb", "newest", "path"], [(C.gb(s), C.day(t), p) for s, p, t in top_files])
    C.write_tsv(os.path.join(a.out, "known.tsv"), ["gb", "class", "name", "how", "partial", "paths"],
                [(C.gb(k["bytes"]), k["class"], k["name"], k["how"], k["partial"], " | ".join(k["paths"])) for k in known])

    lines = ["scan_disk on %s, %d files in %s%s" % (C.os_label(), stats["files"], C.elapsed(t0),
                                                    "  (PARTIAL: stopped at --max-minutes)" if stats["partial"] else "")]
    for v in vols:
        lines.append("  %-28s %8s free of %8s (%s%% free)" % (v["mount"], C.human(v["free"]), C.human(v["total"]), v["free_pct"]))
    lines.append("biggest folders (local bytes; cloud-only shown separately):")
    shown = 0
    for f in frows:
        if f["depth"] == 0:
            continue
        lines.append("  %8s  %s%s" % (C.human(f["bytes"]), C.tilde(f["path"]),
                                       "  (+%s cloud-only)" % C.human(f["cloud_bytes"]) if f["cloud_bytes"] else ""))
        shown += 1
        if shown >= 20:
            break
    lines.append("biggest files:")
    lines += ["  %8s  %s" % (C.human(s), C.tilde(p)) for s, p, _ in top_files[:12]]
    if cloud:
        lines.append("cloud-sync folders (never delete inside; use the provider's free-up-space option):")
        for c in cloud:
            if "local_bytes" in c:
                lines.append("  %s: %s stored locally, %s cloud-only" % (C.tilde(c["path"]), C.human(c["local_bytes"]), C.human(c["cloud_only_bytes"])))
            else:
                lines.append("  %s: %s" % (C.tilde(c["path"]), c["note"]))
    lines.append("known space hogs (>= 100 MB):")
    for k in known:
        if k["bytes"] >= 100 * 1024 ** 2:
            lines.append("  %8s  %-6s %s%s: %s" % (C.human(k["bytes"]), k["class"], k["name"], " (partial: some folders need admin)" if k["partial"] else "", k["how"]))
    if extras:
        lines.append("extras: " + ", ".join("%s=%s" % (k, v) for k, v in extras.items()
                                            if not isinstance(v, (list, dict)) and k not in ("docker_df", "wsl_note") and v is not None))
        for d in extras.get("wsl_distros", []):
            lines.append("  WSL %s disk file: %s  (%s)" % (d["name"], C.human(d["bytes"]), d["vhdx"]))
        for d in extras.get("docker_disks", []):
            lines.append("  Docker disk file: %s  (%s)" % (C.human(d["bytes"]), d["vhdx"]))
        if isinstance(extras.get("restore_points"), dict):
            lines.append("  restore points: used %s, max %s" % (extras["restore_points"]["used"], extras["restore_points"]["max"]))
        for n, b in (extras.get("system_files") or {}).items():
            lines.append("  %s: %s" % (n, C.human(b)))
        for s in extras.get("snap_disabled", [])[:10]:
            lines.append("  old snap %s rev %s: %s  -> %s" % (s["name"], s["revision"], C.human(s["bytes"]), s["remove"]))
        if extras.get("docker_df"):
            lines.append("  docker system df:\n    " + extras["docker_df"].replace("\n", "\n    "))
    if stats["skipped"]:
        lines.append("skipped (privacy-protected, not sized): %d folders, e.g. %s" % (len(stats["skipped"]), C.tilde(stats["skipped"][0])))
    data = {"roots": roots, "depth": a.depth, "volumes": vols, "stats": stats,
            "folders": [dict(f, path=f["path"]) for f in frows[:500]],
            "files": [{"bytes": s, "path": p, "newest": C.day(t)} for s, p, t in top_files],
            "cloud": cloud, "known": known, "extras": extras}
    C.finish(a.out, "disk", data, lines)


if __name__ == "__main__":
    main()
