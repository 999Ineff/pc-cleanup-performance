#!/usr/bin/env python3
"""READ-ONLY scan of the Downloads folder (deletes nothing).

Finds:
  - items older than --cutoff-days (default 182 = ~6 months). "Age" = the NEWEST of created and
    modified time, for the item and everything inside it, so a folder with one recent file is new.
  - byte-identical duplicates (full SHA-256) of files >= --min-dup-mb that also live elsewhere
    under home (projects, Documents ...), plus duplicates inside Downloads itself ("file (1).zip").
    Copies in caches, temp, trash and scratch folders don't count as the kept copy.

Writes (--out): items.tsv, dupes.tsv, plan.tsv (input for delete_paths.py), downloads.json, summary.txt
Default plan (the owner still approves the exact list):
  duplicate with a verified copy elsewhere   -> trash  (--dupes-action delete to delete for good)
  old installer / disk image / partial file  -> delete (re-downloadable)
  any other old item (docs, photos, archives, folders) -> trash (restorable)
Usage: python3 -I scan_downloads.py [--dir PATH] [--cutoff-days 182] [--min-dup-mb 10]
       [--compare-root DIR ...] [--protect live-refs.txt] [--dupes-action trash|delete] [--out DIR]
"""
import argparse
import hashlib
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

INSTALLER_EXT = {".exe", ".msi", ".msix", ".msixbundle", ".appx", ".dmg", ".pkg", ".mpkg", ".deb", ".rpm",
                 ".appimage", ".iso", ".img", ".snap", ".flatpakref", ".apk", ".xip"}
PARTIAL_EXT = {".crdownload", ".part", ".partial", ".download", ".opdownload", ".tmp"}
ARCHIVE_EXT = {".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".zst"}
DOC_EXT = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".ppt", ".pptx", ".txt", ".md", ".rtf", ".odt",
           ".ods", ".odp", ".pages", ".numbers", ".key", ".json", ".html", ".epub"}
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "__pycache__", "$Recycle.Bin", ".Trash", "Trash", ".cache",
             "AppData", "Library", "site-packages", "target", ".next", "dist", "build"}
# Copies under these path fragments don't count as "the kept copy" (scratch, caches, trash).
DEFAULT_IGNORE_TWIN = ["quarantine", ".runstate", "pc-cleanup-performance-runs", "$recycle.bin", "/.trash/", "/trash/",
                       "/temp/", "/tmp/", "/cache/", "/caches/"]


def norm_frag(p):
    """Lower-case, forward-slash form with a trailing slash, for fragment matching."""
    return C.sp(p).lower().replace("\\", "/") + "/"


def category(name, is_dir):
    if is_dir:
        return "folder"
    ext = os.path.splitext(name)[1].lower()
    if ext in INSTALLER_EXT:
        return "installer"
    if ext in PARTIAL_EXT:
        return "partial"
    if ext in ARCHIVE_EXT:
        return "archive"
    if ext in C.MEDIA_EXT:
        return "media"
    if ext in DOC_EXT:
        return "document"
    return "other"


def walk_files(root, skip_top=(), skip_names=SKIP_DIRS, skip_keys=()):
    """(path, stat) for local files under root; never follows links, never yields cloud-only placeholders."""
    root_lp = C.lp(root)
    stack = [root_lp]
    while stack:
        d = stack.pop()
        for e, kind, st in C.list_dir(d) or []:
            if kind in ("dir", "cloud_dir"):
                if e.name in skip_names or (d == root_lp and e.name in skip_top) or (d == root_lp and e.name.startswith(".")):
                    continue
                if C.key(e.path) in skip_keys:
                    continue
                stack.append(e.path)
            elif kind == "file" and not C.is_cloud_only(st):
                yield e.path, st


class Hasher(object):
    def __init__(self):
        self.cache = {}

    def quick(self, path, size):
        k = ("q", path)
        if k not in self.cache:
            with open(path, "rb") as f:
                head = f.read(65536)
                if size > 131072:
                    f.seek(-65536, 2)
                head += f.read(65536)
            self.cache[k] = hashlib.sha256(head).hexdigest()
        return self.cache[k]

    def full(self, path):
        k = ("f", path)
        if k not in self.cache:
            self.cache[k] = C.sha256_file(path)
        return self.cache[k]


