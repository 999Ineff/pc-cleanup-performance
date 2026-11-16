#!/bin/bash
# READ-ONLY ext4 check of a WSL distro's disk file while WSL is OFF. Run on WINDOWS from Git Bash.
#
#   bash wsl_fsck.sh <path-to-ext4.vhdx> <root-filesystem-UUID>
#
# Get both BEFORE stopping WSL:
#   UUID:  wsl.exe -d <distro> -e findmnt -no UUID /
#   path:  scan_disk.py extras (wsl_distros), or the BasePath under
#          HKCU\Software\Microsoft\Windows\CurrentVersion\Lxss\<id>  +  \ext4.vhdx
#
# It REFUSES to run while any distro is running: stopping WSL (wsl --shutdown) closes every Linux
# session, so that is the owner's call and happens (with their yes) BEFORE this script.
# Steps: bare-attach the disk (no admin needed) -> e2fsck -f -n inside a small helper distro's
# system shell (-n = read-only, changes nothing) -> detach -> wsl --shutdown.
# It never repairs. A repair (e2fsck -y) is the corruption playbook in references/wsl.md.
#
# REPAIR_DISTRO = a distro whose system shell has e2fsck (default: docker-desktop, which Docker
# Desktop installs). Plain `wsl --system` with no -d would boot the distro you are checking.
export MSYS_NO_PATHCONV=1 WSL_UTF8=1
HERE="$(cd "$(dirname "$0")" && pwd)"
VHD="$1"
UUID="$2"
REPAIR_DISTRO="${REPAIR_DISTRO:-docker-desktop}"

if [ -z "$VHD" ] || [ -z "$UUID" ]; then
  sed -n '2,12p' "$0"
  exit 64
fi
if ! wsl.exe --list --quiet 2>/dev/null | tr -d '\r\0' | grep -qx "$REPAIR_DISTRO"; then
  echo "REFUSED: helper distro '$REPAIR_DISTRO' not installed. Set REPAIR_DISTRO to a small distro that has e2fsprogs"
  echo "(not the one being checked), or install Docker Desktop. See references/wsl.md."
  exit 2
fi
running=$(wsl.exe --list --running --quiet 2>/dev/null | tr -d '\r\0' | grep -v '^$')
if [ -n "$running" ]; then
  echo "REFUSED: WSL distros running: $running"
  echo "Ask the owner first; with their yes run: wsl.exe --shutdown   (then re-run this script)"
  exit 2
fi
state=$(powershell.exe -NoProfile -Command "try { \$f=[IO.File]::Open('$VHD','Open','Read','None'); \$f.Close(); 'free' } catch { 'locked' }" | tr -d '\r')
if [ "$state" != "free" ]; then
  echo "REFUSED: $VHD is in use or missing (Explorer window on \\\\wsl.localhost, Docker Desktop, a terminal tab?)"
  exit 3
fi

wsl.exe --mount --vhd "$VHD" --bare | tr -d '\0'
if [ "${PIPESTATUS[0]}" -ne 0 ]; then echo "bare attach failed"; exit 4; fi
timeout 900 wsl.exe --system -d "$REPAIR_DISTRO" -u root -e sh -c "UUID=$UUID; export UUID; $(tr -d '\r' < "$HERE/fsck_check.sh")"
rc=$?
wsl.exe --unmount "$VHD" | tr -d '\0'
wsl.exe --shutdown
echo "fsck exit code: $rc (0 = clean; anything else: stop all cleanup and follow references/wsl.md, corruption playbook)"
exit $rc
