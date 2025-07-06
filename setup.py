#!/usr/bin/env python3
"""
Teleprompter Setup Script
Cross-platform setup script for the Teleprompter application.
"""

import os
import sys
import subprocess
import platform
from pathlib import Path
import venv
import shutil

def run_command(cmd, shell=False, check=True):
    """Run a command and return the result."""
    try:
        if isinstance(cmd, str):
            cmd = cmd.split() if not shell else cmd
        result = subprocess.run(cmd, shell=shell, check=check, 
                              capture_output=True, text=True)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.CalledProcessError as e:
        return False, e.stdout, e.stderr
    except Exception as e:
        return False, "", str(e)

def check_python_version():
    """Check if Python version is compatible."""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 7):
        print("❌ Python 3.7 or higher is required.")
        print(f"   Current version: {version.major}.{version.minor}.{version.micro}")
        return False
    print(f"✅ Python {version.major}.{version.minor}.{version.micro} detected")
    return True

def create_virtual_environment(venv_path):
    """Create a virtual environment."""
    print("📦 Creating virtual environment...")
    
    if venv_path.exists():
        print(f"   Virtual environment already exists at {venv_path}")
        return True
    
    try:
        venv.create(venv_path, with_pip=True)
        print(f"✅ Virtual environment created at {venv_path}")
        return True
    except Exception as e:
        print(f"❌ Failed to create virtual environment: {e}")
        return False

def get_venv_python(venv_path):
    """Get the Python executable path in the virtual environment."""
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "python.exe"
    else:
        return venv_path / "bin" / "python"

def get_venv_pip(venv_path):
    """Get the pip executable path in the virtual environment."""
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "pip.exe"
    else:
        return venv_path / "bin" / "pip"

def install_requirements(venv_path, project_path):
    """Install Python requirements in the virtual environment."""
    print("📋 Installing Python packages...")
    
    pip_exe = get_venv_pip(venv_path)
    requirements_file = project_path / "requirements.txt"
    
    if not requirements_file.exists():
        print("❌ requirements.txt not found")
        return False
    
    # Upgrade pip first
    success, stdout, stderr = run_command([str(pip_exe), "install", "--upgrade", "pip"])
    if not success:
        print(f"⚠️  Warning: Could not upgrade pip: {stderr}")
    
    # Install requirements - try normal install first
    success, stdout, stderr = run_command([str(pip_exe), "install", "-r", str(requirements_file)])
    
    # If that fails and we're on Linux, try with --break-system-packages
    if not success and platform.system().lower() == "linux":
        print("⚠️  Standard install failed, trying with --break-system-packages...")
        success, stdout, stderr = run_command([str(pip_exe), "install", "--break-system-packages", "-r", str(requirements_file)])
    
    if success:
        print("✅ Python packages installed successfully")
        return True
    else:
        print(f"❌ Failed to install packages: {stderr}")
        return False

def install_system_dependencies():
    """Install system dependencies if needed."""
    print("🔧 Checking system dependencies...")
    
    system = platform.system().lower()
    
    if system == "linux":
        # Check for required system packages
        required_packages = ["python3-dev", "libpam0g-dev"]
        missing_packages = []
        
        for package in required_packages:
            success, _, _ = run_command(f"dpkg -l {package}", shell=True, check=False)
            if not success:
                missing_packages.append(package)
        
        if missing_packages:
            print(f"   Missing system packages: {', '.join(missing_packages)}")
            print("   Please install them with:")
            print(f"   sudo apt-get install {' '.join(missing_packages)}")
            return False
    
    elif system == "darwin":  # macOS
        # Check if homebrew is available
        success, _, _ = run_command("which brew", shell=True, check=False)
        if not success:
            print("   Homebrew not found. Some features may not work correctly.")
            print("   Install Homebrew from: https://brew.sh/")
    
    print("✅ System dependencies OK")
    return True

