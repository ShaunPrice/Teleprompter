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
import time
import base64
import tempfile
import threading
import re
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, Response
import json

try:
    import cv2  # For camera enumeration & recording
except Exception:
    cv2 = None

# Import pam only on non-Windows systems
if platform.system() != "Windows":
    try:
        import pam
        PAM_AVAILABLE = True
        print("[OK] PAM authentication available")
    except ImportError:
        PAM_AVAILABLE = False
        print("[WARNING] python-pam not available. Authentication will be simplified.")
    except Exception as e:
        PAM_AVAILABLE = False
        print(f"[WARNING] PAM error ({e}). Authentication will be simplified.")
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

# Global variable to track teleprompter process
teleprompter_process = None
recording_state = {
    'thread': None,
    'stop_event': None,
    'capture': None,
    'writer': None,
    'device': None,
    'output_file': None,
    'start_time': None,
    'width': None,
    'height': None,
    'fps': None,
    'dslr_mode': False
}
recording_lock = threading.Lock()
dslr_config_cache = {}  # {(device): { 'ts': <epoch>, 'configs': [...] }}
DSLR_CONFIG_CACHE_TTL = 30  # seconds

# --- DSLR helpers for Pi/Linux --- #
def _kill_conflicting_camera_processes():
    """Attempt to terminate auto-mount processes that block gphoto2 (best effort)."""
    if os.name != 'posix':
        return
    try:
        subprocess.run(['bash','-lc','pkill -f gvfs-gphoto2 || true; pkill -f gvfsd-mtp || true'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
    except Exception:
        pass

def _run_gphoto_cmd(args, port=None, timeout=10):
    """Run gphoto2 with optional --port, after killing conflicts. Returns CompletedProcess or None."""
    if os.name == 'nt':
        return None
    import shutil
    if not shutil.which('gphoto2'):
        return None
    _kill_conflicting_camera_processes()
    cmd = ['gphoto2']
    if port:
        cmd += ['--port', port]
    cmd += args
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    except Exception:
        return None

# Live view tracking & preview cache
dslr_liveview_state = {}  # device -> bool (active)
dslr_preview_cache = {}   # device -> {'ts': epoch, 'data': bytes}
dslr_preview_min_interval = 0.8  # seconds between actual gphoto preview grabs
dslr_auto_liveview_attempted = set()  # devices for which we've tried automatic live view enable (preview)
ALLOW_DSLR_CAPTURE_PREVIEW = os.environ.get('ALLOW_DSLR_CAPTURE_PREVIEW', '0') in ('1','true','TRUE','yes','YES')

# Background DSLR movie stream readers (continuous) to avoid repeated capture-preview commands
dslr_movie_streams = {}  # device -> {'thread': Thread, 'stop': Event, 'last_frame': bytes|None, 'last_ts': float}

def _start_dslr_movie_stream(device: str):
    if os.name == 'nt':
        return False
    if device in dslr_movie_streams and dslr_movie_streams[device]['thread'].is_alive():
        return True
    import shutil
    if not shutil.which('gphoto2'):
        return False
    stop_event = threading.Event()
    state = {'thread': None, 'stop': stop_event, 'last_frame': None, 'last_ts': 0.0}
    def worker():
        _ensure_liveview(device)
        # Attempt movie mode (best-effort)
        for movie_path in ['/main/actions/movie=1', 'movie=1']:
            proc_set = _run_gphoto_cmd(['--set-config', movie_path], port=device, timeout=4)
            if proc_set and proc_set.returncode == 0:
                break
        try:
            proc = subprocess.Popen(['gphoto2', '--port', device, '--capture-movie', '--stdout'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        except Exception:
            return
        buf = b''
        SOI = b'\xff\xd8'; EOI = b'\xff\xd9'
        last_yield = 0.0
        while not stop_event.is_set():
            if proc.poll() is not None:
                break
            try:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    time.sleep(0.02); continue
                buf += chunk
                while True:
                    start = buf.find(SOI)
                    if start == -1:
                        if len(buf) > 1_000_000: buf = b''
                        break
                    end = buf.find(EOI, start+2)
                    if end == -1:
                        if start > 0: buf = buf[start:]
                        break
                    frame = buf[start:end+2]
                    buf = buf[end+2:]
                    now = time.time()
                    if now - last_yield < 1/35:  # throttle ~35 fps
                        continue
                    state['last_frame'] = frame
                    state['last_ts'] = now
                    last_yield = now
            except Exception:
                break
        try:
            if proc and proc.poll() is None:
                proc.terminate()
        except Exception:
            pass
    t = threading.Thread(target=worker, daemon=True)
    state['thread'] = t
    dslr_movie_streams[device] = state
    t.start()
    return True

def _stop_dslr_movie_stream(device: str):
    entry = dslr_movie_streams.get(device)
    if not entry:
        return False
    entry['stop'].set()
    thr = entry['thread']
    if thr and thr.is_alive():
        thr.join(timeout=2)
    dslr_movie_streams.pop(device, None)
    return True

def _ensure_liveview(device: str):
    """Attempt to enable live view (viewfinder) once per device if not already active.
    Some cameras (e.g. Nikon) require viewfinder=1 before capture-preview returns a JPEG without triggering full captures."""
    if os.name == 'nt':
        return False
    if not dslr_liveview_state.get(device):
        # Try common config path
        proc = _run_gphoto_cmd(['--set-config', '/main/actions/viewfinder=1'], port=device, timeout=5)
        if proc and proc.returncode == 0:
            dslr_liveview_state[device] = True
            return True
        # Fallback alternative path
        proc2 = _run_gphoto_cmd(['--set-config', 'viewfinder=1'], port=device, timeout=5)
        if proc2 and proc2.returncode == 0:
            dslr_liveview_state[device] = True
            return True
        return False
    return True

def _disable_liveview(device: str):
    if os.name == 'nt':
        return False
    if dslr_liveview_state.get(device):
        proc = _run_gphoto_cmd(['--set-config', '/main/actions/viewfinder=0'], port=device, timeout=5)
        if proc and proc.returncode == 0:
            dslr_liveview_state[device] = False
            _stop_dslr_movie_stream(device)
            return True
    return False

# Profiles & Automation
PROFILES_DIR = Path(__file__).parent / 'camera_profiles'
SETTINGS_FILE = Path(__file__).parent / 'camera_settings.json'
PROFILES_DIR.mkdir(exist_ok=True)
DEFAULT_AUTOMATION = {
    'auto_record_with_teleprompter': False,
    'auto_stop_with_teleprompter': True,
    'auto_focus_before_record': True,
    'selected_camera': None,
    'record_width': 1920,
    'record_height': 1080,
    'record_fps': 30
}

def load_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            for k, v in DEFAULT_AUTOMATION.items():
                data.setdefault(k, v)
            return data
        except Exception:
            return DEFAULT_AUTOMATION.copy()
    return DEFAULT_AUTOMATION.copy()

def save_settings(data):
    try:
        with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"[WARN] Failed to save settings: {e}")

def list_profiles():
    profiles = []
    for p in PROFILES_DIR.glob('*.json'):
        try:
            with open(p, 'r', encoding='utf-8') as f:
                prof = json.load(f)
            prof['name'] = p.stem
            prof['builtIn'] = False
            profiles.append(prof)
        except Exception:
            continue
    builtin = [
        {'name': '4k_24p', 'width': 3840, 'height': 2160, 'fps': 24, 'dslr_configs': {}, 'builtIn': True},
        {'name': '1080p_60p', 'width': 1920, 'height': 1080, 'fps': 60, 'dslr_configs': {}, 'builtIn': True},
        {'name': '1080p_30p', 'width': 1920, 'height': 1080, 'fps': 30, 'dslr_configs': {}, 'builtIn': True},
    ]
    return builtin + profiles

def save_profile(name, data):
    if not name:
        raise ValueError('profile name required')
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    path = PROFILES_DIR / f"{safe}.json"
    serializable = {k: data.get(k) for k in ('width','height','fps','dslr_configs') if k in data}
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(serializable, f, indent=2)
    return True

def load_profile(name):
    for prof in list_profiles():
        if prof['name'] == name:
            return prof
    return None

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
                         teleprompter_running=teleprompter_running)

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
        # Automation: start recording if configured
        settings = load_settings()
        if settings.get('auto_record_with_teleprompter'):
            cam = settings.get('selected_camera')
            if cam:
                if settings.get('auto_focus_before_record'):
                    try:
                        dslr_autofocus(cam)
                    except Exception:
                        pass
                with recording_lock:
                    active = recording_state['thread'] and recording_state['thread'].is_alive()
                if not active and cv2 is not None and not cam.startswith('usb:'):
                    try:
                        out_dir = Path('recordings'); out_dir.mkdir(exist_ok=True)
                        fname = f"auto_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
                        width = settings.get('record_width', 1920)
                        height = settings.get('record_height', 1080)
                        fps = settings.get('record_fps', 30)
                        stop_event = threading.Event()
                        t = threading.Thread(target=recording_thread_func, args=(cam,width,height,fps,str(out_dir/fname),stop_event), daemon=True)
                        with recording_lock:
                            recording_state.update({'thread': t,'stop_event': stop_event,'device': cam,'output_file': str(out_dir/fname),'start_time': datetime.utcnow().isoformat(),'width': width,'height': height,'fps': fps})
                        t.start()
                        flash('Auto recording started.', 'info')
                    except Exception as e:
                        flash(f'Auto record failed: {e}', 'warning')
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
            # Automation: stop recording if enabled
            settings = load_settings()
            if settings.get('auto_stop_with_teleprompter'):
                with recording_lock:
                    active = recording_state['thread'] and recording_state['thread'].is_alive()
                if active:
                    try:
                        recording_state['stop_event'].set()
                        thr = recording_state['thread']
                        thr.join(timeout=5)
                        with recording_lock:
                            recording_state.update({'thread': None,'stop_event': None})
                        flash('Recording stopped (automation).', 'info')
                    except Exception:
                        pass
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

# ---------------- Camera Utilities ---------------- #

def detect_cameras(include_dslr: bool = True):
    """Enumerate available cameras.
    On Linux, prefer /dev/video* devices; otherwise probe indices with OpenCV.
    Returns list of dicts: {id, label, backend, path}
    """
    cameras = []
    if cv2 is None:
        return cameras
    # Only DSLR protocol cameras now
    dslrs = detect_dslr_cameras() if include_dslr else []
    return dslrs

def detect_dslr_cameras():
    """Use gphoto2 --auto-detect to find DSLR cameras.
    Returns list of dicts with type 'dslr' and id set to the port path.
    """
    if os.name == 'nt':  # gphoto2 not expected on Windows
        return []
    import shutil
    if not shutil.which('gphoto2'):
        return []
    cams = []
    proc = _run_gphoto_cmd(['--auto-detect'], timeout=7)
    if not proc:
        return []
    if proc.returncode != 0:
        return []
    # Parse table style:
    # Model                          Port
    # ----------------------------------------------------------
    # Nikon DSC D750 (PTP mode)      usb:001,004
    lines = proc.stdout.splitlines()
    # Find header separator line index
    sep_idx = None
    for i, line in enumerate(lines):
        if re.match(r'^-\s*-+$', line.strip().replace(' ', '-')):
            sep_idx = i
            break
    if sep_idx is None:
        # Fallback: skip first 2 lines
        data_lines = lines[2:]
    else:
        data_lines = lines[sep_idx+1:]
    for line in data_lines:
        if not line.strip():
            continue
        # Split by two or more spaces
        parts = re.split(r'\s{2,}', line.strip())
        if len(parts) < 2:
            continue
        model = parts[0].strip()
        port = parts[-1].strip()
        # Additional summary to refine model (optional)
        cams.append({'id': port, 'label': f"{model} ({port})", 'backend': 'gphoto2', 'path': port, 'type': 'dslr', 'model': model})
    return cams

def list_opencv_controls(device):
    """Return subset of adjustable OpenCV properties with current values.
    This is limited by driver support. Values of -1 or 0 may mean unsupported.
    """
    if cv2 is None:
        return []
    prop_map = {
        'brightness': cv2.CAP_PROP_BRIGHTNESS,
        'contrast': cv2.CAP_PROP_CONTRAST,
        'saturation': cv2.CAP_PROP_SATURATION,
        'hue': cv2.CAP_PROP_HUE,
        'gain': cv2.CAP_PROP_GAIN,
        'exposure': cv2.CAP_PROP_EXPOSURE,
    }
    # Some builds expose focus/zoom constants
    if hasattr(cv2, 'CAP_PROP_FOCUS'):
        prop_map['focus'] = getattr(cv2, 'CAP_PROP_FOCUS')
    if hasattr(cv2, 'CAP_PROP_ZOOM'):
        prop_map['zoom'] = getattr(cv2, 'CAP_PROP_ZOOM')
    controls = []
    try:
        cap = cv2.VideoCapture(device)
        if not cap or not cap.isOpened():
            return controls
        for name, pid in prop_map.items():
            try:
                val = cap.get(pid)
                # Filter obviously invalid default -1 values
                if val != -1:
                    controls.append({'name': name, 'value': val, 'min': 0, 'max': 255, 'step': 1, 'source': 'opencv'})
            except Exception:
                continue
    finally:
        if 'cap' in locals() and cap:
            cap.release()
    return controls

def list_v4l2_controls(device):
    """List v4l2 controls via v4l2-ctl if available. Return list of dicts."""
    if os.name == 'nt':
        return []
    controls = []
    try:
        import shutil, re
        if not shutil.which('v4l2-ctl'):
            return []
        proc = subprocess.run(['v4l2-ctl', '-d', device, '-l'], capture_output=True, text=True, timeout=3)
        if proc.returncode != 0:
            return []
        for line in proc.stdout.splitlines():
            # Format: focus_absolute (int)    : min=0 max=250 step=5 default=0 value=0
            m = re.match(r'^(\w+) \(.*?\)\s*:\s*min=([-0-9]+) max=([-0-9]+) step=(\d+) default=([-0-9]+) value=([-0-9]+)', line)
            if m:
                name, min_v, max_v, step, default, value = m.groups()
                controls.append({
                    'name': name,
                    'value': int(value),
                    'min': int(min_v),
                    'max': int(max_v),
                    'step': int(step),
                    'default': int(default),
                    'source': 'v4l2'
                })
    except Exception:
        return []
    return controls

def merge_controls(v4l2_list, opencv_list):
    by_name = {c['name']: c for c in v4l2_list}
    for c in opencv_list:
        if c['name'] not in by_name:
            by_name[c['name']] = c
    return list(by_name.values())

def set_control(device, control, value):
    """Set control either via v4l2-ctl or OpenCV fallback."""
    # Try v4l2 first on Linux
    if os.name != 'nt':
        import shutil
        if shutil.which('v4l2-ctl'):
            try:
                subprocess.run(['v4l2-ctl', '-d', device, f'--set-ctrl={control}={value}'], check=True, timeout=3)
                return True, 'v4l2'
            except Exception:
                pass
    if cv2 is not None:
        cap = cv2.VideoCapture(device)
        if cap and cap.isOpened():
            # Map generic names to known OpenCV props
            name_map = {
                'brightness': cv2.CAP_PROP_BRIGHTNESS,
                'contrast': cv2.CAP_PROP_CONTRAST,
                'saturation': cv2.CAP_PROP_SATURATION,
                'hue': cv2.CAP_PROP_HUE,
                'gain': cv2.CAP_PROP_GAIN,
                'exposure': cv2.CAP_PROP_EXPOSURE,
            }
            if hasattr(cv2, 'CAP_PROP_FOCUS'):
                name_map['focus'] = getattr(cv2, 'CAP_PROP_FOCUS')
            if hasattr(cv2, 'CAP_PROP_ZOOM'):
                name_map['zoom'] = getattr(cv2, 'CAP_PROP_ZOOM')
            pid = name_map.get(control)
            if pid is not None:
                try:
                    cap.set(pid, float(value))
                    cap.release()
                    return True, 'opencv'
                except Exception:
                    cap.release()
        if cap:
            cap.release()
    return False, 'none'

def recording_thread_func(device, width, height, fps, output_file, stop_event):
    if cv2 is None:
        return
    # Open capture
    cap = cv2.VideoCapture(device)
    if not cap or not cap.isOpened():
        return
    # Set properties (may not all succeed)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, fps)
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    writer = cv2.VideoWriter(output_file, fourcc, fps, (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))))
    while not stop_event.is_set():
        ret, frame = cap.read()
        if not ret:
            time.sleep(0.05)
            continue
        writer.write(frame)
    writer.release()
    cap.release()

