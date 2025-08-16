#!/bin/bash

# Teleprompter Setup Script for Linux/macOS
# This script sets up the teleprompter application with all dependencies

set -e  # Exit on any error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Emoji support
CHECK="✅"
CROSS="❌"
ARROW="➡️"
PACKAGE="📦"
ROCKET="🚀"
COMPUTER="🖥️"
WARNING="⚠️"

# Function to print colored output
print_status() {
    echo -e "${GREEN}${CHECK}${NC} $1"
}

print_error() {
    echo -e "${RED}${CROSS}${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}${WARNING}${NC} $1"
}

print_info() {
    echo -e "${BLUE}${ARROW}${NC} $1"
}

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/teleprompter-venv"

echo "🎬 TELEPROMPTER SETUP"
echo "===================="
echo
print_info "Project directory: $SCRIPT_DIR"
print_info "Virtual environment: $VENV_DIR"
echo

# Check Python version
print_info "Checking Python version..."
if command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
    PYTHON_MAJOR=$(echo $PYTHON_VERSION | cut -d. -f1)
    PYTHON_MINOR=$(echo $PYTHON_VERSION | cut -d. -f2)
    
    if [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -ge 7 ]; then
        print_status "Python $PYTHON_VERSION detected"
        PYTHON_CMD="python3"
    else
        print_error "Python 3.7 or higher is required. Found: $PYTHON_VERSION"
        exit 1
    fi
else
    print_error "Python 3 not found. Please install Python 3.7 or higher."
    exit 1
fi

