#!/usr/bin/env python3
"""READ-ONLY scan of video and creative projects (deletes nothing). Every result is REVIEW:
which project can lose what is always the owner's call, project by project.

A project = a folder holding an editor project file or library (Premiere, After Effects, DaVinci
Resolve export, Final Cut library, iMovie library, Blender, Kdenlive, Shotcut, OpenShot, Camtasia,
ScreenFlow, Vegas, Audition, Reaper, Ableton, Logic, GarageBand, Pro Tools, Cinema 4D, Houdini,
Nuke, Remotion ...), or a first-level folder of a root with at least --min-gb of media.
Searched under --root (default: ~/Videos, ~/Movies, ~/Documents, ~/Desktop, plus any you pass).

For each project: last touched, total, what deleting it would really free (hard-link aware), and
its first-level folders sorted into:
  keep   source / footage / raw / originals / takes / audio / assets / final / export(s) /
         deliver(y/ables) / master(s) / covers / thumbnails, and the project files themselves
  work   render(s) / cache / proxy / optimized or transcoded media / frames / intermediates /
         previews / autosave / backup(s) / staging / temp
  other  everything else: look before deciding
Also lists editor-wide caches (Adobe media cache, DaVinci CacheClip / ProxyMedia, Final Cut
generated files) with the in-app way to clear them, which is safer than deleting by hand.

Writes (--out): projects.tsv, folders.tsv, caches.tsv, media.json, summary.txt
Usage: python3 -I scan_media_projects.py [--root DIR ...] [--idle-days 30] [--min-gb 1]
       [--max-depth 6] [--protect live-refs.txt] [--out DIR]
"""
import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

PROJECT_EXT = {
    ".prproj": "Premiere Pro", ".aep": "After Effects", ".aepx": "After Effects", ".drp": "DaVinci Resolve",
    ".blend": "Blender", ".kdenlive": "Kdenlive", ".mlt": "Shotcut", ".osp": "OpenShot", ".tscproj": "Camtasia",
    ".camproj": "Camtasia", ".veg": "Vegas", ".vf": "Vegas", ".sesx": "Audition", ".rpp": "Reaper", ".als": "Ableton Live",
    ".ptx": "Pro Tools", ".c4d": "Cinema 4D", ".hip": "Houdini", ".hiplc": "Houdini", ".nk": "Nuke",
}
BUNDLE_EXT = {".fcpbundle": "Final Cut Pro", ".imovielibrary": "iMovie", ".logicx": "Logic Pro", ".band": "GarageBand",
              ".screenflow": "ScreenFlow", ".cmproj": "Camtasia"}
CODE_MARKERS = {"remotion.config.ts": "Remotion", "remotion.config.js": "Remotion", "remotion.config.mjs": "Remotion"}
KEEP_WORDS = {"source", "sources", "footage", "raw", "rushes", "original", "originals", "takes", "audio", "music",
              "sound", "sfx", "vo", "voiceover", "assets", "graphics", "images", "photos", "final", "finals", "export",
              "exports", "deliver", "delivery", "deliverables", "master", "masters", "covers", "cover", "thumbnails",
              "thumbs", "posters", "project", "projects"}
WORK_WORDS = {"render", "renders", "cache", "caches", "cacheclip", "proxy", "proxies", "proxymedia", "optimized",
              "transcoded", "frames", "frame", "intermediate", "intermediates", "tmp", "temp", "preview", "previews",
              "autosave", "backup", "backups", "staging", "scratch", "wip", "gallery", "peak", "conformed"}
SKIP_WALK = {"node_modules", ".git", ".venv", "venv", "__pycache__", "Library", "AppData"}


def words(name):
    return set(w for w in re.split(r"[^a-z0-9]+", name.lower()) if w)


def hint(name):
    w = words(name)
    keep, work = bool(w & KEEP_WORDS), bool(w & WORK_WORDS)
    if keep and not work:
        return "keep"
    if work and not keep:
        return "work"
    return "other"


def editor_of(names):
    for n in names:
        ext = os.path.splitext(n)[1].lower()
        if ext in PROJECT_EXT:
            return PROJECT_EXT[ext]
        if ext in BUNDLE_EXT:
            return BUNDLE_EXT[ext]
        if n in CODE_MARKERS:
            return CODE_MARKERS[n]
    return ""


def find_projects(roots, max_depth):
    found = {}
    for root in roots:
        stack = [(C.lp(root), 0)]
        while stack:
            d, depth = stack.pop()
            items = C.list_dir(d)
            if items is None:
                continue
            names = [e.name for e, _, _ in items]
            ed = editor_of(names)
            if ed and d != C.lp(root):
                found[C.sp(d)] = ed
                continue
            if depth >= max_depth:
                continue
            for e, kind, st in items:
                if kind in ("dir", "cloud_dir") and e.name not in SKIP_WALK and not e.name.startswith(".") \
                        and os.path.splitext(e.name)[1].lower() not in BUNDLE_EXT:
                    stack.append((e.path, depth + 1))
    return found


