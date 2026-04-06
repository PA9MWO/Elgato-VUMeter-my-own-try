# MUST BE THE FIRST LINES OF THE FILE
import numpy as np
import sys
np.fromstring = np.frombuffer # Force the patch immediately
sys.modules['numpy'] = np     # Inject it into the global module cache

import os
import time
import base64
import threading
import soundcard as sc
from io import BytesIO
from PIL import Image
from streamdeck_sdk import StreamDeck, Action

# REDIRECT LOGS FOR DEBUGGING
log_path = os.path.join(os.path.dirname(__file__), "debug_python.log")
sys.stderr = open(log_path, "w", buffering=1)
sys.stdout = sys.stderr

# --- CONFIG ---
MY_ACTION_UUID = "com.yourname.vumeter.segment"
NUM_SEGMENTS = 8
COLORS = ["#00FF00"] * 4 + ["#FFFF00"] * 2 + ["#FF0000"] * 2

class VUMeterAction(Action):
    UUID = MY_ACTION_UUID

    def __init__(self):
        super().__init__()
        self.active_instances = {} 

    def on_will_appear(self, payload):
        context = payload.context
        if context not in self.active_instances:
            self.active_instances[context] = len(self.active_instances)
        print(f"Action appeared: {context} assigned index {self.active_instances[context]}")

    def on_will_disappear(self, payload):
        if payload.context in self.active_instances:
            del self.active_instances[payload.context]

    def on_key_down(self, payload):
        pass

def get_vubar_image(color_hex):
    img = Image.new('RGB', (72, 72), color_hex)
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{img_str}"

# Pre-cached images
CACHE = {c: get_vubar_image(c) for c in COLORS}
CACHE["#111111"] = get_vubar_image("#111111")

from collections import deque

# Calibration Constants
MIN_SENSITIVITY = 0.02
FALL_SPEED = 0.25 
HISTORY_SIZE = 50
vol_history = deque(maxlen=HISTORY_SIZE)
display_level = 0.0

def audio_monitor_loop(sdk):
    # Add this inside audio_monitor_loop to prevent the log warnings
    np.seterr(divide='ignore', invalid='ignore')
    global display_level
    time.sleep(5)
    print("Starting audio monitor loop with auto-device switching...")
    
    current_mic_name = ""
    
    while True:
        try:
            # 1. Detect current default speaker
            speaker = sc.default_speaker()
            mics = sc.all_microphones(include_loopback=True)
            
            # Find the loopback matching the current speaker
            try:
                mic_device = next(m for m in mics if m.name == speaker.name)
            except StopIteration:
                time.sleep(2)
                continue

            # If the device name changed, update logs
            if mic_device.name != current_mic_name:
                print(f"Now monitoring: {mic_device.name}")
                current_mic_name = mic_device.name

            # 2. Start recording on the current device
            with mic_device.recorder(samplerate=44100, channels=2) as mic:
                last_check_time = time.time()
                
                while True:
                    # Every 2 seconds, check if the Windows default device has changed
                    if time.time() - last_check_time > 2.0:
                        new_default = sc.default_speaker().name
                        if new_default != current_mic_name:
                            print(f"Device switch detected: {new_default}. Restarting...")
                            break # Exit this 'with' block to trigger reconnection
                        last_check_time = time.time()

                    # 3. Audio Processing (Standard Logic)
                    raw_data = mic.record(numframes=1024)
                    data = np.nan_to_num(raw_data, nan=0.0)
                    
                    current_vol = np.mean(np.abs(data))
                    vol_history.append(current_vol)
                    max_recent_vol = max(max(vol_history), MIN_SENSITIVITY)
                    
                    target_level = (current_vol / max_recent_vol) * NUM_SEGMENTS
                    
                    if target_level > display_level:
                        display_level = target_level
                    else:
                        display_level = max(0, display_level - FALL_SPEED)
                    
                    level_int = int(display_level)

                    action = sdk.actions.get(MY_ACTION_UUID)
                    if action:
                        for context, seg_index in action.active_instances.items():
                            color_idx = min(seg_index, len(COLORS)-1)
                            img = CACHE[COLORS[color_idx]] if seg_index < level_int else CACHE["#111111"]
                            sdk.set_image(context, img)
                    
                    time.sleep(0.01)
                    
        except Exception as e:
            print(f"Audio Device Error/Lost: {e}. Retrying in 3s...")
            time.sleep(3)

if __name__ == "__main__":
    print("Plugin starting...")
    vumeter_action = VUMeterAction()
    sd = StreamDeck(actions=[vumeter_action])
    
    monitor_thread = threading.Thread(target=audio_monitor_loop, args=(sd,), daemon=True)
    monitor_thread.start()
    
    sd.run()