def main():
    ap = argparse.ArgumentParser(description="Read-only Downloads scan.")
    ap.add_argument("--dir", default=C.downloads_dir())
    ap.add_argument("--cutoff-days", type=int, default=182)
    ap.add_argument("--min-dup-mb", type=int, default=10)
    ap.add_argument("--compare-root", action="append", default=[], help="where kept copies may live (default: home)")
    ap.add_argument("--ignore-twin", action="append", default=[], help="path fragment whose copies don't count")
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--dupes-action", choices=("trash", "delete"), default="trash")
    ap.add_argument("--out", default=C.default_out("downloads"))
    a = ap.parse_args()
    t0 = time.time()
    dl = os.path.abspath(a.dir)
    if not os.path.isdir(dl):
        sys.exit("Downloads folder not found: %s (pass --dir)" % dl)
    cutoff = time.time() - a.cutoff_days * 86400
    min_dup = a.min_dup_mb * 1024 ** 2
    refs = C.read_ref_files(a.protect)
    cloud = C.cloud_roots()
    dl_in_cloud = C.in_cloud(dl, cloud)
    ignore = [x.lower().replace("\\", "/") for x in DEFAULT_IGNORE_TWIN + a.ignore_twin]

    # 1. top-level items with their newest date
    items = []
    entries = C.list_dir(dl)
    if entries is None:
        sys.exit("Can't read %s. On macOS, allow the terminal / agent app to access Downloads "
                 "(System Settings > Privacy & Security > Files and Folders), then re-run." % dl)
    for e, kind, st in entries:
        if kind == "link" or st is None or e.name in ("desktop.ini", ".DS_Store", ".localized"):
            continue
        is_dir = kind in ("dir", "cloud_dir")
        if is_dir:
            s = C.tree_stats(e.path)
            size, files, nw = s["bytes"], s["files"], max(s["newest"], C.newest_ts(st))
        else:
            size, files, nw = C.local_bytes(st), 1, C.newest_ts(st)
        path = C.sp(e.path)
        live = any(C.overlaps(C.key(path), r) for r in refs)
        items.append({"path": path, "type": category(e.name, is_dir), "bytes": size, "files": files,
                      # st_size is what delete_paths re-checks (logical size; "bytes" may be disk blocks)
                      "st_size": -1 if is_dir else st.st_size, "newest": nw, "old": nw < cutoff, "live": live})
    C.progress("scan_downloads: %d items (%s); indexing other folders for duplicates..." % (len(items), C.elapsed(t0)))

    # 2. duplicates: sizes present in Downloads, then quick hash, then full SHA-256
    dl_files = [(p, st) for p, st in walk_files(dl, skip_names=set()) if st.st_size >= min_dup]
    sizes = set(st.st_size for _, st in dl_files)
    by_size = {}
    roots = [os.path.abspath(r) for r in a.compare_root] or [C.HOME]
    dl_key = C.key(dl)
    for root in roots:
        for p, st in walk_files(root, skip_top={"AppData", "Library"}, skip_keys={dl_key, C.key(C.runs_dir())}):
            if st.st_size in sizes and not any(x in norm_frag(p) for x in ignore):
                by_size.setdefault(st.st_size, []).append(p)
    hasher = Hasher()
    dupes, plan, planned = [], [], set()
    # duplicates inside Downloads: keep the shortest name of each identical group
    groups = {}
    for p, st in dl_files:
        groups.setdefault(st.st_size, []).append(p)
    internal_twin = {}
    for size, paths in groups.items():
        if len(paths) < 2:
            continue
        try:
            by_hash = {}
            for p in paths:
                by_hash.setdefault(hasher.full(p), []).append(p)
        except OSError:
            continue
        for h, same in by_hash.items():
            if len(same) > 1:
                keep = sorted(same, key=lambda x: (len(os.path.basename(x)), x))[0]
                for p in same:
                    if p != keep:
                        internal_twin[p] = (keep, h)
    for p, st in dl_files:
        twin, h, where = None, None, ""
        try:
            cands = by_size.get(st.st_size, [])
            if cands:
                q = hasher.quick(p, st.st_size)
                for c in cands:
                    if hasher.quick(c, st.st_size) == q and hasher.full(c) == hasher.full(p):
                        twin, h, where = c, hasher.full(p), "elsewhere"
                        break
            if twin is None and p in internal_twin:
                twin, h = internal_twin[p]
                where = "inside Downloads"
        except OSError:
            continue
        if twin is None:
            continue
        try:
            same_inode = os.lstat(p).st_ino == os.lstat(twin).st_ino and os.lstat(p).st_dev == os.lstat(twin).st_dev
        except OSError:
            same_inode = False
        dupes.append({"path": C.sp(p), "bytes": st.st_size, "twin": C.sp(twin), "where": where, "hardlink": same_inode, "sha256": h})
    C.progress("scan_downloads: %d duplicates (%s)" % (len(dupes), C.elapsed(t0)))

    # 3. plan (nothing if Downloads itself is cloud-synced)
    if not dl_in_cloud:
        old_keys = [C.key(i["path"]) for i in items if i["old"] and not i["live"]]
        for d in dupes:
            if d["hardlink"] or any(C.overlaps(C.key(d["path"]), r) for r in refs):
                continue
            if d["where"] == "inside Downloads" and any(C.is_under(C.key(d["twin"]), ok) for ok in old_keys):
                continue  # the kept copy is old too: the age rule below handles this item
            plan.append((a.dupes_action, d["path"], d["bytes"], d["twin"], d["sha256"], "", "identical copy %s: %s" % (d["where"], d["twin"])))
            planned.add(C.key(d["path"]))
        for it in items:
            if not it["old"] or it["live"]:
                continue
            k = C.key(it["path"])
            if k in planned or any(C.is_under(pk, k) for pk in planned):
                continue  # a duplicate row already covers it or something inside it
            action = "delete" if it["type"] in ("installer", "partial") else "trash"
            plan.append((action, it["path"], it["st_size"], "", "", int(cutoff),
                         "%s, newest %s (older than %d days)" % (it["type"], C.day(it["newest"]), a.cutoff_days)))
    C.write_tsv(os.path.join(a.out, "items.tsv"), ["path", "type", "mb", "files", "newest", "old", "live"],
                [(i["path"], i["type"], "%.1f" % (i["bytes"] / 1048576.0), i["files"], C.day(i["newest"]), i["old"], i["live"])
                 for i in sorted(items, key=lambda x: -x["bytes"])])
    C.write_tsv(os.path.join(a.out, "dupes.tsv"), ["download_path", "mb", "hardlink_to_copy", "where", "identical_copy"],
                [(d["path"], "%.1f" % (d["bytes"] / 1048576.0), d["hardlink"], d["where"], d["twin"])
                 for d in sorted(dupes, key=lambda x: -x["bytes"])])
    C.write_tsv(os.path.join(a.out, "plan.tsv"), ["action", "path", "expect_size", "twin", "sha256", "cutoff_ts", "reason"], plan)

    old = [i for i in items if i["old"]]
    by_type = {}
    for i in old:
        t = by_type.setdefault(i["type"], [0, 0])
        t[0] += 1
        t[1] += i["bytes"]
    lines = ["Downloads (%s): %d items, %s" % (C.tilde(dl), len(items), C.human(sum(i["bytes"] for i in items))),
             "older than %d days (nothing inside newer): %d items, %s  [%s]" % (
                 a.cutoff_days, len(old), C.human(sum(i["bytes"] for i in old)),
                 ", ".join("%s %d (%s)" % (t, v[0], C.human(v[1])) for t, v in sorted(by_type.items(), key=lambda x: -x[1][1]))),
             "identical copies (>= %d MB): %d files, %s  (%d are hard links = 0 bytes freed)" % (
                 a.min_dup_mb, len(dupes), C.human(sum(d["bytes"] for d in dupes)), sum(1 for d in dupes if d["hardlink"])),
             "plan: %d delete, %d trash -> %s" % (sum(1 for x in plan if x[0] == "delete"), sum(1 for x in plan if x[0] == "trash"),
                                                  os.path.join(a.out, "plan.tsv"))]
    if dl_in_cloud:
        lines.append("WARNING: Downloads is inside a cloud-sync folder, so no plan was made (use the provider's free-up-space option)")
    if any(i["live"] for i in items):
        lines.append("live (kept): " + ", ".join(C.tilde(i["path"]) for i in items if i["live"]))
    lines.append("biggest old items:")
    lines += ["  %8s  %s  %s" % (C.human(i["bytes"]), C.day(i["newest"]), C.tilde(i["path"])) for i in sorted(old, key=lambda x: -x["bytes"])[:12]]
    C.finish(a.out, "downloads", {"dir": dl, "cutoff_days": a.cutoff_days, "items": items, "dupes": dupes,
                                  "plan_rows": len(plan), "downloads_in_cloud": dl_in_cloud}, lines)


if __name__ == "__main__":
    main()
