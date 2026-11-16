# Browser RAM audit

The browser is usually the biggest RAM user on a personal computer, and most of that is tabs,
extensions and the GPU process. This audit is part of the startup/RAM phase.

## Contents
1. Ask first
2. Measure (before)
3. Settings per browser
4. Extensions
5. Optional disk items
6. Measure again (after) and report

## 1. Ask first

**Ask the owner which browser(s) they use every day** (Chrome, Edge, Brave, Firefox, Safari, Arc,
Opera, Vivaldi, other) and tailor everything to that answer. Never assume Chrome. A browser they
only keep installed doesn't need tuning; one they live in does. Then:

```
python3 -I scripts/browser_scan.py --browser chrome,edge      # keys: chrome edge brave chromium vivaldi opera firefox
```

It reads only settings and extension manifests (never cookies, history, passwords or account
details) and prints, per browser: the browser-wide memory and background settings, each profile's
extensions with their flags, the optional disk items, and the RAM of the running browser processes
grouped by kind. Safari and Arc have no readable settings file: use the steps below by hand.

## 2. Measure (before)

Measure the same way before and after, or the comparison means nothing: same browser, one window,
the tabs the owner normally keeps open, about two minutes after opening.

- `browser_scan.py` groups the running processes into **tabs (renderer)**, **extensions**, **gpu**,
  **utility** and **main**. Windows reports private working set (memory only that process uses),
  Linux PSS (shared memory split fairly), macOS RSS (counts shared memory in every process, so
  totals run high; still fine for before/after).
- Inside the browser:

| Browser | See memory per tab and extension |
|---|---|
| Chrome, Edge, Brave, Vivaldi, Opera (Chromium) | **Shift+Esc** opens the browser's Task Manager: every tab, extension, GPU and utility process with its memory |
| Firefox | `about:processes` (Shift+Esc on Windows/Linux) per tab and extension; `about:memory` for detail and "Minimize memory usage"; `about:unloads` shows which tabs it would unload first |
| Safari | Activity Monitor > Memory: "Safari Web Content" processes (one per tab or site); Safari has no per-tab view of its own |

## 3. Settings per browser

Two settings matter most: **tab sleeping** (idle tabs give their memory back) and **keep running in
the background after closing** (off, so closing the browser really frees its RAM).

| Browser | Tab sleeping | Keep running after close: turn OFF |
|---|---|---|
| Chrome | `chrome://settings/performance` > **Memory Saver**: Moderate / Balanced / Maximum | `chrome://settings/system` > "Continue running background apps when Google Chrome is closed" |
| Edge | `edge://settings/system` > **Sleeping tabs** (and the sleep timeout) + **Efficiency mode** | `edge://settings/system` > "Continue running background extensions and apps when Microsoft Edge is closed", and turn **Startup boost** OFF (it keeps Edge loaded at sign-in) |
| Brave | Memory Saver, the same setting as Chrome (under Settings > System or Performance, depending on version) | Settings > System > "Continue running background apps when Brave is closed" |
| Firefox | Automatic: Firefox unloads tabs when memory runs low (`browser.tabs.unloadOnLowMemory` in `about:config`, on by default). Settings > General > Performance: keep "recommended performance settings" unless a content-process limit is needed | n/a: Firefox closes fully |
| Safari | No toggle: macOS manages Safari's tab memory itself | n/a |
| Opera / Opera GX | Tab snoozing in Settings (search "snooze"); Opera GX also has a RAM limiter in GX Control | Settings: search "background" |
| Vivaldi | Tab hibernation: right-click a tab or stack > Hibernate, and the background-tab hibernation option in Settings > Tabs | Settings: search "background" |
| Arc | Auto-archives idle tabs (Settings: archive after 12 h / 24 h / 7 days / 30 days) | Settings: search "background" |

Where a menu path differs on the owner's version, search the browser's Settings for "memory",
"sleep", "snooze", "hibernate" or "background". Don't guess `about:config` / `chrome://flags`
values beyond the ones named here.

