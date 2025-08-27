#!/usr/bin/env python3
"""
Teleprompter Application
A professional teleprompter system with camera overlay and scrolling text.
"""

import cv2
import numpy as np
import time
import os
import glob
import argparse
import json
from pathlib import Path

#####################################
# Screen mirroring and rotation
#   Can be set in the application
#   or on the Raspberry Pi
#####################################
enable_mirror_rotate = False
output_flip = 1    # Mirror = 1
output_rotate = cv2.ROTATE_180
# New: simple horizontal flip control exposed to web UI
flip_video = False
# Focus assist default (also controlled by web UI)
focus_on = True
# Runtime state timestamps
_state_last_mtime = 0.0
_state_last_check = 0.0
presenter_profile = "auto"  # auto | generic | logitech_r800
PRESENTERS_CONFIG = Path(__file__).parent / "presenters.json"
_presenter_maps = {}
#####################################

# Directory containing prompt text files
prompt_dir = "prompts"

# Get all .txt files in the prompt directory (may be empty; CLI can provide a file)
script_files = sorted(glob.glob(os.path.join(prompt_dir, "*.txt")))

current_file_idx = 0

def load_script(file_path):
    # Robust read: UTF-8 with fallback to cp1252
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    except UnicodeDecodeError:
        with open(file_path, "r", encoding="cp1252", errors="replace") as f:
            lines = f.read().splitlines()
    print(f"[INFO] Loaded {len(lines)} lines from {file_path}")
    return lines

# Configuration variables
fullscreen = True
scroll_speed = 3 # pixels per frame
text_y = 600
line_spacing = 60
font_scale = 1.2
font_thickness = 2
alpha = 0.7
frame_width = 1280
frame_height = 720
start_y = frame_height

def check_presenter_keys():
    import cv2
    print("Press keys on your presenter. Press 'q' to quit.")
    cv2.namedWindow("Key Check", cv2.WINDOW_NORMAL)
    while True:
        key = cv2.waitKey(0) & 0xFF
        print(f"Key pressed: {key} (hex: {hex(key)})")
        if key == ord('q'):
            break
    cv2.destroyAllWindows()

