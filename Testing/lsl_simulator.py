#!/usr/bin/env python3
"""
lsl_simulator.py — Real-time Physiological & Behavioral LSL Stream Simulator.

Simulates the exact set of streams and XML metadata schemas expected by the
LabReplay ecosystem:
  1. Polar Sense D851B82E_HR (Heart Rate @ 1Hz)
  2. Neon Companion_Neon Gaze (Eye Tracking + Pupil @ 200Hz)
  3. HRV_Live (Heart Rate Variability @ 0.2Hz)
  4. PhoneSensor_Linear Acceleration (Accelerometer @ 50Hz)
  5. PhoneSensor_Gyroscope (Gyroscope @ 50Hz)
  6. SessionInfo (Metadata string broadcast, irregular)
  7. V_300_VirTraEvents (Marker / Events, irregular)

Interactive Terminal Controls:
  - [h] Trigger stress heart rate spike
  - [m] Trigger high-intensity motion shake
  - [v] Fire random VirTra scenario event
  - [s] Re-broadcast SessionInfo metadata
  - [q] Exit cleanly and close all outlets
"""

import sys
import time
import math
import random
import json
import threading
import select

try:
    import pylsl
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("CRITICAL ERROR: pylsl library is not installed.")
    print("Please install it with: uv pip install pylsl  or  pip install pylsl")
    sys.exit(1)


# ── Configuration & Metadata Builders ──────────────────────────────────────────

