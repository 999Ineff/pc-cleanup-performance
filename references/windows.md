# Windows

Windows 10 and 11. Scripts: `scripts/windows/*.ps1` (Windows PowerShell 5.1 or PowerShell 7), run as
`powershell -NoProfile -ExecutionPolicy Bypass -File <script>`. Python: `python -I` or `py -3 -I`.
If WSL distros are installed, also read `references/wsl.md`.

## Contents
1. Health
2. Where the space goes, and how to get it back
3. Cloud sync (OneDrive Files On-Demand)
4. System trims (one admin prompt)
5. Caches that live tools keep locked: clean at next sign-in
6. Startup and RAM
7. Developer Mode, Search indexing, misc

## 1. Health

`health_check.ps1` counts events per month for the last 12 months. Judge trends, not single events.

| Signal | Normal | Worry when | Then |
|---|---|---|---|
| Event 129 (storage controller reset: stornvme, storahci, RAID drivers) | 0 | any, especially rising month over month | The SSD or its power management is misbehaving. Back up first. Check for SSD firmware and storage driver updates. As a test, set PCI Express > Link State Power Management to Off (Power Options > advanced settings). Plan a replacement if resets continue. |
| disk 7 / 51 / 153 (bad block, paging error, retried IO) | 0 | any | Back up now; the drive is failing or a cable/port is. |
| Ntfs 55 / 98 (warning or error) | 0 | any | `chkdsk C: /scan` in an elevated shell (online, read-only); repair only after a backup. |
| Kernel-Power 41 with BugcheckCode 0 | rare (power cuts) | several a month | Hard freezes, not blue screens. Often storage or power related; look at Event 129 together. |
| EventLog 6008 (unexpected shutdown) | rare | frequent | Same as above. |
| BugCheck 1001 (blue screen) | 0 | any recent | Note the bugcheck code; usually a driver or hardware. |
| WHEA-Logger errors | 0 | any | Hardware error (RAM, CPU, PCIe). Back up; test RAM. |
| `Get-PhysicalDisk` health | Healthy | Warning / Unhealthy | Back up now. Reliability counters (wear, temperature, errors) need an elevated shell. |
| Free space | > 15% | < 10% | Clean up now. Below ~5%, apps and virtual disks that live on the drive can corrupt. |
| WSL crash dumps (`%TEMP%\wsl-crashes`) | none | new ones | `references/wsl.md`, corruption playbook. |

If any row is in "worry", lead the report with it and recommend a backup before cleanup.

## 2. Where the space goes, and how to get it back

`scan_disk.py` lists these with sizes. Prefer Windows' own tools: they know what is in use.

| Item | Class | How |
|---|---|---|
| Temporary files, Windows Update cleanup, Delivery Optimization, error reports, thumbnails | SAFE | Settings > System > Storage > Temporary files (tick, review, Remove). Admin items included. |
| Storage Sense | - | Settings > System > Storage > Storage Sense: on, with Recycle Bin and Downloads rules the owner chooses. `scan_disk.py` reports whether it is on. |
| Recycle Bin | SAFE | Empty after a last look. |
| Downloads | SAFE / REVIEW | `scan_downloads.py` plan through `delete_paths.py`. |
| Dev junk | SAFE / REVIEW | `scan_dev_junk.py`, `references/dev-junk.md`. |
| Video / creative projects | REVIEW | `scan_media_projects.py`, `references/media-projects.md`. |
| Unused apps | REVIEW | Settings > Apps > Installed apps (sort by size), or `winget list` then `winget uninstall --id <id>`. `scan_disk.py` lists the biggest by their reported size. Leftover data folders in `%LOCALAPPDATA%` / `%APPDATA%` afterwards: REVIEW, through `delete_paths.py`. |
| Virtual machines (Hyper-V, VirtualBox, VMware), emulator images | REVIEW | Delete from the app that owns them, not by hand. |
| WSL distros and Docker Desktop disk files (`*.vhdx`) | REVIEW | They never shrink by themselves: `references/wsl.md`. |
| Browser caches | SAFE | From the browser: Delete browsing data > Cached images and files. Never under a running browser. |
| Chrome's on-device AI model (`OptGuideOnDeviceModel`, ~4 GB) | REVIEW | Comes back after updates unless turned off at `chrome://flags` (optimization-guide-on-device-model). |
| Restore points, hibernation file, Windows.old | REVIEW | Section 4, or Temporary files > Previous Windows installation(s) (removes rollback). |
| `C:\Windows\Installer`, `WinSxS`, `ProgramData\Package Cache` | KEEP | Never by hand. WinSxS only via `DISM /Online /Cleanup-Image /StartComponentCleanup` (admin). |

