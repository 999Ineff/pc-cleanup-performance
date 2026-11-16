# Safety: the rules and why each one exists

Read this before the first delete of a session. Every rule below comes from something that went
wrong, or nearly did, on a real machine.

## Contents
1. The approval model
2. Rules, with the reason for each
3. What `delete_paths.py` checks
4. Trash vs permanent delete
5. If you must use PowerShell (or any shell) to delete
6. Admin and sudo
7. Things this skill never does

## 1. The approval model

```
scan (read-only) -> show specifics -> owner says yes to an exact list -> dry run -> apply -> log -> report
```

- A **category** the owner agreed to ("delete old installers", "node_modules in idle projects") is a
  standing policy, not an approval. Each run still shows the exact list (count, total size, biggest
  items) and gets a yes on that list.
- SAFE items can be approved as one list. REVIEW items are approved one by one.
- "Yes" to one list doesn't extend to the next one, or to the next session.

## 2. Rules, with the reason for each

| Rule | Why |
|---|---|
| Read-only scans first | Most of the value is in the map. Nothing is lost by looking first. |
| Exact paths, re-validated at delete time | Between the scan and the delete, things change: a download finishes, a tool starts, a project gets touched. Globs and variables can expand to something else entirely. |
| Dry run of the same plan before apply | The dry run is where refusals show up. `delete_paths.py` records a fingerprint of the plan and refuses `--apply` on a plan that wasn't dry-run (or was edited since). |
| Never delete inside a cloud-sync folder | A delete there syncs: the cloud copy goes too, on every device. To get local space back, use the provider's "free up space" / "remove download" / "Optimize Storage" instead. |
| Never a top-level home folder, home itself, or a system folder | A single wrong path at that level is a disaster. Real cleanup targets are always deeper. |
| Never anything a live tool uses | Coding agents' "safe to delete" lists have included the `node_modules` of running MCP servers and the build output another tool served from. A tool that breaks at 2 a.m. costs more than the space was worth. `live_refs.py` reads agent configs, processes, scheduled tasks, services, startup items and PATH, and a reference protects the whole project around it. |
| Never dirty or unpushed git work | Uncommitted lines and local-only commits exist nowhere else. `git worktree remove` refuses dirty trees; `delete_paths.py` refuses repos, worktrees, nested `.git` folders and git-tracked files. |
| Ask before closing apps, stopping WSL/VMs, admin/sudo | Stopping WSL closes every Linux session; closing an app can lose unsaved work. These are the owner's calls. |
| One batched admin prompt | Approving ten prompts trains people to click Yes without reading. Batch the approved steps, explain them, ask once. |
| Undo for every startup/system change | Apps re-enable themselves after updates and people forget what was turned off. A written undo makes "why doesn't X start any more?" a one-minute fix. |
| Hard-link-aware sizing | uv, pnpm, some backup tools and media pipelines hard-link files. A naive sum overstated one machine's video folder by a third and a package cache by nearly three times. Promise only what will really be freed. |
| Prefer Python for deleting | See section 5. |
| Health before cleanup | A failing SSD or a corrupt file system needs a backup, not a cleanup. Deleting and compacting on damaged storage can make it worse. |

## 3. What `delete_paths.py` checks (every row, at run time)

- The path is absolute, inside home (or an `--extra-root` the owner named), at least 2 levels
  below home.
- Not a protected folder (home, Desktop, Documents, Downloads, Pictures, Music, Videos/Movies,
  AppData and its Local/Roaming/LocalLow/Temp, `~/Library/*`, `.config`, `.local/*`, `.cache`, the
  temp folder, cloud roots, the runs folder, this skill).
- Not inside, and not holding, a sensitive folder: `.ssh`, `.gnupg`, password stores, cloud CLI credentials,
  keychains, mail, messages, iOS backups, app containers, Photos library, Windows credential and
  certificate stores, coding-agent folders (`.claude`, `.codex`, `.cursor`, `.gemini`...), and
  browser profile folders (Chromium "User Data", Firefox profiles, `~/Library/Safari`): saved
  passwords, cookies and history live there; caches are cleared from inside the browser.
