#!/usr/bin/env bash
#
# AL display — app installer for Raspberry Pi OS (Bookworm).
# Idempotent: safe to re-run. Installs system + Python deps, clones/updates the
# repo, applies the display config, and installs the systemd user service.
#
# Run as the target user (must have sudo):  bash setup.sh
# Config is read from pi-config.json (next to this script, or $AL_CONFIG).
#
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${AL_CONFIG:-$HERE/pi-config.json}"
log() { echo "[al-setup] $*"; }

if [ ! -f "$CONFIG" ]; then
  echo "Config not found: $CONFIG" >&2
  exit 1
fi

# Read a value from the JSON manifest via python3 (always present on Pi OS).
cfg() { python3 -c "import json,sys;d=json.load(open('$CONFIG'))
k=sys.argv[1].split('.')
for p in k: d=d[p]
print(d)" "$1"; }

USER_NAME="$(cfg user)"
REPO_URL="$(cfg repo.url)"
REPO_BRANCH="$(cfg repo.branch)"
REPO_DIR="$(cfg repo.dir)"
PY_VER="$(cfg python_version)"
SERVICE_NAME="$(cfg service.name)"

log "user=$USER_NAME repo=$REPO_URL@$REPO_BRANCH dir=$REPO_DIR python=$PY_VER"

# ---------------------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------------------
log "Installing apt packages..."
APT_PKGS="$(python3 -c "import json;print(' '.join(json.load(open('$CONFIG'))['apt_packages']))")"
sudo apt-get update -y
sudo apt-get install -y $APT_PKGS

# ---------------------------------------------------------------------------
# 2. uv (Python package manager)
# ---------------------------------------------------------------------------
export PATH="$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  log "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
UV="$(command -v uv)"
log "uv: $($UV --version)"

# ---------------------------------------------------------------------------
# 3. Clone / update the repo
# ---------------------------------------------------------------------------
if [ -d "$REPO_DIR/.git" ]; then
  log "Updating existing repo in $REPO_DIR"
  git -C "$REPO_DIR" fetch --depth 1 origin "$REPO_BRANCH"
  git -C "$REPO_DIR" checkout "$REPO_BRANCH"
  git -C "$REPO_DIR" reset --hard "origin/$REPO_BRANCH"
else
  log "Cloning $REPO_URL -> $REPO_DIR"
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$REPO_DIR"
fi

# ---------------------------------------------------------------------------
# 4. Python virtualenv + dependencies
# ---------------------------------------------------------------------------
cd "$REPO_DIR"
if [ ! -d .venv ]; then
  log "Creating venv (python$PY_VER)"
  "$UV" venv --python "python$PY_VER" || "$UV" venv
fi
log "Installing Python dependencies..."
"$UV" pip install --python "$REPO_DIR/.venv/bin/python" .

# ---------------------------------------------------------------------------
# 5. App config files (gitignored, created from samples if missing)
# ---------------------------------------------------------------------------
[ -f config.yaml ] || { cp config.sample.yaml config.yaml; log "Created config.yaml from sample"; }
[ -f .env ] || { touch .env; log "Created empty .env"; }

# ---------------------------------------------------------------------------
# 6. Display config (small low-res HDMI screen)
# ---------------------------------------------------------------------------
BOOT_CFG=/boot/firmware/config.txt
[ -f "$BOOT_CFG" ] || BOOT_CFG=/boot/config.txt
log "Applying display config to $BOOT_CFG"
ensure_kv() {  # ensure "key=value" present (replace existing key line)
  local key="$1" val="$2"
  if grep -qE "^\s*${key}=" "$BOOT_CFG"; then
    sudo sed -i -E "s|^\s*${key}=.*|${key}=${val}|" "$BOOT_CFG"
  else
    echo "${key}=${val}" | sudo tee -a "$BOOT_CFG" >/dev/null
  fi
}
ensure_kv hdmi_force_hotplug "$(cfg display.hdmi_force_hotplug)"
ensure_kv hdmi_group "$(cfg display.hdmi_group)"
ensure_kv hdmi_mode "$(cfg display.hdmi_mode)"
ensure_kv disable_overscan "$(cfg display.disable_overscan)"

# ---------------------------------------------------------------------------
# 7. Desktop autologin (so DISPLAY=:0 exists for the fullscreen app)
# ---------------------------------------------------------------------------
if command -v raspi-config >/dev/null 2>&1; then
  log "Setting desktop autologin"
  sudo raspi-config nonint do_boot_behaviour B4 || true
fi

# ---------------------------------------------------------------------------
# 8. systemd user service
# ---------------------------------------------------------------------------
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
SVC_DESC="$(cfg service.description)"
SVC_EXEC="$(cfg service.exec)"
log "Installing $SERVICE_NAME"
cat > "$UNIT_DIR/$SERVICE_NAME" <<UNIT
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
RemainAfterExit=no

[Install]
WantedBy=default.target
UNIT

# Start user services at boot without an interactive login.
sudo loginctl enable-linger "$USER_NAME" || true
# Ensure `systemctl --user` works even when run from a non-login context
# (e.g. the first-boot installer service).
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
for _ in 1 2 3 4 5; do [ -d "$XDG_RUNTIME_DIR" ] && break; sleep 1; done
systemctl --user daemon-reload || true
systemctl --user enable "$SERVICE_NAME" || true
systemctl --user restart "$SERVICE_NAME" || true

log "Done. The app will start on the display; scan the on-screen QR to configure."
