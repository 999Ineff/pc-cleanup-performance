# Report template

Write `REPORT.md` into the run folder (`~/pc-cleanup-performance-runs/<YYYY-MM-DD>/REPORT.md`), next to the
scan outputs. Real, measured numbers; tables; no filler. If an older `REPORT.md` exists, add a
"vs last run" for every number that has one. Then give the owner a short plain-language summary.

```markdown
# PC cleanup & performance report <YYYY-MM-DD>

## Summary
<OS and version> · free space **<before> → <after> GB** (+<delta>) on <drive> · RAM available <x> of <total> GB
<one sentence: the biggest finding or risk>

## Health
| Check | Result | vs last run |
|---|---|---|
| Disk health / SMART | <healthy / warning> | |
| Storage controller resets / I/O errors | <n> (<per month>) | |
| File-system errors | <n> | |
| Unexpected shutdowns / panics / blue screens | <n> | |
| WSL file system (if any) | <dmesg 0 / fsck clean> | |
| Free space | <x>% | |

## Where the space came from
| Item | GB | Class | Outcome |
|---|---|---|---|
| <e.g. node_modules in 40 idle projects> | <x> | SAFE | done (+<GB> measured) |
| <e.g. cloud folder set to online-only> | <x> | CLOUD | done (nothing deleted) |
| <e.g. old video project, work folders> | <x> | REVIEW | approved / declined / kept |

## Decisions the owner made
- <question> → <answer>  (a standing policy for next time? note it here)

## Startup and RAM
| Item | Was | Now | Undo |
|---|---|---|---|
| <name and kind> | on | off (<how>) | <exact undo command or click path> |

RAM in use at idle after sign-in: <before> → <after> GB (measured the same way both times)

## Browser (<the browser the owner uses daily>)
| | Before | After |
|---|---|---|
| Extensions running in every page | <n> | <n> |
| Tab sleeping / Memory Saver | <off> | <on, level> |
| Keeps running after close | <yes> | <no: 0 processes> |
| RAM, one window: tabs / extensions / GPU / total | <...> | <...> |
Extensions flagged for security (all-site data, cookies, history, orphaned antivirus): <list or none>

## Actions and logs
- <step>: +<GB>, log `<path>`; failures: <none / list>

## Regrowth
| Source | This run | Last run | Fix offered / applied |
|---|---|---|---|
| Agent worktrees | <n, GB> | | |
| Duplicated recordings / downloads | | | |
| Logs / run folders | | | |
| Docker / VM disks | | | |
| Startup items that came back | | | |

## Pending
- <waiting on a reboot, the admin prompt, the owner's decisions, emptying the Trash>
- Next run: <date about 3 months out>
```
