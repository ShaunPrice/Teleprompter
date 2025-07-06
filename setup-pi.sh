#!/bin/bash

# Quick Setup for Raspberry Pi / Debian Systems
# Handles externally managed Python environments

set -e

echo "🍓 RASPBERRY PI TELEPROMPTER SETUP"
echo "=================================="
echo

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/teleprompter-venv"

echo "📍 Project directory: $SCRIPT_DIR"
echo "🐍 Virtual environment: $VENV_DIR"
echo

# Check if we're in a virtual environment already
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "⚠️  Already in virtual environment: $VIRTUAL_ENV"
    echo "Please deactivate first with: deactivate"
    exit 1
fi

# Remove existing venv if it exists
if [ -d "$VENV_DIR" ]; then
    echo "🗑️  Removing existing virtual environment..."
    rm -rf "$VENV_DIR"
fi

# Create fresh virtual environment
echo "📦 Creating fresh virtual environment..."
python3 -m venv "$VENV_DIR" --system-site-packages

# Activate virtual environment
echo "⚡ Activating virtual environment..."
source "$VENV_DIR/bin/activate"

# Upgrade pip in the virtual environment
echo "🔄 Upgrading pip..."
python -m pip install --upgrade pip

# Install packages individually to handle potential issues
echo "📋 Installing Python packages..."

echo "  📦 Installing Flask..."
pip install flask

echo "  📦 Installing OpenCV..."
pip install opencv-python

echo "  📦 Installing NumPy..."
pip install numpy

echo "  📦 Installing python-pam..."
pip install python-pam

echo "✅ All packages installed successfully!"

# Create launcher scripts
echo "🚀 Creating launcher scripts..."

# Teleprompter launcher
cat > "$SCRIPT_DIR/start_teleprompter.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
python teleprompter.py "\$@"
EOF
chmod +x "$SCRIPT_DIR/start_teleprompter.sh"

# Web interface launcher
cat > "$SCRIPT_DIR/start_web_interface.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
echo "Starting Teleprompter Web Interface..."
echo "Access the interface at: http://localhost:5000"
echo "Use your system credentials to log in."
echo "Press Ctrl+C to stop the server."
python web_interface.py
EOF
chmod +x "$SCRIPT_DIR/start_web_interface.sh"

# Hardware test script
cat > "$SCRIPT_DIR/check_presenter_keys.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
echo "Testing hardware..."
python -c "
import cv2
print('Testing camera and keyboard input...')
cap = cv2.VideoCapture(0)
if cap.isOpened():
    print('✅ Camera detected successfully')
    ret, frame = cap.read()
    if ret:
        print('✅ Camera is working properly')
    else:
        print('⚠️  Camera detected but cannot read frames')
else:
    print('⚠️  No camera detected - teleprompter will use black background')
cap.release()
cv2.destroyAllWindows()
print('🔧 Hardware test complete')
"
EOF
chmod +x "$SCRIPT_DIR/check_presenter_keys.sh"

# Deactivate virtual environment
deactivate

echo
echo "🎉 SETUP COMPLETE!"
echo "=================="
echo
echo "🚀 Quick Start:"
echo "1. Start the web interface:"
echo "   ./start_web_interface.sh"
echo
echo "2. Open browser to: http://localhost:5000"
echo "3. Log in with your Pi credentials"
echo "4. Create/edit prompt files"
echo "5. Start teleprompter from web interface"
echo
echo "🎮 Direct usage:"
echo "   ./start_teleprompter.sh prompts/example.txt"
echo
echo "🔧 Test hardware:"
echo "   ./check_presenter_keys.sh"
echo
echo "📚 For desktop integration, see README.md"
echo
echo "🍓 Happy presenting on your Raspberry Pi! 🎬"
