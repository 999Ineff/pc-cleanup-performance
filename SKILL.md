---
name: pc-cleanup-performance
description: Safely audit and clean up a Windows, macOS or Linux computer (and WSL inside Windows) with the owner's approval at every step. Checks disk and SSD health, maps what is using the disk, finds safe junk (node_modules and build folders in idle projects, leftover git worktrees, package caches, temp files, old installers, duplicate downloads), reviews personal and creative files item by item, slims startup apps, browser and RAM use, and sets up guards so the junk doesn't grow back. Use this whenever someone says "clean up my computer", "free up disk space", "my disk is full", "my C drive is red", "my Mac/PC is slow", "what's eating my disk", "what's using my RAM", "my browser eats too much memory", "startup apps", "too many things open at login", "is my SSD OK", "WSL Input/output error", or asks for a checkup, tune-up or spring cleaning of their machine, even if they never name this skill. Also use it before any large delete on a personal computer.
---

# PC cleanup & performance

A careful checkup and cleanup of the computer this agent runs on. The shape never changes:

**scan read-only → show the owner specifics → they say yes → dry run → apply → log → report.**

Every script here is read-only except `scripts/delete_paths.py` (guarded, dry run by default),
`scripts/windows/admin_maintenance.ps1` (dry run unless `-Apply`) and `scripts/wsl/wsl_fsck.sh` (its
file-system check is read-only, but it attaches the WSL disk file and ends with `wsl --shutdown`, so
ask the owner first). Everything else that changes the system is a documented command in
`references/`, run only after the owner says yes.

## Safety rules (non-negotiable)

These exist because each one was learned the hard way. `references/safety.md` explains every rule;
read it before the first delete of a session.

1. **Read-only first.** Scan and report before proposing anything.
2. **Exact paths only.** Deletions come from a plan file of exact paths, re-validated at delete time. Never globs, never variables, never hand-written `rm -rf` / `Remove-Item -Recurse`.
3. **Dry run before apply**, of the same plan file (`delete_paths.py` enforces this).
4. **Approval is per list, not per category.** "Old downloads can go" is a policy; the owner still sees and approves the exact list.
5. **Personal files go to the Trash / Recycle Bin; regenerable junk is deleted permanently.**
6. **Never delete inside a cloud-sync folder** (OneDrive, iCloud Drive, Dropbox, Google Drive...): that deletes the cloud copy. Use the provider's "free up space" instead.
7. **Never delete a top-level home folder, the home folder itself, or a system folder.**
8. **Never touch anything a live tool uses**: run `live_refs.py` first and pass its list to everything with `--protect`. If an agent's "safe" list disagrees with it, the live list wins.
9. **Never delete uncommitted or unpushed git work.** Worktrees go through `git worktree remove`, which refuses dirty trees.
10. **Ask before** closing apps, stopping WSL or VMs, or anything that needs admin/sudo, and batch all admin steps into **one** prompt.
11. **Record an undo** for every startup or system change.
12. **Size with hard links in mind** (uv, pnpm and media pipelines share files; naive sums overstate).
13. **Prefer Python for deleting.** PowerShell variables are case-insensitive and aliases outrank functions; both have turned "delete this" into "delete that".

If unsure, it's a question for the owner, not a decision for the agent.

## Step 0: detect the machine (always first)

```
python3 -I scripts/sysinfo.py        # Windows: python -I scripts\sysinfo.py  (or py -3 -I ...)
```

Run scripts from this skill's folder (the one holding this file), with `-I` so nothing in the current
folder can shadow a standard module. Python 3.8+ standard library only; nothing to install.
`sysinfo.py` prints the OS (`windows`, `macos`, `linux` or `wsl`), RAM, free space, tools present,
cloud-sync folders and agent configs, and which reference to follow:

| sysinfo says | Read | Shell helpers |
|---|---|---|
| `windows` | `references/windows.md` (+ `references/wsl.md` if WSL distros are listed) | `scripts/windows/*.ps1` |
| `macos` | `references/macos.md` | `scripts/macos/*.sh` |
| `linux` | `references/linux.md` | `scripts/linux/*.sh` |
| `wsl` | `references/linux.md` + `references/wsl.md` | `scripts/linux/*.sh` inside; Windows side separately |

