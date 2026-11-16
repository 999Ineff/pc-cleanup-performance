#!/bin/sh
# READ-ONLY health check for Linux, including WSL distros (changes nothing). POSIX sh.
#   sh health_check.sh
# Covers: free space, disks and SMART (if smartctl is installed), file-system error counters,
# kernel I/O / NVMe / file-system errors, journal errors this boot, unclean shutdowns, OOM kills,
# failed services, memory. How to read it: references/linux.md, "Health" (WSL: references/wsl.md).
# Never prompts for a password: anything that needs root is printed as a command for the owner.
exec </dev/null
WSL=""
if [ -n "$WSL_DISTRO_NAME" ] || grep -qi microsoft /proc/version 2>/dev/null; then WSL=1; fi

echo "=== SYSTEM"
[ -r /etc/os-release ] && . /etc/os-release && echo "$PRETTY_NAME"
echo "kernel $(uname -r) | $(uptime | sed 's/^ *//')"
[ -n "$WSL" ] && echo "running inside WSL ($WSL_DISTRO_NAME): disk hardware is Windows' job (scripts/windows/health_check.ps1)"

echo
echo "=== FREE SPACE"
df -hT -x tmpfs -x devtmpfs -x squashfs -x overlay -x efivarfs 2>/dev/null || df -h

echo
echo "=== DISKS"
if command -v lsblk >/dev/null 2>&1; then lsblk -d -o NAME,MODEL,SIZE,ROTA,TYPE 2>/dev/null | grep -v " loop"; fi
if [ -z "$WSL" ]; then
  if command -v smartctl >/dev/null 2>&1; then
    for d in /dev/nvme[0-9]n1 /dev/sd[a-z]; do
      [ -e "$d" ] || continue
      out=$(smartctl -H "$d" 2>/dev/null | grep -iE 'overall-health|SMART Health Status')
      if [ -n "$out" ]; then echo "$d: $(echo "$out" | sed 's/^ *//')"
      else echo "$d: needs root. Owner can run: sudo smartctl -a $d"; fi
    done
  else
    echo "smartctl not installed (package smartmontools), so no SMART data"
  fi
fi

echo
echo "=== FILE-SYSTEM ERROR COUNTERS (ext4; 0 expected)"
found=""
for f in /sys/fs/ext4/*/errors_count; do
  [ -r "$f" ] || continue
  found=1
  dev=$(basename "$(dirname "$f")")
  echo "  $dev: $(cat "$f") errors, lifetime writes $(awk '{printf "%.0f GB", $1/1048576}' "/sys/fs/ext4/$dev/lifetime_write_kbytes" 2>/dev/null)"
done
[ -z "$found" ] && echo "  (no ext4 file systems, or counters not readable)"

echo
echo "=== KERNEL I/O AND FILE-SYSTEM ERRORS SINCE BOOT (0 expected)"
pat='ext4.*error|btrfs.*error|xfs.*(error|corrupt)|i/o error|blk_update_request|buffer i/o|nvme.*(timeout|reset)|ata[0-9].*(failed|error)'
if dmesg >/dev/null 2>&1; then
  echo "  matching kernel lines: $(dmesg | grep -ciE "$pat")"
  dmesg | grep -iE "$pat" | tail -5 | sed 's/^/    /'
elif command -v journalctl >/dev/null 2>&1; then
  echo "  matching kernel lines: $(journalctl -k -b --no-pager -q 2>/dev/null | grep -ciE "$pat")  (dmesg needs root here)"
else
  echo "  dmesg needs root. Owner can run: sudo dmesg | grep -iE '$pat'"
fi

if command -v journalctl >/dev/null 2>&1; then
  echo
  echo "=== JOURNAL: errors this boot"
  n=$(journalctl -b -p err --no-pager -q 2>/dev/null | wc -l | tr -d ' ')
  echo "  error lines: $n (last 8 below)"
  journalctl -b -p err --no-pager -q 2>/dev/null | tail -8 | cut -c1-200 | sed 's/^/    /'
  echo "  OOM kills this boot: $(journalctl -k -b --no-pager -q 2>/dev/null | grep -ci 'out of memory')"
fi

echo
echo "=== UNCLEAN SHUTDOWNS (crash = the system went down without shutting down)"
if command -v last >/dev/null 2>&1; then
  last -x 2>/dev/null | grep -E "crash|shutdown|reboot" | head -8 | sed 's/^/  /'
fi

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  echo
  echo "=== FAILED SERVICES"
  systemctl --failed --no-legend --no-pager 2>/dev/null | sed 's/^/  system: /'
  systemctl --user --failed --no-legend --no-pager 2>/dev/null | sed 's/^/  user:   /'
fi

echo
echo "=== MEMORY"
free -h 2>/dev/null
echo "(startup items and the biggest RAM users: startup_audit.sh)"
