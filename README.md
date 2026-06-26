# AL - Sonos Music Display

A music display application for Sonos speakers, optimized for CRT displays. Shows currently playing track info and album art from your Sonos system with smooth text scrolling effects.

## Features

- **Sonos Integration**: Automatically detects and displays currently playing track from your Sonos speaker
- **CRT Display Optimization**: 
  - Stretch mode ('s' key) for proper 4:3 display on CRT monitors
  - Fullscreen toggle ('f' key)
  - Smooth text scrolling for long titles
- **Visual Elements**:
  - Dynamic album art display with caching
  - High-contrast text with outlines for better visibility
  - Smooth fade transitions
  - Auto-scrolling for long song titles
  - Mouse cursor auto-hidden in fullscreen
- **Schedule Display**: Configurable operating hours shown on screen
- **Text Interrupts**: Configurable announcements rotate with the operating-hours display cadence
- **Remote Config**: Optional config updates from a GitHub Gist

## Installation

```bash
# Clone the repository
# on debian:
sudo apt-get update
sudo apt install git curl

git clone https://github.com/wjhrdy/AL.git
cd AL

# Install Just command runner
# On macOS:
brew install just
# On Raspberry Pi:
curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | sudo bash -s -- --to /usr/local/bin

# Install all dependencies and set up the environment
just install
```

## Raspberry Pi Setup Guide

### 1. Initial Raspberry Pi Setup
1. Download the latest Raspberry Pi OS (64-bit) from [Raspberry Pi's official website](https://www.raspberrypi.com/software/operating-systems/)
2. Flash the OS to your SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
3. Insert the SD card into your Raspberry Pi and connect:
   - Power supply
   - Keyboard and mouse
   - Display (CRT or other monitor)
4. Boot up your Raspberry Pi and complete the initial setup wizard

### 2. System Configuration
1. Open Terminal and update your system:
   ```bash
   sudo apt update
   sudo apt upgrade -y
   ```

2. Enable required interfaces:
   ```bash
   sudo raspi-config
   ```
   - Navigate to "Interface Options"
   - Enable: SSH

3. Install required system packages:
   ```bash
   sudo apt install -y git python3-pip python3-venv

### 3. Install Just Command Runner
1. Download and install Just:
   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | sudo bash -s -- --to /usr/local/bin
   ```

### 4. Application Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/wjhrdy/AL.git
   cd AL
   ```

2. Run the Raspberry Pi setup script:
   ```bash
   just setup-pi
   ```

3. Set up the development environment:
   ```bash
   just dev
   ```

## Auto-start Setup

To make the application run automatically when your Raspberry Pi boots up:

1. Enable autostart:
   ```bash
   just enable-autostart
   ```

2. To disable autostart:
   ```bash
   just disable-autostart
   ```

3. Check service status:
   ```bash
   sudo systemctl status al.service
   ```

4. View logs if needed:
   ```bash
   journalctl -u al.service -f
   ```

## Running the Application

1. Start the application:
    ```bash
    just run
    ```
    For debugging mode:
    ```bash
    just debug
    ```

2. Controls:
    - Press 'f' to toggle fullscreen mode
    - Press 's' to toggle stretch mode (for 4:3 CRT displays)
    - Press 'o' to toggle always-open mode (bypass schedule)
    - Arrow keys to adjust display offset

## Display Configuration

### Text Interrupts and Announcements
Operating hours appear as a timed text interrupt. Add any number of announcement
interrupts in `config.yaml` under `display.announcements`; they rotate on the
same cadence as the hours display.

```yaml
display:
  schedule_interval: 60
  schedule_duration: 10
  announcements:
    - title: Announcement
      message: "Welcome in."
    - title: Reminder
      lines:
        - "Ask about today's specials."
        - "Thanks for listening."
```

### For CRT Displays
1. Connect your CRT display to the Raspberry Pi using appropriate adapters
   - For composite or S-Video output, you can use [this HDMI to Composite/S-Video adapter](https://www.amazon.com/TIXILINBI-Converter-Composite-S-Video-Adpater/dp/B0C7GGKWZZ)
   - For component video (YPbPr - red/green/blue) connections, you can use [this HDMI to Component adapter](https://www.amazon.com/gp/product/B083ZF5BBP/)
   - Connect the adapter to the Raspberry Pi's HDMI port
   - Use the appropriate cables for your chosen connection type (composite/S-Video or component)
2. Edit `/boot/config.txt` for custom resolutions if needed:
   ```bash
   sudo nano /boot/config.txt
   ```
3. Common CRT settings to add:
   ```
   hdmi_group=1
   hdmi_mode=4  # For 720p
   ```
4. Once the application is running, press 's' to enable stretch mode for proper 4:3 display

### Normal Mode
- Default 16:9 display ratio
- Centered album art and text

### Stretch Mode (CRT)
- Optimized for 4:3 CRT displays
- Compensates for 16:9 signal squeeze
- Maintains proper aspect ratios

## Troubleshooting

### Sonos Issues
1. Ensure your Raspberry Pi is on the same network as your Sonos speakers
2. Check that Sonos speakers are discoverable — the app auto-connects to the first speaker found
3. Run in debug mode for detailed logging:
   ```bash
   just debug
   ```

### Display Issues
1. Check current resolution:
   ```bash
   tvservice -s
   ```
2. For CRT displays:
   - Toggle stretch mode ('s' key)
   - Verify HDMI/adapter connections
   - Check `/boot/config.txt` settings

### Application Issues
1. Check logs:
   ```bash
   just debug
   ```
2. Verify all dependencies are installed:
   ```bash
   just dev
   ```
3. Clean and reinstall if needed:
   ```bash
   just clean
   just dev
   ```

## Development

- Clean environment: `just clean`
- Update dependencies: `just update`
- Add new dependency: `just add [package-name]`

## Contributing

Feel free to submit issues and enhancement requests!