def create_launcher_scripts(project_path, venv_path):
    """Create launcher scripts for different platforms."""
    print("🚀 Creating launcher scripts...")
    
    python_exe = get_venv_python(venv_path)
    system = platform.system().lower()
    
    scripts_created = []
    
    if system in ["linux", "darwin"]:
        # Create bash scripts
        scripts = {
            "start_teleprompter.sh": f"""#!/bin/bash
cd "{project_path}"
"{python_exe}" teleprompter.py "$@"
""",
            "start_web_interface.sh": f"""#!/bin/bash
cd "{project_path}"
echo "Starting Teleprompter Web Interface..."
echo "Access the interface at: http://localhost:5000"
echo "Use your system credentials to log in."
echo "Press Ctrl+C to stop the server."
"{python_exe}" web_interface.py
""",
            "check_presenter_keys.sh": f"""#!/bin/bash
# Check if presenter keys are working
cd "{project_path}"
"{python_exe}" -c "
import cv2
print('Testing camera and keyboard input...')
print('Press any key to test, ESC to exit')
cap = cv2.VideoCapture(0)
if cap.isOpened():
    print('Camera detected successfully')
else:
    print('No camera detected - teleprompter will use black background')
cap.release()
cv2.destroyAllWindows()
"
"""
        }
        
        for script_name, content in scripts.items():
            script_path = project_path / script_name
            with open(script_path, 'w') as f:
                f.write(content)
            script_path.chmod(0o755)  # Make executable
            scripts_created.append(script_name)
    
    elif system == "windows":
        # Create batch scripts
        scripts = {
            "start_teleprompter.bat": f"""@echo off
cd /d "{project_path}"
"{python_exe}" teleprompter.py %*
pause
""",
            "start_web_interface.bat": f"""@echo off
cd /d "{project_path}"
echo Starting Teleprompter Web Interface...
echo Access the interface at: http://localhost:5000
echo Use your system credentials to log in.
echo Press Ctrl+C to stop the server.
"{python_exe}" web_interface.py
pause
""",
            "check_presenter_keys.bat": f"""@echo off
cd /d "{project_path}"
"{python_exe}" -c "
import cv2
print('Testing camera and keyboard input...')
print('Press any key to test, ESC to exit')
cap = cv2.VideoCapture(0)
if cap.isOpened():
    print('Camera detected successfully')
else:
    print('No camera detected - teleprompter will use black background')
cap.release()
cv2.destroyAllWindows()
"
pause
"""
        }
        
        for script_name, content in scripts.items():
            script_path = project_path / script_name
            with open(script_path, 'w') as f:
                f.write(content)
            scripts_created.append(script_name)
    
    print(f"✅ Created launcher scripts: {', '.join(scripts_created)}")
    return True

def create_desktop_entries(project_path, venv_path):
    """Create desktop entries (Linux/macOS)."""
    if platform.system().lower() not in ["linux", "darwin"]:
        return True
    
    print("🖥️  Creating desktop entries...")
    
    python_exe = get_venv_python(venv_path)
    
    # Desktop entries
    desktop_entries = {
        "teleprompter.desktop": f"""[Desktop Entry]
Name=Teleprompter
Comment=Professional teleprompter application
Exec="{python_exe}" "{project_path}/teleprompter.py"
Icon=video-display
Terminal=false
Type=Application
Categories=AudioVideo;Video;
""",
        "teleprompter-web.desktop": f"""[Desktop Entry]
Name=Teleprompter Web Interface
Comment=Web interface for managing teleprompter files
Exec="{python_exe}" "{project_path}/web_interface.py"
Icon=applications-internet
Terminal=true
Type=Application
Categories=Network;WebBrowser;
"""
    }
    
    # Create desktop files in project directory (user can copy to applications)
    desktop_files_created = []
    for filename, content in desktop_entries.items():
        desktop_path = project_path / filename
        with open(desktop_path, 'w') as f:
            f.write(content)
        desktop_path.chmod(0o755)
        desktop_files_created.append(filename)
    
    print(f"✅ Created desktop entries: {', '.join(desktop_files_created)}")
    print("   Copy these .desktop files to ~/.local/share/applications/ to add to your application menu")
    
    return True

