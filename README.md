# 🎬 Professional Teleprompter System

A robust, feature-rich teleprompter application with web-based management interface, camera overlay, and professional presentation controls.

![Teleprompter Demo](https://img.shields.io/badge/Status-Production%20Ready-brightgreen)
![Python](https://img.shields.io/badge/Python-3.7%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)

## ✨ Features

### 🎯 Core Functionality
- **Smooth Text Scrolling**: Professional-grade scrolling with adjustable speed
- **Camera Overlay**: Live presenter view with customizable positioning
- **Keyboard Controls**: Real-time speed adjustment, pause/resume, manual scrolling
- **Full-Screen Display**: Distraction-free presentation mode
- **High-Quality Text Rendering**: Anti-aliased fonts with customizable sizes

### 🌐 Web Interface
- **Secure Authentication**: System-based login using PAM (Pluggable Authentication Modules)
- **File Management**: Create, edit, and delete prompt files through a modern web interface
- **Remote Control**: Start and stop teleprompter sessions remotely
- **Real-time Status**: Monitor teleprompter process status
- **Responsive Design**: Works on desktop, tablet, and mobile devices

### 🖥️ Desktop Integration
- **Application Shortcuts**: Native desktop entries for easy access
- **Autostart Support**: Optional automatic startup on system boot
- **Cross-Platform**: Works on Windows, Linux, and macOS
- **Launcher Scripts**: Easy-to-use startup scripts

## 🚀 Quick Start

### Prerequisites
- Python 3.7 or higher
- Webcam (optional, but recommended for presenter overlay)
- System with GUI support

### Installation

#### Option 1: Automatic Setup (Recommended)

**Linux/macOS:**
```bash
# Clone or download the project
git clone <repository-url>
cd teleprompter

# Run the setup script
chmod +x setup.sh
./setup.sh
```

**Raspberry Pi / Debian (if setup.sh fails):**
```bash
# Use the Pi-specific setup script
chmod +x setup-pi.sh
./setup-pi.sh
```

**Windows:**
```batch
# Clone or download the project
git clone <repository-url>
cd teleprompter

# Run the setup script
python setup.py
```

#### Option 2: Manual Setup

1. **Create Virtual Environment:**
   ```bash
   python -m venv teleprompter-venv
   
   # Activate (Linux/macOS)
   source teleprompter-venv/bin/activate
   
   # Activate (Windows)
   teleprompter-venv\Scripts\activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **System Dependencies (Linux only):**
   ```bash
   # Ubuntu/Debian
   sudo apt-get install python3-dev libpam0g-dev
   
   # Red Hat/CentOS/Fedora
   sudo yum install python3-devel pam-devel
   ```

## 📖 Usage

### Web Interface (Recommended)

1. **Start the Web Interface:**
   ```bash
   ./start_web_interface.sh          # Linux/macOS
   start_web_interface.bat           # Windows
   ```

2. **Access the Interface:**
   - Open your browser to: http://localhost:5000
   - Log in with your system credentials
   - Create or edit prompt files
   - Start the teleprompter with one click

### Direct Command Line

```bash
# Basic usage
./start_teleprompter.sh prompts/example.txt

# With options
python teleprompter.py prompts/my-script.txt --speed 3 --font-size 3

# Windowed mode (for testing)
python teleprompter.py prompts/example.txt --windowed

# Without camera overlay
python teleprompter.py prompts/example.txt --no-camera
```

### Keyboard Controls (During Presentation)

| Key | Action |
|-----|--------|
| `SPACE` | Pause/Resume scrolling |
| `↑` / `↓` | Increase/Decrease scroll speed |
| `←` / `→` | Manual scroll up/down |
| `R` | Reset to beginning |
| `ESC` / `Q` | Quit teleprompter |

## 🔧 Configuration

### Command Line Options

```
python teleprompter.py [OPTIONS] [FILE]

Options:
  -s, --speed INTEGER         Scroll speed (1-10, default: 2)
  -f, --font-size INTEGER     Font size scale (1-5, default: 2)
  --no-camera                 Disable camera overlay
  --windowed                  Run in windowed mode (not fullscreen)
  --camera-pos [top-left|top-right|bottom-left|bottom-right]
                             Camera overlay position (default: top-right)
```

### Web Interface Configuration

The web interface runs on `http://localhost:5000` by default. To change the port or bind address, modify `web_interface.py`:

```python
app.run(host='0.0.0.0', port=5000, debug=False)
```

## 🖥️ Desktop Integration

### Application Menu Integration

**Linux:**
```bash
# Copy desktop entries to applications directory
cp *.desktop ~/.local/share/applications/

# Update desktop database
update-desktop-database ~/.local/share/applications/
```

**macOS:**
```bash
# Desktop entries work similarly, or create aliases/shortcuts
```

**Windows:**
```batch
# Create shortcuts from the .bat files
# Right-click -> Create Shortcut
```

### Autostart Setup

**Linux (GNOME/KDE/XFCE):**
```bash
# Enable autostart of web interface
mkdir -p ~/.config/autostart
cp teleprompter-autostart.desktop ~/.config/autostart/
```

**macOS:**
```bash
# Add to Login Items in System Preferences
# Or use launchd for more control
```

**Windows:**
```batch
# Add to Startup folder
# Win+R -> shell:startup
# Copy start_web_interface.bat to the startup folder
```

## 📁 Project Structure

```
teleprompter/
├── teleprompter.py              # Main teleprompter application
├── web_interface.py             # Flask web interface
├── setup.py                     # Cross-platform setup script
├── setup.sh                     # Linux/macOS setup script
├── requirements.txt             # Python dependencies
├── README.md                    # This file
├── .gitignore                   # Git ignore rules
├── templates/                   # Web interface templates
│   ├── index.html              # Dashboard template
│   ├── edit.html               # File editor template
│   └── login.html              # Login template
├── prompts/                     # Prompt files directory
│   └── example.txt             # Example prompt file
├── teleprompter-venv/          # Virtual environment (created by setup)
├── *.desktop                   # Desktop entries (created by setup)
├── start_*.sh                  # Linux/macOS launcher scripts
├── start_*.bat                 # Windows launcher scripts
└── check_*.sh/bat              # Hardware test scripts
```

## 🔐 Security

### Authentication

The web interface uses **PAM (Pluggable Authentication Modules)** for secure authentication:

- Uses your system's existing user accounts
- No passwords stored by the application
- Same authentication as your system login
- Secure session management

### Network Security

- Web interface binds to localhost by default
- No external network access required
- Session-based authentication with secure cookies
- Input validation on all forms

### File Security

- Prompt files are stored locally in the `prompts/` directory
- No sensitive data transmitted over network
- File operations restricted to designated directories

## 🛠️ Troubleshooting

### Common Issues

**Camera Not Working:**
```bash
# Test camera access
./check_presenter_keys.sh        # Linux/macOS
check_presenter_keys.bat         # Windows

# Check camera permissions (Linux)
ls /dev/video*

# Check camera usage
lsof /dev/video0
```

**Web Interface Login Issues:**
```bash
# Check PAM configuration (Linux)
cat /etc/pam.d/login

# Test system authentication
su - yourusername
```

**Performance Issues:**
- Reduce font size for better performance
- Close other applications using the camera
- Lower scroll speed for smoother operation
- Use windowed mode for testing

**Package Installation Errors:**
```bash
# Linux: Install development headers
sudo apt-get install python3-dev libpam0g-dev

# macOS: Install Xcode command line tools
xcode-select --install

# Windows: Install Visual Studio Build Tools
```

**Raspberry Pi "externally-managed-environment" Error:**
```bash
# If setup.sh fails with externally managed environment error:
chmod +x setup-pi.sh
./setup-pi.sh

# Alternative: Manual setup with system packages
sudo apt install python3-flask python3-opencv python3-numpy python3-pam
```

### Hardware Requirements

**Minimum:**
- Python 3.7+
- 2GB RAM
- Basic graphics support
- Optional: USB webcam

**Recommended:**
- Python 3.8+
- 4GB RAM
- Dedicated graphics card
- HD webcam with good low-light performance
- External monitor for teleprompter display

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### Development Setup

1. Fork the repository
2. Create a virtual environment
3. Install development dependencies
4. Make your changes
5. Test thoroughly
6. Submit a pull request

### Coding Standards

- Follow PEP 8 style guidelines
- Add docstrings to functions and classes
- Include type hints where appropriate
- Write tests for new features
- Update documentation as needed

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- **OpenCV** for computer vision capabilities
- **Flask** for the web interface framework
- **python-pam** for system authentication
- **NumPy** for efficient array operations

## 📞 Support

If you encounter any issues or have questions:

1. Check the troubleshooting section above
2. Run the hardware test script: `./check_presenter_keys.sh`
3. Check the project issues on the repository
4. Create a new issue with detailed information about your problem

---

**Happy Presenting! 🎬**

*Professional teleprompter solution for content creators, broadcasters, and public speakers.*
