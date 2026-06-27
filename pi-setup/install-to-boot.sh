#!/usr/bin/env bash
#
# Copy the AL first-boot files onto a freshly-imaged SD card's boot partition
# and wire the kernel cmdline to run firstrun.sh on first boot.
#
# Usage:  ./install-to-boot.sh /Volumes/bootfs      (macOS)
#         ./install-to-boot.sh /media/$USER/bootfs  (Linux)
#
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BOOT="${1:?Usage: install-to-boot.sh <path-to-mounted-boot-partition>}"

[ -d "$BOOT" ] || { echo "Not a directory: $BOOT" >&2; exit 1; }
[ -f "$BOOT/cmdline.txt" ] || { echo "No cmdline.txt in $BOOT — is this the boot partition?" >&2; exit 1; }

echo "Copying setup files to $BOOT"
cp "$HERE/firstrun.sh" "$HERE/setup.sh" "$HERE/pi-config.json" "$BOOT/"
if [ -f "$HERE/firstrun.env" ]; then
  cp "$HERE/firstrun.env" "$BOOT/"
  echo "Copied firstrun.env (secrets)"
else
  echo "NOTE: no firstrun.env found — Wi-Fi/password won't be set unless you use"
  echo "      Raspberry Pi Imager's customization. Copy firstrun.env.sample -> firstrun.env first."
fi

TOKEN="systemd.run=/boot/firmware/firstrun.sh systemd.run_success_action=reboot systemd.unit=kernel-command-line.target"
if grep -q "systemd.run=" "$BOOT/cmdline.txt"; then
  echo "cmdline.txt already has a systemd.run token — leaving it as-is."
else
  # cmdline.txt must stay a single line.
  sed -i.bak "s#\$# ${TOKEN}#" "$BOOT/cmdline.txt" 2>/dev/null || \
    sed -i '' "s#\$# ${TOKEN}#" "$BOOT/cmdline.txt"
  echo "Added first-boot token to cmdline.txt"
fi

echo "Done. Eject the card and boot the Pi."