def project_stats(proj):
    """Per first-level folder: bytes and bytes freed if deleted (hard-link aware, exact on every OS)."""
    files = []
    base = len(C.lp(proj).rstrip("\\/")) + 1
    stack = [C.lp(proj)]
    cloud = 0
    while stack:
        for e, kind, st in C.list_dir(stack.pop()) or []:
            if kind in ("dir", "cloud_dir"):
                stack.append(e.path)
            elif kind == "file":
                if C.is_cloud_only(st):
                    cloud += st.st_size
                    continue
                rel = e.path[base:]
                top = rel.replace("\\", "/").split("/", 1)[0] if ("/" in rel or "\\" in rel) else "(files)"
                lst = st
                if C.IS_WIN:
                    try:
                        lst = os.lstat(e.path)
                    except OSError:
                        continue
                files.append((top, (lst.st_dev, lst.st_ino), lst.st_nlink, C.local_bytes(st), C.newest_ts(st)))
    uniq, count, in_top = {}, {}, {}
    for f in files:
        uniq[f[1]] = f
        count[f[1]] = count.get(f[1], 0) + 1
        in_top[(f[0], f[1])] = in_top.get((f[0], f[1]), 0) + 1
    total = sum(f[3] for f in uniq.values())
    frees = sum(f[3] for i, f in uniq.items() if count[i] >= max(1, f[2]))
    tops = {}
    for (top, ino), c in in_top.items():
        f = uniq[ino]
        t = tops.setdefault(top, [0, 0])
        t[0] += f[3]
        if c >= max(1, f[2]):
            t[1] += f[3]
    newest = max([f[4] for f in files] or [0.0])
    return {"bytes": total, "frees": frees, "newest": newest, "tops": tops, "cloud_bytes": cloud}


def editor_caches():
    H = C.HOME
    j = os.path.join
    if C.IS_WIN:
        R = os.environ.get("APPDATA", j(H, "AppData", "Roaming"))
        L = os.environ.get("LOCALAPPDATA", j(H, "AppData", "Local"))
        adobe = [j(R, "Adobe", "Common", "Media Cache Files"), j(R, "Adobe", "Common", "Media Cache")]
        ae = [j(L, "Temp", "Adobe", "After Effects")]
        vids = j(H, "Videos")
    else:
        sup = j(H, "Library", "Application Support")
        adobe = [j(sup, "Adobe", "Common", "Media Cache Files"), j(sup, "Adobe", "Common", "Media Cache")]
        ae = [j(H, "Library", "Caches", "Adobe", "After Effects")]
        vids = j(H, "Movies") if C.IS_MAC else j(H, "Videos")
    return [
        ("Adobe media cache (Premiere / After Effects)", adobe, "Premiere > Settings > Media Cache > Delete (unused), with no project open"),
        ("After Effects disk cache", ae, "After Effects > Settings > Media & Disk Cache > Empty Disk Cache"),
        ("DaVinci Resolve render cache", [j(vids, "CacheClip")], "Resolve > Playback > Delete Render Cache > All (per project)"),
        ("DaVinci Resolve proxies", [j(vids, "ProxyMedia")], "Media Pool > right-click clips > Delete Proxy Media (re-creatable)"),
    ]