- Not in a cloud-sync folder (by folder list, `PCM_CLOUD_DIRS`, and on Windows by the cloud reparse
  tag on any parent folder).
- Inside WSL: not on a Windows drive (`/mnt/c`...). From Linux the Windows cloud folders, live
  tools and protected folders are invisible, so Windows files are cleaned from Windows.
- The real path, with symlinks and junctions resolved, passes the same checks.
- No overlap with the live list: the target isn't inside a live folder and doesn't contain one.
  The live list must be under 12 hours old for `--apply`.
- It exists; it isn't a link, junction or mount point; it isn't a git repo or worktree, holds no
  nested `.git`, no git-tracked files, and isn't inside a locked worktree.
- No cloud-only placeholders inside it.
- Optional checks from the plan still hold: file size, the verified copy (`twin`) still exists with
  the same size and is not itself in the plan, SHA-256 of both still match, nothing changed after
  the scan cutoff.
- Rows inside another row, and duplicate rows, are dropped.
- With `--apply`, a folder is renamed in place first. On Windows a rename fails if anything inside
  is open, so a locked folder is skipped whole instead of half-deleted.

## 4. Trash vs permanent delete

| Goes to the Trash / Recycle Bin (`trash`) | Deleted permanently (`delete`) |
|---|---|
| Documents, photos, videos, archives, folders of personal files, anything someone made | `node_modules`, venvs, build output, caches, old installers and disk images, partial downloads |

Trashed items free space only when the Trash is emptied. Say so in the report, and let the owner
empty it after a last look. Windows asks before deleting an item too big for the Recycle Bin
(`delete_paths.py` sets that flag) instead of silently deleting it for good.

## 5. If you must use PowerShell (or any shell) to delete

`delete_paths.py` exists so you don't have to. If a one-off really needs a shell:

- **PowerShell variable names are case-insensitive.** `$t` and `$T` are the same variable. A test
  helper once reused `$t` in a session where a script had set `$T = $env:TEMP`, and wiped the temp
  folder. Use unique, prefixed names (`$zzTarget`), and never dot-source a script into a session
  that already holds variables.
- **Aliases outrank functions.** A helper named `Del` or `Group` never runs: the built-in alias
  does. Check `Get-Command <name>` returns nothing before naming a function.
- Put a protected-roots guard inside any delete helper, normalize paths with
  `[IO.Path]::GetFullPath`, refuse empty paths.
- Test in a separate `powershell -File` process on throwaway folders.
- bash: quote every variable, `set -u`, never `rm -rf "$DIR/"*` where `$DIR` could be empty.
- Never feed a script to `wsl.exe ... bash` through stdin: a command that reads stdin (npm, ssh)
  eats the rest of the script. See `references/wsl.md`.

## 6. Admin and sudo

- Collect every approved admin step first. Explain each in one line. Ask once.
- Windows: `scripts/windows/admin_maintenance.ps1` dry run (no admin), then one elevated run with
  only the approved switches. Wait for `admin.done`, then read `admin-log.txt`.
- macOS / Linux: give the owner one `sudo` block to run (or run it once if they say so). Never
  store or ask for a password in chat.
- A cancelled UAC or sudo prompt means nothing ran. Say so; offer to retry.

## 7. Things this skill never does

- Delete without a yes on the exact list, or apply without a dry run.
- Touch `C:\Windows`, `Program Files`, `ProgramData`, `/System`, `/usr`, `/bin`, `/etc`, SIP-protected
  paths, `C:\Windows\Installer`, `WinSxS` (only `DISM ... /StartComponentCleanup`, with admin).
- Delete restore points or snapshots by hand (only the OS's own commands, with a yes).
- Use `wsl --manage --set-sparse` (corruption reports), or compact a disk that failed fsck.
- Edit repositories (commit, stash, reset, clean) or move code: that's the owner's call.
- Disable antivirus, firewall, cloud sync, or the OS's own services.
- Change agent settings without showing the diff and getting a yes; it merges, never overwrites.