def capture_preview_frame(device, force_new: bool = False):
    """Return JPEG bytes for a preview frame for the given device (webcam or DSLR).
    force_new: bypass DSLR cache (used during DSLR recording for smoother stream)
    """
    # DSLR path detection: gphoto2 port strings often start with 'usb:' or end with something like 'ptp:'
    if os.name != 'nt' and (device.startswith('usb:') or device.startswith('ptp:')):
        if not dslr_liveview_state.get(device):
            # One-time automatic enable attempt (safe: uses viewfinder=1 only)
            if device not in dslr_auto_liveview_attempted:
                dslr_auto_liveview_attempted.add(device)
                _ensure_liveview(device)
            if not dslr_liveview_state.get(device):
                cached = dslr_preview_cache.get(device)
                return cached['data'] if cached else None
        # Prefer movie stream frame
        stream_entry = dslr_movie_streams.get(device)
        if stream_entry and stream_entry.get('last_frame'):
            frame = stream_entry['last_frame']
            dslr_preview_cache[device] = {'ts': time.time(), 'data': frame}
            return frame
        # Fallback capture-preview disabled unless explicitly allowed via env flag to avoid shutter firing
        if not ALLOW_DSLR_CAPTURE_PREVIEW:
            cached = dslr_preview_cache.get(device)
            return cached['data'] if cached else None
        import shutil
        if not shutil.which('gphoto2'):
            return None
        now = time.time()
        cached = dslr_preview_cache.get(device)
        if not force_new and cached and now - cached['ts'] < dslr_preview_min_interval:
            return cached['data']
        tmpname = None
        try:
            with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tf:
                tmpname = tf.name
            proc = _run_gphoto_cmd(['--capture-preview', '--filename', tmpname], port=device, timeout=6)
            if proc and proc.returncode == 0 and os.path.exists(tmpname):
                with open(tmpname, 'rb') as f:
                    data = f.read()
                dslr_preview_cache[device] = {'ts': now, 'data': data}
                os.unlink(tmpname)
                return data
        except Exception:
            pass
        finally:
            if tmpname and os.path.exists(tmpname):
                try:
                    os.unlink(tmpname)
                except Exception:
                    pass
        return cached['data'] if cached else None
    # Webcam path
    if cv2 is None:
        return None
    cap = cv2.VideoCapture(device)
    if not cap or not cap.isOpened():
        if cap:
            cap.release()
        return None
    ret, frame = cap.read()
    cap.release()
    if not ret:
        return None
    # Encode JPEG
    ok, buf = cv2.imencode('.jpg', frame)
    if not ok:
        return None
    return buf.tobytes()