# Detect OS and install system dependencies
print_info "Checking system dependencies..."
OS="$(uname -s)"
case "${OS}" in
    Linux*)
        print_info "Linux detected"
        
        # Check for required packages
    REQUIRED_PACKAGES=("python3-dev" "python3-venv" "python3-pip" "gphoto2" "libgphoto2-dev" "usbutils" "lsof")
        MISSING_PACKAGES=()
        
        if command -v apt-get &> /dev/null; then
            # Debian/Ubuntu
            for package in "${REQUIRED_PACKAGES[@]}"; do
                if ! dpkg -l "$package" &> /dev/null; then
                    MISSING_PACKAGES+=("$package")
                fi
            done
            
            # Check for PAM development files
            if ! dpkg -l "libpam0g-dev" &> /dev/null; then MISSING_PACKAGES+=("libpam0g-dev"); fi
            # Add user to plugdev if group exists
            if getent group plugdev > /dev/null 2>&1; then
                if ! id -nG "$USER" | grep -qw plugdev; then
                    print_info "Adding $USER to plugdev group for camera access"
                    sudo usermod -aG plugdev "$USER"
                    print_warning "You may need to log out/in for plugdev group to apply"
                fi
            fi
            
            if [ ${#MISSING_PACKAGES[@]} -ne 0 ]; then
                print_warning "Missing system packages: ${MISSING_PACKAGES[*]}"
                print_info "Installing system packages..."
                sudo apt-get update
                sudo apt-get install -y "${MISSING_PACKAGES[@]}"
                print_status "System packages installed"
            fi
            
        elif command -v yum &> /dev/null; then
            # Red Hat/CentOS/Fedora
            REQUIRED_PACKAGES=("python3-devel" "python3-pip")
            for package in "${REQUIRED_PACKAGES[@]}"; do
                if ! rpm -q "$package" &> /dev/null; then
                    MISSING_PACKAGES+=("$package")
                fi
            done
            
            if ! rpm -q "pam-devel" &> /dev/null; then
                MISSING_PACKAGES+=("pam-devel")
            fi
            
            if [ ${#MISSING_PACKAGES[@]} -ne 0 ]; then
                print_warning "Missing system packages: ${MISSING_PACKAGES[*]}"
                print_info "Installing system packages..."
                sudo yum install -y "${MISSING_PACKAGES[@]}"
                print_status "System packages installed"
            fi
        else
            print_warning "Unknown package manager. Please ensure python3-dev and libpam-dev are installed."
        fi
        ;;
    Darwin*)
        print_info "macOS detected"
        
        # Check for Homebrew
        if ! command -v brew &> /dev/null; then
            print_warning "Homebrew not found. Some features may not work correctly."
            print_info "Install Homebrew from: https://brew.sh/"
        else
            print_status "Homebrew detected"
        fi
        ;;
    *)
        print_warning "Unknown OS: ${OS}. Proceeding with basic setup..."
        ;;
esac

print_status "System dependencies OK"

# Create virtual environment
print_info "Creating virtual environment..."
if [ -d "$VENV_DIR" ]; then
    print_warning "Virtual environment already exists at $VENV_DIR"
    print_info "Removing existing virtual environment..."
    rm -rf "$VENV_DIR"
fi

# Create fresh virtual environment
$PYTHON_CMD -m venv "$VENV_DIR"
print_status "Virtual environment created"

# Activate virtual environment
source "$VENV_DIR/bin/activate"

# Upgrade pip
print_info "Upgrading pip..."
pip install --upgrade pip

# Install requirements
print_info "Installing Python packages..."
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    # Try normal install first, fallback to --break-system-packages if needed
    if ! pip install -r "$SCRIPT_DIR/requirements.txt" 2>/dev/null; then
        print_warning "Standard install failed, trying with --break-system-packages..."
        pip install --break-system-packages -r "$SCRIPT_DIR/requirements.txt"
    fi
    print_status "Python packages installed"
else
    print_error "requirements.txt not found"
    exit 1
fi

# Create launcher scripts
print_info "Creating launcher scripts..."

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

# Presenter keys check script
cat > "$SCRIPT_DIR/check_presenter_keys.sh" << EOF
#!/bin/bash
cd "$SCRIPT_DIR"
source "$VENV_DIR/bin/activate"
python -c "
import cv2
print('Testing camera and keyboard input...')
print('Press any key to test, ESC to exit')
cap = cv2.VideoCapture(0)
if cap.isOpened():
    print('Camera detected successfully')
    ret, frame = cap.read()
    if ret:
        print('Camera is working properly')
    else:
        print('Camera detected but cannot read frames')
else:
    print('No camera detected - teleprompter will use black background')
cap.release()
cv2.destroyAllWindows()
print('Test complete')
"
EOF
chmod +x "$SCRIPT_DIR/check_presenter_keys.sh"

print_status "Launcher scripts created"

# Create desktop entries
print_info "Creating desktop entries..."

# Main teleprompter desktop entry
cat > "$SCRIPT_DIR/teleprompter.desktop" << EOF
[Desktop Entry]
Name=Teleprompter
Comment=Professional teleprompter application
Exec="$VENV_DIR/bin/python" "$SCRIPT_DIR/teleprompter.py"
Icon=video-display
Terminal=false
Type=Application
Categories=AudioVideo;Video;
Path=$SCRIPT_DIR
EOF
chmod +x "$SCRIPT_DIR/teleprompter.desktop"

# Web interface desktop entry
cat > "$SCRIPT_DIR/teleprompter-web.desktop" << EOF
[Desktop Entry]
Name=Teleprompter Web Interface
Comment=Web interface for managing teleprompter files
Exec="$VENV_DIR/bin/python" "$SCRIPT_DIR/web_interface.py"
Icon=applications-internet
Terminal=true
Type=Application
Categories=Network;WebBrowser;
Path=$SCRIPT_DIR
EOF
chmod +x "$SCRIPT_DIR/teleprompter-web.desktop"

print_status "Desktop entries created"

# Create autostart entry
print_info "Creating autostart configuration..."
cat > "$SCRIPT_DIR/teleprompter-autostart.desktop" << EOF
[Desktop Entry]
Name=Teleprompter Web Interface
Comment=Start teleprompter web interface on login
Exec="$VENV_DIR/bin/python" "$SCRIPT_DIR/web_interface.py"
Icon=applications-internet
Terminal=false
Type=Application
Categories=Network;
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Path=$SCRIPT_DIR
EOF
chmod +x "$SCRIPT_DIR/teleprompter-autostart.desktop"

print_status "Autostart configuration created"

# Deactivate virtual environment
deactivate

echo
echo "=========================================="
print_status "TELEPROMPTER SETUP COMPLETE!"
echo "=========================================="
echo
print_info "Project location: $SCRIPT_DIR"
print_info "Virtual environment: $VENV_DIR"
echo
print_info "${ROCKET} Quick Start:"
echo "1. Start the web interface:"
echo "   ./start_web_interface.sh"
echo
echo "2. Open your browser to: http://localhost:5000"
echo "3. Log in with your system credentials"
echo "4. Create or edit prompt files"
echo "5. Start the teleprompter from the web interface"
echo
print_info "${COMPUTER} Desktop Integration:"
echo "   Copy .desktop files to ~/.local/share/applications/"
echo "   cp *.desktop ~/.local/share/applications/"
echo
echo "   Enable autostart (optional):"
echo "   mkdir -p ~/.config/autostart"
echo "   cp teleprompter-autostart.desktop ~/.config/autostart/"
echo
print_info "🎮 Direct teleprompter usage:"
echo "   ./start_teleprompter.sh prompts/example.txt"
echo
print_info "🔧 Test your setup:"
echo "   ./check_presenter_keys.sh"
echo
print_info "${PACKAGE} Troubleshooting:"
echo "   - Ensure your camera is not being used by other applications"
echo "   - Check system permissions for camera access"
echo "   - Run the check_presenter_keys.sh script to test hardware"
echo
echo "=========================================="
print_status "Happy presenting! 🎬"
echo "=========================================="