- **Memory Saver and background mode are browser-wide** (Chromium keeps them in the user-data
  folder's `Local State`), so setting them once covers every profile. `browser_scan.py` reports
  them: Memory Saver `on, Maximum` / `not set (browser default)`, background `False` / `True`.
- After turning background mode off and closing all windows, `browser_scan.py` should show no
  processes left for that browser.
- **Hardware acceleration** creates the GPU process (often 0.5-1 GB). That is normal and makes
  pages fast; don't turn it off just for RAM. Consider it only if the GPU process alone keeps
  growing past a few GB, and test pages afterwards.
- "On startup: continue where you left off" reopens every old tab at launch. Fine with tab sleeping
  on; without it, it's a common reason a browser starts at 2-3 GB.

## 4. Extensions

Each extension that injects a script into web pages runs **in every tab**, so its cost multiplies
by the number of tabs, on top of its own process. `browser_scan.py` marks those "injects into
every page", plus "background worker" (always running) and access flags.

1. **Disable what isn't used daily.** A disabled extension costs nothing and keeps its settings;
   remove it if it's not needed at all.
2. **Set the rest to "On click"** (Chromium: right-click the extension's icon > "This can read and
   change site data" > "When you click the extension", or the extensions page > Details > Site
   access; Firefox: about:addons > the extension > Permissions). It then only runs where the owner
   asks. The scanner tags these "site access: on click".
3. **Extensions are per profile; Memory Saver and background settings are browser-wide.** Check
   each profile the owner actually opens. Profiles they never open cost no RAM (only some disk).
4. **Security, not just RAM.** Flag for review, in plain words:
   - "can read cookies of all sites" or "can read and change data on all sites": such an extension
     can see logins and anything on any page. Keep only ones the owner knows and trusts.
   - "can read browsing history".
   - Antivirus or "web protection" add-ons whose desktop app was already uninstalled (the scanner
     says "no ... desktop app is installed"): left behind, still injecting into every page.
   - Unpacked extensions (loaded from a folder): fine for developers, odd for anyone else.
5. Leftover extension folders the scanner reports as "not registered, never load" cost disk only;
   the browser cleans them up on its next starts.

All changes are made by the owner in the browser (or by the agent clicking through it with their
yes). Never edit `Preferences`, `Secure Preferences` or `Local State` by hand: Chromium protects
them and may reset the profile.

## 5. Optional disk items

| Item | Size | How |
|---|---|---|
| Chrome's on-device AI model (`OptGuideOnDeviceModel` in the user-data folder) | about 4 GB | REVIEW. It powers on-device AI features and comes back after updates. To turn it off: `chrome://flags/#optimization-guide-on-device-model` ("Enables optimization guide on device") > Disabled, restart. If the folder is still there after that, the owner deletes it in Explorer / Finder with Chrome closed: `delete_paths.py` never deletes inside browser profile folders. |
| Browser caches (`Cache`, `Code Cache`, `GPUCache`, service-worker caches per profile) | often several GB | SAFE, but only from inside the browser: Settings > Privacy > Delete browsing data > "Cached images and files". Never delete under a running browser. |

## 6. Measure again (after) and report

Re-measure exactly as in section 2 (restart the browser, same tabs, wait two minutes), then add to
`REPORT.md`:

| Browser | Before | After |
|---|---|---|
| Extensions running in every page | <n> | <n> |
| Memory Saver / tab sleeping | <off / level> | <on, level> |
| Keeps running after close | <yes> | <no: 0 processes after closing> |
| RAM with one window (tabs / extensions / GPU / total) | <x / x / x / x GB> | <...> |

Example from one machine: six extensions loading into every page went down to one (extension
processes 79 → 24 MB), Memory Saver was turned on (Maximum), and background mode off, so closing
the browser now leaves 0 processes running. Before the changes, one window used 1.7 GB: 0.8 GB of tabs and 0.6 GB of GPU process.
