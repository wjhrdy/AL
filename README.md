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

## Requirements

- Python 3.x
- PyAudio
- NumPy
- PyDub
- Pygame
- Requests
- ShazamIO

## Installation

1. Install system dependencies:

   **macOS**:
   ```bash
   just setup-mac
   ```

   **Raspberry Pi**:
   ```bash
   just setup-pi
   ```

2. Set up development environment:
   ```bash
   just dev
   ```
   This will create a virtual environment and install all required dependencies.

## Usage

1. Run the application:
```bash
just run
```

For debugging:
```bash
just debug
```

2. Controls:
- Press 'f' to toggle fullscreen mode
- Press 's' to toggle stretch mode (for 4:3 CRT displays)
- Press 'q' to quit the application

## Display Modes

### Normal Mode
- Default 16:9 display ratio
- Centered album art and text

### Stretch Mode (CRT)
- Optimized for 4:3 CRT displays
- Compensates for 1080p signal squeeze
- Maintains proper aspect ratios

## Technical Details

- Audio processing runs in a separate thread to prevent UI freezing
- Maintains 60 FPS with efficient sleep intervals
- Automatic text scaling and scrolling for long titles
- Smooth fade transitions for song information
- Error handling for audio device management

## Troubleshooting

If you encounter issues:

1. **No Audio Input**:
   - Check if your microphone is properly connected
   - Verify microphone permissions

2. **Display Issues**:
   - Toggle stretch mode ('s' key) for CRT displays
   - Adjust your monitor's display settings

## Development

- Clean environment: `just clean`
- Update dependencies: `just update`
- Add new dependency: `just add [package-name]`

## Contributing

Feel free to submit issues and enhancement requests!