def dslr_autofocus(device):
    """Attempt to trigger autofocus for a DSLR via gphoto2. Returns True if any command succeeded."""
    if os.name == 'nt':
        return False
    import shutil
    if not shutil.which('gphoto2'):
        return False
    focus_paths = ['autofocusdrive=1', '/main/actions/autofocus=1']
    for p in focus_paths:
        proc = _run_gphoto_cmd(['--set-config', p], port=device, timeout=5)
        if proc and proc.returncode == 0:
            return True
    return False

@app.route('/api/dslr/liveview', methods=['GET','POST'])
@require_login
def api_dslr_liveview():
    if request.method == 'GET':
        device = request.args.get('device')
        if not device:
            return jsonify({'error': 'device required'}), 400
        return jsonify({'device': device, 'liveview': bool(dslr_liveview_state.get(device)), 'movie_stream': device in dslr_movie_streams})
    data = request.get_json(force=True, silent=True) or {}
    device = data.get('device')
    enable = bool(data.get('enable'))
    if not device:
        return jsonify({'error': 'device required'}), 400
    if enable:
        ok = _ensure_liveview(device)
        if ok:
            _start_dslr_movie_stream(device)
    else:
        ok = _disable_liveview(device)
    return jsonify({'device': device, 'liveview': bool(dslr_liveview_state.get(device)), 'changed': ok, 'movie_stream': device in dslr_movie_streams})