def main():
    ap = argparse.ArgumentParser(description="Read-only scan of video / creative projects.")
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--idle-days", type=int, default=30)
    ap.add_argument("--min-gb", type=float, default=1.0)
    ap.add_argument("--max-depth", type=int, default=6)
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--out", default=C.default_out("media"))
    a = ap.parse_args()
    t0 = time.time()
    idle_ts = t0 - a.idle_days * 86400
    refs = C.read_ref_files(a.protect)
    cloud = C.cloud_roots()
    defaults = [os.path.join(C.HOME, n) for n in ("Videos", "Movies", "Documents", "Desktop")]
    roots = [os.path.abspath(os.path.expanduser(r)) for r in a.root] or [d for d in defaults if os.path.isdir(d)]
    projects = find_projects(roots, a.max_depth)
    # media-heavy first-level folders that hold no project file
    proj_keys = [C.key(p) for p in projects]
    for root in roots:
        for e, kind, st in C.list_dir(root) or []:
            if kind not in ("dir", "cloud_dir") or e.name.startswith(".") or e.name in SKIP_WALK:
                continue
            k = C.key(e.path)
            if any(C.overlaps(k, pk) for pk in proj_keys):
                continue
            s = C.tree_stats(e.path, media=True)
            if s.get("media_bytes", 0) >= a.min_gb * 1024 ** 3:
                projects[C.sp(e.path)] = "media folder"
    C.progress("scan_media_projects: %d projects under %s (%s); sizing..." % (
        len(projects), ", ".join(C.tilde(r) for r in roots), C.elapsed(t0)))
    prows, frows = [], []
    for proj, editor in sorted(projects.items()):
        s = project_stats(proj)
        if not s["bytes"] and not s["cloud_bytes"]:
            continue
        k = C.key(proj)
        state = "CLOUD" if C.in_cloud(proj, cloud) else "LIVE" if any(C.overlaps(k, r) for r in refs) else "REVIEW"
        split = {"keep": 0, "work": 0, "other": 0}
        for top, (b, fr) in s["tops"].items():
            h = "keep" if top == "(files)" else hint(top)
            split[h] += fr
            frows.append({"project": proj, "folder": top, "bytes": b, "frees": fr, "hint": h})
        prows.append({"project": proj, "editor": editor, "state": state, "newest": s["newest"], "idle": s["newest"] < idle_ts,
                      "bytes": s["bytes"], "frees": s["frees"], "keep": split["keep"], "work": split["work"],
                      "other": split["other"], "cloud_bytes": s["cloud_bytes"]})
    prows.sort(key=lambda r: -r["frees"])
    frows.sort(key=lambda r: -r["frees"])
    crows = []
    for name, paths, how in editor_caches():
        present = [p for p in paths if os.path.isdir(p)]
        if present:
            crows.append({"name": name, "bytes": sum(C.tree_stats(p)["bytes"] for p in present), "paths": present, "how": how})
    for p in prows:
        if p["editor"] == "Final Cut Pro":
            crows.append({"name": "Final Cut generated files in %s" % os.path.basename(p["project"]), "bytes": p["work"],
                          "paths": [p["project"]], "how": "Final Cut > select the library > File > Delete Generated Library Files"})

    C.write_tsv(os.path.join(a.out, "projects.tsv"),
                ["state", "editor", "last_touched", "idle", "gb", "frees_gb", "keep_gb", "work_gb", "other_gb", "project"],
                [(p["state"], p["editor"], C.day(p["newest"]), p["idle"], C.gb(p["bytes"]), C.gb(p["frees"]), C.gb(p["keep"]),
                  C.gb(p["work"]), C.gb(p["other"]), p["project"]) for p in prows])
    C.write_tsv(os.path.join(a.out, "folders.tsv"), ["hint", "gb", "frees_gb", "project", "folder"],
                [(f["hint"], C.gb(f["bytes"]), C.gb(f["frees"]), f["project"], f["folder"]) for f in frows])
    C.write_tsv(os.path.join(a.out, "caches.tsv"), ["gb", "name", "how", "paths"],
                [(C.gb(c["bytes"]), c["name"], c["how"], " | ".join(c["paths"])) for c in crows])
    idle = [p for p in prows if p["idle"] and p["state"] == "REVIEW"]
    lines = ["scan_media_projects (%s): %d projects, %s | idle %d+ days: %d projects, %s freeable, of which %s in work folders" % (
        C.elapsed(t0), len(prows), C.human(sum(p["bytes"] for p in prows)), a.idle_days, len(idle),
        C.human(sum(p["frees"] for p in idle)), C.human(sum(p["work"] for p in idle)))]
    lines.append("biggest projects (frees if deleted / keep / work / other, last touched):")
    lines += ["  %8s / %8s / %8s / %8s  %s  %-6s %s%s" % (C.human(p["frees"]), C.human(p["keep"]), C.human(p["work"]), C.human(p["other"]),
                                                         C.day(p["newest"]), p["state"], C.tilde(p["project"]),
                                                         "  [%s]" % p["editor"] if p["editor"] else "") for p in prows[:15]]
    work = [f for f in frows if f["hint"] == "work"]
    if work:
        lines.append("biggest work folders (usually re-creatable; owner decides per project):")
        lines += ["  %8s  %s / %s" % (C.human(f["frees"]), C.tilde(f["project"]), f["folder"]) for f in work[:12]]
    if crows:
        lines.append("editor caches (clear from inside the app):")
        lines += ["  %8s  %s: %s" % (C.human(c["bytes"]), c["name"], c["how"]) for c in crows]
    lines.append("All of this is REVIEW: show the owner project, date and keep/work split, and let them choose.")
    C.finish(a.out, "media", {"roots": roots, "projects": prows, "folders": frows[:2000], "caches": crows}, lines)


if __name__ == "__main__":
    main()
