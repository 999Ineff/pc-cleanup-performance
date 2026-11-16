#!/usr/bin/env python3
"""READ-ONLY dev-junk scan (deletes nothing). Works the same on Windows, macOS, Linux and WSL.

Finds, under --root (default: home; repeatable, e.g. a code drive):
  artifacts  regenerable folders inside projects: node_modules; Python venvs (any folder with
             pyvenv.cfg, plus .tox/.nox); __pycache__ and tool caches; build outputs (.next, .nuxt,
             .svelte-kit, .turbo, .parcel-cache, .angular, .dart_tool, and dist / build / out /
             target / coverage / .gradle / bin / obj only next to a matching project file)
  worktrees  every extra git worktree: uncommitted lines, unpushed commits, merged or on a remote
  caches     package-manager and tool caches (located with the tool's own command when installed),
             each with its official clean command
  agent      Claude Code worktree.symlinkDirectories, disk-guard hook, Windows Developer Mode

Classes:
  SAFE    regenerable, project idle for --idle-days (30), not live, no git-tracked files inside
  LIVE    in a project that a running tool, agent config, startup item or scheduled job uses
  ACTIVE  project changed in the last --idle-days
  REVIEW  owner decides: venv with no lock/requirements file, build folder that is mostly media
          (render frames!), build folder tracked in git, dirty / unpushed / locked / busy worktree,
          worktree holding git-ignored files that aren't regenerable (.env, databases, keys, data)
  CLOUD   inside a cloud-sync folder: never deleted there (code belongs outside sync folders)

Writes (--out): artifacts.tsv, worktrees.tsv, caches.tsv, safe-artifacts.tsv (plan for
delete_paths.py), safe-worktrees.tsv (git worktree remove commands), devjunk.json, summary.txt
Usage: python3 -I scan_dev_junk.py [--root DIR ...] [--idle-days 30] [--protect live-refs.txt]
       [--fast] [--no-tool-queries] [--exact-links] [--out DIR]
"""
import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcm_common as C  # noqa: E402

DEPS = {"node_modules"}
TOOL_CACHES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".hypothesis"}
VENV_NAMES = {".tox", ".nox"}
ALWAYS_BUILD = {".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache", ".angular", ".dart_tool"}
GRADLE_FILES = {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "gradlew"}
MARKED = {
    "dist": {"package.json", "pyproject.toml", "setup.py", "setup.cfg"},
    "build": {"package.json", "pyproject.toml", "setup.py", "CMakeLists.txt", "pubspec.yaml"} | GRADLE_FILES,
    "out": {"package.json"},
    "target": {"Cargo.toml", "pom.xml"},
    "coverage": {"package.json", "pyproject.toml", "setup.py"},
    ".gradle": GRADLE_FILES,
}
DOTNET_EXT = (".csproj", ".fsproj", ".vbproj")
LOCK_FILES = {"requirements.txt", "requirements-dev.txt", "uv.lock", "poetry.lock", "pdm.lock", "Pipfile.lock",
              "environment.yml", "environment.yaml", "pyproject.toml", "setup.py"}
SOURCE_SKIP = DEPS | TOOL_CACHES | VENV_NAMES | ALWAYS_BUILD | set(MARKED) | {".git", "venv", ".venv", "bin", "obj", "worktrees"}
# Git-ignored folders that are safe to lose with a worktree (`git worktree remove` deletes ignored files without asking).
REGEN_IGNORED_DIRS = {"node_modules", ".venv", "venv", "__pycache__", "dist", "build", "out", ".next", ".turbo", ".cache",
                      "target", "coverage"} | TOOL_CACHES | VENV_NAMES | ALWAYS_BUILD


def git(repo, *args, **kw):
    try:
        r = subprocess.run(["git", "--no-optional-locks", "-C", repo] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, timeout=kw.get("timeout", 90))
        return r.returncode, r.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError):
        return -1, ""