if __name__ == "__main__":
    import sys
    # CLI args
    parser = argparse.ArgumentParser(description="Teleprompter")
    parser.add_argument("file", nargs="?", help="Prompt file (.txt)")
    parser.add_argument("--no-camera", action="store_true", help="Disable camera overlay")
    parser.add_argument("--windowed", action="store_true", help="Run windowed (not fullscreen)")
    args = parser.parse_args()

    # Special: check keys
    if args.file == "check_keys":
        check_presenter_keys()
        raise SystemExit(0)

    # Linux GUI guard
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        print("[ERROR] No graphical display detected (DISPLAY/WAYLAND_DISPLAY not set).")
        raise SystemExit(2)

    # Load script content
    if args.file:
        if not os.path.isfile(args.file):
            raise FileNotFoundError(f"File not found: {args.file}")
        script_lines = load_script(args.file)
    else:
        files = sorted(glob.glob(os.path.join(prompt_dir, "*.txt")))
        if not files:
            raise FileNotFoundError(f"No .txt files found in {prompt_dir}")
        script_lines = load_script(files[current_file_idx])

    # Camera init (optional)
    cap = None
    camera_available = False
    if not args.no_camera:
        cap = cv2.VideoCapture(0)
        camera_available = cap.isOpened()
        if camera_available:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
            print("Camera detected and initialized")
        else:
            print("No camera detected - using black background")
            if cap is not None:
                cap.release()
            cap = None

    cv2.namedWindow("Teleprompter", cv2.WINDOW_NORMAL)
    if not args.windowed:
        cv2.setWindowProperty("Teleprompter", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)

    text_y = start_y
    scrolling = True
    frame_count = 0
    max_failed_reads = 100
    layout_initialized = False
    debug_keys = False  # toggle runtime key logging with 'k'
    focus_on = True     # Laplacian focus overlay (toggle with 'f')
    blackout_on = False # Presenter black screen toggle

    # Page mode state (default enabled)
    page_mode = True
    page_anchor_y = 120  # align page anchor near top

    def compute_page_anchors(lines):
        anchors = []
        # Always consider the start of file as an anchor
        anchors.append(0)
        for i, ln in enumerate(lines):
            s = ln.lstrip()
            if s.startswith("========"):
                # Next non-marker line becomes an anchor if it exists
                anchor = i + 1 if i + 1 < len(lines) else i
                anchors.append(anchor)
        # Deduplicate and sort
        anchors = sorted(set([a for a in anchors if 0 <= a < len(lines)]))
        return anchors

    page_anchors = compute_page_anchors(script_lines)

    def jump_to_anchor(anchor_idx):
        global text_y
        text_y = page_anchor_y - anchor_idx * line_spacing

    def get_current_top_index():
        # Estimate which line is aligned to the anchor (page_anchor_y)
        return max(0, int(round((page_anchor_y - text_y) / float(line_spacing))))

    def jump_next_page():
        cur_top = get_current_top_index()
        for a in page_anchors:
            if a > cur_top + 1:  # strictly after current view
                jump_to_anchor(a)
                return
        # If none found, stay at last page

    def jump_prev_page():
        cur_top = get_current_top_index()
        prev = None
        for a in page_anchors:
            if a < cur_top:
                prev = a
            else:
                break
        if prev is not None:
            jump_to_anchor(prev)

    # If starting in page mode, align to the first page anchor immediately
    if page_mode and page_anchors:
        jump_to_anchor(page_anchors[0])
        layout_initialized = True  # prevent first-frame override of text_y

    # Runtime state from web UI (focus/flip)
    RUNTIME_STATE = Path(__file__).parent / "runtime_state.json"
    # Load presenter mappings from config (JSON). Generic is kept in code.
    try:
        if PRESENTERS_CONFIG.exists():
            with open(PRESENTERS_CONFIG, 'r', encoding='utf-8') as pf:
                data = json.load(pf)
                if isinstance(data, dict):
                    _presenter_maps = data
    except Exception:
        _presenter_maps = {}
    def _maybe_load_state(now):
        """Reload focus/flip from runtime_state.json if it changed (checked at most 2x/sec)."""
        global _state_last_mtime, focus_on, flip_video, _state_last_check, presenter_profile
        if now - _state_last_check < 0.5:
            return
        _state_last_check = now
        try:
            if RUNTIME_STATE.exists():
                m = RUNTIME_STATE.stat().st_mtime
                if m > _state_last_mtime:
                    with open(RUNTIME_STATE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    focus_on = bool(data.get('focus_on', focus_on))
                    flip_video = bool(data.get('flip_video', flip_video))
                    presenter_profile = str(data.get('presenter_profile', presenter_profile))
                    _state_last_mtime = m
        except Exception:
            # ignore state errors
            pass

    try:
        # Prepare key log file (for service/non-console runs)
        keylog_file = Path(__file__).parent / "teleprompter.log"

        while True:
            # Background frame
            if camera_available and cap is not None:
                ret, frame = cap.read()
                if not ret:
                    frame_count += 1
                    if frame_count > max_failed_reads:
                        print("Error: Too many failed camera reads, switching to black background")
                        camera_available = False
                        if cap is not None:
                            cap.release()
                        cap = None
                    else:
                        time.sleep(0.1)
                        continue
                else:
                    frame_count = 0
                    frame = cv2.flip(frame, 0)

                frame = cv2.rotate(frame, cv2.ROTATE_180)

                # Simple horizontal flip controlled by web UI
                if not flip_video:
                    frame = cv2.flip(frame, 1)
            else:
                frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)

            # Use actual frame size for layout
            fh, fw = frame.shape[:2]
            if not layout_initialized:
                text_y = fh
                layout_initialized = True

            # Keep a copy for focus edges before dimming
            pre_dim_frame = frame.copy()

            # Dim video by blending with black
            overlay = np.zeros_like(frame)
            frame = cv2.addWeighted(frame, 1 - alpha, overlay, alpha, 0)

            # Focus assist: Laplacian magnitude overlay (on by default)
            if focus_on and not blackout_on:
                try:
                    gray = cv2.cvtColor(pre_dim_frame, cv2.COLOR_BGR2GRAY)
                    lap16 = cv2.Laplacian(gray, cv2.CV_16S, ksize=3)
                    lap = cv2.convertScaleAbs(lap16)
                    # Otsu to adaptively pick a focus threshold across lighting/cameras
                    _, mask = cv2.threshold(lap, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
                    red_layer = np.zeros_like(frame)
                    red_layer[:, :, 2] = mask  # red channel
                    frame = cv2.addWeighted(frame, 1.0, red_layer, 0.6, 0)
                except Exception:
                    # If anything goes wrong, disable to avoid loop failures
                    focus_on = False

            # Draw text (unless blackout)
            lines_drawn = 0
            if not blackout_on:
                for l, line in enumerate(script_lines):
                    this_y = int(text_y + l * line_spacing)
                    if -line_spacing < this_y < fh + line_spacing:
                        text_size = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)[0]
                        x_pos = int((fw - text_size[0]) / 2)
                        cv2.putText(frame, line, (x_pos, this_y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), font_thickness)
                        lines_drawn += 1

                if lines_drawn == 0:
                    cv2.putText(frame, "No text visible (scrolling)...", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (180, 180, 180), 2)
            else:
                # Force full black output
                frame[:] = 0

            # Help overlay (hidden during blackout)
            if not blackout_on:
                help_text = 'q: quit | o: fullscreen | f: focus | < >: prev/next | ^ v : speed | space: pause | p/Enter/F5: page mode'
                cv2.putText(frame, help_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (100, 100, 100), 2)

            # Page mode indicator (hidden during blackout)
            if page_mode and not blackout_on:
                cv2.putText(frame, 'PAGE MODE', (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)

            # Mirror/rotate for glass rigs (legacy)
            if enable_mirror_rotate:
                frame = cv2.flip(frame, output_flip)
                frame = cv2.rotate(frame, output_rotate)

            # Show (with optional blank screen for presenter 'black screen' button)
            display_frame = frame
            cv2.imshow("Teleprompter", display_frame)

            # Scroll text upward if not paused
            if scrolling and not page_mode and not blackout_on:
                text_y -= scroll_speed
            if text_y + len(script_lines) * line_spacing < 0:
                text_y = fh

            # Periodically reload state from web UI
            _maybe_load_state(time.time())

            # Read key in both raw and masked forms (special keys need raw)
            key_raw = cv2.waitKey(30)
            key = key_raw & 0xFF

            if debug_keys and key_raw != -1:
                # Print both values and append to log for service scenarios
                msg = f"[KEY] raw={key_raw} masked={key}"
                print(msg)
                try:
                    with open(keylog_file, 'a', encoding='utf-8') as lf:
                        lf.write(msg + "\n")
                except Exception:
                    pass

            # Presenter profile specific mappings from config (if not generic)
            # Known raw codes often observed for different platforms; the 'k' logger can help refine
            if presenter_profile and presenter_profile != "generic":
                m = _presenter_maps.get(presenter_profile) or {}
                KEY_BLACK_RAW = set(m.get("KEY_BLACK_RAW", []))
                KEY_LEFT_RAW = set(m.get("KEY_LEFT_RAW", []))
                KEY_RIGHT_RAW = set(m.get("KEY_RIGHT_RAW", []))
                KEY_PAGEUP_RAW = set(m.get("KEY_PAGEUP_RAW", []))
                KEY_PAGEDOWN_RAW = set(m.get("KEY_PAGEDOWN_RAW", []))
                KEY_PLAY_RAW = set(m.get("KEY_PLAY_RAW", []))
                KEY_PREV_ASCII = set(m.get("KEY_PREV_ASCII", []))
                KEY_NEXT_ASCII = set(m.get("KEY_NEXT_ASCII", []))
            else:
                # Generic built-in defaults
                KEY_BLACK_RAW = {98, 66}
                KEY_LEFT_RAW = {81, 2424832}
                KEY_RIGHT_RAW = {83, 2555904}
                KEY_PAGEUP_RAW = {65365, 2162688}
                KEY_PAGEDOWN_RAW = {65366, 2228224}
                KEY_PLAY_RAW = {13, 0x74, 65474}
                KEY_PREV_ASCII = {60}
                KEY_NEXT_ASCII = {62}

            if key == ord('q'):
                break
            elif key == ord('o'):
                fullscreen = not fullscreen
                if fullscreen:
                    cv2.setWindowProperty("Teleprompter", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                else:
                    cv2.setWindowProperty("Teleprompter", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            elif key == ord('f'):
                focus_on = not focus_on
            elif key in (32, 27):  # Spacebar, Escape -> pause/resume scrolling
                # If Escape is used as Play/Page toggle in current mapping, don't use it for pause.
                if key == 27 and (27 in KEY_PLAY_RAW or key_raw in KEY_PLAY_RAW):
                    pass  # handled below as a PAGE MODE toggle
                else:
                    scrolling = not scrolling
            else:
                # Use computed groups above
                if (key_raw in KEY_LEFT_RAW) or (key_raw in KEY_PAGEUP_RAW) or (key in KEY_PREV_ASCII):
                    if page_mode:
                        jump_prev_page()
                    else:
                        scroll_speed = max(scroll_speed - 1, 1)
                elif (key_raw in KEY_RIGHT_RAW) or (key_raw in KEY_PAGEDOWN_RAW) or (key in KEY_NEXT_ASCII):
                    if page_mode:
                        jump_next_page()
                    else:
                        scroll_speed = min(scroll_speed + 1, 20)
                elif (key_raw in KEY_PLAY_RAW) or (key in (ord('p'), ord('P'))):
                    # Toggle page mode; align appropriately
                    page_mode = not page_mode
                    cur_top = get_current_top_index()
                    if page_mode:
                        # Align to current page start
                        candidate = 0
                        for a in page_anchors:
                            if a <= cur_top:
                                candidate = a
                            else:
                                break
                        jump_to_anchor(candidate)
                    else:
                        # Leaving page mode: keep current top line visible
                        text_y = page_anchor_y - cur_top * line_spacing
                elif (key_raw in KEY_BLACK_RAW) or (key in (ord('b'), ord('B'))):
                    # Toggle blackout mode
                    blackout_on = not blackout_on
                elif ((key in (46,) and 46 not in KEY_BLACK_RAW) or key in (ord('r'), ord('R'))):
                    # Reload current file list and re-read the same index
                    files = sorted(glob.glob(os.path.join(prompt_dir, "*.txt")))
                    if files:
                        idx = current_file_idx if current_file_idx < len(files) else 0
                        script_lines = load_script(files[idx])
                        text_y = fh
                        page_anchors = compute_page_anchors(script_lines)
                elif key in (ord('k'), ord('K')):
                    debug_keys = not debug_keys
                    state_msg = f"[INFO] Key debug {'ON' if debug_keys else 'OFF'}"
                    print(state_msg)
                    try:
                        with open(keylog_file, 'a', encoding='utf-8') as lf:
                            lf.write(state_msg + "\n")
                    except Exception:
                        pass
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
