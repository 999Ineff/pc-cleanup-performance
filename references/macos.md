# macOS

macOS 12 Monterey and newer (Apple silicon and Intel). Scripts: `sh scripts/macos/<script>.sh`
(POSIX sh, so the stock shell works). Python: `python3 -I` (Xcode Command Line Tools or Homebrew).
These commands follow Apple's documented behavior; report anything that differs on your version.

## Contents
1. Before you start: privacy prompts
2. Health
3. Where the space goes, and how to get it back
4. iCloud Drive and other cloud folders
5. Startup (Login Items, LaunchAgents) and RAM
6. Off-limits
7. Verify on your Mac

## 1. Before you start: privacy prompts

macOS guards some folders (Transparency, Consent and Control):
- The first scan of Desktop, Documents and Downloads makes macOS ask "Terminal would like to access
  files in your Documents folder". That is expected; the owner clicks Allow (or Don't Allow, and
  those folders are skipped).
- Mail, Messages, Safari, other apps' containers, iOS backups and the Photos library need **Full
  Disk Access** for the terminal or agent app (System Settings > Privacy & Security > Full Disk
  Access). `scan_disk.py` skips them on purpose so it never triggers a wave of prompts; macOS's own
  System Settings > General > Storage shows their sizes.
- Never ask the owner to turn off System Integrity Protection.

## 2. Health

`health_check.sh` covers disk SMART status, the APFS container, shutdown causes, panics, memory,
thermal throttling, battery and local snapshots.

| Signal | Normal | Worry when | Then |
|---|---|---|---|
| `diskutil info /` SMART Status | Verified | Failing / Not Supported on an internal SSD that used to say Verified | Back up now (Time Machine), then Apple support. `smartctl` (Homebrew `smartmontools`) gives detail with `sudo smartctl -a disk0`. |
| "Previous shutdown cause" | 5 (normal), 3 (power button held) | the same negative number repeating | Negative codes mean the Mac went down for a hardware, power, thermal or kernel reason. Look the code up, and if it repeats run Apple Diagnostics (Apple silicon: hold the power button until Startup Options, then Command-D; Intel: hold D at startup). |
| Kernel panics (`/Library/Logs/DiagnosticReports/*panic*`) | none | any recent | Read the panic file's first lines for the culprit extension or hardware. |
| Memory pressure (`memory_pressure` free %) | > 20% | single digits, swap growing for days | Too much open, or a leaking app: section 5. |
| Thermal `CPU_Speed_Limit` | 100 | below 100 often | Throttling: dust, blocked vents, a runaway process. |
| Battery "Condition" | Normal | Service Recommended | Battery service. |
| Free space | > 15% | < 10% | APFS and Time Machine snapshots need room; updates fail under ~20 GB free. |

## 3. Where the space goes, and how to get it back

Start with System Settings > General > Storage: its per-category view (Applications, Documents,
Photos, Messages, iOS Files, Developer, macOS, System Data) is authoritative and can remove some
items itself. "System Data" includes caches, logs, local snapshots and VM files.

| Item | Class | How |
|---|---|---|
| Trash | SAFE | Finder > Empty Trash after a last look (enable "Remove items from the Trash after 30 days" in Finder settings). |
| `~/Library/Caches/<app>` | SAFE | Per app, with that app quit; apps rebuild them. Through `delete_paths.py` (`delete` rows). Never the whole `Caches` folder. |
| `~/Library/Logs`, old crash reports | SAFE | Old files can go. |
| Time Machine local snapshots | INFO | macOS thins them automatically when space runs low. List: `tmutil listlocalsnapshots /`. If the owner wants them gone now: `sudo tmutil thinlocalsnapshots / 999999999999 4` (asks macOS to free up to that many bytes, urgency 4), or `sudo tmutil deletelocalsnapshots <date>`. |
| Homebrew | SAFE | `brew cleanup -n -s` shows what would go (read-only); `brew cleanup -s` removes old versions and the download cache; `brew autoremove` removes unused dependencies. |
| Xcode | SAFE / REVIEW | `references/dev-junk.md`: DerivedData (SAFE), Archives (REVIEW), DeviceSupport (SAFE), `xcrun simctl delete unavailable` (SAFE). |
| iOS / iPadOS device backups | REVIEW | Finder > (device) > Manage Backups, or Storage > iOS Files. Never delete inside `MobileSync` by hand. |
| Old `.ipsw` update files (`~/Library/iTunes/*Software Updates`) | SAFE | Re-downloaded when needed. |
| `/Applications/Install macOS *.app` | REVIEW | 12+ GB each; drag to Trash unless a bootable installer is wanted. |
| Docker Desktop disk image (`Docker.raw`) | REVIEW | `docker system df`, then `docker system prune` (`-a` / `--volumes` only with a clear yes). Docker Desktop > Settings > Resources sets the image's size limit. `Docker.raw` is sparse: Finder shows its maximum, `scan_disk.py` and `du` show what it really uses. |
| Messages attachments, Mail downloads | REVIEW | Messages > Settings > Keep messages; Storage > Messages > review large attachments. |
| GarageBand / Logic sound libraries | REVIEW | Remove from the app's Sound Library menu. |
| Photos library | KEEP | Use iCloud Photos with "Optimize Mac Storage" to shrink it. Never touch inside the library package. |
| Apps | REVIEW | Storage > Applications (sorted by size, with last-used date), then drag to Trash. App data left behind in `~/Library/Application Support/<app>` and `~/Library/Containers/<bundle id>`: REVIEW; containers are best removed by the uninstaller or Storage. |

