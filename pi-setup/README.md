# Raspberry Pi setup

Reproducible setup for the AL display Pi: flash a card, boot it, and it
installs this repo + all dependencies, applies the display config, installs the
auto-start service, and shows a QR code linking to the config web app.

Target device (from the live unit): **Raspberry Pi 4, Raspberry Pi OS Bookworm
(64-bit), small HDMI screen at a low-res CEA mode**.

## Files

| File | Purpose |
|------|---------|
| `pi-config.json` | Non-secret settings (hostname, locale, repo, display, service). Source of truth read by the scripts. |
| `setup.sh` | Idempotent installer: apt + uv + venv deps, repo clone, display config, systemd service. Runnable by hand. |
| `firstrun.sh` | First-boot provisioning (hostname/Wi-Fi/SSH/locale/password) + stages the installer. Runs as root via the kernel `systemd.run` cmdline. |
| `install-to-boot.sh` | Copies the above onto a mounted SD boot partition and wires the cmdline. |
| `firstrun.env.sample` | Template for secrets (Wi-Fi, password). Copy to `firstrun.env` (gitignored). |
| `custom.toml` | Optional Bookworm-native OS provisioning (no app install). |

## Zero-touch flow (recommended)

1. **Flash** Raspberry Pi OS Bookworm (64-bit, *with desktop*) to the card using
   **Raspberry Pi Imager**. No GUI customization needed.
2. **Secrets:** `cp firstrun.env.sample firstrun.env` and fill in your Wi-Fi
   (and optionally a login password). Adjust `pi-config.json` if your hostname,
   repo, or display differ.
3. **Wire the card** (boot partition still mounted after flashing):
   ```bash
   ./pi-setup/install-to-boot.sh /Volumes/bootfs     # macOS
   # ./pi-setup/install-to-boot.sh /media/$USER/bootfs  # Linux
   ```
4. **Eject and boot the Pi.** It will: provision the OS and reboot → install the
   app (apt/uv/clone/venv), write the display config + `al.service`, enable
   lingering → start the display, which shows the **setup QR code** for ~30s.
5. **Scan the QR** (or browse to `http://<pi-ip>:8080`) to configure schedule,
   announcements, fonts, etc.

Progress is logged to `al-firstrun.log` on the boot partition and to
`journalctl -u al-setup.service` / `journalctl --user -u al.service` on the Pi.

## Manual flow (existing/already-booted Pi)

```bash
git clone https://github.com/wjhrdy/AL.git ~/AL
AL_CONFIG=~/AL/pi-setup/pi-config.json bash ~/AL/pi-setup/setup.sh
```

## Notes

- Display: `pi-config.json -> display` forces the small screen's HDMI mode
  (`hdmi_group=1, hdmi_mode=2` ≈ 480p). Edit those for a different panel.
- The QR splash duration is `web.setup_splash_seconds` (env `AL_SETUP_SPLASH_SECS`).
- Video downscaling auto-targets the display resolution (env `AL_VIDEO_MAX_DIM`).
- The first-boot/OS-provisioning path depends on Raspberry Pi OS internals and
  should be validated on a real flash; `setup.sh` (the installer) matches the
  inspected, working device and is the reliable core.