## 3. Cloud sync (OneDrive Files On-Demand)

Deleting a file inside OneDrive (or any synced folder) deletes it in the cloud and on every device.
To get the local space back while keeping the files:

- Explorer: right-click a folder > **Free up space**. Files become cloud-only placeholders (cloud
  icon) and download again when opened.
- Command line, per folder (no admin): `attrib +U -P "<folder>\*" /S /D`
  (`+U` = online-only, `-P` = not pinned). Undo: `attrib -U +P ...` (Always keep on this device).
- `scan_disk.py` reports each sync folder's local vs cloud-only bytes; the drop shows up as OneDrive
  finishes (minutes to hours for large folders).
- **Code should not live in a synced folder**: sync engines and `git`/`node_modules` fight, and
  `git status` there downloads every cloud-only file. `scan_dev_junk.py` lists repos found inside sync
  folders as CLOUD. Moving them out is the owner's call; check the remote and last commit first.

One run's example: about 95 GB came back from setting a large OneDrive to cloud-only. Nothing was
deleted.

## 4. System trims (one admin prompt)

Batch the approved ones into a single elevated run of `scripts/windows/admin_maintenance.ps1`:

```
# dry run (no admin, changes nothing)
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\windows\admin_maintenance.ps1 -CapRestoreGB 10 -ReducedHiberfil -RunDir <run>\admin
# after the yes: one UAC prompt (tell the owner to click Yes), then wait for <run>\admin\admin.done
Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File','"<skill>\scripts\windows\admin_maintenance.ps1"','-Apply','-CapRestoreGB','10','-ReducedHiberfil','-RunDir','"<run>\admin"'
```

| Switch | Effect | Undo |
|---|---|---|
| `-CapRestoreGB N` | Caps System Restore storage; Windows drops the oldest points beyond the cap | `vssadmin resize shadowstorage /for=C: /on=C: /maxsize=<old>` (admin) |
| `-ReducedHiberfil` | Hibernation file about half size; Fast Startup still works, full hibernate doesn't | `powercfg /h /type full` (admin) |
| `-CompactVhd <path>` | Compacts a WSL/Docker disk file, only after the recipe in `references/wsl.md` | n/a (the content is unchanged) |

One run's example: restore points capped at 10 GB and a reduced hibernation file gave back about
15 GB; compacting a WSL disk file took it from 90.5 GB to 68.6 GB in four minutes.

`Start-Process` throws if the UAC prompt is cancelled: nothing ran; offer to retry.

## 5. Caches that live tools keep locked: clean at next sign-in

Agents start MCP servers and helpers from package caches (`npx` → `%LOCALAPPDATA%\npm-cache\_npx`,
`uvx` → the uv cache). While they run, Windows won't let those files go, and deleting them would
break the tools anyway. Clean them at the next sign-in, before any agent starts, with a one-time
RunOnce entry (current user, no admin). Only after the owner approved it:

