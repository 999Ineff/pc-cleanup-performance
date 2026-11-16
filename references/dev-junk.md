# Dev junk (every OS)

`scan_dev_junk.py --protect <live-refs.txt>` finds three kinds of regenerable data. On a machine
used daily with AI coding agents it is often the biggest SAFE win: one run cleared about 50 GB of
`node_modules` in idle projects, leftover agent worktrees and build output.

## Contents
1. Project artifacts
2. Git worktrees
3. Tool caches
4. Sizing: hard links
5. What stays REVIEW

## 1. Project artifacts

A folder is an artifact only when it is regenerable and sits where the tool puts it:

| Folder | Counted when | Recreate with |
|---|---|---|
| `node_modules` | always | `npm ci` / `pnpm install` / `yarn` / `bun install` |
| any folder with `pyvenv.cfg` (`.venv`, `venv`...), `.tox`, `.nox` | always | `uv sync`, `pip install -r requirements.txt`, `poetry install`... |
| `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `.hypothesis` | always | automatic |
| `.next`, `.nuxt`, `.svelte-kit`, `.turbo`, `.parcel-cache`, `.angular`, `.dart_tool` | always | the build |
| `dist`, `build`, `out`, `coverage`, `target`, `.gradle`, `bin`/`obj` | only next to a matching project file (`package.json`, `pyproject.toml`, `Cargo.toml`, `pom.xml`, Gradle files, `*.csproj`...) | the build |

Classification:
- **SAFE**: the project's own files (dependencies and builds excluded) haven't changed for
  `--idle-days` (30), no live tool uses the project, nothing inside is tracked by git, and it isn't
  inside a locked or live worktree. Approved as one list; `safe-artifacts.tsv` is the plan for
  `delete_paths.py` (permanent delete: it's regenerable).
- **REVIEW**: a venv with no requirements or lock file next to it (it may not be reproducible); a
  build folder that is mostly images or video (render frames and exports, not ordinary build
  output); anything tracked by git; anything inside a claimed worktree.
- **ACTIVE / LIVE / CLOUD**: kept. CLOUD lists code inside a sync folder, which belongs outside it.

## 2. Git worktrees

Coding agents create a worktree per task (`<repo>/.claude/worktrees/...` and similar), each with its
own full `node_modules`. Thirty of them can hold tens of GB. Each extra worktree gets:

| Class | When |
|---|---|
| SAFE | no uncommitted or untracked changes, no git-ignored files except regenerable ones (see below), and its commits are pushed (on any remote branch, or 0 ahead of upstream) or merged into the default branch, untouched for 24 h |
| SAFE (prune) | its folder is already gone: `git worktree prune` only tidies git's list |
| REVIEW | uncommitted/untracked changes, ignored files worth keeping, unpushed commits, commits nowhere else, locked (a tool claimed it), changed in the last 24 h, or sitting directly in the home folder (it may be a project the owner opens) |

**Ignored files.** `git worktree remove` deletes git-ignored files without asking, and `git status`
doesn't show them. The scanner runs `git status --porcelain --ignored`: ignored `node_modules`,
`.venv`/`venv`, `__pycache__`, `dist`, `build`, `out`, `.next`, `.turbo`, `.cache`, `target`,
`coverage`, `*.pyc`, `.DS_Store`, and the tool caches and build folders from section 1 are fine.
Anything else ignored (`.env*`, `*.db`/`*.sqlite`,
`*.pem`/`*.key`, local data) makes the worktree REVIEW, and the reason lists those files.
| LIVE / CLOUD | used by a live tool / inside a sync folder |

Remove SAFE ones with the commands in `safe-worktrees.tsv`: `git -C <repo> worktree remove <path>`,
never `--force`, never a plain folder delete. Git refuses a dirty worktree, which is a second check.
Stashes are per repository; the scanner shows the count. Dirty or unpushed work is handed to the
owner: this skill never commits, stashes or resets.

## 3. Tool caches

Use each tool's own command. The scanner asks the tool where its cache is when it's installed,
otherwise checks the default places below.

| Cache | Windows | macOS | Linux | Clear | Class |
|---|---|---|---|---|---|
| npm | `%LOCALAPPDATA%\npm-cache` | `~/.npm` | `~/.npm` | `npm cache clean --force` | SAFE |
| npx tools (`_npx` inside the npm cache) | | | | delete the folder when no agent runs | SAFE, often IN USE |
| pnpm store | `%LOCALAPPDATA%\pnpm\store` | `~/Library/pnpm/store` | `~/.local/share/pnpm/store` | `pnpm store prune` | SAFE |
| yarn | `%LOCALAPPDATA%\Yarn\Cache` | `~/Library/Caches/Yarn` | `~/.cache/yarn` | `yarn cache clean` | SAFE |
| bun | `~/.bun/install/cache` | same | same | `bun pm cache rm` | SAFE |
| pip | `%LOCALAPPDATA%\pip\Cache` | `~/Library/Caches/pip` | `~/.cache/pip` | `pip cache purge` | SAFE |
| uv | `%LOCALAPPDATA%\uv\cache` | `~/.cache/uv` | `~/.cache/uv` | `uv cache clean` (or `uv cache prune`) | SAFE, often IN USE |
| Poetry | `%LOCALAPPDATA%\pypoetry\Cache` | `~/Library/Caches/pypoetry` | `~/.cache/pypoetry` | `poetry cache clear --all PyPI` | SAFE |
| conda | `~/miniconda3/pkgs` etc. | same | same | `conda clean --all` | SAFE |
| Go build | `%LOCALAPPDATA%\go-build` | `~/Library/Caches/go-build` | `~/.cache/go-build` | `go clean -cache` | SAFE |
| Go modules | `~/go/pkg/mod` | same | same | `go clean -modcache` (read-only files: use the command) | REVIEW |
| Cargo | `~/.cargo/registry`, `~/.cargo/git` | same | same | delete `registry/cache` and `registry/src`, or `cargo cache --autoclean` | SAFE |
| Gradle | `~/.gradle/caches`, `wrapper/dists` | same | same | `gradle --stop`, then old versions | REVIEW |
| Maven | `~/.m2/repository` | same | same | re-downloaded on the next build | REVIEW |
| NuGet | `~/.nuget/packages` | same | same | `dotnet nuget locals all --clear` | SAFE |
| Playwright browsers | `%LOCALAPPDATA%\ms-playwright` | `~/Library/Caches/ms-playwright` | `~/.cache/ms-playwright` | keep the newest of each browser | REVIEW |
| Hugging Face models | `~/.cache/huggingface/hub` (`HF_HOME`) | same | same | `hf cache scan`, `hf cache delete` | REVIEW |
| Ollama models | `~/.ollama/models` (`OLLAMA_MODELS`) | same | same | `ollama list`, `ollama rm <model>` | REVIEW |
| Docker | `%LOCALAPPDATA%\Docker\wsl` | Docker Desktop VM | `/var/lib/docker`, `~/.local/share/docker` | `docker system df`, `docker system prune` | REVIEW |
| VS Code caches | `%APPDATA%\Code\Cache*` | `~/Library/Application Support/Code/Cache*` | `~/.config/Code/Cache*` | close VS Code first | SAFE |
| Xcode DerivedData | | `~/Library/Developer/Xcode/DerivedData` | | delete with Xcode closed | SAFE |
| Xcode Archives | | `~/Library/Developer/Xcode/Archives` | | old ones only (needed to read crash reports of shipped builds) | REVIEW |
| Device support | | `~/Library/Developer/Xcode/*OS DeviceSupport` | | old OS versions | SAFE |
| Simulators | | `~/Library/Developer/CoreSimulator` | | `xcrun simctl delete unavailable` | SAFE |
| Android emulators / SDK | `~/.android/avd`, SDK folder | same | same | Android Studio's Device / SDK Manager | REVIEW |

**IN USE** = a live tool runs from that cache (agents start MCP servers with `npx` and `uvx`, which
run straight out of these caches). Clearing it under a running tool breaks the tool; clear it when
no agent is running. On Windows the files are locked anyway: use the next-sign-in pattern in
`references/windows.md`. The tools re-download what they need on their next start.

## 4. Sizing: hard links

uv's cache and pnpm's store hard-link the same file into many places, and so do some media
pipelines. A naive sum counts it every time. One uv cache summed to about 31 GB by file but freed
about 8 GB when cleared; one video folder summed to 176 GB but held 118 GB.

- macOS/Linux: every scanner counts each inode once.
- Windows: the link count costs a per-file call, so scanners check it for files over 1 MB and
  inside known hard-link stores; `--exact-links` checks every file (slower).
- "est_free" / "frees" columns = what deleting really frees (inodes whose every link is inside).
- When reporting, promise the hard-link-aware number, and measure free space before and after.

## 5. What stays REVIEW, always

Local AI models (Hugging Face, Ollama, LM Studio, Whisper...), Docker images and volumes, virtual
machines and emulators, venvs without a lock file, anything with uncommitted or unpushed work, and
anything the scanner didn't recognize. These are the owner's, item by item.