**WSL = two machines.** The Linux distro lives in one disk file on Windows that never shrinks by
itself. Clean inside the distro first, then compact from Windows (`references/wsl.md`).

Outputs go to `~/pc-cleanup-performance-runs/<YYYY-MM-DD>/<scanner>/` (override with `PCM_RUNS_DIR`). If a
previous run's `REPORT.md` exists there, compare every number with it.

## The run

### 1. Health (read-only)
Run the OS health script (`health_check.ps1` / `.sh`). Look for storage controller resets, disk or
file-system errors, unexpected shutdowns, SMART warnings, and in WSL, ext4 errors. **If health is
bad, stop cleaning and lead the report with it**: back up first, cleanup can wait. How to read each
signal: the OS reference, section "Health".

### 2. Map the disk (read-only)
`python3 -I scripts/scan_disk.py` (1-5 minutes; run it in the background). It reports volumes, the
biggest folders and files, cloud-sync folders (local vs cloud-only), known space hogs for this OS with
the official way to clear each, and OS extras (restore points, snapshots, journal, WSL/Docker disks).

### 3. Live-tool cross-check (read-only, before any classifying)
`python3 -I scripts/live_refs.py --out <run>/live-refs`. It collects paths used by MCP servers and
hooks in agent configs (Claude Code, Claude Desktop, Cursor, Windsurf, Codex, Gemini CLI, VS Code and
others), running processes, scheduled tasks, services, startup items and PATH. Pass
`<run>/live-refs/live-refs.txt` as `--protect` to every scanner and to `delete_paths.py`. Read the
`BROAD` warnings: a whole top-level folder in use means close that app and re-run, or ask.

### 4. Find candidates (read-only)
- `scan_dev_junk.py --protect ...`: artifacts in idle projects, git worktrees, tool caches → `references/dev-junk.md`
- `scan_downloads.py --protect ...`: Downloads older than ~6 months, byte-identical duplicates
- `scan_media_projects.py --protect ...`: video and creative projects, keep/work split → `references/media-projects.md`
- OS items from `scan_disk.py` (`known`, `extras`) → the OS reference

### 5. Decide with the owner
Show one compact table, real numbers, biggest first:
- **SAFE**: one approval for the whole list (count, total GB, the 5-10 biggest items).
- **REVIEW**: each item on its own with specifics (what it is, last touched, size, what is inside).
  Use the agent's question tool if it has one (multi-select works well).
- **LIVE / ACTIVE / CLOUD / KEEP**: information only, never offered for deletion.

| Class | Meaning | Who decides |
|---|---|---|
| SAFE | Regenerable, unused for 30+ days, not live, nothing tracked by git | Owner approves the list once |
| REVIEW | Personal, creative, models, VMs, Docker, dirty or unpushed worktrees, anything unusual | Owner, item by item |
| LIVE | A running tool, agent config, startup item or scheduled job uses it | Nobody: keep |
| ACTIVE | Project changed recently | Keep |
| CLOUD | Inside a cloud-sync folder | Use the provider's free-up-space; never delete |
| KEEP | Needed by the OS or apps (Installer cache, WinSxS, Photos library...) | Keep |

### 6. Execute (only what was approved)
- **Files and folders:** a plan TSV through `delete_paths.py`. Dry run, show any refusals, then
  `--apply --log <run>/delete-log.txt`. Refusals are reported, never forced with another tool.
- **Git worktrees:** the commands in `safe-worktrees.tsv` (`git worktree remove`, no `--force`).
- **Caches:** each tool's own clean command (`npm cache clean --force`, `uv cache clean`,
  `pnpm store prune`, `brew cleanup -s`...). If a live tool runs from a cache, clear it when no agent
  is running (Windows: the next-sign-in pattern in `references/windows.md`).
- **Admin/sudo steps:** collected into **one** prompt. Windows: `admin_maintenance.ps1` (dry run,
  then one UAC run with only the approved switches). macOS/Linux: one `sudo` block the owner runs.
- After each step, record the free-space change. Report failures plainly.

### 7. Startup and RAM
Run `startup_audit.ps1` / `.sh`. Agree with the owner what should start at login (keep antivirus,
cloud sync, drivers, and anything they use daily). Turn the rest off the way the OS reference shows,
writing the **undo** for each into the report. Save a baseline (Windows: `-SaveBaseline <run>/startup-baseline.json`)
so the next run can flag what crept back.

