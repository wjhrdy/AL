# Default recipe to run when just is called without arguments
default:
    @just --list

# Load environment variables from .env file if it exists
set dotenv-load

# Install all dependencies and set up the environment
install:
    #!/usr/bin/env bash
    set -euo pipefail
    
    # Read Python version from .python-version
    PYTHON_VERSION=$(cat .python-version)
    echo "Using Python version: $PYTHON_VERSION"
    
    # Detect OS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "Installing dependencies for macOS..."
        # Install Homebrew if not present
        if ! command -v brew &> /dev/null; then
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        fi
        # Install Python if not present or wrong version
        if ! command -v python$PYTHON_VERSION &> /dev/null; then
            brew install python@$PYTHON_VERSION
            brew link python@$PYTHON_VERSION
        fi
        # Install system dependencies
        if ! brew list --versions portaudio &> /dev/null; then
            brew install portaudio
        fi
    elif [[ "$OSTYPE" == "linux"* ]]; then
        echo "Installing dependencies for Raspberry Pi..."
        sudo apt-get update
        # Install Python with specific version
        sudo apt-get install -y python$PYTHON_VERSION python$PYTHON_VERSION-venv
        # Install audio dependencies including those needed for PyAudio
        sudo apt-get install -y python3-pygame libportaudio2 portaudio19-dev python3-dev gcc
    else
        echo "Unsupported operating system: $OSTYPE"
        exit 1
    fi
    
    # Install uv if not present
    if ! command -v uv &> /dev/null; then
        echo "Installing uv package manager..."
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # Ensure uv is in PATH for the rest of the script
        export PATH="$HOME/.local/bin:$PATH"
        hash -r
        # Verify uv is now available
        if ! command -v uv &> /dev/null; then
            echo "Error: uv installation successful but command not found in PATH"
            echo "Please run: export PATH=\"\$HOME/.local/bin:\$PATH\""
            exit 1
        fi
    fi
    
    # Create virtual environment and install dependencies
    echo "Setting up Python environment..."
    uv venv --python python$PYTHON_VERSION
    source .venv/bin/activate
    
    # Try to install shazamio normally first
    echo "Attempting to install shazamio and dependencies..."
    if ! uv pip install "shazamio==0.7.0" "pydub>=0.25.1" 2>/dev/null; then
        echo "No pre-built wheel available for shazamio, installing from source..."
        # Install build dependencies
        if [[ "$OSTYPE" == "darwin"* ]]; then
            # Install pipx if not present
            if ! command -v pipx &> /dev/null; then
                brew install pipx
            fi
            # Install maturin using pipx if not present
            if ! command -v maturin &> /dev/null; then
                pipx install maturin
            fi
        elif [[ "$OSTYPE" == "linux"* ]]; then
            # Install build essentials for Rust compilation
            sudo apt-get install -y build-essential
            # Install pipx and maturin
            sudo apt-get install -y pipx
            if ! command -v maturin &> /dev/null; then
                pipx install maturin
            fi
        fi
        
        # Install Rust if not present
        if ! command -v rustc &> /dev/null; then
            echo "Installing Rust toolchain..."
            curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
            source "$HOME/.cargo/env"
        fi
        
        # Install shazamio-core from source
        echo "Installing shazamio-core from source..."
        TEMP_DIR=$(mktemp -d)
        git clone https://github.com/shazamio/shazamio-core.git "$TEMP_DIR"
        cd "$TEMP_DIR"
        git switch --detach 1.0.7
        uv pip install .
        cd -
        rm -rf "$TEMP_DIR"
    fi
    
    # Install remaining dependencies
    echo "Installing remaining dependencies..."
    uv pip install .
    
    echo "Installation complete! 🎉"

# Helper recipe to select audio device
select-device:
    #!/usr/bin/env bash
    set -euo pipefail
    # If AL_DEVICE is set, use that value
    if [ "${AL_DEVICE:-}" != "" ]; then
        printf "%s" "$AL_DEVICE"
        exit 0
    fi
    # Otherwise, prompt for device selection
    .venv/bin/python hello.py --list-devices >/dev/tty
    read -p "Select input device (number): " DEVICE_NUMBER </dev/tty
    # Validate input is a number
    if [[ "$DEVICE_NUMBER" =~ ^[0-9]+$ ]]; then
        printf "%d" "$DEVICE_NUMBER"
    else
        echo "Invalid input. Please enter a number." >&2
        exit 1
    fi

# Run the application
run: 
    #!/usr/bin/env bash
    set -euo pipefail
    DEVICE=$(just select-device) || exit 1
    .venv/bin/python hello.py --device "$DEVICE"

# Run the application in debug mode
debug:
    #!/usr/bin/env bash
    set -euo pipefail
    DEVICE=$(just select-device) || exit 1
    .venv/bin/python hello.py --debug --device "$DEVICE"

# Clean up virtual environment and cache
clean:
    rm -rf .venv
    rm -rf __pycache__
    rm -rf *.pyc

# Clean up old pipx installation
clean-pipx:
    #!/usr/bin/env bash
    set -euo pipefail
    echo "Cleaning up pipx installations..."
    # Remove pipx binary and its directory
    rm -rf ~/.local/pipx
    rm -rf ~/Library/Python/3.9/bin/pipx
    # Remove pipx from brew if installed
    if brew list --versions pipx &> /dev/null; then
        brew uninstall pipx
    fi
    echo "Cleaned up pipx installations"

# Update dependencies
update:
    uv pip install --upgrade .

# Add a new dependency to pyproject.toml
add *ARGS:
    uv add {{ARGS}}

# Enable autostart on Raspberry Pi
enable-autostart:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$OSTYPE" != "linux"* ]]; then
        echo "Autostart is only supported on Raspberry Pi"
        exit 1
    fi
    
    # Select device and store it in .env if not already set
    if [ "${AL_DEVICE:-}" = "" ]; then
        DEVICE=$(just select-device) || exit 1
        echo "AL_DEVICE=$DEVICE" > .env
        echo "Stored device selection in .env file"
    fi
    
    # Store the current path
    CURRENT_PATH=$(pwd)
    
    echo "Creating systemd service file..."
    sudo tee /etc/systemd/system/al.service > /dev/null << EOL
    [Unit]
    Description=AL Music Recognition
    After=network.target sound.target

    [Service]
    Type=simple
    User=${USER}
    WorkingDirectory=${CURRENT_PATH}
    EnvironmentFile=${CURRENT_PATH}/.env
    ExecStart=${CURRENT_PATH}/.venv/bin/python hello.py --device \${AL_DEVICE}
    Restart=always
    RestartSec=10
    StandardOutput=journal
    StandardError=journal

    [Install]
    WantedBy=multi-user.target
    EOL
    
    echo "Setting permissions..."
    sudo chmod 644 /etc/systemd/system/al.service
    echo "Enabling and starting service..."
    sudo systemctl enable al.service
    sudo systemctl start al.service
    echo "Autostart enabled! Check status with: sudo systemctl status al.service"

# Disable autostart on Raspberry Pi
disable-autostart:
    #!/usr/bin/env bash
    set -euo pipefail
    if [[ "$OSTYPE" != "linux"* ]]; then
        echo "Autostart is only supported on Raspberry Pi"
        exit 1
    fi
    sudo systemctl disable al.service
    sudo systemctl stop al.service
    sudo rm /etc/systemd/system/al.service
    echo "Autostart disabled!"
