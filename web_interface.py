#!/usr/bin/env python3
"""
Teleprompter Web Interface
A Flask-based web interface for managing prompt files and controlling the teleprompter.
"""

import os
import sys
import subprocess
import signal
import platform
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import json

# Import pam only on non-Windows systems
if platform.system() != "Windows":
    try:
        import pam  # type: ignore
        PAM_AVAILABLE = True
        print("[OK] PAM authentication available")
    except Exception as e:
        PAM_AVAILABLE = False
        print(f"[WARNING] PAM not available ({e}). Authentication will be simplified.")
else:
    PAM_AVAILABLE = False
    print("[INFO] Running on Windows - Authentication disabled for development.")

app = Flask(__name__)
app.secret_key = "teleprompter_dev_secret"  # Use a fixed string for development

# Add datetime filter for templates
@app.template_filter('datetime')
def datetime_filter(timestamp):
    """Convert timestamp to readable datetime string."""
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime('%Y-%m-%d %H:%M:%S')
    except (ValueError, TypeError):
        return 'Unknown'

# Configuration
PROMPTS_DIR = Path(__file__).parent / "prompts"
TELEPROMPTER_SCRIPT = Path(__file__).parent / "teleprompter.py"
VENV_PATH = Path(__file__).parent / "teleprompter-venv"
RUNTIME_STATE = Path(__file__).parent / "runtime_state.json"

# Global variable to track teleprompter process
teleprompter_process = None

# UI toggle state defaults
focus_on = True
flip_video = False
presenter_profile = "auto"  # auto | generic | logitech_r800
SUPPORTED_PRESENTER_PROFILES = ["auto", "generic", "logitech_r800"]

