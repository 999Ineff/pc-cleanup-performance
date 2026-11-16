#!/bin/sh
# READ-ONLY startup + RAM audit for Linux, including WSL distros (changes nothing). POSIX sh.
#   sh startup_audit.sh
# Lists services enabled at boot (system and user), desktop autostart entries, @reboot cron jobs,
# boot time and the slowest units, then memory and the biggest RAM users. Turning things off is
# never done here: references/linux.md, "Startup".
exec </dev/null

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  echo "=== SYSTEM SERVICES ENABLED AT BOOT"
  systemctl list-unit-files --type=service --state=enabled --no-legend --no-pager 2>/dev/null | awk '{print "  " $1}'
  echo
  echo "=== USER SERVICES ENABLED (start at login)"
  systemctl --user list-unit-files --state=enabled --no-legend --no-pager 2>/dev/null | awk '{print "  " $1}'
  echo
  echo "=== BOOT TIME"
  systemd-analyze 2>/dev/null | head -2 | sed 's/^/  /'
  echo "slowest units:"
  systemd-analyze blame --no-pager 2>/dev/null | head -12 | sed 's/^/  /'
else
  echo "=== systemd not running here (common in containers and older WSL): no service list"
fi

echo
echo "=== DESKTOP AUTOSTART (.desktop files; a user copy with Hidden=true disables a system one)"
cfg="${XDG_CONFIG_HOME:-$HOME/.config}"
for f in "$cfg"/autostart/*.desktop /etc/xdg/autostart/*.desktop; do
  [ -e "$f" ] || continue
  name=$(grep -m1 '^Name=' "$f" | cut -d= -f2-)
  exe=$(grep -m1 '^Exec=' "$f" | cut -d= -f2-)
  off=""
  grep -qiE '^Hidden=true|^X-GNOME-Autostart-enabled=false' "$f" && off=" (disabled)"
  echo "  ${name:-$(basename "$f")}$off  ->  $exe   [$f]"
done

echo
echo "=== CRON @reboot"
if command -v crontab >/dev/null 2>&1; then
  crontab -l 2>/dev/null | grep -E '^[[:space:]]*@reboot' | sed 's/^/  /'
fi

echo
echo "=== RAM"
free -h 2>/dev/null
echo "biggest RAM users (resident memory, MB):"
ps -eo rss=,comm= 2>/dev/null | awk '{ m[$2] += $1; c[$2]++ } END { for (k in m) printf "%8.0f MB  x%-3d %s\n", m[k] / 1024, c[k], k }' \
  | sort -rn | head -15
if [ -n "$WSL_DISTRO_NAME" ] || grep -qi microsoft /proc/version 2>/dev/null; then
  echo "(WSL: this whole distro shows on Windows as vmmemWSL; cap it in %USERPROFILE%\\.wslconfig, see references/wsl.md)"
fi
