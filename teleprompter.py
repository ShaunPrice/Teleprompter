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

    try:
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
                    frame = cv2.flip(frame, 1)
            else:
                frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)

            # Use actual frame size for layout
            fh, fw = frame.shape[:2]
            if not layout_initialized:
                text_y = fh
                layout_initialized = True

            # Dim video by blending with black
            overlay = np.zeros_like(frame)
            frame = cv2.addWeighted(frame, 1 - alpha, overlay, alpha, 0)

            # Draw text lines centered
            lines_drawn = 0
            for l, line in enumerate(script_lines):
                this_y = int(text_y + l * line_spacing)
                if -line_spacing < this_y < fh + line_spacing:
                    text_size = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX, font_scale, font_thickness)[0]
                    x_pos = int((fw - text_size[0]) / 2)
                    cv2.putText(frame, line, (x_pos, this_y), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), font_thickness)
                    lines_drawn += 1

            if lines_drawn == 0:
                cv2.putText(frame, "No text visible (scrolling)...", (20, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (180, 180, 180), 2)

            # Help overlay
            help_text = 'q: quit | f: fullscreen | < >: prev/next | ^ v : speed | space: pause'
            cv2.putText(frame, help_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (100, 100, 100), 2)

            # Mirror/rotate for glass rigs
            # frame = cv2.flip(frame, 1)
            # frame = cv2.rotate(frame, cv2.ROTATE_180)

            # Show
            cv2.imshow("Teleprompter", frame)

            # Scroll text upward if not paused
            if scrolling:
                text_y -= scroll_speed
            if text_y + len(script_lines) * line_spacing < 0:
                text_y = fh

            key = cv2.waitKey(30) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('f'):
                fullscreen = not fullscreen
                if fullscreen:
                    cv2.setWindowProperty("Teleprompter", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                else:
                    cv2.setWindowProperty("Teleprompter", cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            elif key in (32, 13, 27):  # Spacebar, Enter, Escape
                scrolling = not scrolling
            elif key in (81,):  # Left arrow
                scroll_speed = max(scroll_speed - 1, 1)
            elif key in (83,):  # Right arrow
                scroll_speed = min(scroll_speed + 1, 20)
            elif key in (46, 98):  # '.' or 'b'
                # Reload current file list and re-read the same index
                files = sorted(glob.glob(os.path.join(prompt_dir, "*.txt")))
                if files:
                    idx = current_file_idx if current_file_idx < len(files) else 0
                    script_lines = load_script(files[idx])
                    text_y = fh
    finally:
        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