def find_artifacts(root, cloud_keys):
    """Walk root. Returns artifacts [(path, kind)] and repos {path: is_linked_worktree}."""
    arts, repos = [], {}
    root_lp = C.lp(root)
    skip_top = {"AppData", "Library", "$Recycle.Bin"} if C.key(root) == C.key(C.HOME) else set()
    runs_k = C.key(C.runs_dir())
    stack = [root_lp]
    while stack:
        d = stack.pop()
        items = C.list_dir(d)
        if items is None:
            continue
        names = set(e.name for e, _, _ in items)
        if "pyvenv.cfg" in names and d != root_lp:
            arts.append((C.sp(d), "venv"))
            continue
        if ".git" in names:
            g = next(e for e, _, _ in items if e.name == ".git")
            repos[C.sp(d)] = not g.is_dir(follow_symlinks=False)  # a .git FILE = linked worktree
        dotnet = any(n.endswith(DOTNET_EXT) for n in names)
        for e, kind, st in items:
            if kind not in ("dir", "cloud_dir"):
                continue
            n = e.name
            if d == root_lp and (n in skip_top or (n.startswith(".") and C.key(root) == C.key(C.HOME))):
                continue  # home-level app data and dot folders: caches are handled separately
            if n == ".git" or C.key(e.path) == runs_k:
                continue
            path = C.sp(e.path)
            if n in DEPS:
                arts.append((path, "deps"))
            elif n in TOOL_CACHES:
                arts.append((path, "pycache"))
            elif n in VENV_NAMES:
                arts.append((path, "venv"))
            elif n in ALWAYS_BUILD:
                arts.append((path, "build"))
            elif n in MARKED and names & MARKED[n]:
                arts.append((path, "build"))
            elif n in ("bin", "obj") and dotnet:
                arts.append((path, "build"))
            else:
                stack.append(e.path)
    return arts, repos


def repo_of(path, repos):
    p = os.path.dirname(path)
    while True:
        if p in repos:
            return p
        parent = os.path.dirname(p)
        if parent == p:
            return ""
        p = parent


def newest_source(root, cache):
    """Newest modification time of a project's own files (dependencies, builds and .git excluded)."""
    if root not in cache:
        nw = 0.0
        stack = [C.lp(root)]
        while stack:
            for e, kind, st in C.list_dir(stack.pop()) or []:
                if kind in ("dir", "cloud_dir"):
                    if e.name not in SOURCE_SKIP:
                        stack.append(e.path)
                elif kind == "file":
                    nw = max(nw, st.st_mtime)
        cache[root] = nw
    return cache[root]


def keep_worthy_ignored(porcelain_z):
    """Paths from `git status --porcelain=v1 -z --ignored` that are ignored but NOT regenerable junk
    (.env files, databases, keys, local data). Removing the worktree would delete them."""
    out = []
    for entry in porcelain_z.split("\0"):
        if not entry.startswith("!! "):
            continue
        path = entry[3:].rstrip("/")
        parts = path.split("/")
        if any(p in REGEN_IGNORED_DIRS for p in parts) or parts[-1].endswith(".pyc") or parts[-1] == ".DS_Store":
            continue
        out.append(path)
    return out


def default_branch(repo):
    rc, out = git(repo, "symbolic-ref", "-q", "--short", "refs/remotes/origin/HEAD")
    if rc == 0 and out.strip():
        return out.strip()
    for b in ("main", "master"):
        if git(repo, "rev-parse", "-q", "--verify", "refs/heads/" + b)[0] == 0:
            return b
    return ""


