"""Shared helpers for the pc-cleanup-performance scripts.

Standard library only, Python 3.8+. Nothing in this module deletes anything.

Version guards:
  - DirEntry.is_junction / os.path.isjunction (3.12+) are never used; Windows reparse
    points are classified from st_file_attributes / st_reparse_tag instead (3.8+).
  - hashlib.file_digest (3.11+) has a chunked fallback.
  - st_birthtime exists on macOS, BSD and Windows 3.12+; Linux falls back to ctime.
"""
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime

# A file name or extension name the console can't encode must never crash a scan.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):  # 3.7+
        try:
            _stream.reconfigure(errors="replace")
        except (ValueError, OSError):
            pass

IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")
HOME = os.path.abspath(os.path.expanduser("~"))
SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(SCRIPTS_DIR)

# Windows file attributes
_REPARSE = 0x400
_OFFLINE = 0x1000
_RECALL_ON_OPEN = 0x40000
_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ONLY_ATTRS = _OFFLINE | _RECALL_ON_OPEN | _RECALL_ON_DATA_ACCESS
# macOS st_flags bit for a "dataless" file whose content lives only in iCloud
_SF_DATALESS = 0x40000000

MEDIA_EXT = {
    ".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mxf", ".prores", ".braw", ".r3d", ".mts", ".m2ts",
    ".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".aac",
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".exr", ".dpx", ".webp", ".heic", ".psd", ".raw", ".dng", ".cr2", ".cr3", ".nef", ".arw",
}


# ---------------------------------------------------------------- OS detection

