# READ-ONLY check of a bare-attached ext4 disk (e2fsck -n changes nothing).
# Runs INSIDE the helper distro's system shell; wsl_fsck.sh passes UUID in the environment.
# That shell is minimal (no awk, no blkid), so the disk is found with dumpe2fs.
exec </dev/null
[ -n "$UUID" ] || { echo "ABORT: UUID not set"; exit 1; }
DEV=
for d in /dev/sd?; do
  dumpe2fs -h "$d" 2>/dev/null | grep -q "Filesystem UUID: *$UUID" && DEV=$d
done
[ -n "$DEV" ] || { echo "ABORT: no attached disk has UUID $UUID"; exit 1; }
echo "disk: $DEV"
dumpe2fs -h "$DEV" 2>/dev/null | grep -E "^Filesystem state|^FS Error count|^Last error time|^Inode count|^Block count"
e2fsck -f -n "$DEV" > /tmp/pcm-fsck.txt 2>&1
rc=$?
tail -4 /tmp/pcm-fsck.txt
echo "e2fsck -f -n exit code: $rc (0 = clean)"
exit $rc