def scan_worktrees(repos, refs, cloud, exact):
    rows = []
    for repo, linked in sorted(repos.items()):
        if linked:
            continue
        rc, out = git(repo, "worktree", "list", "--porcelain")
        if rc != 0:
            continue
        blocks = [b for b in out.replace("\r", "").strip().split("\n\n") if b.strip()]
        if len(blocks) < 2:
            continue
        base = default_branch(repo)
        rc, stash = git(repo, "stash", "list")
        stashes = len(stash.splitlines()) if rc == 0 else 0
        for blk in blocks[1:]:
            kv = {}
            for line in blk.splitlines():
                k, _, v = line.partition(" ")
                kv[k] = v
            wt = os.path.normpath(kv.get("worktree", ""))
            branch = kv.get("branch", "(detached)").replace("refs/heads/", "")
            row = {"repo": repo, "worktree": wt, "branch": branch, "bytes": 0, "dirty": "-", "ahead": "-",
                   "on_remote": False, "merged": False, "newest": 0.0, "stashes_in_repo": stashes}
            if "prunable" in kv or not os.path.isdir(wt):
                row.update(cls="SAFE", why="folder already gone: `git worktree prune` just tidies the list",
                           cmd='git -C "%s" worktree prune' % repo)
                rows.append(row)
                continue
            s = C.tree_stats(wt, exact_links=exact, frees=not C.IS_WIN or exact)
            row["bytes"], row["newest"] = s.get("frees_bytes", s["bytes"]), s["newest"]
            rc, st = git(wt, "status", "--porcelain")
            dirty = len(st.splitlines()) if rc == 0 else "?"
            rc, ig = git(wt, "status", "--porcelain=v1", "-z", "--ignored")
            kept = keep_worthy_ignored(ig) if rc == 0 else None
            row["ignored_kept"] = kept
            rc, ah = git(wt, "rev-list", "--count", "@{u}..HEAD")
            ahead = ah.strip() if rc == 0 else "no-upstream"
            rc, head = git(wt, "rev-parse", "HEAD")
            head = head.strip()
            on_remote = bool(head) and bool(git(repo, "for-each-ref", "--contains", head, "--count=1", "refs/remotes")[1].strip())
            merged = bool(head and base) and git(repo, "merge-base", "--is-ancestor", head, base)[0] == 0
            row.update(dirty=dirty, ahead=ahead, on_remote=on_remote, merged=merged,
                       cmd='git -C "%s" worktree remove "%s"' % (repo, wt))
            k = C.key(wt)
            if C.in_cloud(wt, cloud):
                row.update(cls="CLOUD", why="inside a cloud-sync folder")
            elif any(C.overlaps(k, r) for r in refs):
                row.update(cls="LIVE", why="a running tool or agent config uses it")
            elif "locked" in kv:
                row.update(cls="REVIEW", why="locked (%s)" % (kv.get("locked") or "a tool marked it in use"))
            elif dirty != 0:
                row.update(cls="REVIEW", why="%s uncommitted/untracked changes" % dirty)
            elif kept is None:
                row.update(cls="REVIEW", why="could not list its git-ignored files")
            elif kept:
                row.update(cls="REVIEW", why="git-ignored files that worktree removal would delete: %s%s" % (
                    ", ".join(kept[:5]), " (+%d more)" % (len(kept) - 5) if len(kept) > 5 else ""))
            elif ahead not in ("0", "no-upstream"):
                row.update(cls="REVIEW", why="%s commits not pushed" % ahead)
            elif not (on_remote or merged or ahead == "0"):
                row.update(cls="REVIEW", why="commits exist only here (not on a remote, not merged into %s)" % (base or "main"))
            elif time.time() - row["newest"] < 86400:
                row.update(cls="REVIEW", why="changed in the last 24 h (an agent may be working in it)")
            elif C.depth_below(k, C.key(C.HOME)) == 1:
                row.update(cls="REVIEW", why="a top-level home folder: confirm it isn't a project the owner opens directly")
            else:
                row.update(cls="SAFE", why="clean and " + ("merged into " + base if merged else "pushed"))
            rows.append(row)
    return rows


