# Video and creative projects

Video work is often the single biggest thing on a creator's disk, and none of it is the agent's to
decide. `scan_media_projects.py` makes the decision easy for the owner; it never makes it for them.

## What the scanner reports

For each project (a folder with an editor project file or library, or a first-level folder of a root
holding at least `--min-gb` of media):

- **last touched**, and whether it's idle (`--idle-days`, default 30)
- **frees if deleted**: hard-link aware, so footage linked from elsewhere isn't counted twice
- a split of its first-level folders:
  - **keep**: source / footage / raw / originals / takes / audio / music / assets / final / export(s)
    / deliver(y/ables) / master(s) / covers / thumbnails, and the project files themselves
  - **work**: render(s) / cache / proxy / optimized or transcoded media / frames / intermediates /
    previews / autosave / backup(s) / staging / temp
  - **other**: everything else. Look before deciding.
- editor-wide caches with the in-app way to clear them

Default roots: `~/Videos`, `~/Movies`, `~/Documents`, `~/Desktop`. Add the owner's real project
folders with `--root` (repeatable): ask where they keep projects. Everything is **REVIEW**.

## How to present it

One row per idle project, biggest first: name, last touched, frees-if-deleted, keep / work / other.
Then ask per project, offering three choices:

1. **Archive** (the usual sweet spot): keep source + final + covers + project files, remove the
   work folders. Typically a large share of a finished project is re-creatable work files: renders
   of each stage, proxies, caches, staging copies.
2. **Delete the whole project** (after confirming the final is delivered or backed up elsewhere).
3. **Keep as is.**

Then build a plan: work folders and whole projects the owner chose → `trash` rows (they are personal
files) unless the owner explicitly says to delete permanently. Dry run, then apply.

Better still, move finished projects to an external or archive drive, and verify the copy (size and
a few hashes) before removing the original.

## Prefer the editor's own cleanup

| Editor | Built-in cleanup |
|---|---|
| Premiere Pro / After Effects | Settings > Media Cache > Delete (unused); After Effects > Media & Disk Cache > Empty Disk Cache; Project Manager to collect and trim a finished project |
| DaVinci Resolve | Playback > Delete Render Cache > All; Media Pool > Delete Proxy Media / Optimized Media; the project's `CacheClip` / `ProxyMedia` folders follow from those |
| Final Cut Pro | Select the library > File > Delete Generated Library Files (renders, optimized and proxy media) |
| Blender | Remove baked caches in the physics/simulation panels; `blendcache_*` folders are bake caches |
| Code-based video (Remotion and similar) | Renders in `out/` are often the finals: ask; dependency folders are dev junk |

## Never

- Delete source footage, recordings or finals without an explicit yes for that project.
- Touch a project that is LIVE (a render or app is using it) or inside a cloud-sync folder.
- Delete inside an editor's library package (`.fcpbundle`, Photos library) by hand.

## Regrowth

Pipelines that copy the raw recording into every project (often twice, while the original stays in
Downloads) multiply disk use. `scan_downloads.py` finds the duplicates; the lasting fix is in the
pipeline: hard-link or reference sources instead of copying, and add a post-delivery "archive"
step. See `references/regrowth.md`.
