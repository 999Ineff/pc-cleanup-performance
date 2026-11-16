# WSL (Linux inside Windows)

A WSL 2 distro is a Linux machine whose whole disk is one file on Windows,
`%LOCALAPPDATA%\...\ext4.vhdx` (the exact folder is the distro's `BasePath` under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss\<id>`; `scan_disk.py` and `health_check.ps1`
print it). Docker Desktop keeps its data the same way (`docker_data.vhdx`).

## Contents
1. What is different about WSL
2. Driving WSL from Windows without surprises
3. Cleaning: inside first, then compact
4. Read-only fsck
5. Compaction recipe (proven)
6. Corruption playbook ("Input/output error", E_UNEXPECTED)
7. RAM: .wslconfig

## 1. What is different about WSL

- **The disk file never shrinks by itself.** Deleting inside the distro frees space inside it, not on
  Windows. Windows gets the space back only after compaction (section 5).
- Inside the distro, `df /` shows the virtual disk's maximum (often 1 TB), not real free space. The
  real number is the Windows drive: `df -h /mnt/c`, or the `/mnt/c` line in `scan_disk.py`.
- Run the Linux scripts inside the distro (`scripts/linux/*.sh`, the Python scanners with `python3`)
  and the Windows scripts on Windows. They are two separate runs with two live-ref lists.
- `wsl --shutdown` stops every distro and closes every Linux session and terminal. Always ask first.

## 2. Driving WSL from Windows without surprises

- **Never feed a script to `wsl.exe ... bash` through stdin.** A command inside that reads stdin
  (npm, ssh, apt) swallows the rest of the script. Pass it as an argument instead, from Git Bash:
  `MSYS_NO_PATHCONV=1 wsl.exe -d <distro> -e bash -c "$(tr -d '\r' < script.sh)"`, and start scripts
  with `exec </dev/null`.
- Put an explicit Linux `PATH` at the top of such scripts; otherwise Windows' `npm`/`uv` on `/mnt/c`
  can run instead of the Linux ones.
- Windows PowerShell 5.1 mangles inner double quotes passed to `wsl.exe`; drive WSL from Git Bash or
  `cmd`, or keep commands quote-free.
- `WSL_UTF8=1` makes `wsl.exe --list` print readable text instead of UTF-16.
- Things that keep a distro running (and its disk file locked), so compaction fails:
  Explorer windows on `\\wsl.localhost\...`, terminal tabs, Docker Desktop, editors attached to WSL,
  and desktop apps that start WSL at sign-in to check a version. Close them (with the owner) before
  stopping WSL, and confirm the file can be opened exclusively for ~20 seconds afterwards.

## 3. Cleaning: inside first, then compact

1. Inside the distro: `scan_disk.py`, `scan_dev_junk.py`, `scan_downloads.py` with Linux paths;
   `references/linux.md` for apt/journal/snap; `delete_paths.py` for approved plans.
2. Only if real space was freed inside (several GB), compact from Windows (section 5).

## 4. Read-only fsck

`scripts/wsl/wsl_fsck.sh` (run from Git Bash on Windows) checks the ext4 file system inside a disk
file without changing anything:

1. Before stopping WSL, get the root file system UUID: `wsl.exe -d <distro> -e findmnt -no UUID /`.
2. With the owner's yes: `wsl.exe --shutdown`.
3. `bash scripts/wsl/wsl_fsck.sh '<path>\ext4.vhdx' <uuid>`

It refuses if any distro is running or the file is locked. It attaches the disk bare
(`wsl --mount --vhd ... --bare`, no admin), runs `e2fsck -f -n` from a small helper distro's system
shell (default `docker-desktop`, which has e2fsprogs; set `REPAIR_DISTRO` to another small distro
otherwise; never the one being checked), detaches and shuts WSL down. Exit 0 = clean. 1-3 minutes
for ~60 GB used. Plain `wsl --system` without `-d` would boot the distro you are checking: don't.

## 5. Compaction recipe (proven: 90.5 GB to 68.6 GB in about four minutes)

With the owner's yes (it closes Linux sessions):

1. Trim inside: `wsl.exe -d <distro> -u root -e sh -c 'findmnt -no OPTIONS /; fstrim -v /'`
2. `wsl.exe --shutdown`, then check the disk file opens exclusively for ~20 s (nothing re-locked it).
3. Read-only fsck (section 4). **It must be clean. Never compact a damaged file system.**
4. One admin prompt: `admin_maintenance.ps1 -Apply -CompactVhd '<path>\ext4.vhdx' -RunDir <run>`
   (diskpart: `attach vdisk readonly`, `compact vdisk`, `detach vdisk`, plus a safety detach).
   If Windows offers to "initialize" or "format" a disk during this, click **Cancel**.
5. Read-only fsck again: inode and block counts must match step 3.
6. Boot test: `wsl.exe -d <distro> -e sh -c 'df -h /; dmesg | grep -ciE "ext4.*error|i/o error"'` → 0.

The file can't shrink below what the file system really uses plus metadata (a disk with ~57 GB used
stopped at ~68 GB). **Never use `wsl --manage <distro> --set-sparse`**: there are reports of
corruption with it. Docker Desktop's disk: `docker system prune` first, quit Docker Desktop, then the
same steps 2, 4 and 6 (Docker Desktop also has its own "Clean / Purge data" and disk-limit settings).

## 6. Corruption playbook ("Input/output error", E_UNEXPECTED)

Symptoms: every command in the distro fails with `Input/output error`, or WSL refuses to start with
`Catastrophic failure` / `Wsl/Service/E_UNEXPECTED`. **This is file-system corruption inside the
disk file, not a full disk** (even if the disk is also nearly full). Restarting doesn't help: the
kernel re-detects the damage seconds after every mount.

Stop all cleanup. Tell the owner. Then, step by step with their yes:
1. Back up the disk file first (with WSL stopped): `robocopy <folder> <backup folder> ext4.vhdx /J`.
2. Bare-attach it (no admin): `wsl --mount --vhd <path> --bare`.
3. From a helper distro's system shell (`wsl --system -d <helper> -u root`), find the disk by UUID
   with `dumpe2fs -h /dev/sdX`, then `e2fsck -f -y /dev/sdX` (repairs), then `e2fsck -f -n` must exit 0.
4. `wsl --unmount <path>`, boot the distro, check `dmesg` for ext4 errors.
5. Look for the cause: a full drive at the time, or storage resets in `health_check.ps1` (Event 129).
   A drive that loses writes will do it again: plan a replacement.

`debugfs -c` (catastrophic mode, with `DEBUGFS_PAGER=__none__`) reads a damaged file system for
forensics without changing it.

## 7. RAM: .wslconfig

WSL's VM (shown on Windows as `vmmemWSL`) takes RAM as Linux uses it and gives it back slowly. Cap
it in `%USERPROFILE%\.wslconfig` (applies after `wsl --shutdown`):

```ini
[wsl2]
# pick about half the machine's RAM, or less (comments go on their own line, never after a value)
memory=8GB
[experimental]
autoMemoryReclaim=gradual
```

Undo: remove the lines and `wsl --shutdown`.
