# Stop the regrowth

Cleaning is half the job; the same junk comes back within weeks unless its source changes. Check each
item, compare with the last `REPORT.md`, and offer the fix. Nothing here is changed without a yes,
and agent settings are always merged (read, show the diff, write), never overwritten.

## 1. Agent worktrees (usually the biggest regrowth on AI-agent machines)

Each agent task can get its own git worktree, and each worktree its own full `node_modules`.

- **Remove finished ones** each run: SAFE rows in `safe-worktrees.tsv` (`git worktree remove`).
- **Share `node_modules` instead of copying it (Claude Code).** In `~/.claude/settings.json`:
  ```json
  { "worktree": { "symlinkDirectories": ["node_modules"] } }
  ```
  Every agent still gets its own worktree (its own copy of the code and its own branch, so parallel
  agents never touch each other's edits); only the installed-dependencies folder is shared with the
  main checkout by symlink. Trade-off: an agent that adds or upgrades a package inside its worktree
  changes the shared `node_modules` the main checkout uses too. Fine for most work; leave it off for
  agents whose job is dependency changes. `scan_dev_junk.py` reports whether it's set.
- **Windows needs Developer Mode** for this (Settings > System > For developers), so a normal user
  can create symlinks. `scan_dev_junk.py` reports it.
- Other agents (Cursor, Codex, ...) keep worktrees in their own folders; the scanner finds them
  through `git worktree list` from the main repository.

## 2. Disk-space guard for agents (optional, Claude Code)

`scripts/disk_guard_hook.py` is a PreToolUse hook. Below 50 GB free it blocks heavy commands
(renders, encodes, downloads, installs, clones, big copies, image pulls); below 20 GB it allows only
inspection and cleanup. It checks the home drive, and inside WSL also the Windows drive that holds the
distro's disk file. It fails open: if the hook itself errors, the command runs. Overnight agent jobs
are how drives usually fill up, and a full drive can corrupt what lives on it.

Install (merge into `~/.claude/settings.json`; use the real absolute path of the skill):
```json
{
  "hooks": {
    "PreToolUse": [
      { "matcher": "Bash|PowerShell",
        "hooks": [ { "type": "command", "command": "python3 /absolute/path/to/pc-cleanup-performance/scripts/disk_guard_hook.py" } ] }
    ]
  }
}
```
On Windows use `python` and forward slashes (`C:/Users/<you>/...`). Test it before trusting it:
```
echo '{"tool_name":"Bash","tool_input":{"command":"npm install"}}' | PCM_GUARD_FAKE_FREE_GB=40 python3 scripts/disk_guard_hook.py
```
(prints a deny; with `PCM_GUARD_FAKE_FREE_GB=200` it prints nothing). Thresholds:
`PCM_GUARD_WARN_GB`, `PCM_GUARD_CRIT_GB`. The owner pauses it for 2 hours by creating
`~/.claude/disk-guard-off` (don't put that in the deny message, so agents don't pause it themselves).
It only covers agents that load these settings; other tools and scheduled jobs aren't guarded.

## 3. Media pipelines copying recordings

Video jobs that copy the raw recording into each project (and leave the original in Downloads)
multiply disk use. `scan_downloads.py` duplicates since the last run show the rate. The fix belongs
in the pipeline: hard-link or reference sources, and add a post-delivery archive step that keeps
source + final + covers (`references/media-projects.md`).

## 4. Logs and run folders

Tools that write a log per day, or keep every run's output, grow forever. Offer a retention rule in
the tool itself (keep 30 days, or the last N runs). Clearing old logs with `delete_paths.py` is the
one-off; the retention rule is the fix. Linux journal: `SystemMaxUse=500M` in `journald.conf`.

## 5. Indexing and OS extras

- Windows Search indexing over big dev or media trees can write many GB a day: exclude those folders
  (Settings > Privacy & security > Searching Windows).
- Docker only grows: `docker system prune` each run (with a yes), then compact its disk file
  (`references/wsl.md`), or set Docker Desktop's disk limit.
- Chrome's on-device AI model returns after updates unless turned off in `chrome://flags`.
- Snap keeps old revisions: `sudo snap set system refresh.retain=2`.
- Downloads: Windows Storage Sense rule, or Finder's "Remove items from the Trash after 30 days".
- Cloud folders re-download files people open: set the big folders back to online-only.

## 6. Startup creep

Apps re-enable their own auto-start after updates. Compare with the saved baseline each run
(Windows `startup_audit.ps1 -Baseline`; macOS/Linux: compare the lists in the last `REPORT.md`) and
ask about anything new.

## 7. Cadence

Every ~3 months, or when free space drops under ~15%, or right after any disk or file-system error.
Put the next date in the report.