def load_runtime_state():
    global focus_on, flip_video, presenter_profile
    try:
        if RUNTIME_STATE.exists():
            with open(RUNTIME_STATE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                focus_on = bool(data.get('focus_on', focus_on))
                flip_video = bool(data.get('flip_video', flip_video))
                presenter_profile = str(data.get('presenter_profile', presenter_profile))
    except Exception as e:
        print(f"[WARN] Failed to load runtime state: {e}")

def save_runtime_state():
    try:
        with open(RUNTIME_STATE, 'w', encoding='utf-8') as f:
            json.dump({'focus_on': focus_on, 'flip_video': flip_video, 'presenter_profile': presenter_profile}, f)
    except Exception as e:
        print(f"[WARN] Failed to save runtime state: {e}")

def authenticate_user(username, password):
    """Authenticate user using PAM (simplified fallback if PAM unavailable)."""
    if not PAM_AVAILABLE:
        # Simplified authentication: just check for non-empty credentials
        return username and password
    
    try:
        p = pam.pam()
        return p.authenticate(username, password)
    except Exception as e:
        print(f"PAM authentication error: {e}")
        # Fallback to simplified auth if PAM fails
        print("Falling back to simplified authentication")
        return username and password

def require_login(f):
    """Decorator to require login for routes."""
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

def get_python_executable():
    """Get the Python executable from the virtual environment."""
    if os.name == 'nt':  # Windows
        venv_python = VENV_PATH / "Scripts" / "python.exe"
        if venv_python.exists():
            return venv_python
        else:
            # Fallback to system python
            return "python"
    else:  # Linux/macOS
        venv_python = VENV_PATH / "bin" / "python"
        if venv_python.exists():
            return venv_python
        else:
            # Fallback to system python
            return "python3"

@app.route('/')
@require_login
def index():
    """Main dashboard."""
    # Ensure prompts directory exists
    PROMPTS_DIR.mkdir(exist_ok=True)
    load_runtime_state()
    
    # Get list of prompt files (just filenames for template compatibility)
    prompt_files = []
    for file_path in PROMPTS_DIR.glob("*.txt"):
        prompt_files.append(file_path.name)
    
    # Sort files alphabetically
    prompt_files.sort()
    
    # Check if teleprompter is running
    global teleprompter_process
    teleprompter_running = teleprompter_process and teleprompter_process.poll() is None
    
    return render_template('index.html', 
                         prompt_files=prompt_files, 
                         teleprompter_running=teleprompter_running,
                         focus_on=focus_on,
                         flip_video=flip_video,
                         presenter_profile=presenter_profile,
                         supported_profiles=SUPPORTED_PRESENTER_PROFILES)

@app.route('/login', methods=['GET', 'POST'])
def login():
    """Login page."""
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        if authenticate_user(username, password):
            session['logged_in'] = True
            session['username'] = username
            session['pam_available'] = PAM_AVAILABLE
            flash('Login successful!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Invalid username or password.', 'error')
    
    return render_template('login.html', pam_available=PAM_AVAILABLE)

@app.route('/logout')
def logout():
    """Logout and clear session."""
    session.clear()
    flash('You have been logged out.', 'info')
    return redirect(url_for('login'))

@app.route('/edit/<filename>')
@app.route('/edit')
@require_login
def edit_file(filename=None):
    """Edit a prompt file."""
    content = ""
    if filename:
        file_path = PROMPTS_DIR / filename
        if file_path.exists():
            try:
                # Try UTF-8 first
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                try:
                    # Fallback to Windows-1252 (common on Windows)
                    with open(file_path, 'r', encoding='cp1252') as f:
                        content = f.read()
                    flash('File was not UTF-8, loaded with Windows-1252 encoding.', 'warning')
                except Exception as e:
                    flash(f'Error reading file: {e}', 'error')
                    return redirect(url_for('index'))
            except Exception as e:
                flash(f'Error reading file: {e}', 'error')
                return redirect(url_for('index'))
        # If file does not exist, do NOT flash an error—just open the editor with empty content
    return render_template('edit.html', filename=filename, content=content)

@app.route('/save', methods=['POST'])
@require_login
def save_file():
    """Save a prompt file."""
    filename = request.form['filename']
    content = request.form['content']
    
    if not filename:
        flash('Filename is required.', 'error')
        return redirect(url_for('edit_file'))
    
    # Ensure filename has .txt extension
    if not filename.endswith('.txt'):
        filename += '.txt'
    
    # Validate filename (no path traversal)
    if '/' in filename or '\\' in filename or '..' in filename:
        flash('Invalid filename.', 'error')
        return redirect(url_for('edit_file'))
    
    file_path = PROMPTS_DIR / filename
    
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        flash(f'File {filename} saved successfully.', 'success')
    except Exception as e:
        flash(f'Error saving file: {e}', 'error')
    
    return redirect(url_for('index'))

@app.route('/create_file', methods=['POST'])
@require_login
def create_file():
    """Create a new prompt file by redirecting to edit page."""
    filename = request.form.get('filename', '').strip()
    
    if not filename:
        flash('Filename is required.', 'error')
        return redirect(url_for('index'))
    
    # Validate filename (no path traversal, only safe characters)
    if '/' in filename or '\\' in filename or '..' in filename:
        flash('Invalid filename.', 'error')
        return redirect(url_for('index'))
    
    # Ensure filename has .txt extension
    if not filename.endswith('.txt'):
        filename += '.txt'
    
    # Redirect to edit page for this filename
    return redirect(url_for('edit_file', filename=filename))

@app.route('/upload_file', methods=['POST'])
@require_login
def upload_file():
    """Upload a text file."""
    if 'file' not in request.files:
        flash('No file selected.', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected.', 'error')
        return redirect(url_for('index'))
    
    if file and file.filename.endswith('.txt'):
        # Secure the filename
        filename = file.filename
        # Validate filename (no path traversal)
        if '/' in filename or '\\' in filename or '..' in filename:
            flash('Invalid filename.', 'error')
            return redirect(url_for('index'))
        
        try:
            file_path = PROMPTS_DIR / filename
            file.save(str(file_path))
            flash(f'File {filename} uploaded successfully.', 'success')
        except Exception as e:
            flash(f'Error uploading file: {e}', 'error')
    else:
        flash('Please select a .txt file.', 'error')
    
    return redirect(url_for('index'))

@app.route('/delete/<filename>')
@require_login
def delete_file(filename):
    """Delete a prompt file."""
    file_path = PROMPTS_DIR / filename
    
    if file_path.exists():
        try:
            file_path.unlink()
            flash(f'File {filename} deleted successfully.', 'success')
        except Exception as e:
            flash(f'Error deleting file: {e}', 'error')
    else:
        flash(f'File {filename} not found.', 'error')
    
    return redirect(url_for('index'))

@app.route('/start_teleprompter/<filename>')
@require_login
def start_teleprompter(filename):
    """Start the teleprompter with the specified file."""
    global teleprompter_process
    
    # Stop existing process if running
    if teleprompter_process and teleprompter_process.poll() is None:
        try:
            teleprompter_process.terminate()
            teleprompter_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            teleprompter_process.kill()
        except Exception as e:
            print(f"Error stopping teleprompter: {e}")
    
    file_path = PROMPTS_DIR / filename
    if not file_path.exists():
        flash(f'File {filename} not found.', 'error')
        return redirect(url_for('index'))
    
    try:
        python_exe = get_python_executable()
        cmd = [str(python_exe), str(TELEPROMPTER_SCRIPT), str(file_path)]
        
        print(f"[DEBUG] Starting teleprompter with command: {' '.join(cmd)}")
        print(f"[DEBUG] Python executable: {python_exe}")
        print(f"[DEBUG] Teleprompter script: {TELEPROMPTER_SCRIPT}")
        print(f"[DEBUG] File path: {file_path}")
        
        # Start teleprompter process with proper window visibility
        if os.name == 'nt':  # Windows
            # Use CREATE_NEW_CONSOLE to ensure the teleprompter window can appear
            teleprompter_process = subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_CONSOLE
            )
        else:  # Linux/macOS
            teleprompter_process = subprocess.Popen(cmd)
        
        print(f"[DEBUG] Teleprompter started with PID: {teleprompter_process.pid}")
        flash(f'Teleprompter started with {filename} (PID: {teleprompter_process.pid})', 'success')
    except Exception as e:
        print(f"[ERROR] Failed to start teleprompter: {e}")
        print(f"[ERROR] Command was: {' '.join(cmd) if 'cmd' in locals() else 'Unknown'}")
        flash(f'Error starting teleprompter: {e}', 'error')
    
    return redirect(url_for('index'))

@app.route('/stop_teleprompter')
@require_login
def stop_teleprompter():
    """Stop the running teleprompter."""
    global teleprompter_process
    
    if teleprompter_process and teleprompter_process.poll() is None:
        try:
            if os.name == 'nt':
                # Windows
                teleprompter_process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                # Linux/macOS
                teleprompter_process.terminate()
            
            teleprompter_process.wait(timeout=5)
            flash('Teleprompter stopped.', 'success')
        except subprocess.TimeoutExpired:
            teleprompter_process.kill()
            flash('Teleprompter force-stopped.', 'warning')
        except Exception as e:
            flash(f'Error stopping teleprompter: {e}', 'error')
    else:
        flash('No teleprompter process running.', 'info')
    
    return redirect(url_for('index'))

@app.route('/teleprompter_status')
@require_login
def teleprompter_status():
    """Get teleprompter status as JSON."""
    global teleprompter_process
    
    if teleprompter_process and teleprompter_process.poll() is None:
        return jsonify({'running': True, 'pid': teleprompter_process.pid})
    else:
        return jsonify({'running': False})

@app.route('/focus_toggle')
@require_login
def focus_toggle():
    """Toggle focus assist mode."""
    global focus_on
    focus_on = not focus_on
    save_runtime_state()
    return jsonify({'focus_on': focus_on})

@app.route('/get_focus_status')
@require_login
def get_focus_status():
    """Get focus assist mode status."""
    global focus_on
    load_runtime_state()
    return jsonify({'focus_on': focus_on})

@app.route('/flip_video_toggle')
@require_login
def flip_video_toggle():
    """Toggle video flip mode."""
    global flip_video
    flip_video = not flip_video
    save_runtime_state()
    return jsonify({'flip_video': flip_video})

@app.route('/get_flip_status')
@require_login
def get_flip_status():
    """Get video flip mode status."""
    global flip_video
    load_runtime_state()
    return jsonify({'flip_video': flip_video})

@app.route('/get_presenter_profile')
@require_login
def get_presenter_profile():
    """Return the current presenter profile and available profiles."""
    load_runtime_state()
    return jsonify({'presenter_profile': presenter_profile, 'supported_profiles': SUPPORTED_PRESENTER_PROFILES})

@app.route('/set_presenter_profile')
@require_login
def set_presenter_profile():
    """Set the presenter profile (auto|generic|logitech_r800)."""
    global presenter_profile
    profile = request.args.get('profile', '').strip()
    if profile not in SUPPORTED_PRESENTER_PROFILES:
        return jsonify({'ok': False, 'error': 'Unsupported profile', 'supported': SUPPORTED_PRESENTER_PROFILES}), 400
    presenter_profile = profile
    save_runtime_state()
    return jsonify({'ok': True, 'presenter_profile': presenter_profile})

def create_example_file():
    """Create an example prompt file if none exist."""
    PROMPTS_DIR.mkdir(exist_ok=True)
    
    example_file = PROMPTS_DIR / "example.txt"
    if not example_file.exists():
        example_content = """Welcome to the Teleprompter System

This is an example prompt file that demonstrates how the teleprompter works.

You can edit this text using the web interface and create new prompt files for your presentations.

The teleprompter supports:
- Automatic scrolling at adjustable speeds
- Camera overlay for presenter view
- Keyboard controls for manual operation
- Full-screen display for professional use

Key Features:
- Smooth text scrolling
- Customizable font sizes
- Pause and resume functionality
- Speed adjustment during playback
- Manual scroll control

Controls when running:
- SPACE: Pause/Resume
- UP/DOWN arrows: Adjust speed
- LEFT/RIGHT arrows: Manual scroll
- R: Reset to beginning
- ESC or Q: Quit

Use the web interface to create and manage your prompt files, then start the teleprompter with a single click.

Happy presenting!"""
        
        try:
            with open(example_file, 'w', encoding='utf-8') as f:
                f.write(example_content)
            print(f"Created example file: {example_file}")
        except Exception as e:
            print(f"Error creating example file: {e}")

if __name__ == '__main__':
    # Create example file if needed
    create_example_file()
    
    # Start the web server
    print("Starting Teleprompter Web Interface...")
    print("Access the interface at: http://localhost:5000")
    if PAM_AVAILABLE:
        print("Use your system credentials to log in.")
    else:
        print("Authentication disabled for development - use any username/password.")
    
    app.run(host='0.0.0.0', port=5000, debug=False)
