#!/bin/sh
# READ-ONLY startup + RAM audit for macOS (changes nothing). POSIX sh: works with the stock /bin/sh.
#   sh startup_audit.sh
# Lists Login Items / background items, third-party LaunchAgents and LaunchDaemons (never the
# Apple ones in /System, which are off-limits), what launchd has loaded that isn't Apple's, and
# the biggest RAM users. Turning things off is never done here: references/macos.md, "Startup".

echo "=== LOGIN ITEMS AND BACKGROUND ITEMS"
if sfltool dumpbtm >/dev/null 2>&1; then
  sfltool dumpbtm 2>/dev/null | grep -E "^[[:space:]]*(Name|Disposition|Type|Executable Path|URL):" | sed 's/^ *//' | head -120
else
  echo "Needs admin on this macOS version. Ask the owner, or check System Settings > General > Login Items & Extensions."
  echo "With their yes: sudo sfltool dumpbtm | less     (macOS 13 or newer)"
fi

echo
echo "=== THIRD-PARTY LAUNCH AGENTS / DAEMONS (files)"
for d in "$HOME/Library/LaunchAgents" /Library/LaunchAgents /Library/LaunchDaemons; do
  [ -d "$d" ] || continue
  echo "$d:"
  for f in "$d"/*.plist; do
    [ -e "$f" ] || continue
    prog=$(plutil -extract Program raw -o - "$f" 2>/dev/null)
    [ -z "$prog" ] && prog=$(plutil -extract ProgramArguments.0 raw -o - "$f" 2>/dev/null)
    echo "  $(basename "$f")  ->  ${prog:-?}"
  done
done

echo
echo "=== LOADED BY LAUNCHD FOR THIS USER (not com.apple; PID '-' = loaded but not running)"
launchctl list 2>/dev/null | awk 'NR == 1 || $3 !~ /^com\.apple\./' | head -60
echo
echo "disabled overrides (non-Apple):"
launchctl print-disabled "gui/$(id -u)" 2>/dev/null | grep -v "com.apple" | grep -E "=>" | head -40

echo
echo "=== RAM"
mem=$(sysctl -n hw.memsize 2>/dev/null)
[ -n "$mem" ] && echo "installed: $((mem / 1073741824)) GB"
vm_stat 2>/dev/null | awk '
  /page size of/ { for (i = 1; i <= NF; i++) if ($i == "of") ps = $(i + 1) }
  /^Pages (free|active|inactive|speculative|wired down|occupied by compressor):/ {
    name = $0; sub(/:.*/, "", name); v = $NF; gsub(/\./, "", v); val[name] = v }
  END { if (!ps) ps = 4096; for (k in val) printf "  %-32s %6.2f GB\n", k, val[k] * ps / 1073741824 }'
memory_pressure 2>/dev/null | tail -1
sysctl vm.swapusage 2>/dev/null
echo "biggest RAM users:"
top -l 1 -o mem -n 15 -stats pid,command,mem 2>/dev/null | awk 'f; /^PID/ { f = 1; print }'