# ---------- DSLR Config Management (Nikon etc.) ---------- #

INTERESTING_DSLR_CONFIGS = [
    '/main/capturesettings/f-number',
    '/main/capturesettings/shutterspeed',
    '/main/capturesettings/iso',
    '/main/capturesettings/exposurecompensation',
    '/main/capturesettings/meteringmode',
    '/main/capturesettings/exposuremode',
    '/main/capturesettings/focusmode',
    '/main/capturesettings/flashmode',
    '/main/capturesettings/drive',
    '/main/capturesettings/drive mode',
    '/main/imgsettings/whitebalance',
    '/main/imgsettings/colortemperature',
    '/main/imgsettings/picturecontrol',
    '/main/imgsettings/picturestyle'
]

def _gphoto_available():
    if os.name == 'nt':
        return False
    import shutil
    return bool(shutil.which('gphoto2'))

def list_dslr_configs(device):
    """Return cached list of DSLR config entries with choices & current value."""
    if not _gphoto_available():
        return []
    now = time.time()
    # Cache key
    try:
        cache_entry = dslr_config_cache.get(device)
        if cache_entry and now - cache_entry['ts'] < DSLR_CONFIG_CACHE_TTL:
            return cache_entry['configs']
    except Exception:
        pass

    configs = []
    # Determine which of our interesting configs actually exist by listing
    try:
        proc_list = subprocess.run(['gphoto2', '--port', device, '--list-config'], capture_output=True, text=True, timeout=10)
        if proc_list.returncode == 0:
            available_paths = set(l.strip() for l in proc_list.stdout.splitlines() if l.strip().startswith('/'))
        else:
            available_paths = set()
    except Exception:
        available_paths = set()

    targets = [p for p in INTERESTING_DSLR_CONFIGS if p in available_paths]
    for path in targets:
        try:
            proc_cfg = subprocess.run(['gphoto2', '--port', device, '--get-config', path], capture_output=True, text=True, timeout=5)
            if proc_cfg.returncode != 0:
                continue
            entry = parse_gphoto_config_output(path, proc_cfg.stdout)
            if entry:
                configs.append(entry)
        except Exception:
            continue
    dslr_config_cache[device] = {'ts': now, 'configs': configs}
    return configs