def is_wsl():
    if not IS_LINUX:
        return False
    if os.environ.get("WSL_DISTRO_NAME") or os.path.exists("/proc/sys/fs/binfmt_misc/WSLInterop"):
        return True
    try:
        with open("/proc/version") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def windows_mounts():
    """Inside WSL: mount points of Windows drives (/mnt/c ...). Elsewhere: []."""
    if not is_wsl():
        return []
    out = []
    try:
        with open("/proc/mounts") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 4 and (parts[2] in ("drvfs", "virtiofs") or "aname=drvfs" in parts[3]):
                    out.append(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return out


def os_label():
    """windows | macos | linux | wsl (Linux running inside Windows)."""
    if IS_WIN:
        return "windows"
    if IS_MAC:
        return "macos"
    if IS_LINUX:
        return "wsl" if is_wsl() else "linux"
    return sys.platform


# ---------------------------------------------------------------- paths

def lp(p):
    """Windows long-path form (\\\\?\\...) so deep trees can be walked. No-op elsewhere."""
    if not IS_WIN or p.startswith("\\\\?\\"):
        return p
    p = os.path.abspath(p)
    if p.startswith("\\\\"):
        return "\\\\?\\UNC\\" + p[2:]
    return "\\\\?\\" + p


def sp(p):
    """Undo lp() for display and for APIs that can't take the long-path prefix."""
    if IS_WIN:
        if p.startswith("\\\\?\\UNC\\"):
            return "\\\\" + p[8:]
        if p.startswith("\\\\?\\"):
            return p[4:]
    return p


def key(p):
    """Comparison key for a path: absolute, normalized, and case-folded where the file system
    usually ignores case (Windows, and macOS APFS by default). Over-matching is the safe side."""
    p = os.path.normcase(os.path.normpath(os.path.abspath(sp(p))))
    if IS_MAC:
        p = p.lower()
    return p


def is_under(child_key, parent_key):
    """True if child is parent or inside it (both arguments are key() values)."""
    if child_key == parent_key:
        return True
    parent = parent_key if parent_key.endswith(os.sep) else parent_key + os.sep
    return child_key.startswith(parent)


def overlaps(a_key, b_key):
    return is_under(a_key, b_key) or is_under(b_key, a_key)


def depth_below(child_key, parent_key):
    """How many path components child is below parent (0 = same, -1 = not inside)."""
    if child_key == parent_key:
        return 0
    if not is_under(child_key, parent_key):
        return -1
    rest = child_key[len(parent_key):].strip(os.sep)
    return len([x for x in rest.split(os.sep) if x])


def tilde(p):
    """Show a path with the home folder as ~ (for summaries)."""
    p = sp(p)
    if key(p) == key(HOME):
        return "~"
    if is_under(key(p), key(HOME)):
        return "~" + p[len(HOME):]
    return p


def expand(p):
    """Expand ~, $VAR / ${VAR} and %VAR% (Windows) in a path template."""
    return os.path.abspath(os.path.expandvars(os.path.expanduser(p)))


def xdg(var, default_rel):
    return os.environ.get(var) or os.path.join(HOME, default_rel)


# ---------------------------------------------------------------- file entries

def is_cloud_tag(tag):
    """Windows Cloud Files API reparse tags (OneDrive, and other providers using it)."""
    return (tag & 0xFFFF0FFF) == 0x9000001A


def classify(entry):
    """Classify a DirEntry without following anything.
    Returns (kind, stat) with kind in: dir, cloud_dir, file, link, other, error.
    'link' = symlinks, Windows junctions, mount points, app-exec links: never followed or counted."""
    try:
        if entry.is_symlink():
            return "link", None
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return "error", None
    if IS_WIN and getattr(st, "st_file_attributes", 0) & _REPARSE:
        tag = getattr(st, "st_reparse_tag", 0)
        is_dir = stat.S_ISDIR(st.st_mode)
        if is_cloud_tag(tag):
            return ("cloud_dir" if is_dir else "file"), st
        if is_dir:
            return "link", st
        return "file", st  # e.g. dedup or app-exec stubs: counted by size, never followed
    if stat.S_ISDIR(st.st_mode):
        return "dir", st
    if stat.S_ISREG(st.st_mode):
        return "file", st
    return "other", st


def list_dir(path):
    """[(DirEntry, kind, stat)] for a directory, or None if it can't be read."""
    try:
        with os.scandir(lp(path)) as it:
            entries = list(it)
    except OSError:
        return None
    return [(e,) + classify(e) for e in entries]


def is_cloud_only(st):
    """True for placeholders whose content lives only in the cloud (reading them downloads them)."""
    if st is None:
        return False
    if IS_WIN:
        return bool(getattr(st, "st_file_attributes", 0) & CLOUD_ONLY_ATTRS)
    if IS_MAC:
        return bool(getattr(st, "st_flags", 0) & _SF_DATALESS)
    return False


def local_bytes(st):
    """Bytes the file really occupies on this disk. Cloud-only placeholders count 0. On macOS and
    Linux the allocated blocks are used, so sparse disk images (Docker.raw) count what they use."""
    if is_cloud_only(st):
        return 0
    if IS_WIN:
        return st.st_size
    blocks = getattr(st, "st_blocks", None)
    return st.st_size if blocks is None else blocks * 512


def birth_ts(st):
    b = getattr(st, "st_birthtime", None)
    if b:
        return b
    if IS_WIN or IS_LINUX:
        # Windows < 3.12: st_ctime is the creation time. Linux: ctime is the last metadata change,
        # which is never older than creation, so it errs toward "recent" (the safe side).
        return st.st_ctime
    return 0.0


def newest_ts(st):
    """Newest of modified and created time: a file downloaded today but modified years ago is new."""
    return max(st.st_mtime, birth_ts(st))


class LinkTracker:
    """Counts each hard-linked file once (uv, pnpm, media pipelines and backup tools hard-link
    heavily, so a naive sum can overstate the real size by a third or more). On Windows,
    DirEntry.stat() reports st_nlink = 0, so the real link count costs one os.lstat() per file:
    exact=True pays that for every file; otherwise only files >= BIG_FILE are checked, which is
    cheap (big files are few) and catches the big hard-linked media and disk images."""
    BIG_FILE = 1024 * 1024

    def __init__(self, exact=True):
        self.exact = exact or not IS_WIN
        self.seen = set()
        self.linked_files = 0

    def first_time(self, path, st, force_exact=False):
        if IS_WIN:
            if not (self.exact or force_exact) and st.st_size < self.BIG_FILE:
                return True
            try:
                st = os.lstat(path)
            except OSError:
                return True
        if st.st_nlink <= 1:
            return True
        self.linked_files += 1
        k = (st.st_dev, st.st_ino)
        if k in self.seen:
            return False
        self.seen.add(k)
        return True


def tree_stats(root, exact_links=None, frees=False, same_fs=True, skip_names=(), media=False):
    """Read-only size of a folder.
    Returns dict: bytes (each hard-linked inode once), files, newest, cloud_files, cloud_bytes,
    linked_files, unreadable, and with frees=True also frees_bytes = what deleting the whole folder
    would really free (inodes whose every link is inside it). media=True adds media_bytes."""
    if exact_links is None:
        exact_links = not IS_WIN
    tracker = LinkTracker(exact=exact_links or frees)
    res = {"bytes": 0, "files": 0, "newest": 0.0, "cloud_files": 0, "cloud_bytes": 0, "linked_files": 0,
           "unreadable": 0, "exact_links": bool(exact_links or frees or not IS_WIN)}
    if media:
        res["media_bytes"] = 0
    link_count, link_size, link_nlink = {}, {}, {}
    plain = 0
    try:
        root_dev = os.lstat(lp(root)).st_dev
    except OSError:
        res["unreadable"] = 1
        return res
    skip = set(skip_names)
    stack = [lp(root)]
    while stack:
        d = stack.pop()
        items = list_dir(d)
        if items is None:
            res["unreadable"] += 1
            continue
        for e, kind, st in items:
            if kind in ("dir", "cloud_dir"):
                if e.name in skip:
                    continue
                if same_fs and not IS_WIN and st.st_dev != root_dev:
                    continue
                stack.append(e.path)
            elif kind == "file":
                res["files"] += 1
                res["newest"] = max(res["newest"], newest_ts(st))
                if is_cloud_only(st):
                    res["cloud_files"] += 1
                    res["cloud_bytes"] += st.st_size
                    continue
                size = local_bytes(st)
                if media and os.path.splitext(e.name)[1].lower() in MEDIA_EXT:
                    res["media_bytes"] += size
                if frees:
                    lst = st
                    if IS_WIN:
                        try:
                            lst = os.lstat(e.path)
                        except OSError:
                            lst = st
                    if lst.st_nlink > 1:
                        k = (lst.st_dev, lst.st_ino)
                        link_count[k] = link_count.get(k, 0) + 1
                        link_size[k] = size
                        link_nlink[k] = lst.st_nlink
                    else:
                        plain += size
                if tracker.first_time(e.path, st):
                    res["bytes"] += size
    res["linked_files"] = tracker.linked_files
    if frees:
        res["frees_bytes"] = plain + sum(link_size[k] for k, c in link_count.items() if c >= link_nlink[k])
    return res


def linked_fraction(root, sample=200, cap=4000):
    """Estimate of how much of a folder is hard links (cheap on Windows, where exact is slow)."""
    files = []
    stack = [lp(root)]
    while stack and len(files) < cap:
        items = list_dir(stack.pop())
        for e, kind, st in items or []:
            if kind in ("dir", "cloud_dir"):
                stack.append(e.path)
            elif kind == "file":
                files.append(e.path)
    if not files:
        return 0.0
    step = max(1, len(files) // sample)
    pick = files[::step][:sample]
    linked = 0
    for f in pick:
        try:
            linked += os.lstat(f).st_nlink > 1
        except OSError:
            pass
    return linked / float(len(pick))


# ---------------------------------------------------------------- hashing

def _sha256_chunked(f, chunk=1 << 20):
    h = hashlib.sha256()
    while True:
        b = f.read(chunk)
        if not b:
            break
        h.update(b)
    return h.hexdigest()


def sha256_file(path):
    with open(path, "rb") as f:
        fd = getattr(hashlib, "file_digest", None)  # 3.11+
        if fd is not None:
            return fd(f, "sha256").hexdigest()
        return _sha256_chunked(f)


# ---------------------------------------------------------------- well-known folders

def downloads_dir():
    if IS_WIN:
        try:
            import ctypes
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            fid = GUID(0x374DE290, 0x123F, 0x4565, (ctypes.c_ubyte * 8)(0x91, 0x64, 0x39, 0xC4, 0x92, 0x5E, 0x46, 0x7B))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(fid), 0, None, ctypes.byref(out)) == 0:
                path = out.value
                ctypes.windll.ole32.CoTaskMemFree(out)
                if path and os.path.isdir(path):
                    return path
        except Exception:
            pass
    if IS_LINUX:
        try:
            with open(os.path.join(xdg("XDG_CONFIG_HOME", ".config"), "user-dirs.dirs"), encoding="utf-8") as f:
                for line in f:
                    m = re.match(r'\s*XDG_DOWNLOAD_DIR="(.+)"', line)
                    if m:
                        p = m.group(1).replace("$HOME", HOME)
                        if os.path.isdir(p):
                            return p
        except OSError:
            pass
    return os.path.join(HOME, "Downloads")


def trash_dirs():
    """The current user's trash locations (read-only use: sizing)."""
    out = []
    if IS_WIN:
        for d in fixed_drives():
            out.append(os.path.join(d, "$Recycle.Bin"))
    elif IS_MAC:
        out.append(os.path.join(HOME, ".Trash"))
    else:
        out.append(os.path.join(xdg("XDG_DATA_HOME", os.path.join(".local", "share")), "Trash"))
    return out


def fixed_drives():
    """Windows: local fixed drives (C:\\, D:\\ ...). Elsewhere: []."""
    if not IS_WIN:
        return []
    try:
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        drives = []
        for i in range(26):
            if mask & (1 << i):
                root = "%s:\\" % chr(65 + i)
                if ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == 3:  # DRIVE_FIXED
                    drives.append(root)
        return drives
    except Exception:
        return [os.environ.get("SystemDrive", "C:") + "\\"]


CLOUD_DIR_NAMES = ("OneDrive", "Dropbox", "Google Drive", "GoogleDrive", "My Drive", "Box", "Box Sync",
                   "iCloudDrive", "iCloud Drive", "MEGA", "MEGAsync", "Nextcloud", "ownCloud", "pCloud Drive",
                   "pCloudDrive", "SynologyDrive", "Tresorit", "Sync")


def cloud_roots():
    """Folders kept in sync with a cloud service. Deleting inside one deletes the cloud copy too,
    so nothing in here is ever deleted by these scripts. Add more with PCM_CLOUD_DIRS (os.pathsep list)."""
    found = []

    def add(p):
        if p and os.path.isdir(p):
            found.append(os.path.abspath(p))

    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        add(os.environ.get(var))
    try:
        for e in os.scandir(HOME):
            if e.is_dir(follow_symlinks=False) and (e.name in CLOUD_DIR_NAMES or e.name.startswith(("OneDrive", "Dropbox"))):
                add(e.path)
    except OSError:
        pass
    if IS_MAC:
        add(os.path.join(HOME, "Library", "Mobile Documents"))
        cs = os.path.join(HOME, "Library", "CloudStorage")
        try:
            for e in os.scandir(cs):
                if e.is_dir(follow_symlinks=False):
                    add(e.path)
        except OSError:
            pass
        # iCloud "Desktop & Documents Folders": ~/Desktop and ~/Documents are synced too.
        if os.path.isdir(os.path.join(HOME, "Library", "Mobile Documents", "com~apple~CloudDocs", "Desktop")):
            add(os.path.join(HOME, "Desktop"))
            add(os.path.join(HOME, "Documents"))
    if IS_WIN:
        try:
            import winreg
            base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                    except OSError:
                        break
                    i += 1
                    try:
                        with winreg.OpenKey(k, sub + r"\UserSyncRoots") as uk:
                            j = 0
                            while True:
                                try:
                                    _, val, _ = winreg.EnumValue(uk, j)
                                except OSError:
                                    break
                                j += 1
                                add(str(val))
                    except OSError:
                        continue
        except OSError:
            pass
    for p in (os.environ.get("PCM_CLOUD_DIRS") or "").split(os.pathsep):
        add(p.strip())
    out, seen = [], set()
    for p in found:
        if key(p) not in seen:
            seen.add(key(p))
            out.append(p)
    return out


def in_cloud(path, roots=None):
    """True if path is inside a cloud-sync folder (by folder list, or on Windows by a cloud
    reparse tag on any parent folder)."""
    k = key(path)
    for r in roots if roots is not None else cloud_roots():
        if is_under(k, key(r)):
            return True
    if IS_WIN:
        p = os.path.abspath(sp(path))
        while True:
            try:
                st = os.lstat(p)
                if getattr(st, "st_file_attributes", 0) & _REPARSE and is_cloud_tag(getattr(st, "st_reparse_tag", 0)):
                    return True
            except OSError:
                pass
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
    return False


def runs_dir():
    return os.path.abspath(os.path.expanduser(os.environ.get("PCM_RUNS_DIR") or os.path.join(HOME, "pc-cleanup-performance-runs")))


def default_out(name):
    return os.path.join(runs_dir(), datetime.now().strftime("%Y-%m-%d"), name)


# Never deleted as a whole (exact match). Anything at depth 1 under home is refused separately.
_PROTECTED_REL = [
    "AppData", "AppData/Local", "AppData/Roaming", "AppData/LocalLow", "AppData/Local/Temp", "AppData/Local/Programs",
    "AppData/Local/Microsoft", "AppData/Roaming/Microsoft", "AppData/Local/Packages",
    "Library", ".config", ".local", ".local/share", ".local/state", ".local/bin", ".local/lib", ".cache",
    "Desktop", "Documents", "Downloads", "Pictures", "Videos", "Movies", "Music", "Public", "Templates",
]
# Nothing inside these is ever touched (keys, credentials, mail, agent memory and configs, iOS backups).
_SENSITIVE_REL = [
    ".ssh", ".gnupg", ".password-store", ".aws", ".azure", ".kube", ".config/gcloud", ".local/share/keyrings",
    ".claude", ".codex", ".cursor", ".gemini", ".continue", ".codeium", ".windsurf",
    "Library/Keychains", "Library/Mail", "Library/Messages", "Library/Mobile Documents", "Library/CloudStorage",
    "Library/Application Support/MobileSync", "Library/Containers", "Library/Group Containers",
    "Pictures/Photos Library.photoslibrary",
    "AppData/Roaming/Microsoft/Credentials", "AppData/Local/Microsoft/Credentials", "AppData/Roaming/Microsoft/Protect",
    "AppData/Roaming/Microsoft/Crypto", "AppData/Roaming/Microsoft/SystemCertificates", "AppData/Local/Microsoft/Vault",
    "AppData/Local/Packages", "AppData/Local/Microsoft/WindowsApps",
]


def protected_paths():
    """Exact folders that are never deleted whole: home, standard user folders, the temp folder,
    every direct child of ~/Library, cloud-sync roots, the runs folder and this skill itself."""
    out = [HOME, tempfile.gettempdir(), runs_dir(), SKILL_DIR]
    out += [os.path.join(HOME, *r.split("/")) for r in _PROTECTED_REL]
    lib = os.path.join(HOME, "Library")
    if IS_MAC and os.path.isdir(lib):
        try:
            out += [e.path for e in os.scandir(lib) if e.is_dir(follow_symlinks=False)]
        except OSError:
            pass
    return out


def browser_profile_roots():
    """Browser user-data folders: profiles hold saved passwords, cookies, history and sessions."""
    H, j = HOME, os.path.join
    if IS_WIN:
        L = os.environ.get("LOCALAPPDATA") or j(H, "AppData", "Local")
        R = os.environ.get("APPDATA") or j(H, "AppData", "Roaming")
        return [j(L, "Google", "Chrome", "User Data"), j(L, "Google", "Chrome Beta", "User Data"),
                j(L, "Microsoft", "Edge", "User Data"), j(L, "BraveSoftware", "Brave-Browser", "User Data"),
                j(L, "Chromium", "User Data"), j(L, "Vivaldi", "User Data"), j(R, "Opera Software"), j(R, "Mozilla", "Firefox")]
    if IS_MAC:
        S = j(H, "Library", "Application Support")
        return [j(S, "Google", "Chrome"), j(S, "Microsoft Edge"), j(S, "BraveSoftware"), j(S, "Chromium"), j(S, "Vivaldi"),
                j(S, "com.operasoftware.Opera"), j(S, "Arc"), j(S, "Firefox"), j(H, "Library", "Safari")]
    X = xdg("XDG_CONFIG_HOME", ".config")
    return [j(X, "google-chrome"), j(X, "google-chrome-beta"), j(X, "microsoft-edge"), j(X, "BraveSoftware"),
            j(X, "chromium"), j(X, "vivaldi"), j(X, "opera"), j(H, ".mozilla"), j(H, "snap", "chromium"),
            j(H, "snap", "firefox"), j(H, ".var", "app", "org.mozilla.firefox")]


def sensitive_paths():
    return [os.path.join(HOME, *r.split("/")) for r in _SENSITIVE_REL] + browser_profile_roots() + [runs_dir(), SKILL_DIR]


SYSTEM_ROOTS_WIN = ["{d}\\", "{d}\\Windows", "{d}\\Program Files", "{d}\\Program Files (x86)", "{d}\\ProgramData", "{d}\\Users"]
SYSTEM_ROOTS_POSIX = ["/", "/bin", "/sbin", "/usr", "/etc", "/var", "/opt", "/boot", "/lib", "/lib64", "/proc", "/sys",
                      "/dev", "/run", "/srv", "/root", "/home", "/Users", "/System", "/Library", "/Applications",
                      "/private", "/Volumes", "/mnt", "/media", "/snap", "/nix"]


def is_system_path(path):
    """True for OS folders and drive roots (and anything inside the OS folders)."""
    k = key(path)
    if IS_WIN:
        drive = os.path.splitdrive(os.path.abspath(sp(path)))[0] or "C:"
        roots = [key(r.format(d=drive)) for r in SYSTEM_ROOTS_WIN]
        if k in roots:
            return True
        return any(is_under(k, r) for r in roots[1:5])  # inside Windows / Program Files / ProgramData
    if k in [key(r) for r in SYSTEM_ROOTS_POSIX]:
        return True
    inside_only = ["/bin", "/sbin", "/usr", "/etc", "/boot", "/lib", "/lib64", "/proc", "/sys", "/dev", "/System"]
    return any(is_under(k, key(r)) for r in inside_only)


# ---------------------------------------------------------------- agent configs (for live_refs and sysinfo)

def _vscode_user_dirs():
    names = ["Code", "Code - Insiders", "VSCodium", "Cursor", "Windsurf"]
    if IS_WIN:
        base = os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming"))
    elif IS_MAC:
        base = os.path.join(HOME, "Library", "Application Support")
    else:
        base = xdg("XDG_CONFIG_HOME", ".config")
    return [(n, os.path.join(base, n, "User")) for n in names]


def agent_config_files():
    """(label, path, mcp_keys) for coding-agent and editor config files that can start MCP servers or
    hooks. mcp_keys = top-level JSON keys holding server definitions ('*raw*' = scan all strings)."""
    if IS_WIN:
        appdata = os.environ.get("APPDATA", os.path.join(HOME, "AppData", "Roaming"))
        claude_desktop = os.path.join(appdata, "Claude", "claude_desktop_config.json")
        managed = os.path.join(os.environ.get("ProgramData", "C:\\ProgramData"), "ClaudeCode", "managed-mcp.json")
    elif IS_MAC:
        claude_desktop = os.path.join(HOME, "Library", "Application Support", "Claude", "claude_desktop_config.json")
        managed = "/Library/Application Support/ClaudeCode/managed-mcp.json"
    else:
        claude_desktop = os.path.join(xdg("XDG_CONFIG_HOME", ".config"), "Claude", "claude_desktop_config.json")
        managed = "/etc/claude-code/managed-mcp.json"
    files = [
        ("claude-code", os.path.join(HOME, ".claude.json"), ("mcpServers",)),
        ("claude-code-settings", os.path.join(HOME, ".claude", "settings.json"), ("hooks", "statusLine", "mcpServers")),
        ("claude-code-settings", os.path.join(HOME, ".claude", "settings.local.json"), ("hooks", "statusLine", "mcpServers")),
        ("claude-code-managed", managed, ("mcpServers",)),
        ("claude-desktop", claude_desktop, ("mcpServers",)),
        ("cursor", os.path.join(HOME, ".cursor", "mcp.json"), ("mcpServers",)),
        ("windsurf", os.path.join(HOME, ".codeium", "windsurf", "mcp_config.json"), ("mcpServers",)),
        ("gemini-cli", os.path.join(HOME, ".gemini", "settings.json"), ("mcpServers",)),
        ("codex", os.path.join(HOME, ".codex", "config.toml"), ("mcp_servers",)),
        ("continue", os.path.join(HOME, ".continue", "config.json"), ("mcpServers", "experimental")),
        ("continue", os.path.join(HOME, ".continue", "config.yaml"), ("*raw*",)),
        ("zed", os.path.join(os.environ.get("APPDATA", HOME), "Zed", "settings.json") if IS_WIN
         else os.path.join(xdg("XDG_CONFIG_HOME", ".config"), "zed", "settings.json"), ("context_servers",)),
    ]
    for name, udir in _vscode_user_dirs():
        files.append(("%s-mcp" % name.lower(), os.path.join(udir, "mcp.json"), ("servers", "mcpServers")))
        files.append(("%s-settings" % name.lower(), os.path.join(udir, "settings.json"), ("mcp",)))
        files.append(("%s-cline" % name.lower(), os.path.join(udir, "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json"), ("mcpServers",)))
        files.append(("%s-roo" % name.lower(), os.path.join(udir, "globalStorage", "rooveterinaryinc.roo-cline", "settings", "mcp_settings.json"), ("mcpServers",)))
    return files


def load_jsonc(path):
    """Parse JSON that may contain // and /* */ comments and trailing commas (VS Code style)."""
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    try:
        return json.loads(text)
    except ValueError:
        pass
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out))
    return json.loads(cleaned)


