# Default recipe to run when just is called without arguments
default:
    @just --list

# Create a new virtual environment using uv
setup:
    uv venv
    @echo "Virtual environment created. Activate it with 'source .venv/bin/activate'"

# Install dependencies from pyproject.toml using uv
install:
    uv pip install .

# Run the application
run: 
    .venv/bin/python hello.py

# Run the application in debug mode (will exit after 30 seconds)
debug:
    .venv/bin/python hello.py --debug

# Clean up virtual environment and cache
clean:
    rm -rf .venv
    rm -rf __pycache__
    rm -rf *.pyc

# Install system dependencies for macOS
setup-mac:
    if ! command -v brew &> /dev/null; then
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    fi
    if ! brew list --versions portaudio &> /dev/null; then
        brew install portaudio
    fi

# Install system dependencies for Raspberry Pi
setup-pi:
    sudo apt-get update
    sudo apt-get install -y python3-pygame libportaudio2

# Combined setup for development
dev: setup install
    @echo "Development environment ready!"

# Update dependencies
update:
    uv pip install --upgrade .

# Add a new dependency to pyproject.toml
add *ARGS:
    uv add {{ARGS}}
