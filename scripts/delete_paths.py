#!/usr/bin/env python3
"""The ONLY deletion tool of this skill. Dry run by default; --apply only after the owner said yes
to this exact plan, and only after a dry run of the same plan file.

Plan = TSV with a header row (columns in any order; action and path are required):
  action       delete (permanent: regenerable junk) | trash (OS Trash / Recycle Bin: personal files)
  path         absolute path
  expect_size  optional: file size in bytes at scan time; refused if it changed
  twin         optional: a verified identical copy that must still exist with the same size
  sha256       optional (with twin): both files are re-hashed and must still match
  cutoff_ts    optional: unix time; refused if anything inside changed since then
  reason       optional: free text for the log
A plain list (one path per line, # comments allowed) needs --default-action delete|trash.

Every row is re-validated at run time and REFUSED unless all of these hold:
  - absolute path inside home (or an --extra-root), at least 2 levels below home: top-level home
    folders, home itself and system folders are never deleted
  - not a protected folder (standard user folders, temp root, ~/Library/*, AppData roots, cloud
    roots, the runs folder, this skill) and not inside or holding a sensitive one (.ssh, keychains, agent
    configs, mail, iOS backups, app containers, browser profiles)
  - not inside a cloud-sync folder (deleting there deletes the cloud copy)
  - inside WSL: not on a Windows drive (/mnt/c ...): those are cleaned from Windows
  - the real path (symlinks and junctions resolved) passes the same checks
  - no overlap with a live path from --protect (live_refs.py output, less than 12 hours old)
  - exists; is not a link, junction or mount point; is not a git repo/worktree, holds no nested
    .git and no git-tracked files; no cloud-only placeholders inside
  - expect_size / twin / sha256 / cutoff_ts still hold, and no twin is itself in the plan
With --apply a folder is first renamed in place: if something has a file open (Windows), the rename
fails and nothing is deleted, instead of leaving a half-deleted folder.

Usage:
  python3 -I delete_paths.py PLAN --protect live-refs.txt                # dry run (writes PLAN.dryrun.json)
  python3 -I delete_paths.py PLAN --protect live-refs.txt --apply --log run/delete-log.txt
Options: --extra-root DIR (repeatable; e.g. a data drive with projects), --out DIR, --default-action
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

PROTECT_MAX_AGE_H = 12
DRYRUN_MAX_AGE_H = 24


class Row(object):
    def __init__(self, line, action, path, expect_size=-1, twin="", sha256="", cutoff=0.0, reason=""):
        self.line, self.action, self.path = line, action, path
        self.expect_size, self.twin, self.sha256, self.cutoff, self.reason = expect_size, twin, sha256, cutoff, reason
        self.bytes, self.files = 0, 0


def read_plan(path, default_action):
    with open(path, encoding="utf-8-sig") as f:
        lines = [(n, l.rstrip("\r\n")) for n, l in enumerate(f, 1)]
    lines = [(n, l) for n, l in lines if l.strip() and not l.lstrip().startswith("#")]
    if not lines:
        return []
    head = [h.strip().lower() for h in lines[0][1].split("\t")]
    rows = []
    if "action" in head and "path" in head:
        col = dict((h, i) for i, h in enumerate(head))
        for n, l in lines[1:]:
            c = l.split("\t")

            def get(name):
                i = col.get(name)
                return c[i].strip() if i is not None and i < len(c) else ""
            size = get("expect_size")
            cutoff = get("cutoff_ts")
            action = get("action").lower()
            rows.append(Row(n, "trash" if action == "recycle" else action, get("path"),
                            int(size) if size.lstrip("-").isdigit() else -1, get("twin"), get("sha256").lower(),
                            float(cutoff) if cutoff.replace(".", "", 1).isdigit() else 0.0, get("reason")))
    else:
        if default_action not in ("delete", "trash"):
            sys.exit("This plan is a plain list: pass --default-action delete (regenerable junk) or trash (personal files).")
        rows = [Row(n, default_action, l.strip()) for n, l in lines]
    return rows


def is_link_like(p):
    if os.path.islink(p):
        return True
    if C.IS_WIN:
        try:
            st = os.lstat(p)
        except OSError:
            return False
        return bool(getattr(st, "st_file_attributes", 0) & 0x400) and not C.is_cloud_tag(getattr(st, "st_reparse_tag", 0))
    return False


def repo_root_of(p):
    d = os.path.dirname(os.path.abspath(p))
    while True:
        if os.path.lexists(os.path.join(d, ".git")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return ""
        d = parent


def inspect_tree(p):
    """Walk the target once: bytes, files, newest timestamp, nested .git, cloud-only files."""
    info = {"bytes": 0, "files": 0, "newest": 0.0, "git": "", "cloud": 0}
    try:
        st = os.lstat(C.lp(p))
    except OSError:
        return info
    info["newest"] = C.newest_ts(st)
    if not os.path.isdir(p):
        info["files"], info["bytes"] = 1, C.local_bytes(st)
        info["cloud"] = int(C.is_cloud_only(st))
        return info
    stack = [C.lp(p)]
    while stack:
        for e, kind, est in C.list_dir(stack.pop()) or []:
            if e.name == ".git":
                info["git"] = C.sp(e.path)
            if kind in ("dir", "cloud_dir"):
                info["newest"] = max(info["newest"], C.newest_ts(est))
                stack.append(e.path)
            elif kind == "file":
                info["files"] += 1
                info["newest"] = max(info["newest"], C.newest_ts(est))
                if C.is_cloud_only(est):
                    info["cloud"] += 1
                else:
                    info["bytes"] += C.local_bytes(est)
    return info


class Guard(object):
    def __init__(self, refs, extra_roots):
        self.home = C.key(C.HOME)
        self.allowed = [self.home] + [C.key(r) for r in extra_roots]
        self.cloud = C.cloud_roots()
        self.protected = set(C.key(p) for p in C.protected_paths() + self.cloud + list(extra_roots))
        self.sensitive = [C.key(p) for p in C.sensitive_paths()]
        self.refs = refs
        self.git = C.which("git")
        self.win_mounts = [C.key(m) for m in C.windows_mounts()]

    def place(self, label, path):
        k = C.key(path)
        if C.is_system_path(path):
            return "%s is a system folder or drive root" % label
        if any(C.is_under(k, m) for m in self.win_mounts):
            return ("%s is on a Windows drive seen from WSL: clean it from Windows, where the cloud, live-tool and "
                    "protected-folder checks can see it" % label)
        root = next((r for r in self.allowed if C.is_under(k, r)), None)
        if root is None:
            return "%s is outside home and the --extra-root folders" % label
        if k in self.protected:
            return "%s is a protected folder (never deleted whole)" % label
        if C.depth_below(k, root) < (2 if root == self.home else 1):
            return "%s is a top-level folder (never deleted whole)" % label
        s = next((s for s in self.sensitive if C.overlaps(k, s)), None)
        if s:
            return "%s is or holds a sensitive folder: %s" % (label, s)
        if C.in_cloud(path, self.cloud):
            return "%s is inside a cloud-sync folder (deleting there deletes the cloud copy)" % label
        hit = next((r for r in self.refs if C.overlaps(k, r)), None)
        if hit:
            return "%s overlaps a live tool path: %s" % (label, hit)
        return ""

    def check(self, row, plan_keys):
        if row.action not in ("delete", "trash"):
            return "bad action '%s' (use delete or trash)" % row.action
        p = C.sp(row.path)
        if not p or not os.path.isabs(p):
            return "not an absolute path"
        why = self.place("path", p) or self.place("real path", os.path.realpath(p))
        if why:
            return why
        if not os.path.lexists(p):
            return "gone (already deleted or moved)"
        if is_link_like(p):
            return "is a symlink/junction (remove the link by hand if that is really intended)"
        if os.path.isdir(p) and os.path.ismount(p):
            return "is a mount point"
        if os.path.isdir(p) and os.path.lexists(os.path.join(p, ".git")):
            return "is a git repository or worktree (use `git worktree remove`, or ask the owner)"
        info = inspect_tree(p)
        row.bytes, row.files = info["bytes"], info["files"]
        if info["git"]:
            return "contains a git repository: %s" % info["git"]
        if info["cloud"]:
            return "contains %d cloud-only placeholder files" % info["cloud"]
        if row.expect_size >= 0 and os.path.isfile(p) and os.path.getsize(p) != row.expect_size:
            return "size changed since the scan (%d != %d)" % (os.path.getsize(p), row.expect_size)
        if row.cutoff and info["newest"] >= row.cutoff:
            return "something inside changed after the scan cutoff (%s)" % C.day(info["newest"])
        if row.twin:
            t = C.sp(row.twin)
            if any(C.is_under(C.key(t), pk) for pk in plan_keys):
                return "its verified copy is also in this plan: %s" % t
            if not (os.path.isfile(t) and os.path.isfile(p) and os.path.getsize(t) == os.path.getsize(p)):
                return "verified copy no longer present or different size: %s" % t
            try:
                if C.is_cloud_only(os.lstat(t)):
                    return "verified copy is cloud-only (can't re-check it without downloading): %s" % t
            except OSError:
                return "verified copy unreadable: %s" % t
            if row.sha256:
                try:
                    if C.sha256_file(p) != row.sha256 or C.sha256_file(t) != row.sha256:
                        return "SHA-256 no longer matches the verified copy"
                except OSError as e:
                    return "could not re-hash: %s" % e
        repo = repo_root_of(p)
        if repo:
            dotgit = os.path.join(repo, ".git")
            if os.path.isfile(dotgit):  # linked worktree: .git is a file "gitdir: <repo>/.git/worktrees/<name>"
                try:
                    with open(dotgit, encoding="utf-8", errors="replace") as f:
                        gitdir = f.read().strip().split("gitdir:", 1)[-1].strip()
                    if not os.path.isabs(gitdir):
                        gitdir = os.path.join(repo, gitdir)
                    if os.path.exists(os.path.join(gitdir, "locked")):
                        return "inside a locked git worktree (a tool has claimed it): %s" % repo
                except OSError:
                    return "can't read the worktree link %s" % dotgit
            if not self.git:
                return "inside git repo %s but git is not installed to check for tracked files" % repo
            rel = os.path.relpath(p, repo)
            try:
                r = subprocess.run([self.git, "--no-optional-locks", "-C", repo, "ls-files", "-z", "--", rel],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=120)
            except (OSError, subprocess.SubprocessError) as e:
                return "git check failed: %s" % e
            if r.returncode != 0:
                return "git check failed in %s" % repo
            if r.stdout.strip(b"\0"):
                return "holds files tracked by git in %s (not regenerable junk)" % repo
        return ""


# ---------------------------------------------------------------- deleting

def _retry_writable(top):
    def handler(func, path, _exc):
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD | (stat.S_IEXEC if os.path.isdir(path) else 0))
            parent = os.path.dirname(path)
            if not C.IS_WIN and C.is_under(C.key(parent), C.key(top)):
                os.chmod(parent, stat.S_IRWXU)  # read-only folders, e.g. the Go module cache
        except OSError:
            pass
        func(path)
    return handler


def rmtree(path):
    h = _retry_writable(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(C.lp(path), onexc=h)
    else:
        shutil.rmtree(C.lp(path), onerror=h)


def remove_permanently(p):
    if os.path.isdir(p):
        tmp = os.path.join(os.path.dirname(p), ".pcm-deleting-%s-%d" % (os.path.basename(p), int(time.time())))
        try:
            os.rename(C.lp(p), C.lp(tmp))
        except OSError as e:
            raise OSError("in use or locked, nothing deleted (%s)" % e)
        rmtree(tmp)
        if os.path.lexists(tmp):
            raise OSError("partly removed; leftovers in %s" % tmp)
        return "deleted"
    try:
        os.remove(C.lp(p))
    except PermissionError:
        os.chmod(C.lp(p), stat.S_IWRITE | stat.S_IREAD)
        os.remove(C.lp(p))
    return "deleted"


def _recycle_windows(p):
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", ctypes.c_void_p),
                    ("pTo", ctypes.c_void_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_void_p)]

    buf = ctypes.create_unicode_buffer(os.path.abspath(p) + "\0")  # double-NUL-terminated list of one path
    # FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING: if an item is
    # too big for the Recycle Bin, Windows asks instead of silently deleting it for good.
    op = SHFILEOPSTRUCTW(wFunc=3, pFrom=ctypes.addressof(buf), fFlags=0x40 | 0x10 | 0x4 | 0x400 | 0x4000)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if rc != 0 or op.fAnyOperationsAborted or os.path.lexists(p):
        raise OSError("Recycle Bin move failed or was cancelled (code %s)" % rc)
    return "recycle bin"


def _move_into(trash_files, p):
    name = os.path.basename(p.rstrip(os.sep))
    dest, n = os.path.join(trash_files, name), 1
    while os.path.lexists(dest):
        n += 1
        dest = os.path.join(trash_files, "%s.%d" % (name, n))
    os.rename(p, dest)
    return dest


def _trash_mac(p):
    # The POSIX-path -> alias coercion happens outside the Finder tell block: inside it, Finder can
    # answer "Can't get POSIX file" for a path held in a variable.
    script = ['-e', 'on run argv', '-e', 'set f to (POSIX file (item 1 of argv)) as alias',
              '-e', 'tell application "Finder" to delete f', '-e', 'end run']
    rc, _ = C.run(["osascript"] + script + [os.path.abspath(p)], timeout=120)
    if rc == 0 and not os.path.lexists(p):
        return "trash (Finder)"
    trash = os.path.join(C.HOME, ".Trash")
    if os.path.isdir(trash) and os.lstat(p).st_dev == os.lstat(trash).st_dev:
        _move_into(trash, p)  # no "Put Back", but restorable by dragging it out of the Trash
        return "trash (moved to ~/.Trash)"
    raise OSError("Finder could not trash it (allow the terminal to control Finder, or use Finder)")


def _trash_linux(p):
    for cmd in (["gio", "trash", "--"], ["trash-put", "--"]):
        if C.which(cmd[0]):
            rc, _ = C.run(cmd + [os.path.abspath(p)], timeout=300)
            if rc == 0 and not os.path.lexists(p):
                return "trash (%s)" % cmd[0]
    # freedesktop.org Trash spec, home trash only (same file system as home)
    base = os.path.join(C.xdg("XDG_DATA_HOME", os.path.join(".local", "share")), "Trash")
    files, info = os.path.join(base, "files"), os.path.join(base, "info")
    C.ensure_dir(files)
    C.ensure_dir(info)
    if os.lstat(p).st_dev != os.lstat(files).st_dev:
        raise OSError("not on the home file system: no trash here (decide delete vs keep)")
    try:
        from urllib.parse import quote
    except ImportError:
        quote = None
    name = os.path.basename(p.rstrip(os.sep))
    n = 1
    while True:
        cand = name if n == 1 else "%s.%d" % (name, n)
        info_path = os.path.join(info, cand + ".trashinfo")
        try:
            fd = os.open(info_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError:
            n += 1
    with os.fdopen(fd, "w") as f:
        f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (
            quote(os.path.abspath(p), safe="/") if quote else os.path.abspath(p), datetime.now().strftime("%Y-%m-%dT%H:%M:%S")))
    try:
        os.rename(p, os.path.join(files, cand))
    except OSError:
        os.remove(info_path)
        raise
    return "trash (~/.local/share/Trash)"


def to_trash(p):
    if C.IS_WIN:
        return _recycle_windows(p)
    if C.IS_MAC:
        return _trash_mac(p)
    return _trash_linux(p)


# ---------------------------------------------------------------- main

def plan_digest(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def volumes(rows, extra_roots):
    roots = [C.HOME] + list(extra_roots)
    return dict((r, C.free_bytes(r)) for r in roots)


def main():
    ap = argparse.ArgumentParser(description="Guarded deletion: dry run by default.")
    ap.add_argument("plan")
    ap.add_argument("--protect", action="append", default=[], help="live-refs.txt from live_refs.py (repeatable)")
    ap.add_argument("--apply", action="store_true", help="really delete/trash (after a dry run of this exact plan)")
    ap.add_argument("--extra-root", action="append", default=[], help="another folder this tool may delete inside")
    ap.add_argument("--default-action", choices=("delete", "trash"), default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--log", default="")
    a = ap.parse_args()

    for r in a.extra_root:
        if not os.path.isdir(r) or C.is_system_path(r) or os.path.dirname(os.path.abspath(r)) == os.path.abspath(r) \
                or C.in_cloud(r) or C.is_under(C.key(C.HOME), C.key(r)):
            sys.exit("--extra-root %s refused: must be an existing, non-system, non-cloud folder below a drive root" % r)
        if any(C.is_under(C.key(r), C.key(m)) for m in C.windows_mounts()):
            sys.exit("--extra-root %s refused: a Windows drive seen from WSL. Clean Windows files from Windows." % r)
    if a.apply and not a.protect:
        sys.exit("--apply needs --protect <live-refs.txt>: run live_refs.py first (this is what keeps live tools safe).")
    for pf in a.protect:
        age_h = (time.time() - os.path.getmtime(pf)) / 3600.0
        if a.apply and age_h > PROTECT_MAX_AGE_H:
            sys.exit("%s is %.0f h old: re-run live_refs.py, then dry-run again." % (pf, age_h))
    digest = plan_digest(a.plan)
    stamp_path = a.plan + ".dryrun.json"
    if a.apply:
        try:
            with open(stamp_path, encoding="utf-8") as f:
                stamp = json.load(f)
        except (OSError, ValueError):
            stamp = {}
        if stamp.get("sha256") != digest or time.time() - stamp.get("time", 0) > DRYRUN_MAX_AGE_H * 3600:
            sys.exit("No matching dry run of this exact plan in the last %d h: run it without --apply first." % DRYRUN_MAX_AGE_H)

    refs = C.read_ref_files(a.protect)
    rows = read_plan(a.plan, a.default_action)
    guard = Guard(refs, a.extra_root)
    plan_keys = [C.key(C.sp(r.path)) for r in rows if r.path and os.path.isabs(C.sp(r.path))]
    ok, refused = [], []
    for r in rows:
        why = guard.check(r, plan_keys)
        (refused if why else ok).append((r, why))
    by_key, nested = {}, []
    for r, _ in ok:
        k = C.key(C.sp(r.path))
        if k in by_key:
            nested.append((r, "duplicate of line %d" % by_key[k].line))
        else:
            by_key[k] = r
    for k, r in by_key.items():
        anc = os.path.dirname(k)
        while anc and anc != os.path.dirname(anc):
            if anc in by_key:
                o = by_key[anc]
                nested.append((r, "already covered by line %d (%s)" % (o.line, o.path)))
                break
            anc = os.path.dirname(anc)
    if nested:
        drop = set(id(r) for r, _ in nested)
        ok = [(r, w) for r, w in ok if id(r) not in drop]
        refused += nested
    before = volumes(rows, a.extra_root)
    mode = "APPLY" if a.apply else "DRY RUN"
    est = sum(r.bytes for r, _ in ok)
    out = ["=== %s %s  plan=%s" % (mode, C.now_stamp(), a.plan),
           "live-refs: %d paths%s" % (len(refs), "" if refs else "  (WARNING: none given; --apply will refuse)"),
           "would act on %d (delete %d, trash %d), about %s%s" % (
               len(ok), sum(1 for r, _ in ok if r.action == "delete"), sum(1 for r, _ in ok if r.action == "trash"),
               C.human(est), " (hard links can make the real gain smaller)" if est else ""),
           "refused %d:" % len(refused)]
    out += ["  REFUSED line %d  %s  <- %s" % (r.line, r.path, why) for r, why in refused]
    results, fails = [], 0
    if a.apply:
        for r, _ in ok:
            p = C.sp(r.path)
            try:
                how = to_trash(p) if r.action == "trash" else remove_permanently(p)
                out.append("  %s  %s  (%s)" % (how.upper(), p, C.human(r.bytes)))
                results.append({"path": p, "action": r.action, "result": how, "bytes": r.bytes})
            except Exception as e:
                fails += 1
                out.append("  FAILED %s %s: %s" % (r.action, p, e))
                results.append({"path": p, "action": r.action, "result": "failed: %s" % e, "bytes": r.bytes})
        after = volumes(rows, a.extra_root)
        for vol in before:
            if before[vol] is not None and after.get(vol) is not None:
                out.append("free space at %s: %s -> %s (%+.2f GB)" % (
                    C.tilde(vol), C.human(before[vol]), C.human(after[vol]), (after[vol] - before[vol]) / 1024.0 ** 3))
        if any(r.action == "trash" for r, _ in ok):
            out.append("(trashed items free space only when the Trash / Recycle Bin is emptied)")
        out.append("=== done: %d ok, %d failed" % (len(ok) - fails, fails))
    else:
        out += ["  would %s  %s  (%s)" % (r.action, r.path, C.human(r.bytes)) for r, _ in ok[:60]]
        if len(ok) > 60:
            out.append("  ... and %d more (see the JSON)" % (len(ok) - 60))
        try:
            with open(stamp_path, "w", encoding="utf-8") as f:
                json.dump({"sha256": digest, "time": time.time(), "ok": len(ok), "refused": len(refused)}, f)
        except OSError as e:
            out.append("WARNING: could not record the dry run (%s): --apply will refuse" % e)
    text = "\n".join(out)
    print(text)
    record = {"mode": mode, "plan": a.plan, "plan_sha256": digest, "time": C.now_stamp(), "os": C.os_label(),
              "refused": [{"line": r.line, "path": r.path, "why": why} for r, why in refused],
              "ok": [{"line": r.line, "path": r.path, "action": r.action, "bytes": r.bytes, "reason": r.reason} for r, _ in ok],
              "results": results}
    out_dir = a.out or os.path.join(os.path.dirname(os.path.abspath(a.plan)))
    C.write_json(os.path.join(out_dir, "delete-%s-%s.json" % ("apply" if a.apply else "dryrun", datetime.now().strftime("%H%M%S"))), record)
    if a.log:
        C.ensure_dir(os.path.dirname(os.path.abspath(a.log)))
        with open(a.log, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