# ---------------------------------------------------------------- processes and output

def run(cmd, timeout=60, env=None):
    """Run a read-only command. Returns (returncode, stdout); never raises. rc -1 = could not run."""
    try:
        r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env,
                           stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError, ValueError):
        return -1, ""
    out = r.stdout
    if b"\x00" in out[:200]:  # wsl.exe prints UTF-16
        try:
            return r.returncode, out.decode("utf-16-le", errors="replace")
        except Exception:
            pass
    return r.returncode, out.decode("utf-8", errors="replace")


def which(name):
    return shutil.which(name)


def powershell(script, timeout=120):
    """Run a PowerShell snippet (Windows) with UTF-8 output. Returns (rc, stdout)."""
    exe = which("powershell") or which("pwsh")
    if not exe:
        return -1, ""
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ErrorActionPreference='SilentlyContinue'; "
    return run([exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prefix + script], timeout)


def free_bytes(path):
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def gb(b):
    return "%.2f" % (b / 1024.0 ** 3)


def human(b):
    if b is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(b) < 1024 or unit == "TB":
            return ("%d %s" % (b, unit)) if unit == "B" else ("%.1f %s" % (b, unit))
        b /= 1024.0
    return "?"


def day(ts):
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""


def now_stamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ensure_dir(p):
    os.makedirs(p, exist_ok=True)
    return p


def write_tsv(path, header, rows):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(header) + "\n")
        for r in rows:
            f.write("\t".join(str(x).replace("\t", " ").replace("\n", " ") for x in r) + "\n")


def write_json(path, obj):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False, default=str)
        f.write("\n")


def write_text(path, text):
    ensure_dir(os.path.dirname(os.path.abspath(path)))
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def finish(out_dir, name, data, summary_lines):
    """Write <name>.json + summary.txt into out_dir and print the human summary."""
    data = dict(data)
    data.setdefault("generated", now_stamp())
    data.setdefault("os", os_label())
    write_json(os.path.join(out_dir, name + ".json"), data)
    text = "\n".join(summary_lines)
    write_text(os.path.join(out_dir, "summary.txt"), text)
    print(text)
    print("-> " + out_dir)


def read_ref_files(paths):
    """Load live-refs.txt files (one path per line, # comments) into key() form."""
    refs = []
    for p in paths or []:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    refs.append(key(line))
    return refs


def progress(msg):
    sys.stderr.write("[%s] %s\n" % (datetime.now().strftime("%H:%M:%S"), msg))
    sys.stderr.flush()


def elapsed(t0):
    return "%ds" % (time.time() - t0)
