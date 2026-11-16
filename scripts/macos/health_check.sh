#!/bin/sh
# READ-ONLY health check for macOS (changes nothing). POSIX sh: works with the stock /bin/sh.
#   sh health_check.sh [DAYS]     DAYS of system log to search for shutdown causes (default 14)
# Covers: free space, the internal disk (SMART status, APFS container), unexpected shutdowns and
# kernel panics, memory pressure and swap, thermal throttling, battery health, Time Machine local
# snapshots. How to read it: references/macos.md, "Health".
DAYS="${1:-14}"
case "$DAYS" in ''|*[!0-9]*) DAYS=14 ;; esac

echo "=== SYSTEM"
sw_vers 2>/dev/null
echo "arch: $(uname -m)"
echo "uptime: $(uptime | sed 's/^ *//')"

echo
echo "=== FREE SPACE"
df -h / /System/Volumes/Data 2>/dev/null

echo
echo "=== INTERNAL DISK"
diskutil info / 2>/dev/null | grep -E "Device / Media Name|Solid State|SMART Status|Container Total Space|Container Free Space|File System Personality"
diskutil apfs list 2>/dev/null | grep -E "APFS Container Reference|Size \(Capacity Ceiling\)|Capacity In Use By Volumes|Capacity Not Allocated" | head -12
system_profiler SPNVMeDataType SPSerialATADataType 2>/dev/null | grep -E "Model:|S\.M\.A\.R\.T\. status:|Capacity:" | head -8
if command -v smartctl >/dev/null 2>&1; then
  echo "smartctl (may need sudo for full data):"
  smartctl -H disk0 2>&1 | tail -3
else
  echo "smartctl not installed (optional: 'brew install smartmontools', then 'sudo smartctl -a disk0')"
fi

echo
echo "=== UNEXPECTED SHUTDOWNS (last $DAYS days; this log search can take a minute)"
# "Previous shutdown cause: 5" is a normal shutdown, 3 is a forced power-off; negative numbers are
# hardware, power or thermal problems (references/macos.md lists the common ones).
log show --last "${DAYS}d" --style compact --predicate 'eventMessage CONTAINS "Previous shutdown cause"' 2>/dev/null \
  | grep -o "Previous shutdown cause: -*[0-9]*" | sort | uniq -c
echo "kernel panics (newest first):"
for d in /Library/Logs/DiagnosticReports "$HOME/Library/Logs/DiagnosticReports"; do
  ls -t "$d" 2>/dev/null | grep -iE "panic" | head -5 | sed "s|^|  $d/|"
done
n=$(find "$HOME/Library/Logs/DiagnosticReports" -type f -mtime "-$DAYS" 2>/dev/null | wc -l | tr -d ' ')
echo "app crash reports in the last $DAYS days: $n"

echo
echo "=== MEMORY"
mem=$(sysctl -n hw.memsize 2>/dev/null)
[ -n "$mem" ] && echo "installed: $((mem / 1073741824)) GB"
memory_pressure 2>/dev/null | tail -1
sysctl vm.swapusage 2>/dev/null
echo "(biggest RAM users and login items: startup_audit.sh)"

echo
echo "=== THERMAL (CPU speed limit below 100 = throttling)"
pmset -g therm 2>/dev/null | grep -iE "speed|limit|warning" | head -5

echo
echo "=== BATTERY (laptops)"
system_profiler SPPowerDataType 2>/dev/null | grep -E "Cycle Count|Condition|Maximum Capacity" | sed 's/^ *//'

echo
echo "=== TIME MACHINE LOCAL SNAPSHOTS"
c=$(tmutil listlocalsnapshots / 2>/dev/null | grep -c "com.apple")
echo "local snapshots on /: $c  (macOS frees them automatically when space runs low; see references/macos.md)"