def cache_specs():
    H, W, M = C.HOME, C.IS_WIN, C.IS_MAC
    L = os.environ.get("LOCALAPPDATA", os.path.join(H, "AppData", "Local"))
    R = os.environ.get("APPDATA", os.path.join(H, "AppData", "Roaming"))
    xc = C.xdg("XDG_CACHE_HOME", ".cache")
    xd = C.xdg("XDG_DATA_HOME", os.path.join(".local", "share"))
    xcfg = C.xdg("XDG_CONFIG_HOME", ".config")
    lc = os.path.join(H, "Library", "Caches")

    def pick(win, mac, linux):
        return win if W else mac if M else linux
    j = os.path.join
    code_base = pick(j(R, "Code"), j(H, "Library", "Application Support", "Code"), j(xcfg, "Code"))
    specs = [
        # (name, query command or None, default paths, how to clear, class, hard-link heavy)
        ("npm cache", ["npm", "config", "get", "cache"], [pick(j(L, "npm-cache"), j(H, ".npm"), j(H, ".npm"))],
         "npm cache clean --force  (its _npx folder holds tools npx runs, e.g. MCP servers: clear that only when none run)", "SAFE", False),
        ("pnpm store", ["pnpm", "store", "path"], [pick(j(L, "pnpm", "store"), j(H, "Library", "pnpm", "store"), j(xd, "pnpm", "store")), j(H, ".pnpm-store")],
         "pnpm store prune", "SAFE", True),
        ("yarn cache", ["yarn", "cache", "dir"], [pick(j(L, "Yarn", "Cache"), j(lc, "Yarn"), j(xc, "yarn")), j(H, ".yarn", "berry", "cache")],
         "yarn cache clean", "SAFE", False),
        ("bun cache", None, [j(H, ".bun", "install", "cache")], "bun pm cache rm", "SAFE", False),
        ("pip cache", ["pip", "cache", "dir"], [pick(j(L, "pip", "Cache"), j(lc, "pip"), j(xc, "pip"))], "pip cache purge", "SAFE", False),
        ("uv cache", ["uv", "cache", "dir"], [pick(j(L, "uv", "cache"), j(xc, "uv"), j(xc, "uv"))],
         "uv cache clean (or `uv cache prune` for unused entries only). Tools started with uvx run from it: clear it when none run", "SAFE", True),
        ("Poetry cache", None, [pick(j(L, "pypoetry", "Cache"), j(lc, "pypoetry"), j(xc, "pypoetry"))], "poetry cache clear --all PyPI", "SAFE", False),
        ("conda packages", None, [j(H, d, "pkgs") for d in ("miniconda3", "anaconda3", "miniforge3", ".conda")], "conda clean --all", "SAFE", True),
        ("Go build cache", ["go", "env", "GOCACHE"], [pick(j(L, "go-build"), j(lc, "go-build"), j(xc, "go-build"))], "go clean -cache", "SAFE", False),
        ("Go module cache", ["go", "env", "GOMODCACHE"], [j(H, "go", "pkg", "mod")], "go clean -modcache (builds re-download what they need)", "REVIEW", False),
        ("Cargo registry", None, [j(H, ".cargo", "registry"), j(H, ".cargo", "git")],
         "delete ~/.cargo/registry/cache and ~/.cargo/registry/src (re-downloaded), or `cargo cache --autoclean`", "SAFE", False),
        ("Gradle caches", None, [j(os.environ.get("GRADLE_USER_HOME") or j(H, ".gradle"), "caches"), j(os.environ.get("GRADLE_USER_HOME") or j(H, ".gradle"), "wrapper", "dists")],
         "gradle --stop, then delete old versions inside caches/ and wrapper/dists/", "REVIEW", False),
        ("Maven repository", None, [j(H, ".m2", "repository")], "re-downloaded on the next build; delete only if you rarely build Java", "REVIEW", False),
        ("NuGet packages", None, [j(H, ".nuget", "packages")], "dotnet nuget locals all --clear", "SAFE", False),
        ("Composer cache", None, [pick(j(L, "Composer"), j(lc, "composer"), j(xc, "composer"))], "composer clear-cache", "SAFE", False),
        ("Deno cache", None, [pick(j(L, "deno"), j(lc, "deno"), j(xc, "deno"))], "deno clean", "SAFE", False),
        ("ccache", None, [pick(j(L, "ccache"), j(lc, "ccache"), j(xc, "ccache"))], "ccache -C", "SAFE", False),
        ("Bazel cache", None, [j(xc, "bazel")] if not W else [], "bazel clean --expunge in each workspace", "REVIEW", False),
        ("Playwright browsers", None, [pick(j(L, "ms-playwright"), j(lc, "ms-playwright"), j(xc, "ms-playwright"))],
         "keep the newest of each browser; browser-automation tools (including MCP servers) need them", "REVIEW", False),
        ("Puppeteer browsers", None, [j(H, ".cache", "puppeteer")], "re-downloaded on the next install", "SAFE", False),
        ("Electron / node-gyp downloads", None, [pick(j(L, "electron", "Cache"), j(lc, "electron"), j(xc, "electron")),
                                                pick(j(L, "node-gyp", "Cache"), j(lc, "node-gyp"), j(xc, "node-gyp"))],
         "re-downloaded when needed", "SAFE", False),
        ("Hugging Face models", None, [j(os.environ.get("HF_HOME") or j(H, ".cache", "huggingface"), "hub")],
         "your downloaded AI models: `hf cache scan` then `hf cache delete` (older CLI: huggingface-cli) for unused ones", "REVIEW", False),
        ("Ollama models", None, [os.environ.get("OLLAMA_MODELS") or j(H, ".ollama", "models")],
         "your local AI models: `ollama list`, then `ollama rm <model>` for unused ones", "REVIEW", False),
        ("LM Studio models", None, [j(H, ".lmstudio", "models"), j(H, ".cache", "lm-studio", "models")],
         "remove models in LM Studio > My Models", "REVIEW", False),
        ("Docker data", None, [pick(j(L, "Docker", "wsl"), "", j(xd, "docker"))],
         "`docker system df`, then `docker system prune` (-a / --volumes only with a clear yes); then compact the disk file", "REVIEW", False),
        ("VS Code caches", None, [j(code_base, n) for n in ("Cache", "CachedData", "CachedExtensionVSIXs", "Code Cache")],
         "close VS Code first; rebuilt automatically", "SAFE", False),
        ("Android emulators", None, [j(H, ".android", "avd")], "delete unused ones in Android Studio > Device Manager", "REVIEW", False),
        ("Android SDK", None, [pick(j(L, "Android", "Sdk"), j(H, "Library", "Android", "sdk"), j(H, "Android", "Sdk"))],
         "remove old system images and platforms in the SDK Manager", "REVIEW", False),
    ]
    if M:
        dev = j(H, "Library", "Developer")
        specs += [
            ("Xcode DerivedData", None, [j(dev, "Xcode", "DerivedData")], "delete with Xcode closed (rebuilt on the next build)", "SAFE", False),
            ("Xcode Archives", None, [j(dev, "Xcode", "Archives")],
             "needed to symbolicate crashes of shipped builds: delete only old ones (Xcode > Organizer)", "REVIEW", False),
            ("Device support files", None, [j(dev, "Xcode", n) for n in ("iOS DeviceSupport", "watchOS DeviceSupport", "tvOS DeviceSupport")],
             "re-created when a device connects; old OS versions can go", "SAFE", False),
            ("iOS Simulators", None, [j(dev, "CoreSimulator", "Devices")],
             "xcrun simctl delete unavailable (simulators whose runtime is gone)", "SAFE", False),
            ("Simulator caches", None, [j(dev, "CoreSimulator", "Caches")], "delete with Simulator closed", "SAFE", False),
            ("CocoaPods cache", None, [j(lc, "CocoaPods")], "pod cache clean --all", "SAFE", False),
        ]
    return specs


