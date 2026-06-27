#!/usr/bin/env bash
#
# AL display — first-boot provisioning (runs as root via the kernel cmdline
# `systemd.run=`, the same mechanism Raspberry Pi Imager uses).
#
# It provisions the OS (hostname / Wi-Fi / SSH / locale / password) from
# firstrun.env (secrets) + pi-config.json (non-secret), then installs a oneshot
# service that runs the app installer once the network is up, and reboots.
#
set +e

BOOT=/boot/firmware; [ -d "$BOOT" ] || BOOT=/boot
LOG="$BOOT/al-firstrun.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== AL firstrun: $(date) ==="

[ -f "$BOOT/firstrun.env" ] && { echo "Loading firstrun.env"; . "$BOOT/firstrun.env"; }

CFG="$BOOT/pi-config.json"
jget() { python3 -c "import json,sys
d=json.load(open('$CFG'))
for p in sys.argv[1].split('.'): d=d[p]
print(d)" "$1" 2>/dev/null; }

HOSTNAME_VAL="${HOSTNAME_VAL:-$(jget hostname)}"
USER_NAME="${USER_NAME:-$(jget user)}"
TIMEZONE="$(jget locale.timezone)"
KEYMAP="$(jget locale.keymap)"
WIFI_COUNTRY="${WIFI_COUNTRY:-$(jget locale.wifi_country)}"
LANGUAGE="$(jget locale.language)"

IMAGER=/usr/lib/raspberrypi-sys-mods/imager_custom
echo "Provisioning: hostname=$HOSTNAME_VAL user=$USER_NAME tz=$TIMEZONE"

if [ -x "$IMAGER" ]; then
  [ -n "$HOSTNAME_VAL" ] && "$IMAGER" set_hostname "$HOSTNAME_VAL"
  "$IMAGER" enable_ssh
  [ -n "$KEYMAP" ] && "$IMAGER" set_keymap "$KEYMAP"
  [ -n "$TIMEZONE" ] && "$IMAGER" set_timezone "$TIMEZONE"
  if [ -n "${WIFI_SSID:-}" ]; then
    echo "Configuring Wi-Fi for SSID=$WIFI_SSID"
    "$IMAGER" set_wlan "$WIFI_SSID" "$WIFI_PSK" "${WIFI_COUNTRY:-US}"
  fi
else
  echo "imager_custom missing; applying basics directly"
  [ -n "$HOSTNAME_VAL" ] && hostnamectl set-hostname "$HOSTNAME_VAL" 2>/dev/null
  [ -n "$TIMEZONE" ] && timedatectl set-timezone "$TIMEZONE" 2>/dev/null
  systemctl enable ssh 2>/dev/null
fi

[ -n "$LANGUAGE" ] && raspi-config nonint do_change_locale "$LANGUAGE" 2>/dev/null

# Optional user password (set USER_PASS in firstrun.env to change it)
if [ -n "${USER_PASS:-}" ] && [ -x /usr/lib/userconf-pi/userconf ]; then
  enc="$(echo "$USER_PASS" | openssl passwd -6 -stdin)"
  /usr/lib/userconf-pi/userconf "$USER_NAME" "$enc"
fi

# Passwordless sudo so the post-reboot installer runs unattended
echo "$USER_NAME ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/010-al-nopasswd
chmod 440 /etc/sudoers.d/010-al-nopasswd

# Stage the installer for after reboot (network will be up)
install -d /var/lib/al-setup
cp "$BOOT/setup.sh" /var/lib/al-setup/setup.sh
cp "$CFG" /var/lib/al-setup/pi-config.json
chown -R "$USER_NAME:$USER_NAME" /var/lib/al-setup

cat > /etc/systemd/system/al-setup.service <<UNIT
[Unit]
Description=AL first-boot app installer
After=network-online.target
Wants=network-online.target
ConditionPathExists=/var/lib/al-setup/setup.sh

[Service]
Type=oneshot
User=$USER_NAME
Environment=AL_CONFIG=/var/lib/al-setup/pi-config.json
ExecStart=/usr/bin/env bash /var/lib/al-setup/setup.sh
ExecStartPost=/bin/systemctl disable al-setup.service
TimeoutStartSec=2400

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable al-setup.service

# Remove our first-run token so this never runs again
sed -i 's# systemd.run=[^ ]*##g; s# systemd.run_success_action=[^ ]*##g; s# systemd.unit=[^ ]*##g' "$BOOT/cmdline.txt"

echo "=== firstrun complete; rebooting into installer ==="
