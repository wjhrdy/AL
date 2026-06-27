# AL in Raspberry Pi Imager (custom OS list)

This makes AL installable directly from **Raspberry Pi Imager**, like OpenScan:
pick the AL image, flash, boot. The image has the app, all dependencies, the
display config, and the auto-start service baked in, and boots to the display
showing a QR code to the web config app.

## Use it (end user)

1. Open **Raspberry Pi Imager**.
2. `Ctrl/Cmd` + `Shift` + `X` is not needed — instead launch Imager pointed at the
   custom repo:
   - macOS: `"/Applications/Raspberry Pi Imager.app/Contents/MacOS/rpi-imager" --repo https://raw.githubusercontent.com/wjhrdy/AL/main/dist/repo.json`
   - Linux: `rpi-imager --repo https://raw.githubusercontent.com/wjhrdy/AL/main/dist/repo.json`
3. Choose Device → **AL Display** → your SD card.
4. Optionally use Imager's customization (Wi-Fi, hostname, password) — the image
   supports it (`init_format: cloudinit-rpi`).
5. Write, boot. Scan the on-screen QR to configure.

## How it's built (maintainer)

`repo.json` points at a prebuilt image published to GitHub Releases. The image and
the checksums in `repo.json` are produced by CI:

- **`.github/workflows/build-image.yml`** — on a `v*` tag (or manual run), builds
  a Raspberry Pi OS image with [`pguyot/arm-runner-action`], runs
  `dist/build-image-install.sh` inside it, compresses to `AL_arm64.img.xz`,
  fills `repo.json` via `dist/gen_repo_json.py`, commits the updated `repo.json`,
  and attaches the image to the release.
- **`dist/build-image-install.sh`** — bakes AL in (apt deps, uv, venv, display
  config, autologin, user service + lingering). Reads `pi-setup/pi-config.json`.
- **`dist/gen_repo_json.py`** — computes download/extract sizes + SHA256 and
  writes them into `repo.json`.
- **`dist/assets/icon.png`** — the icon shown in Imager.

To cut a release:

```bash
git tag v1.0.0 && git push origin v1.0.0
```

The `url` in `repo.json` uses the stable `releases/latest/download/...` path, so it
keeps working across releases; only the checksums change per build.

## Notes / status

- The image build runs in CI and **has not yet been validated by a real run** —
  the first tag will exercise it; check the Actions log and adjust
  `build-image-install.sh` as needed (autologin/session specifics can vary).
- For setting up an existing Pi without re-imaging, use `pi-setup/` (`setup.sh`).

[`pguyot/arm-runner-action`]: https://github.com/pguyot/arm-runner-action