def scan_caches(refs, fast, exact, tool_queries):
    rows = []
    for name, query, defaults, how, cls, heavy in cache_specs():
        paths = []
        if query and tool_queries and C.which(query[0]):
            rc, out = C.run([C.which(query[0])] + query[1:], timeout=30)
            first = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else ""
            if first and os.path.isdir(first):
                paths.append(first)
        paths += [p for p in defaults if p and os.path.isdir(p)]
        seen, uniq = set(), []
        for p in paths:
            if C.key(p) not in seen and not any(C.is_under(C.key(p), s) for s in seen):
                seen.add(C.key(p))
                uniq.append(os.path.abspath(p))
        if not uniq:
            continue
        total, files, partial, nw = 0, 0, False, 0.0
        for p in uniq:
            s = C.tree_stats(p, exact_links=exact or (heavy and not fast))
            total += s["bytes"]
            files += s["files"]
            nw = max(nw, s["newest"])
            partial = partial or s["unreadable"] > 0
        in_use = [r for r in refs for p in uniq if C.is_under(r, C.key(p))]
        rows.append({"name": name, "class": cls, "bytes": total, "files": files, "newest": nw, "paths": uniq, "how": how,
                     "sizing": "hard-link aware" if (exact or (heavy and not fast) or not C.IS_WIN) else "naive",
                     "in_use": len(in_use), "partial": partial})
    return sorted(rows, key=lambda r: -r["bytes"])