```powershell
$zzRun = '<run folder>'            # e.g. ~\pc-cleanup-performance-runs\2026-01-31
$zzSkill = '<skill folder>'
$zzScript = Join-Path $zzRun 'next-login-cleanup.ps1'
@"
`$zzLog = Join-Path '$zzRun' 'next-login-cleanup-log.txt'
`$zzCaches = @("`$env:LOCALAPPDATA\uv\cache", "`$env:LOCALAPPDATA\npm-cache\_npx")
`$zzBusy = @(Get-CimInstance Win32_Process | Where-Object { `$zzCl = "`$(`$_.ExecutablePath) `$(`$_.CommandLine)"; @(`$zzCaches | Where-Object { `$zzCl -like "*`$_*" }).Count })
& {
  "=== start `$(Get-Date)  C: free `$([math]::Round((Get-PSDrive C).Free/1GB,1)) GB"
  if (`$zzBusy.Count) { "SKIPPED: tools already running from the caches: `$((`$zzBusy.Name | Sort-Object -Unique) -join ', ')"; return }
  uv cache clean
  python -I '$zzSkill\scripts\live_refs.py' --out '$zzRun\live-refs-login'
  python -I '$zzSkill\scripts\delete_paths.py' '$zzRun\npx-plan.tsv' --protect '$zzRun\live-refs-login\live-refs.txt'
  python -I '$zzSkill\scripts\delete_paths.py' '$zzRun\npx-plan.tsv' --protect '$zzRun\live-refs-login\live-refs.txt' --apply --log '$zzRun\delete-log.txt'
  "=== done `$(Get-Date)  C: free `$([math]::Round((Get-PSDrive C).Free/1GB,1)) GB"
} *>&1 | Out-File -FilePath `$zzLog -Encoding utf8
"@ | Out-File -FilePath $zzScript -Encoding utf8
New-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce' -Name 'PcMaintenanceNextLogin' -PropertyType String -Force `
  -Value "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$zzScript`""
```

`npx-plan.tsv` is the approved plan (`delete` rows for the `_npx` cache folder). The login run makes
a fresh live list, dry-runs, then applies, so every guard still holds. Tell the owner a PowerShell
window will flash at sign-in, and check `next-login-cleanup-log.txt` next session. Real freed space
for the uv cache is often a third of its file total because of hard links. Some antivirus products
block RunOnce scripts; if the log is missing, run the same steps by hand with all agents closed.
The tools re-download what they need on their next start.

## 6. Startup and RAM

`startup_audit.ps1` lists every startup item with its state and exact location, then RAM and the
biggest users. Agree on the list with the owner; keep antivirus, cloud sync, audio/graphics/input
drivers, and anything they use every day. Turn the rest off like this, writing the undo into the
report:

| Kind | Turn off | Undo |
|---|---|---|
| Run key / Startup folder item | Task Manager > Startup apps > Disable (writes `StartupApproved` first byte `03`) | Task Manager > Enable |
| Store app task | Settings > Apps > Startup > toggle off | toggle on |
| Logon/boot scheduled task | `Disable-ScheduledTask -TaskPath '<path>' -TaskName '<name>'` (some need admin) | `Enable-ScheduledTask ...` |
| Auto-start service | `Set-Service -Name <n> -StartupType Manual` (admin). Record the old type first; delayed start is `sc.exe config <n> start= delayed-auto` | `Set-Service -Name <n> -StartupType Automatic` |

- Some apps switch themselves back on after updates or from their own settings. Also turn off
  "start with Windows" inside the app, then save a baseline:
  `startup_audit.ps1 -SaveBaseline <run>\startup-baseline.json`. Next run: `-Baseline <that file>`
  flags anything new.
- Remote-access tools (support apps) set to Manual stop accepting incoming connections until started
  again. Say so before changing them.
- A vendor "telemetry" or "improvement program" service that writes gigabytes a day is worth an
  uninstall (Settings > Apps), with a yes.
- Example result on one machine: about a dozen auto-start apps and four background services turned
  off; RAM in use at idle right after sign-in went from about 10 GB to about 6 GB.

**RAM reading:** "available" is what matters; Windows uses spare RAM for cache on purpose.
`vmmem` / `vmmemWSL` is the WSL or Docker VM: cap it in `%USERPROFILE%\.wslconfig` (`references/wsl.md`).

## 7. Developer Mode, Search indexing, misc

- **Developer Mode** (Settings > System > For developers) lets a normal user create symlinks. Agent
  tools that share `node_modules` between git worktrees by symlink (`references/regrowth.md`) need
  it. `scan_disk.py` and `scan_dev_junk.py` report whether it is on. Note: Windows PowerShell 5.1's
  `New-Item -ItemType SymbolicLink` still asks for admin even with Developer Mode; Node, Python and
  git don't.
- **Windows Search indexing** can write many GB a day over big dev or media trees. Settings > Privacy
  & security > Searching Windows > exclude dependency-heavy and render folders. Offer it; don't
  change it silently.
- **`chkdsk`**: `chkdsk C: /scan` is online and read-only; `/f` or `/r` need a reboot and a backup first.
- **PowerShell for deletes**: avoid; see `references/safety.md`, section 5.