def parse_gphoto_config_output(path, text):
    label = None
    current = None
    readonly = False
    ctype = None
    choices = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('Label:'):
            label = line.split('Label:',1)[1].strip()
        elif line.startswith('Type:'):
            ctype = line.split('Type:',1)[1].strip()
        elif line.startswith('Current:'):
            current = line.split('Current:',1)[1].strip()
        elif line.startswith('Readonly:'):
            ro_val = line.split('Readonly:',1)[1].strip()
            readonly = ro_val == '1'
        elif line.startswith('Choice:'):
            # Choice: 0 value with spaces
            m = re.match(r'^Choice:\s*\d+\s+(.*)$', line)
            if m:
                choices.append(m.group(1).strip())
    if not label:
        label = path.split('/')[-1]
    if current is None and choices:
        current = choices[0]
    return {
        'path': path,
        'label': label,
        'readonly': readonly,
        'type': ctype,
        'current': current,
        'choices': choices
    }

def set_dslr_config(device, path, value):
    if not _gphoto_available():
        return False
    try:
        proc = subprocess.run(['gphoto2', '--port', device, '--set-config', f'{path}={value}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=8)
        if proc.returncode == 0:
            # Invalidate cache
            if device in dslr_config_cache:
                dslr_config_cache.pop(device, None)
            return True
    except Exception:
        pass
    return False

# ---------------- Camera Routes ---------------- #

@app.route('/camera')
@require_login
def camera_page():
    return render_template('camera.html')

@app.route('/api/cameras')
@require_login
def api_cameras():
    cams = detect_cameras(include_dslr=True)
    return jsonify({'cameras': cams, 'protocol_only': True})

@app.route('/api/camera/controls')
@require_login
def api_camera_controls():
    device = request.args.get('device')
    if not device:
        return jsonify({'error': 'device required'}), 400
    controls = merge_controls(list_v4l2_controls(device), list_opencv_controls(device))
    return jsonify({'device': device, 'controls': controls})

@app.route('/api/camera/set_control', methods=['POST'])
@require_login
def api_set_control():
    data = request.get_json(force=True, silent=True) or {}
    device = data.get('device')
    control = data.get('control')
    value = data.get('value')
    if device is None or control is None or value is None:
        return jsonify({'error': 'device, control, value required'}), 400
    ok, method = set_control(device, control, value)
    return jsonify({'success': ok, 'method': method})

@app.route('/api/camera/start_record', methods=['POST'])
@require_login
def api_start_record():
    data = request.get_json(force=True, silent=True) or {}
    device = data.get('device')
    width = int(data.get('width', 1280))
    height = int(data.get('height', 720))
    fps = int(data.get('fps', 30))
    filename = data.get('filename') or f"recording_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    output_dir = Path('recordings')
    output_dir.mkdir(exist_ok=True)
    output_file = str(output_dir / filename)
    if not device:
        return jsonify({'error': 'device required'}), 400
    with recording_lock:
        if recording_state['thread'] and recording_state['thread'].is_alive():
            return jsonify({'error': 'recording already active'}), 400
        stop_event = threading.Event()
        dslr_movie = False
        if device.startswith('usb:') and cv2 is not None:
            # DSLR recording via movie stream / preview frames
            if not _ensure_liveview(device):
                return jsonify({'error': 'failed to enable live view'}), 500
            # Start (or confirm) movie stream
            _start_dslr_movie_stream(device)
            # Attempt movie=1 (best-effort)
            for movie_path in ['/main/actions/movie=1', 'movie=1']:
                proc = _run_gphoto_cmd(['--set-config', movie_path], port=device, timeout=4)
                if proc and proc.returncode == 0:
                    dslr_movie = True
                    break
            def dslr_record():
                import numpy as np
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                writer = None
                target_dt = 1.0 / max(1, fps)
                print(f"[REC][DSLR] start device={device} target_dt={target_dt:.4f}")
                while not stop_event.is_set():
                    # Try to use movie stream frame directly; fallback to capture
                    stream_entry = dslr_movie_streams.get(device)
                    jpeg = None
                    if stream_entry and stream_entry.get('last_frame'):
                        jpeg = stream_entry['last_frame']
                    if jpeg is None:
                        jpeg = capture_preview_frame(device, force_new=True)
                    if not jpeg:
                        time.sleep(0.05)
                        continue
                    arr = np.frombuffer(jpeg, dtype=np.uint8)
                    frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if frame is None:
                        continue
                    if writer is None:
                        h_, w_, _ = frame.shape
                        writer = cv2.VideoWriter(output_file, fourcc, fps, (w_, h_))
                        with recording_lock:
                            recording_state['writer'] = True
                    writer.write(frame)
                    # granular sleep for responsive stop
                    remaining = target_dt
                    while remaining > 0 and not stop_event.is_set():
                        sl = min(0.01, remaining)
                        time.sleep(sl)
                        remaining -= sl
                if writer:
                    try:
                        writer.release()
                    except Exception:
                        pass
                print(f"[REC][DSLR] exit device={device}")
            t = threading.Thread(target=dslr_record, daemon=True)
        else:
            t = threading.Thread(target=recording_thread_func, args=(device, width, height, fps, output_file, stop_event), daemon=True)
        recording_state.update({
            'thread': t,
            'stop_event': stop_event,
            'device': device,
            'output_file': output_file,
            'start_time': datetime.utcnow().isoformat(),
            'width': width,
            'height': height,
            'fps': fps,
            'dslr_mode': device.startswith('usb:')
        })
        print(f"[REC][START] device={device} file={output_file} dslr={recording_state['dslr_mode']} movie={dslr_movie}")
        t.start()
    return jsonify({'started': True, 'file': output_file, 'dslr_mode': device.startswith('usb:'), 'dslr_movie': dslr_movie})

@app.route('/api/camera/stop_record', methods=['POST'])
@require_login
def api_stop_record():
    forced = False
    with recording_lock:
        thr = recording_state['thread']
        if not (thr and thr.is_alive()):
            return jsonify({'stopped': False, 'error': 'no active recording'}), 400
        print(f"[REC][STOP] request device={recording_state['device']}")
        recording_state['stop_event'].set()
    thr.join(timeout=5)
    if thr.is_alive():
        forced = True
        print('[REC][STOP] thread did not exit in 5s, forcing clear')
    with recording_lock:
        if forced:
            recording_state['forced_stop'] = True
        recording_state.update({'thread': None, 'stop_event': None, 'capture': None, 'writer': None})
    return jsonify({'stopped': True, 'forced': forced})

@app.route('/api/camera/force_stop', methods=['POST'])
@require_login
def api_force_stop():
    with recording_lock:
        thr = recording_state['thread']
        if thr and thr.is_alive():
            recording_state['stop_event'].set()
        recording_state.update({'thread': None, 'stop_event': None, 'capture': None, 'writer': None, 'forced_stop': True})
    print('[REC][FORCE] state cleared')
    return jsonify({'forced_cleared': True})

@app.route('/api/camera/debug_record')
@require_login
def api_debug_record():
    with recording_lock:
        info = {k: v for k, v in recording_state.items() if k not in ('thread','stop_event')}
        info['thread_alive'] = bool(recording_state['thread'] and recording_state['thread'].is_alive())
    return jsonify(info)

@app.route('/api/camera/status')
@require_login
def api_camera_status():
    with recording_lock:
        active = recording_state['thread'] and recording_state['thread'].is_alive()
        status = {k: v for k, v in recording_state.items() if k not in ('thread', 'stop_event', 'capture', 'writer')}
        status['active'] = bool(active)
    return jsonify(status)

@app.route('/api/camera/preview_frame')
@require_login
def api_camera_preview():
    device = request.args.get('device')
    if not device:
        return jsonify({'error': 'device required'}), 400
    data = capture_preview_frame(device)
    if data is None:
        if device.startswith('usb:'):
            if not dslr_liveview_state.get(device):
                return jsonify({'error': 'live view disabled'}), 412
            stream_entry = dslr_movie_streams.get(device)
            if stream_entry and stream_entry.get('last_frame') is None:
                return jsonify({'error': 'waiting for movie stream'}), 425
            if not ALLOW_DSLR_CAPTURE_PREVIEW:
                return jsonify({'error': 'no frame (capture-preview disabled)'}), 503
        return jsonify({'error': 'capture failed'}), 500
    width = height = None
    if cv2 is not None:
        try:
            import numpy as np
            arr = np.frombuffer(data, dtype=np.uint8)
            frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if frame is not None:
                height, width = frame.shape[:2]
        except Exception:
            pass
    b64 = base64.b64encode(data).decode('ascii')
    return jsonify({'image': 'data:image/jpeg;base64,' + b64, 'width': width, 'height': height})

@app.route('/api/camera/focus', methods=['POST'])
@require_login
def api_camera_focus():
    data = request.get_json(force=True, silent=True) or {}
    device = data.get('device')
    if not device:
        return jsonify({'error': 'device required'}), 400
    success = dslr_autofocus(device)
    return jsonify({'success': success})

@app.route('/api/dslr/configs')
@require_login
def api_dslr_configs():
    device = request.args.get('device')
    if not device:
        return jsonify({'error': 'device required'}), 400
    cfgs = list_dslr_configs(device)
    return jsonify({'device': device, 'configs': cfgs})

@app.route('/api/dslr/set_config', methods=['POST'])
@require_login
def api_dslr_set_config():
    data = request.get_json(force=True, silent=True) or {}
    device = data.get('device')
    path = data.get('path')
    value = data.get('value')
    if not all([device, path, value]):
        return jsonify({'error': 'device, path, value required'}), 400
    ok = set_dslr_config(device, path, value)
    return jsonify({'success': ok})

@app.route('/api/dslr/profiles')
@require_login
def api_dslr_profiles():
    return jsonify({'profiles': list_profiles()})

@app.route('/api/dslr/save_profile', methods=['POST'])
@require_login
def api_dslr_save_profile():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name')
    try:
        save_profile(name, data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/api/dslr/load_profile', methods=['POST'])
@require_login
def api_dslr_load_profile():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name')
    prof = load_profile(name)
    if not prof:
        return jsonify({'error': 'profile not found'}), 404
    return jsonify({'profile': prof})

@app.route('/api/dslr/delete_profile', methods=['POST'])
@require_login
def api_dslr_delete_profile():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'name required'}), 400
    safe = re.sub(r'[^a-zA-Z0-9_-]', '_', name)
    path = PROFILES_DIR / f"{safe}.json"
    if path.exists():
        path.unlink()
        return jsonify({'deleted': True})
    return jsonify({'deleted': False, 'error': 'not found'}), 404

@app.route('/api/settings/automation', methods=['GET','POST'])
@require_login
def api_settings_automation():
    if request.method == 'GET':
        return jsonify(load_settings())
    data = request.get_json(force=True, silent=True) or {}
    settings = load_settings()
    for k in DEFAULT_AUTOMATION.keys():
        if k in data:
            settings[k] = data[k]
    save_settings(settings)
    return jsonify({'saved': True, 'settings': settings})

@app.route('/camera/stream')
@require_login
def camera_stream():
    device = request.args.get('device')
    fps = float(request.args.get('fps', '5'))
    if not device:
        return 'device required', 400
    boundary = 'frame'
    interval = max(0.05, 1.0 / max(0.1, fps))
    def gen():
        while True:
            frame = capture_preview_frame(device)
            if frame is not None:
                yield (f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(frame)}\r\n\r\n".encode() + frame + b"\r\n")
            time.sleep(interval)
    return Response(gen(), mimetype=f'multipart/x-mixed-replace; boundary={boundary}')

@app.route('/api/dslr/diagnostics')
@require_login
def api_dslr_diagnostics():
    """Return raw diagnostic info to help when camera can't be claimed.
    Provides:
      - gphoto2 --auto-detect
      - gphoto2 --summary (may fail)
      - List of processes holding gphoto2/PTP related device files (Linux only)
    """
    diag = {}
    def run_cmd(cmd):
        try:
            p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)
            return {'code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr}
        except Exception as e:
            return {'error': str(e)}
    _kill_conflicting_camera_processes()
    diag['auto_detect'] = run_cmd(['gphoto2', '--auto-detect'])
    diag['summary'] = run_cmd(['gphoto2', '--summary'])
    # Try listing processes using gphoto (Linux). Non-fatal if lsof not present.
    if os.name == 'posix':
        # Common device path patterns used by libgphoto2 (usb, mtp)
        diag['processes'] = run_cmd(['bash', '-lc', "command -v lsof >/dev/null 2>&1 && lsof -n | grep -E 'gphoto2|PTP|usb' || echo 'lsof unavailable'"])
        # Check gvfs processes which often auto-mount cameras
        diag['gvfs_ps'] = run_cmd(['bash', '-lc', "ps -ef | grep -E 'gvfs(gphoto|d-mtp)' | grep -v grep || true"])
        diag['dmesg_usb_tail'] = run_cmd(['bash','-lc', "dmesg | grep -i usb | tail -n 40 || true"])
    else:
        diag['processes'] = {'info': 'process scan not implemented on this OS'}
    return jsonify(diag)

@app.route('/api/dslr/state')
@require_login
def api_dslr_state():
    state = {}
    for dev, info in dslr_movie_streams.items():
        state[dev] = {
            'thread_alive': info['thread'].is_alive() if info.get('thread') else False,
            'last_ts': info.get('last_ts'),
            'has_frame': info.get('last_frame') is not None
        }
    return jsonify({'liveview': dslr_liveview_state, 'movie_streams': state, 'allow_capture_preview': ALLOW_DSLR_CAPTURE_PREVIEW})

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