"Purgeable" space (shown in Finder and Storage) is space macOS will free by itself (snapshots,
cloud-only copies, caches). Report it separately; don't count it as a cleanup win.

## 4. iCloud Drive and other cloud folders

- **iCloud Drive** lives in `~/Library/Mobile Documents`; with "Desktop & Documents Folders" on,
  `~/Desktop` and `~/Documents` sync too. Deleting there deletes in iCloud and on every device.
- Get local space back by evicting, not deleting: System Settings > Apple Account > iCloud > iCloud
  Drive > **Optimize Mac Storage**, or per item: Finder right-click > **Remove Download**, or
  `brctl evict <path>` (keeps the cloud copy; downloads again on open).
- Dropbox, Google Drive and OneDrive use `~/Library/CloudStorage/<provider>`: use each app's
  "online-only" / "make available online only" option.
- `delete_paths.py` refuses anything inside these folders.

## 5. Startup (Login Items, LaunchAgents) and RAM

`startup_audit.sh` lists Login Items and background items, third-party LaunchAgents/LaunchDaemons
(files and what launchd has loaded), then RAM and the biggest users.

| Kind | Turn off | Undo |
|---|---|---|
| Login item ("Open at Login") | System Settings > General > Login Items & Extensions > remove (−) | add it back (+) |
| Background item ("Allow in the Background") | Same panel > toggle off | toggle on |
| User LaunchAgent (`~/Library/LaunchAgents/x.plist`) | `launchctl bootout gui/$(id -u)/<label>` then `launchctl disable gui/$(id -u)/<label>` | `launchctl enable gui/$(id -u)/<label>` then `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/x.plist` |
| System LaunchAgent/Daemon (`/Library/...`) | Prefer the app's own setting or uninstaller. With admin: `sudo launchctl disable system/<label>` | `sudo launchctl enable system/<label>` |

- `sfltool dumpbtm` (macOS 13+) prints every Login/background item; it may need `sudo`.
- Keep security software, cloud sync, input/audio drivers and what the owner uses daily.
- Many apps re-add themselves; also turn off "Open at login" inside the app.
- **RAM:** Activity Monitor > Memory, "Memory Pressure" graph: green is fine even with little "free"
  memory (macOS uses spare RAM for cache and compression). Yellow/red plus growing swap
  (`sysctl vm.swapusage`) means too much is running. `top -l 1 -o mem` lists the biggest users.

## 6. Off-limits

- Anything under `/System`, `/usr` (except `/usr/local`), `/bin`, `/sbin`, `/private/var/db`, and
  other SIP-protected paths. Apple's own LaunchAgents/Daemons in `/System/Library`.
- `~/Library` folders themselves (`Caches`, `Application Support`, `Preferences`...): only items
  inside them, per app, with a reason.
- Keychains, Mail and Messages stores, the Photos library package, iOS backups: only through the
  owning app.

## 7. Verify on your Mac

This skill has not yet run on a real Mac. These points are the most likely to differ; check them
on the first run and report what you see:
- `sfltool dumpbtm` without `sudo` (the script falls back to System Settings if it is refused).
- "Previous shutdown cause" lines in `log show` on Apple silicon (they may be missing there).
- iCloud "Desktop & Documents Folders" detection (`~/Desktop`, `~/Documents` treated as cloud).
- Moving to the Trash through Finder (`delete_paths.py`, `trash` rows): macOS asks once to let the
  terminal control Finder.
- `browser_scan.py`: Firefox content processes grouped as tabs; RSS totals run high (shared memory).
