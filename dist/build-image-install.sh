#!/usr/bin/env bash
#
# Bake the AL app into a Raspberry Pi OS image. Runs as root INSIDE the image
# during the CI build (e.g. pguyot/arm-runner-action), with this repo checked
# out at the current working directory. Not for running on a live Pi — use
# pi-setup/setup.sh for that.
#
set -euxo pipefail

SRC="$(pwd)"
CFG="$SRC/pi-setup/pi-config.json"
cfg() { python3 -c "import json,sys
d=json.load(open('$CFG'))
for p in sys.argv[1].split('.'): d=d[p]
print(d)" "$1"; }

USER_NAME="$(cfg user)"
REPO_DIR="$(cfg repo.dir)"
PY_VER="$(cfg python_version)"
SERVICE_NAME="$(cfg service.name)"
HOME_DIR="/home/$USER_NAME"

# --- user -------------------------------------------------------------------
if ! id -u "$USER_NAME" >/dev/null 2>&1; then
  useradd -m -s /bin/bash "$USER_NAME"
fi
usermod -aG sudo,video,render,audio,plugdev "$USER_NAME" 2>/dev/null || true

# --- system packages --------------------------------------------------------
APT_PKGS="$(python3 -c "import json;print(' '.join(json.load(open('$CFG'))['apt_packages']))")"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y curl $APT_PKGS

# --- copy the repo into place ----------------------------------------------
mkdir -p "$REPO_DIR"
rsync -a --delete \
  --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
  --exclude 'announcements/*' --exclude 'fonts/*' --exclude 'build' \
  --exclude 'debug_output' --exclude 'al.egg-info' \
  "$SRC"/ "$REPO_DIR"/
chown -R "$USER_NAME:$USER_NAME" "$REPO_DIR"

# --- uv + venv + deps (as the user) ----------------------------------------
sudo -u "$USER_NAME" sh -c 'command -v ~/.local/bin/uv >/dev/null 2>&1 || curl -LsSf https://astral.sh/uv/install.sh | sh'
sudo -u "$USER_NAME" bash -lc "cd '$REPO_DIR' && ~/.local/bin/uv venv --python python$PY_VER || ~/.local/bin/uv venv"
sudo -u "$USER_NAME" bash -lc "cd '$REPO_DIR' && ~/.local/bin/uv pip install --python '$REPO_DIR/.venv/bin/python' ."

# --- app config (created from samples) -------------------------------------
sudo -u "$USER_NAME" bash -lc "cd '$REPO_DIR' && { [ -f config.yaml ] || cp config.sample.yaml config.yaml; }; touch .env"

# --- display config ---------------------------------------------------------
BOOT_CFG=/boot/firmware/config.txt; [ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt
ensure_kv() {
  local k="$1" v="$2"
  if grep -qE "^\s*${k}=" "$BOOT_CFG"; then sed -i -E "s|^\s*${k}=.*|${k}=${v}|" "$BOOT_CFG"
  else echo "${k}=${v}" >> "$BOOT_CFG"; fi
}
ensure_kv hdmi_force_hotplug "$(cfg display.hdmi_force_hotplug)"
ensure_kv hdmi_group "$(cfg display.hdmi_group)"
ensure_kv hdmi_mode "$(cfg display.hdmi_mode)"
ensure_kv disable_overscan "$(cfg display.disable_overscan)"

# --- desktop autologin ------------------------------------------------------
raspi-config nonint do_boot_behaviour B4 2>/dev/null || true

# --- systemd user service (enabled offline) --------------------------------
SVC_DESC="$(cfg service.description)"; SVC_EXEC="$(cfg service.exec)"
install -d -o "$USER_NAME" -g "$USER_NAME" "$HOME_DIR/.config/systemd/user/default.target.wants"
cat > "$HOME_DIR/.config/systemd/user/$SERVICE_NAME" <<UNIT
[Unit]
Description=$SVC_DESC
After=graphical.target
Wants=graphical.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
EnvironmentFile=$REPO_DIR/.env
Environment=DISPLAY=:0
Environment=SDL_VIDEODRIVER=x11
ExecStart=$REPO_DIR/.venv/bin/python $SVC_EXEC
Restart=always
RestartSec=10

[Install]
WantedBy=default.target
UNIT
ln -sf "../$SERVICE_NAME" "$HOME_DIR/.config/systemd/user/default.target.wants/$SERVICE_NAME"
chown -R "$USER_NAME:$USER_NAME" "$HOME_DIR/.config"

# enable lingering so the user service starts at boot without a login
install -d /var/lib/systemd/linger
touch "/var/lib/systemd/linger/$USER_NAME"

echo "AL baked into image for user $USER_NAME at $REPO_DIR"
