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

# Directory containing prompt text files
prompt_dir = "prompts"

# Get all .txt files in the prompt directory
script_files = sorted(glob.glob(os.path.join(prompt_dir, "*.txt")))
if not script_files:
    raise FileNotFoundError(f"No .txt files found in {prompt_dir}")

current_file_idx = 0

def load_script(file_path):
    with open(file_path, "r") as f:
        lines = f.readlines()
    return [line.strip() for line in lines]

# Configuration variables
fullscreen = True
scroll_speed = 3 # pixels per frame
text_y = 600
line_spacing = 60
font_scale = 1.2
font_thickness = 2
alpha = 0.3
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
    if len(sys.argv) > 1 and sys.argv[1] == "check_keys":
        check_presenter_keys()
    else:
        script_lines = load_script(script_files[current_file_idx])
        
        cap = cv2.VideoCapture('/dev/video0')
        camera_available = cap.isOpened()
        
        if camera_available:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, frame_width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, frame_height)
            print("Camera detected and initialized")
        else:
            print("No camera detected - using black background")
            cap.release()  # Clean up the failed capture object
            cap = None

        cv2.namedWindow("Teleprompter",cv2.WINDOW_NORMAL)
        cv2.setWindowProperty("Teleprompter",cv2.WND_PROP_FULLSCREEN,cv2.WINDOW_FULLSCREEN)

        text_y = start_y
        scrolling = True
        frame_count = 0
        max_failed_reads = 100

        while True:
            if camera_available and cap is not None:
                ret, frame = cap.read()
                if not ret:
                    frame_count += 1
                    if frame_count > max_failed_reads:
                        print("Error: Too many failed camera reads, switching to black background")
                        camera_available = False
                        cap.release()
                        cap = None
                    else:
                        time.sleep(0.1)  # Brief pause before retry
                        continue
                else:
                    frame_count = 0  # Reset counter on successful read
                    # Flip horizontally
                    frame = cv2.flip(frame, 1)
            else:
                # Create black background when no camera
                frame = np.zeros((frame_height, frame_width, 3), dtype=np.uint8)

            # Dim video by blending frame with black overlay
            overlay = frame.copy()
            cv2.rectangle(overlay, (0,0), (frame_width, frame_height), (0,0,0), -1)
            # Swap weights: keep frame at alpha, overlay (black) at 1-alpha for proper dimming
            frame = cv2.addWeighted(frame, alpha, overlay, 1 - alpha, 0)

            # Draw each line
            for l, line in enumerate(script_lines):
                this_y = int(text_y + l * line_spacing)
                if -line_spacing < this_y < frame_height + line_spacing:
                    text_size = cv2.getTextSize(line, cv2.FONT_HERSHEY_SIMPLEX,font_scale,font_thickness)[0]
                    x_pos = int((frame_width - text_size[0]) / 2)
                    cv2.putText(frame,line,(x_pos,this_y),cv2.FONT_HERSHEY_SIMPLEX,font_scale, (255,255,255),font_thickness)

            # Add help
            help_text = 'q: quit | f: fullscreen | < >: prev/next file | ^ v : speed | space: pause'
            cv2.putText(frame,help_text,(10,30),cv2.FONT_HERSHEY_SIMPLEX,1.0,(100,100,100),2)

            frame = cv2.flip(frame,1)
            frame = cv2.rotate(frame,cv2.ROTATE_180)

            # Blend the overlay with the frame (dim video, show text)
            cv2.imshow("Teleprompter",frame)

            # Scroll text upward if not paused
            if scrolling:
                text_y -= scroll_speed
            if text_y + len(script_lines) * line_spacing < 0:
                text_y = start_y

            key = cv2.waitKey(30) & 0xFF

            # Logitech R800 presenter key mapping
            # Play: F5 (key==0x74) or Enter (key==13)
            # Back: Left Arrow (key==81)
            # Forward: Right Arrow (key==83)
            # Screen: '.' (key==46) or 'b' (key==98)

            if key == ord('q'):
                break
            elif key == ord('f'):
                fullscreen = not fullscreen
                if fullscreen:
                    cv2.setWindowProperty("Teleprompter",cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
                else:
                    cv2.setWindowProperty("Teleprompter",cv2.WND_PROP_FULLSCREEN,cv2.WINDOW_NORMAL)
            elif key == 32 or key == 13 or key == 27:  # Spacebar, Enter, Escape
                scrolling = not scrolling
            elif key == 85:  # Left arrow (Back)
                scroll_speed = max(scroll_speed - 1, 1)
            elif key == 86:  # Right arrow (Forward)
                scroll_speed = min(scroll_speed + 1, 20)
            elif key == 46 or key == 98:  # '.' or 'b' (Screen)
                # Reload text files
                script_files = sorted(glob.glob(os.path.join(prompt_dir, "*.txt")))
                if not script_files:
                    raise FileNotFoundError(f"No .txt files found in {prompt_dir}")
                script_lines = load_script(script_files[current_file_idx])
                text_y = start_y

        if cap is not None:
            cap.release()
        cv2.destroyAllWindows()