def create_hr_outlet():
    info = pylsl.StreamInfo(
        name="Polar Sense D851B82E_HR",
        type="HR",
        channel_count=1,
        nominal_srate=1.0,
        channel_format="float32",
        source_id="PolarSense_D851B82E_HR_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    ch = channels.append_child("channel")
    ch.append_child_value("label", "HR")
    ch.append_child_value("unit", "BPM")
    ch.append_child_value("type", "HR")
    desc.append_child_value("manufacturer", "Polar")
    desc.append_child_value("model", "Verity Sense")
    return pylsl.StreamOutlet(info)


def create_gaze_outlet():
    info = pylsl.StreamInfo(
        name="Neon Companion_Neon Gaze",
        type="Gaze",
        channel_count=16,
        nominal_srate=200.0,
        channel_format="float32",
        source_id="NeonCompanion_Gaze_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    
    labels = [
        ("x", "px", "Gaze"),
        ("y", "px", "Gaze"),
        ("pupil_diameter_left", "mm", "Pupil"),
        ("eyeball_center_left_x", "mm", "Position"),
        ("eyeball_center_left_y", "mm", "Position"),
        ("eyeball_center_left_z", "mm", "Position"),
        ("optical_axis_left_x", "unit", "Direction"),
        ("optical_axis_left_y", "unit", "Direction"),
        ("optical_axis_left_z", "unit", "Direction"),
        ("pupil_diameter_right", "mm", "Pupil"),
        ("eyeball_center_right_x", "mm", "Position"),
        ("eyeball_center_right_y", "mm", "Position"),
        ("eyeball_center_right_z", "mm", "Position"),
        ("optical_axis_right_x", "unit", "Direction"),
        ("optical_axis_right_y", "unit", "Direction"),
        ("optical_axis_right_z", "unit", "Direction")
    ]
    
    for lbl, unit, ch_type in labels:
        ch = channels.append_child("channel")
        ch.append_child_value("label", lbl)
        ch.append_child_value("unit", unit)
        ch.append_child_value("type", ch_type)
        
    desc.append_child_value("manufacturer", "Pupil Labs")
    desc.append_child_value("model", "Neon")
    return pylsl.StreamOutlet(info)


def create_hrv_outlet():
    info = pylsl.StreamInfo(
        name="HRV_Live",
        type="HRV",
        channel_count=6,
        nominal_srate=0.2,
        channel_format="float32",
        source_id="HRV_Live_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    metrics = ["mean_hr", "sdnn", "rmssd", "pnn50", "sd1", "sd2"]
    for m in metrics:
        ch = channels.append_child("channel")
        ch.append_child_value("label", m)
        ch.append_child_value("unit", "ms" if m != "mean_hr" and m != "pnn50" else ("BPM" if m == "mean_hr" else "%"))
        ch.append_child_value("type", "HRV")
    return pylsl.StreamOutlet(info)


def create_acc_outlet():
    info = pylsl.StreamInfo(
        name="PhoneSensor_Linear Acceleration",
        type="ACC",
        channel_count=3,
        nominal_srate=50.0,
        channel_format="float32",
        source_id="PhoneSensor_LinearAcceleration_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    for axes in ["ax", "ay", "az"]:
        ch = channels.append_child("channel")
        ch.append_child_value("label", axes)
        ch.append_child_value("unit", "m/s²")
        ch.append_child_value("type", "ACC")
    return pylsl.StreamOutlet(info)


def create_gyro_outlet():
    info = pylsl.StreamInfo(
        name="PhoneSensor_Gyroscope",
        type="GYRO",
        channel_count=3,
        nominal_srate=50.0,
        channel_format="float32",
        source_id="PhoneSensor_Gyroscope_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    for axes in ["gx", "gy", "gz"]:
        ch = channels.append_child("channel")
        ch.append_child_value("label", axes)
        ch.append_child_value("unit", "rad/s")
        ch.append_child_value("type", "GYRO")
    return pylsl.StreamOutlet(info)


def create_session_info_outlet():
    info = pylsl.StreamInfo(
        name="SessionInfo",
        type="SessionInfo",
        channel_count=1,
        nominal_srate=0.0,
        channel_format="string",
        source_id="SessionInfo_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    ch = channels.append_child("channel")
    ch.append_child_value("label", "SessionInfoJSON")
    ch.append_child_value("unit", "JSON")
    ch.append_child_value("type", "Metadata")
    return pylsl.StreamOutlet(info)


def create_virtra_outlet():
    info = pylsl.StreamInfo(
        name="V_300_VirTraEvents",
        type="VirTraEvents",
        channel_count=4,
        nominal_srate=0.0,
        channel_format="string",
        source_id="VirTraEvents_Sim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    labels = ["Event Name", "Event Description", "Event Code", "Event Params JSON"]
    for lbl in labels:
        ch = channels.append_child("channel")
        ch.append_child_value("label", lbl)
        ch.append_child_value("unit", "text")
        ch.append_child_value("type", "Marker")
    return pylsl.StreamOutlet(info)


# ── Interactive Simulator State & Loops ────────────────────────────────────────

class LSLSimulator:
    def __init__(self):
        self.running = True
        self.lock = threading.Lock()
        
        # Simulated states
        self.base_hr = 72.0
        self.current_hr = 72.0
        self.stress_timer = 0.0
        self.motion_timer = 0.0
        self.time_elapsed = 0.0
        
        # Meta info
        self.participant_id = f"P-{random.randint(100, 999)}"
        self.session_name = f"SIM_DRILL_{int(time.time() % 100000)}"
        self.drill = "behdisc"
        
        # Stream references
        self.hr_outlet = None
        self.gaze_outlet = None
        self.hrv_outlet = None
        self.acc_outlet = None
        self.gyro_outlet = None
        self.session_info_outlet = None
        self.virtra_outlet = None

    def initialize_outlets(self):
        print("\n🚀 Initializing LSL simulated outlets...")
        self.hr_outlet = create_hr_outlet()
        self.gaze_outlet = create_gaze_outlet()
        self.hrv_outlet = create_hrv_outlet()
        self.acc_outlet = create_acc_outlet()
        self.gyro_outlet = create_gyro_outlet()
        self.session_info_outlet = create_session_info_outlet()
        self.virtra_outlet = create_virtra_outlet()
        print("✓ All 7 simulated outlets successfully registered on the network!")

    def push_session_info(self):
        with self.lock:
            meta = {
                "participant_id": self.participant_id,
                "session_name": self.session_name,
                "drill": self.drill,
                "synthesized": True,
                "timestamp": time.time()
            }
        payload = json.dumps(meta)
        self.session_info_outlet.push_sample([payload])
        print(f"\n📢 [SessionInfo] Broadcast metadata: {payload}")

    def push_virtra_event(self, name=None, desc=None, code=None, params=None):
        events = [
            ("Threat Engaged", "Trainee engaged active threat #1 in zone B", "ThreatEngaged", {"zone": "B", "trainee_fired": True}),
            ("Actor Fired", "Scenario active actor fired simulated blank rounds", "ActorFired", {"actor_id": 2, "rounds": 1}),
            ("Scenario Started", "V-300 Behavior Discrimination drill started", "DrillStart", {"drill": "behdisc"}),
            ("Trainee Shot", "Traing hit by hostile actor fire in chest plate", "TraineeHit", {"actor_id": 2, "location": "chest"}),
            ("Target Neutralized", "Hostile threat neutralization confirmed", "ThreatNeutralized", {"actor_id": 1})
        ]
        if not name:
            name, desc, code, p_dict = random.choice(events)
            params = json.dumps(p_dict)
        else:
            params = json.dumps(params or {})
            
        self.virtra_outlet.push_sample([name, desc, str(code), params])
        print(f"🎬 [VirTraEvent] Fired: {name} (Code: {code})")

    # ── Thread loops ─────────────────────────────────────────────────────────────

    def start(self):
        self.initialize_outlets()
        
        # Broadcast initial metadata
        time.sleep(0.5)
        self.push_session_info()
        
        # Start background threads for fast-rate streams
        threading.Thread(target=self._gaze_loop, daemon=True).start()
        threading.Thread(target=self._motion_loop, daemon=True).start()
        threading.Thread(target=self._physio_loop, daemon=True).start()
        
        # CLI loop
        self._cli_loop()

    def _physio_loop(self):
        """Simulate Heart Rate (1Hz) and HRV (0.2Hz)"""
        last_hrv_time = 0.0
        
        while self.running:
            start_time = time.time()
            
            with self.lock:
                # Update stress state
                if self.stress_timer > 0:
                    self.stress_timer -= 1.0
                    target_hr = 125.0 + random.uniform(-3, 3)
                    # Smooth ramp up to stress heart rate
                    self.current_hr = self.current_hr * 0.7 + target_hr * 0.3
                else:
                    target_hr = self.base_hr + 4 * math.sin(self.time_elapsed * 0.15)
                    self.current_hr = self.current_hr * 0.95 + target_hr * 0.05
                    
                self.time_elapsed += 1.0
                hr_val = self.current_hr + random.uniform(-0.4, 0.4)
            
            # Push heart rate (1Hz)
            self.hr_outlet.push_sample([float(hr_val)])
            
            # Push HRV occasionally (0.2Hz, every 5s)
            now = time.time()
            if now - last_hrv_time >= 5.0:
                last_hrv_time = now
                with self.lock:
                    # RMSSD decreases when stress/heart rate is high
                    if self.stress_timer > 0:
                        rmssd = 35.0 + random.uniform(-4, 4)
                        sdnn = 40.0 + random.uniform(-5, 5)
                    else:
                        rmssd = 62.0 + random.uniform(-8, 8) + 5 * math.sin(self.time_elapsed * 0.05)
                        sdnn = 70.0 + random.uniform(-10, 10)
                        
                self.hrv_outlet.push_sample([
                    float(hr_val),  # mean_hr
                    float(sdnn),    # sdnn
                    float(rmssd),   # rmssd
                    float(rmssd * 0.8), # pnn50
                    float(sdnn * 0.5),  # sd1
                    float(sdnn * 1.2)   # sd2
                ])

            # Synchronize loop to 1 Hz
            elapsed = time.time() - start_time
            sleep_dur = max(0.01, 1.0 - elapsed)
            time.sleep(sleep_dur)

    def _gaze_loop(self):
        """Simulate Gaze and Pupil tracking @ 200 Hz (approx. 5ms intervals)"""
        tick = 0
        gaze_x = 800.0
        gaze_y = 600.0
        
        while self.running:
            start_time = time.time()
            tick += 1
            
            with self.lock:
                # Gaze sweeps smoothly with occasional quick jumps (saccades)
                if tick % 160 == 0:  # Every 0.8s, trigger a saccade
                    gaze_x = random.uniform(300, 1300)
                    gaze_y = random.uniform(200, 1000)
                else:
                    # Small brownian micro-movements
                    gaze_x += random.uniform(-4, 4)
                    gaze_y += random.uniform(-3, 3)
                    gaze_x = max(100, min(1500, gaze_x))
                    gaze_y = max(50, min(1150, gaze_y))
                
                # Pupil dilation (responds to simulated stress or random fluctuation)
                if self.stress_timer > 0:
                    base_pupil = 5.2 + 0.5 * math.sin(tick * 0.01)
                else:
                    base_pupil = 3.6 + 0.3 * math.sin(tick * 0.005)
                    
                pupil_l = base_pupil + random.uniform(-0.06, 0.06)
                pupil_r = pupil_l + random.uniform(-0.03, 0.03)  # Left/Right pupils are correlated

            # Construct 16-channel gaze data
            sample = [
                float(gaze_x),         # 0. x (px)
                float(gaze_y),         # 1. y (px)
                float(pupil_l),        # 2. pupil_diameter_left (mm)
                0.12, -0.04, 52.3,     # 3,4,5. eyeball_center_left_x/y/z
                0.01, 0.02, 0.99,      # 6,7,8. optical_axis_left_x/y/z
                float(pupil_r),        # 9. pupil_diameter_right (mm)
                -0.12, -0.04, 52.1,    # 10,11,12. eyeball_center_right_x/y/z
                -0.01, 0.02, 0.99      # 13,14,15. optical_axis_right_x/y/z
            ]
            
            self.gaze_outlet.push_sample(sample)
            
            # Target 200 Hz
            elapsed = time.time() - start_time
            sleep_dur = max(0.0005, 0.005 - elapsed)
            time.sleep(sleep_dur)

    def _motion_loop(self):
        """Simulate Phone Accelerometer (50Hz) and Gyroscope (50Hz)"""
        tick = 0
        while self.running:
            start_time = time.time()
            tick += 1
            
            with self.lock:
                # Normal low noise motion
                ax = random.uniform(-0.15, 0.15)
                ay = random.uniform(-0.15, 0.15)
                az = random.uniform(-0.15, 0.15)
                
                gx = random.uniform(-0.03, 0.03)
                gy = random.uniform(-0.03, 0.03)
                gz = random.uniform(-0.03, 0.03)
                
                # Active shake overlay
                if self.motion_timer > 0:
                    self.motion_timer -= 0.02  # 50Hz = 20ms steps
                    shake_factor = math.sin(tick * 0.8) * 8.0 * (self.motion_timer / 3.0)
                    ax += shake_factor + random.uniform(-1, 1)
                    ay += shake_factor * 0.7 + random.uniform(-1, 1)
                    az += shake_factor * 0.5 + random.uniform(-1, 1)
                    
                    gx += math.cos(tick * 0.8) * 2.2 * (self.motion_timer / 3.0)
                    gy += math.sin(tick * 0.6) * 1.5 * (self.motion_timer / 3.0)
                    gz += math.sin(tick * 0.4) * 1.2 * (self.motion_timer / 3.0)

            self.acc_outlet.push_sample([float(ax), float(ay), float(az)])
            self.gyro_outlet.push_sample([float(gx), float(gy), float(gz)])
            
            # Target 50 Hz
            elapsed = time.time() - start_time
            sleep_dur = max(0.001, 0.02 - elapsed)
            time.sleep(sleep_dur)

    # ── Interactive Command Line Interface ────────────────────────────────────────

    def _cli_loop(self):
        print("\n=======================================================")
        print("🧠  LabReplay Physiological & Behavioral LSL Simulator  🧠")
        print("=======================================================")
        print(f"📡 Participant ID:   {self.participant_id}")
        print(f"🎬 Session Name:     {self.session_name}")
        print(f"📖 Drill Key:         {self.drill}")
        print("-------------------------------------------------------")
        print("⌨️  Interactive Commands (Press keys + enter to trigger):")
        print("   [h] Trigger stress heart rate spike (HR -> 125 BPM)")
        print("   [m] Trigger high-intensity hand motion/shake (fuses ACC + GYRO)")
        print("   [v] Fire a random VirTra tactical scenario event")
        print("   [s] Regenerate & re-broadcast SessionInfo metadata")
        print("   [q] Shutdown simulator and close all LSL outlets")
        print("=======================================================\n")
        
        while self.running:
            # Check for console input
            r, _, _ = select.select([sys.stdin], [], [], 0.5)
            if r:
                line = sys.stdin.readline().strip().lower()
                if not line:
                    continue
                cmd = line[0]
                
                if cmd == 'q':
                    print("\n🛑 Shutting down simulator...")
                    self.running = False
                elif cmd == 'h':
                    with self.lock:
                        self.stress_timer = 20.0  # stress lasts for 20 seconds
                    print("\n💓 [Stress Spike] Triggered! Heart rate accelerating up to 125+ BPM...")
                elif cmd == 'm':
                    with self.lock:
                        self.motion_timer = 3.0  # shake lasts for 3 seconds
                    print("\n⚡ [Motion Shake] Triggered! Generating 50Hz high-frequency hand acceleration...")
                elif cmd == 'v':
                    self.push_virtra_event()
                elif cmd == 's':
                    with self.lock:
                        self.participant_id = f"P-{random.randint(100, 999)}"
                        self.session_name = f"SIM_DRILL_{int(time.time() % 100000)}"
                    self.push_session_info()
                else:
                    print(f"Unknown command: '{cmd}'. Press [h], [m], [v], [s], or [q].")
                    
        # Teardown
        print("✓ All simulated LSL outlets successfully disposed. Goodbye!")


if __name__ == "__main__":
    sim = LSLSimulator()
    sim.start()