def agent_settings():
    out = {}
    sp = os.path.join(C.HOME, ".claude", "settings.json")
    if os.path.isfile(sp):
        try:
            data = C.load_jsonc(sp)
            out["claude_worktree_symlinkDirectories"] = (data.get("worktree") or {}).get("symlinkDirectories") or "not set"
            hooks = (data.get("hooks") or {}).get("PreToolUse") or []
            out["claude_disk_guard_hook"] = "registered" if "disk_guard" in str(hooks) else "not registered"
        except (OSError, ValueError) as e:
            out["claude_settings"] = "unreadable: %s" % e
    else:
        out["claude_settings"] = "no ~/.claude/settings.json (Claude Code not used, or never configured)"
    if C.IS_WIN:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock") as k:
                out["windows_developer_mode"] = winreg.QueryValueEx(k, "AllowDevelopmentWithoutDevLicense")[0] == 1
        except OSError:
            out["windows_developer_mode"] = False
    return out


def main():
    ap = argparse.ArgumentParser(description="Read-only dev-junk scan.")
    ap.add_argument("--root", action="append", default=[])
    ap.add_argument("--idle-days", type=int, default=30)
    ap.add_argument("--protect", action="append", default=[])
    ap.add_argument("--fast", action="store_true", help="skip exact hard-link sizing of big caches")
    ap.add_argument("--exact-links", action="store_true", help="Windows: exact hard-link sizing everywhere (slow)")
    ap.add_argument("--no-tool-queries", action="store_true", help="don't ask npm/pip/uv/go... where their caches are")
    ap.add_argument("--out", default=C.default_out("devjunk"))
    a = ap.parse_args()
    t0 = time.time()
    scan_start = int(t0)
    idle_ts = t0 - a.idle_days * 86400
    refs = C.read_ref_files(a.protect)
    cloud = C.cloud_roots()
    roots = [os.path.abspath(os.path.expanduser(r)) for r in a.root] or [C.HOME]
    if not C.which("git"):
        C.progress("git not found: worktrees are skipped and nothing inside a repo can be SAFE")
    arts, repos = [], {}
    for r in roots:
        x, y = find_artifacts(r, cloud)
        arts += x
        repos.update(y)
    C.progress("scan_dev_junk: %d artifact folders, %d repos/worktrees (%s); sizing..." % (len(arts), len(repos), C.elapsed(t0)))
    live_roots = set(rp for rp in repos if refs and any(C.overlaps(C.key(rp), r) for r in refs))
    newest_cache, git_cache, rows = {}, {}, []
    have_git = bool(C.which("git"))
    for path, kind in arts:
        root = repo_of(path, repos) or os.path.dirname(path)
        nw = newest_source(root, newest_cache)
        frees = not C.IS_WIN or a.exact_links
        s = C.tree_stats(path, exact_links=a.exact_links, frees=frees, media=(kind == "build"))
        if frees:
            est = s["frees_bytes"]
        elif kind == "deps":
            est = int(s["bytes"] * (1 - C.linked_fraction(path)))
        else:
            est = s["bytes"]
        if root in repos and root not in git_cache and have_git:
            rc, out = git(root, "status", "--porcelain")
            rc2, out2 = git(root, "log", "--branches", "--not", "--remotes", "--oneline")
            git_cache[root] = (len(out.splitlines()) if rc == 0 else "?", len(out2.splitlines()) if rc2 == 0 else "?")
        dirty, unpushed = git_cache.get(root, ("-", "-"))
        k = C.key(path)
        why = ""
        if C.in_cloud(path, cloud):
            cls, why = "CLOUD", "inside a cloud-sync folder"
        elif (refs and any(C.overlaps(k, r) for r in refs)) or root in live_roots:
            cls, why = "LIVE", "a running tool or agent config uses this project"
        elif kind == "build" and s["bytes"] and s.get("media_bytes", 0) > 0.5 * s["bytes"]:
            cls, why = "REVIEW", "mostly media files: renders or frames, not ordinary build output"
        elif kind == "venv" and os.path.basename(path) not in VENV_NAMES and not (set(os.listdir(root)) & LOCK_FILES if os.path.isdir(root) else False):
            cls, why = "REVIEW", "venv with no requirements/lock file next to it: may not be reproducible"
        elif nw >= idle_ts:
            cls, why = "ACTIVE", "project changed in the last %d days" % a.idle_days
        else:
            cls = "SAFE"
            repo = repo_of(path, repos)
            if repo:
                if not have_git:
                    cls, why = "REVIEW", "git not installed: can't check for tracked files"
                else:
                    rc, out = git(repo, "ls-files", "-z", "--", os.path.relpath(path, repo))
                    if rc != 0 or out.strip("\0"):
                        cls, why = "REVIEW", "holds files tracked by git"
        rows.append({"class": cls, "why": why, "kind": kind, "est_free": est, "bytes": s["bytes"], "files": s["files"],
                     "project_newest": nw, "dirty": dirty, "unpushed": unpushed, "project": root, "path": path})
    order = {"SAFE": 0, "REVIEW": 1, "ACTIVE": 2, "LIVE": 3, "CLOUD": 4}
    rows.sort(key=lambda r: (order.get(r["class"], 9), -r["est_free"]))
    C.progress("scan_dev_junk: artifacts sized (%s); checking worktrees..." % C.elapsed(t0))
    wrows = scan_worktrees(repos, refs, cloud, a.exact_links) if have_git else []
    wrows.sort(key=lambda r: (order.get(r["cls"], 9), -r["bytes"]))
    # A locked or live worktree is claimed by a tool: nothing inside it is SAFE, however idle it looks.
    claimed = [(C.key(w["worktree"]), w["why"]) for w in wrows if w["cls"] == "LIVE" or w["why"].startswith("locked")]
    for r in rows:
        if r["class"] == "SAFE":
            hit = next((why for wk, why in claimed if C.is_under(C.key(r["path"]), wk)), None)
            if hit:
                r["class"], r["why"] = "REVIEW", "inside a worktree a tool has claimed (%s)" % hit
    rows.sort(key=lambda r: (order.get(r["class"], 9), -r["est_free"]))
    C.progress("scan_dev_junk: %d worktrees (%s); sizing caches..." % (len(wrows), C.elapsed(t0)))
    crows = scan_caches(refs, a.fast, a.exact_links, not a.no_tool_queries)
    agent = agent_settings()

    C.write_tsv(os.path.join(a.out, "artifacts.tsv"),
                ["class", "why", "kind", "est_free_gb", "naive_gb", "files", "project_newest", "dirty", "unpushed", "project", "path"],
                [(r["class"], r["why"], r["kind"], C.gb(r["est_free"]), C.gb(r["bytes"]), r["files"], C.day(r["project_newest"]),
                  r["dirty"], r["unpushed"], r["project"], r["path"]) for r in rows])
    C.write_tsv(os.path.join(a.out, "safe-artifacts.tsv"), ["action", "path", "expect_size", "twin", "sha256", "cutoff_ts", "reason"],
                [("delete", r["path"], -1, "", "", scan_start, "%s in idle project (%s)" % (r["kind"], C.day(r["project_newest"])))
                 for r in rows if r["class"] == "SAFE"])
    C.write_tsv(os.path.join(a.out, "worktrees.tsv"),
                ["class", "why", "gb", "branch", "dirty", "unpushed", "on_remote", "merged", "newest", "repo", "worktree"],
                [(w["cls"], w["why"], C.gb(w["bytes"]), w["branch"], w["dirty"], w["ahead"], w["on_remote"], w["merged"],
                  C.day(w["newest"]), w["repo"], w["worktree"]) for w in wrows])
    C.write_tsv(os.path.join(a.out, "safe-worktrees.tsv"), ["repo", "worktree", "command"],
                [(w["repo"], w["worktree"], w["cmd"]) for w in wrows if w["cls"] == "SAFE"])
    C.write_tsv(os.path.join(a.out, "caches.tsv"), ["gb", "class", "name", "in_use_refs", "sizing", "newest", "how", "paths"],
                [(C.gb(c["bytes"]), c["class"], c["name"], c["in_use"], c["sizing"], C.day(c["newest"]), c["how"], " | ".join(c["paths"]))
                 for c in crows])

    def tot(cls, key="est_free"):
        sel = [r for r in rows if r["class"] == cls]
        return len(sel), sum(r[key] for r in sel)
    lines = ["scan_dev_junk on %s (%s): %d artifact folders in %d projects/repos" % (
        C.os_label(), C.elapsed(t0), len(rows), len(set(r["project"] for r in rows)))]
    for cls in ("SAFE", "REVIEW", "ACTIVE", "LIVE", "CLOUD"):
        n, b = tot(cls)
        if n:
            lines.append("  %-6s %5d folders  %8s%s" % (cls, n, C.human(b), "  <- one approval for the whole list" if cls == "SAFE" else ""))
    safe = [r for r in rows if r["class"] == "SAFE"]
    if safe:
        lines.append("biggest SAFE (est. freed / kind / project last changed):")
        lines += ["  %8s  %-7s %s  %s" % (C.human(r["est_free"]), r["kind"], C.day(r["project_newest"]), C.tilde(r["path"])) for r in safe[:12]]
    review = [r for r in rows if r["class"] == "REVIEW"]
    if review:
        lines.append("REVIEW (owner decides per item):")
        lines += ["  %8s  %s  <- %s" % (C.human(r["est_free"]), C.tilde(r["path"]), r["why"]) for r in review[:12]]
    in_cloud = sorted(set(r["project"] for r in rows if r["class"] == "CLOUD"))
    if in_cloud:
        lines.append("code inside cloud-sync folders (%d projects; code belongs outside sync folders, moving it is the owner's call):" % len(in_cloud))
        lines += ["  " + C.tilde(p) for p in in_cloud[:8]] + (["  ..."] if len(in_cloud) > 8 else [])
    if wrows:
        ws = [w for w in wrows if w["cls"] == "SAFE"]
        wa = [w for w in wrows if w["cls"] != "SAFE"]
        lines.append("worktrees: %d SAFE (%s) | %d other (%s)" % (len(ws), C.human(sum(w["bytes"] for w in ws)),
                                                                  len(wa), C.human(sum(w["bytes"] for w in wa))))
        lines += ["  %-6s %8s  %s  <- %s" % (w["cls"], C.human(w["bytes"]), C.tilde(w["worktree"]), w["why"]) for w in wrows[:15]]
    if crows:
        lines.append("caches (clear with the tool's own command; IN USE = a live tool runs from it):")
        lines += ["  %8s  %-6s %s%s: %s" % (C.human(c["bytes"]), c["class"], c["name"], "  IN USE" if c["in_use"] else "", c["how"]) for c in crows]
    lines.append("agent settings: " + ", ".join("%s=%s" % kv for kv in agent.items()))
    if not refs:
        lines.append("WARNING: no --protect file, so live tools were NOT excluded. Run live_refs.py first.")
    C.finish(a.out, "devjunk", {"roots": roots, "idle_days": a.idle_days, "artifacts": rows, "worktrees": wrows,
                                "caches": crows, "agent_settings": agent, "live_refs": len(refs)}, lines)


if __name__ == "__main__":
    main()
