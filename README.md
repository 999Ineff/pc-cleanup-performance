# pc-cleanup-performance

An agent skill that gives your computer a careful checkup and cleanup: **Windows, macOS, Linux, and
WSL**. Hand it to Claude Code, Codex, Cursor, Gemini CLI or any coding agent, say "clean up my
computer", and it works through the machine with you, asking before anything is removed.

## What it does

- 🩺 **Health first.** SSD/disk health, storage controller resets, file-system errors, unexpected
  shutdowns, kernel panics. If the disk is failing, it tells you to back up before anything else.
- 🗺️ **Disk map.** Biggest folders and files, counted correctly when files are hard-linked (package
  caches and media pipelines share files; a naive sum can overstate by a third or more).
- 🛡️ **Live-tool cross-check.** Before calling anything "safe", it reads which folders your running
  apps, MCP servers, agent configs, scheduled tasks and startup items use, and never touches them.
- 🧹 **Safe junk** (one approval for the list): `node_modules`, venvs and build output in idle
  projects, finished git worktrees, package caches (npm, pnpm, yarn, pip, uv, Go, Cargo, Xcode...),
  temp files, old installers.
- 🔍 **Your call, item by item:** old Downloads, byte-identical duplicates (SHA-256), video project
  work files (keeps source, finals and covers), local AI models, Docker images, unused apps and VMs.
- ☁️ **Cloud folders done right.** OneDrive / iCloud / Dropbox files are set to online-only to get the
  space back, never deleted (deleting there deletes the cloud copy).
- ⚙️ **System trims** behind one admin prompt: restore-point cap, smaller hibernation file, WSL and
  Docker disk compaction (Windows), journal, snaps and package caches (Linux), local snapshots,
  Homebrew and Xcode (macOS).
- 🚀 **Startup and RAM.** Lists every auto-start item with how to turn it off and how to undo it.
- 🌐 **Browser RAM audit (asks which browser you use).** Tab sleeping, background mode, extensions
  that load into every page, risky or left-behind extensions, and a before/after measurement for
  Chrome, Edge, Brave, Firefox, Safari, Opera, Vivaldi or Arc.
- 🔁 **Stops the regrowth.** Shared `node_modules` for agent worktrees, an optional disk-space guard
  for agents, and a report to compare against next time.

## Example run

A Windows 11 developer machine with a 1 TB SSD, used daily with AI coding agents:

| | Before | After |
|---|---|---|
| Free space on C: | **132 GB** | **392 GB** |
| Used | 820 GB | 560 GB |
| RAM in use at idle, right after sign-in | ~10 GB | ~6 GB |
| WSL Linux disk file | 90.5 GB | 68.6 GB |

Where the space came from:

| ≈ GB | Source |
|---|---|
| 95 | A cloud-sync folder set to online-only (nothing deleted) |
| 50 | Dev junk: `node_modules` in idle projects, finished agent worktrees, build output |
| 35 | Unused apps and old installers |
| 22 | WSL disk file compacted |
| 15 | Restore points capped, smaller hibernation file |
| 12 | Package caches |
| 11 | Temp files |
| 10 | Duplicate downloads already saved in projects |

Browser, same machine: one window used **1.7 GB** before (0.8 GB of tabs, 0.6 GB GPU process).
Extensions loading into every page went from **6 to 1** (extension processes 79 → 24 MB), Memory
Saver was turned on (Maximum), and background mode was turned off, so closing the browser now
leaves **0 processes** running.

## How it works

```
scan (read-only) -> show you specifics -> you say yes -> dry run -> apply -> log -> report
```

1. Detect the OS (and WSL), run the health check.
2. Map the disk; cross-check every live tool.
3. Sort what it finds into **SAFE** (regenerable, idle, not in use), **REVIEW** (yours to decide,
   item by item), and **keep** (live, active, cloud, system).
4. You approve exact lists. Deletes go through one guarded script, dry run first.
5. Startup and RAM review, regrowth fixes, and a `REPORT.md` with measured before/after numbers in
   `~/pc-cleanup-performance-runs/<date>/`.

## Install

Needs Python 3.8+ (standard library only; nothing to install) and, for git checks, `git`.
Windows uses the built-in PowerShell; macOS and Linux use the stock `sh`.

**Claude Code**
```
git clone https://github.com/999Ineff/pc-cleanup-performance ~/.claude/skills/pc-cleanup-performance
```
or `npx skills add 999Ineff/pc-cleanup-performance`. (Windows PowerShell: clone into
`$env:USERPROFILE\.claude\skills\pc-cleanup-performance`.) Then just ask: *"my disk is almost full, clean
up my computer"*.

**Codex, Cursor, Gemini CLI, and other agents**

Clone it anywhere, then point your agent at `SKILL.md`, either per request
(*"Read ~/tools/pc-cleanup-performance/SKILL.md and follow it to clean up this computer"*) or once, with a
line in your agent's instructions file (`AGENTS.md`, `GEMINI.md`, Cursor rules):
*"For computer cleanup, slowness, disk space or startup apps, follow ~/tools/pc-cleanup-performance/SKILL.md."*

Things people say that trigger it: "clean up my computer", "free up disk space", "C: drive is
full", "my Mac is slow", "what's eating my disk", "what's using my RAM", "too many startup apps",
"is my SSD OK".

## The safety promise

- Nothing is deleted without your yes on the **exact** list, after a dry run of that same list.
- Personal files go to the **Trash / Recycle Bin**. Only regenerable junk is deleted permanently.
- **Never** inside cloud-sync folders, never a top-level home folder, never system folders.
- **Never** anything a running app, MCP server, agent config, startup item or scheduled task uses.
- **Never** uncommitted or unpushed git work. Worktrees are removed with `git worktree remove`.
- Admin / sudo steps are batched into **one** prompt you see first. Every startup change gets an
  undo line.
- All scanners are read-only. The one delete script re-checks every path at delete time and refuses
  anything unsafe, with the reason.

Details: [`references/safety.md`](references/safety.md).

## Tested on

- **Windows 11** with WSL 2: every scanner, health and startup script run read-only on a real
  machine; the delete script's guards tested in dry run (home, cloud, system, live-tool, git,
  junction and sensitive paths are all refused).
- **Linux**: all scripts run inside WSL 2 Ubuntu 24.04.
- **Browsers**: `browser_scan.py` run on Chrome (8 profiles) and Edge on Windows, and on Chrome
  inside WSL; Firefox, Brave, Vivaldi, Opera and Safari paths follow their documented layouts but
  weren't installed on the test machines.
- **macOS**: written from Apple's documented commands, conservative by design, **not yet run on a
  real Mac**. Reports and fixes welcome.
- Python 3.8, 3.12 and 3.14 on Windows (every script); Python 3.12 inside WSL.

## Repository layout

```
SKILL.md                 the agent's instructions (start here)
references/              safety, windows, macos, linux, wsl, browsers, dev-junk, media-projects, regrowth, report-template
scripts/                 Python scanners + the guarded deleter (cross-platform)
scripts/windows/         health_check.ps1, startup_audit.ps1, admin_maintenance.ps1 (dry run unless -Apply)
scripts/macos/           health_check.sh, startup_audit.sh
scripts/linux/           health_check.sh, startup_audit.sh
scripts/wsl/             wsl_fsck.sh, fsck_check.sh (read-only ext4 check)
```

## License

MIT. See [LICENSE](LICENSE).
