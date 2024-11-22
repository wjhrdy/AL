# Real-Time Music Recognition with CRT Display

A real-time music recognition application optimized for CRT displays, featuring automatic song identification, album art display, and smooth text scrolling effects.

## Features

- **Real-time Music Recognition**: Continuously listens to ambient audio and identifies songs using the Shazam API
- **CRT Display Optimization**: 
  - Stretch mode ('s' key) for proper 4:3 display on CRT monitors
  - Fullscreen toggle ('f' key)
  - Smooth text scrolling for long titles
- **Visual Elements**:
  - Dynamic album art display
  - High-contrast text with outlines for better visibility
  - Smooth fade transitions
  - Auto-scrolling for long song titles

## Installation

The installation process will automatically detect your operating system and install all necessary dependencies:

```bash
# Clone the repository
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

This will automatically:
- Install system-specific dependencies (portaudio, pygame)
- Set up Python environment with uv package manager
- Create a virtual environment
- Install all required Python packages

## Raspberry Pi Setup Guide

### 1. Initial Raspberry Pi Setup
1. Download the latest Raspberry Pi OS (64-bit) from [Raspberry Pi's official website](https://www.raspberrypi.com/software/operating-systems/)
2. Flash the OS to your SD card using [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
3. Insert the SD card into your Raspberry Pi and connect:
   - Power supply
   - Keyboard and mouse
   - Display (CRT or other monitor)
   - Audio input device (USB microphone or similar)
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
   - Enable: SSH, Audio

3. Install required system packages:
   ```bash
   sudo apt install -y git python3-pip python3-venv portaudio19-dev

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
    - Press 'q' to quit the application

## Display Configuration

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

### Audio Issues
1. Check audio input:
   ```bash
   arecord -l
   ```
2. Test microphone:
   ```bash
   arecord -d 5 test.wav  # Records 5 seconds
   aplay test.wav         # Plays recording
   ```
3. Verify microphone permissions:
   ```bash
   sudo usermod -a -G audio $USER
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