**Browser RAM audit.** First **ask which browser(s) they use daily** (Chrome, Edge, Brave, Firefox,
Safari, Arc, Opera, Vivaldi, other) and tailor everything to that answer; never assume Chrome. Then
`python3 -I scripts/browser_scan.py --browser <their browsers> --out <run>/browsers` (measure
before), and walk them through `references/browsers.md`: tab sleeping on, keep-running-after-close
off, extensions that inject into every page disabled or set to "On click", risky or orphaned
extensions flagged. Re-measure the same way afterwards and put both numbers in the report.

### 8. Stop the regrowth
Check and offer the fixes in `references/regrowth.md`: agent worktrees sharing `node_modules`, the
optional disk-guard hook, media pipelines that copy recordings, log retention, search indexing,
Docker growth. Nothing is changed without a yes; agent settings are merged, never overwritten.

### 9. Report
Write `<run>/REPORT.md` from `references/report-template.md`: free space before → after (measured, not
estimated), health, what was removed and where each GB came from, decisions, logs, undo records,
pending items, and when to run again (about every 3 months, or when free space drops under ~15%).
Then tell the owner the outcome in plain language.

## Scripts

| Script | What it does |
|---|---|
| `sysinfo.py` | OS / WSL detection, RAM, free space, tools, cloud folders, agent configs, next reference |
| `live_refs.py` | Folders any live tool depends on → `live-refs.txt` for `--protect` |
| `scan_disk.py` | Volumes, biggest folders/files (hard-link aware), cloud local vs cloud-only, known hogs, OS extras |
| `scan_dev_junk.py` | Project artifacts, worktrees (clean/pushed/merged/locked), tool caches + clean commands, agent settings |
| `scan_downloads.py` | Old Downloads items, SHA-256 duplicates, a ready plan (trash personal, delete installers) |
| `scan_media_projects.py` | Video/creative projects: frees-if-deleted, keep/work/other split, editor caches |
| `delete_paths.py` | The only deleter: guarded, dry run by default, Trash for personal files |
| `browser_scan.py` | Browser-wide RAM settings, per-profile extensions (every-page, background, risky, orphaned), browser process RAM by kind |
| `disk_guard_hook.py` | Optional Claude Code hook that blocks heavy commands when disk space is low |
| `windows/health_check.ps1`, `macos/…`, `linux/…` | Disk health, error events, shutdowns, memory |
| `windows/startup_audit.ps1`, `macos/…`, `linux/…` | Startup items with on/off state and location, biggest RAM users |
| `windows/admin_maintenance.ps1` | Restore-point cap, smaller hibernation file, VHDX compaction: dry run unless `-Apply` |
| `wsl/wsl_fsck.sh` (+ `fsck_check.sh`) | Read-only ext4 check of a WSL disk file while WSL is off; attaches the disk and ends with `wsl --shutdown` (ask first) |

All scanners take `--out DIR` and write JSON + TSV + `summary.txt`; the summary is also printed.

## If something goes wrong
- **Health signals are bad** (rising controller resets, disk/file-system errors, SMART warnings, WSL
  "Input/output error"): stop cleanup, tell the owner, back up, then follow the OS reference.
- **A scanner crashes on a path:** re-run with a narrower `--root`, report the path. Never skip `live_refs.py` to "get it working".
- **`delete_paths.py` refuses or fails a row:** report each reason. Don't route around it.
- **A UAC / sudo prompt is cancelled:** nothing ran; say so and offer to retry.
- **Code found in a cloud-sync folder, or a dirty / unpushed worktree:** hand it to the owner. This skill never edits repositories.
- **macOS permission prompts:** expected for Desktop/Documents/Downloads; `references/macos.md` explains Full Disk Access.

## Done when
- [ ] `REPORT.md` exists with measured before/after free space
- [ ] every applied step has a log line and a GB delta; failures are listed
- [ ] every `--apply` was preceded by a dry run of the same plan
- [ ] every startup or system change has an undo line
- [ ] after any WSL compaction: fsck clean before and after, and the distro boots with 0 errors
- [ ] pending items (reboot, admin prompt, owner decisions) are listed with the next-run date