def setup_autostart(project_path, venv_path):
    """Set up autostart configuration."""
    if platform.system().lower() not in ["linux", "darwin"]:
        print("⚠️  Autostart setup is only supported on Linux and macOS")
        return True
    
    print("⚡ Creating autostart configuration...")
    
    python_exe = get_venv_python(venv_path)
    
    autostart_content = f"""[Desktop Entry]
Name=Teleprompter Web Interface
Comment=Start teleprompter web interface on login
Exec="{python_exe}" "{project_path}/web_interface.py"
Icon=applications-internet
Terminal=false
Type=Application
Categories=Network;
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
"""
    
    autostart_file = project_path / "teleprompter-autostart.desktop"
    with open(autostart_file, 'w') as f:
        f.write(autostart_content)
    autostart_file.chmod(0o755)
    
    print("✅ Created autostart configuration file: teleprompter-autostart.desktop")
    print("   Copy this file to ~/.config/autostart/ to enable autostart")
    
    return True

def print_final_instructions(project_path, venv_path):
    """Print final setup instructions."""
    print("\n" + "="*60)
    print("🎉 TELEPROMPTER SETUP COMPLETE!")
    print("="*60)
    
    print(f"\n📁 Project location: {project_path}")
    print(f"🐍 Virtual environment: {venv_path}")
    
    print("\n🚀 Quick Start:")
    print("1. Start the web interface:")
    
    system = platform.system().lower()
    if system in ["linux", "darwin"]:
        print(f"   ./start_web_interface.sh")
        print("   OR")
        print(f"   {get_venv_python(venv_path)} web_interface.py")
    else:
        print(f"   start_web_interface.bat")
        print("   OR")
        print(f"   {get_venv_python(venv_path)} web_interface.py")
    
    print("\n2. Open your browser to: http://localhost:5000")
    print("3. Log in with your system credentials")
    print("4. Create or edit prompt files")
    print("5. Start the teleprompter from the web interface")
    
    print("\n🎮 Direct teleprompter usage:")
    if system in ["linux", "darwin"]:
        print(f"   ./start_teleprompter.sh prompts/example.txt")
    else:
        print(f"   start_teleprompter.bat prompts\\example.txt")
    
    print("\n🖥️  Desktop Integration:")
    if system in ["linux", "darwin"]:
        print("   Copy .desktop files to ~/.local/share/applications/")
        print("   Copy teleprompter-autostart.desktop to ~/.config/autostart/")
    else:
        print("   Use the .bat files or create shortcuts as needed")
    
    print("\n📚 Documentation:")
    print("   - README.md contains detailed usage instructions")
    print("   - Edit prompts/ directory for your presentation files")
    print("   - Use the web interface for easy file management")
    
    print("\n⚡ Troubleshooting:")
    print("   - Ensure your camera is not being used by other applications")
    print("   - Check system permissions for camera and microphone access")
    print("   - Run check_presenter_keys script to test hardware")
    
    print("\n" + "="*60)

def main():
    """Main setup function."""
    print("🎬 TELEPROMPTER SETUP")
    print("=" * 40)
    
    # Get project directory
    project_path = Path(__file__).parent.absolute()
    venv_path = project_path / "teleprompter-venv"
    
    print(f"📍 Project directory: {project_path}")
    print(f"🐍 Target virtual environment: {venv_path}")
    print()
    
    # Check Python version
    if not check_python_version():
        sys.exit(1)
    
    # Install system dependencies
    if not install_system_dependencies():
        print("⚠️  Please install required system dependencies before continuing.")
        response = input("Continue anyway? (y/N): ").lower()
        if response != 'y':
            sys.exit(1)
    
    # Create virtual environment
    if not create_virtual_environment(venv_path):
        sys.exit(1)
    
    # Install Python requirements
    if not install_requirements(venv_path, project_path):
        sys.exit(1)
    
    # Create launcher scripts
    if not create_launcher_scripts(project_path, venv_path):
        sys.exit(1)
    
    # Create desktop entries
    if not create_desktop_entries(project_path, venv_path):
        sys.exit(1)
    
    # Setup autostart
    if not setup_autostart(project_path, venv_path):
        sys.exit(1)
    
    # Print final instructions
    print_final_instructions(project_path, venv_path)

if __name__ == "__main__":
    main()
