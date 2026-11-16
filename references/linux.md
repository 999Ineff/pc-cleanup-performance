# Linux

Any systemd-based distribution (Ubuntu, Debian, Fedora, Arch, openSUSE...), and WSL distros (read
`references/wsl.md` too). Scripts: `sh scripts/linux/<script>.sh` (POSIX sh). Python: `python3 -I`.
The scripts never call `sudo`; anything that needs root is shown as a command for the owner, batched
into one block.

## Contents
1. Health
2. Where the space goes, and how to get it back
3. Startup and RAM

## 1. Health

| Signal | Normal | Worry when | Then |
|---|---|---|---|
| `smartctl -H` / `-a` (smartmontools) | PASSED; NVMe "Percentage Used" low, "Media and Data Integrity Errors" 0 | FAILED, reallocated/pending sectors, media errors, rising CRC errors | Back up now; replace the drive (CRC errors alone: check the cable). |
| ext4 `errors_count` (`/sys/fs/ext4/<dev>/errors_count`) | 0 | > 0 | The file system recorded errors: back up, then `fsck` from a live USB or at boot (`sudo touch /forcefsck` on older systems, or `fsck.mode=force` on the kernel line). |
| Kernel I/O / file-system / NVMe timeout lines (`dmesg`, `journalctl -k`) | 0 | any | Back up; check SMART; reseat or replace the drive. |
| Journal errors this boot (`journalctl -b -p err`) | a few, known | new, repeating | Read the top repeating lines first; most are harmless driver chatter. |
| OOM kills (`journalctl -k \| grep -i "out of memory"`) | 0 | any | Something used all RAM: section 3. |
| `last -x` shows `crash` | none | repeated | The machine went down without a clean shutdown: power, overheating, hard freeze. |
| `systemctl --failed` | empty | anything | Read `systemctl status <unit>`; often a leftover service of an uninstalled app. |
| Free space | > 15% | < 10% | Clean up now; btrfs and package updates need headroom. |

## 2. Where the space goes, and how to get it back

`scan_disk.py` sizes these (system folders partly need root to read; it marks those "partial").

| Item | Class | How (root commands go in the one batched block) |
|---|---|---|
| Trash (`~/.local/share/Trash`) | SAFE | Empty from the file manager after a last look, or `gio trash --empty`. |
| `~/.cache/<app>` | SAFE | Per app, with that app closed (`delete_paths.py`, `delete` rows). Never the whole `~/.cache`. |
| Thumbnails (`~/.cache/thumbnails`) | SAFE | Rebuilt on demand. |
| apt | SAFE | `sudo apt-get clean` (downloaded .debs); `apt-get -s autoremove` shows unused packages and old kernels (read-only), then `sudo apt-get autoremove --purge`. |
| dnf / yum | SAFE | `sudo dnf clean all`; `sudo dnf autoremove`. |
| pacman | SAFE | `sudo paccache -rk2` (pacman-contrib; keeps 2 versions) or `sudo pacman -Sc`; orphans: `pacman -Qdtq`. |
| zypper | SAFE | `sudo zypper clean --all`. |
| systemd journal | SAFE | `journalctl --disk-usage`; `sudo journalctl --vacuum-size=200M` (or `--vacuum-time=4weeks`). Cap it for good with `SystemMaxUse=500M` in `/etc/systemd/journald.conf`. |
| Snap old revisions | SAFE | `snap list --all` (disabled rows; `scan_disk.py` lists them with sizes) then `sudo snap remove <name> --revision=<rev>` per row. Keep fewer from now on: `sudo snap set system refresh.retain=2`. |
| Flatpak unused runtimes | SAFE | `flatpak uninstall --unused` (and `--user`). |
| Core dumps | SAFE | `coredumpctl list` to look first; then `sudo rm` the old files in `/var/lib/systemd/coredump` and `/var/crash`. |
| Docker | REVIEW | `docker system df`, then `docker system prune` (`-a` / `--volumes` only with a clear yes). Rootless data: `~/.local/share/docker`. |
| Dev junk, Downloads, media projects | | `scan_dev_junk.py`, `scan_downloads.py`, `scan_media_projects.py`. |
| Old kernels | SAFE | Through the package manager only (apt autoremove, dnf's `installonly_limit`). Never delete from `/boot` by hand. |
| `/tmp`, `/var/tmp` | INFO | Cleared at boot / by systemd-tmpfiles; leave them. |

## 3. Startup and RAM

`startup_audit.sh` lists enabled system and user services, desktop autostart entries, `@reboot`
cron jobs, boot time with the slowest units, and RAM.

| Kind | Turn off | Undo |
|---|---|---|
| System service | `sudo systemctl disable --now <unit>` | `sudo systemctl enable --now <unit>` |
| User service | `systemctl --user disable --now <unit>` | `systemctl --user enable --now <unit>` |
| Desktop autostart (`~/.config/autostart/x.desktop`) | Add `Hidden=true` to it, or the desktop's "Startup Applications" tool | Remove the line / toggle back |
| System autostart (`/etc/xdg/autostart/x.desktop`) | Copy it to `~/.config/autostart/` and add `Hidden=true` there (user override; no root) | Delete the user copy |
| `@reboot` cron line | `crontab -e`, comment it out | Uncomment |

- Never disable networking, display manager, audio, login, update, firewall or security services.
- **RAM:** `free -h`: "available" is what matters (Linux uses spare RAM for cache). Swap in use with
  plenty available is fine; constantly growing swap and OOM kills mean too much is running.
  `ps -eo rss,comm --sort=-rss | head` lists the biggest users.
- Inside WSL, RAM is capped from Windows: `references/wsl.md`.
