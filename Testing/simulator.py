#!/usr/bin/env python3
"""
behdisc_simulator.py — Automated Real-Time BehDisc Scenario LSL Simulator.

Simulates a ~70-second tactical training scenario to test the
Real-Time Human Performance dashboard and post-session epoch analytics.

IMPORTANT: Run the simulator first, navigate to Real-Time Human Performance
in the browser, and click Start BEFORE the 10-second mark.

Timeline (~70 seconds total):
  - 0.0s : Initialize streams on the network
  - 2.0s : Broadcast SessionInfo metadata
  - 10.0s: Trigger "Scenario Started"     ← user has 8s to navigate + click Start
  - 13.0s: Engagement 1 (Hostile   - Correct  ): Screen_1, FA, FMH, Shot Hit
  - 23.0s: Engagement 2 (NonHostile - Correct  ): Screen_2, FA, FMNH, No-Shoot
  - 34.0s: Engagement 3 (Hostile   - Incorrect): Screen_3, FA, FMH, Shot Miss
  - 45.0s: Engagement 4 (NonHostile - Incorrect): Screen_4, FA, FMNH, Shot Miss
  - 56.0s: Engagement 5 (Hostile   - Correct  ): Screen_5, FA, FMH, Instructor Shot (excluded), Trainee Hit
  - 65.0s: Trigger "Scenario Stopped" → browser auto-stops capture
  - 67.0s: Close outlets and shutdown
"""

import sys
import time
import math
import random
import json
import threading

try:
    import pylsl
    LSL_AVAILABLE = True
except ImportError:
    LSL_AVAILABLE = False
    print("CRITICAL ERROR: pylsl library is not installed.")
    print("Please install it with: uv pip install pylsl  or  pip install pylsl")
    sys.exit(1)


# ── ANSI Terminal Colors ──────────────────────────────────────────────────────
C_RESET = "\033[0m"
C_BOLD = "\033[1m"
C_CYAN = "\033[36m"
C_GREEN = "\033[32m"
C_YELLOW = "\033[33m"
C_RED = "\033[31m"
C_MAGENTA = "\033[35m"
C_BG_BLUE = "\033[44;37m"


# ── LSL Outlet Factories ──────────────────────────────────────────────────────

def create_hr_outlet():
    info = pylsl.StreamInfo(
        name="Polar Sense D851B82E_HR",
        type="HR",
        channel_count=1,
        nominal_srate=1.0,
        channel_format="float32",
        source_id="PolarSense_HR_BehDiscSim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    ch = channels.append_child("channel")
    ch.append_child_value("label", "HR")
    ch.append_child_value("unit", "BPM")
    ch.append_child_value("type", "HR")
    return pylsl.StreamOutlet(info)


def create_gaze_outlet():
    info = pylsl.StreamInfo(
        name="Neon Companion_Neon Gaze",
        type="Gaze",
        channel_count=16,
        nominal_srate=50.0,  # 50Hz is plenty fast for testing and lightweight in Python
        channel_format="float32",
        source_id="NeonCompanion_Gaze_BehDiscSim"
    )
    desc = info.desc()
    channels = desc.append_child("channels")
    
    labels = [
        ("x", "px", "Gaze"), ("y", "px", "Gaze"),
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
    return pylsl.StreamOutlet(info)


def create_session_info_outlet():
    info = pylsl.StreamInfo(
        name="SessionInfo",
        type="SessionInfo",
        channel_count=1,
        nominal_srate=0.0,
        channel_format="string",
        source_id="SessionInfo_BehDiscSim"
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
        source_id="VirTraEvents_BehDiscSim"
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



class BehDiscSimulator:
    def __init__(self):
        self.running = True
        self.lock = threading.Lock()
        
        # Physiological state
        self.target_hr = 72.0
        self.current_hr = 72.0
        self.target_pupil = 3.6
        self.current_pupil = 3.6
        
        # Meta info
        self.participant_id = "TEST-001"
        self.session_name = f"BEHDISC_LIVE_TEST_{int(time.time() % 1000)}"
        self.drill = "behdisc"
        
        # Outlets
        self.hr_outlet = None
        self.gaze_outlet = None
        self.session_info_outlet = None
        self.virtra_outlet = None
        
        # Timeline events
        self.events = []
        self.start_mono_time = 0.0

    def initialize(self):
        print(f"\n{C_BOLD}{C_CYAN}[BehDiscSim]{C_RESET} Spawning LSL streams...")
        self.hr_outlet = create_hr_outlet()
        self.gaze_outlet = create_gaze_outlet()
        self.session_info_outlet = create_session_info_outlet()
        self.virtra_outlet = create_virtra_outlet()
        print(f"{C_GREEN}Polar HR, Neon Gaze, SessionInfo, and VirTraEvents outlets ready!{C_RESET}")

    def push_session_info(self):
        meta = {
            "participant_id": self.participant_id,
            "session_name": self.session_name,
            "drill": self.drill,
            "synthesized": True,
            "timestamp": time.time()
        }
        payload = json.dumps(meta)
        self.session_info_outlet.push_sample([payload])
        print(f"\n{C_CYAN}[SessionInfo]{C_RESET} Metadata broadcasted: participant={self.participant_id}, session={self.session_name}")

    def push_event(self, name, desc, code, params=None):
        params_str = json.dumps(params or [])
        self.virtra_outlet.push_sample([name, desc, str(code), params_str])
        print(f"{C_YELLOW}[VirTraEvent]{C_RESET} {C_BOLD}{name}{C_RESET} — {desc}")

    # ── Physio loops ──────────────────────────────────────────────────────────

    def _physio_loop(self):
        """Simulate dynamic Polar Heart Rate @ 1Hz"""
        tick = 0
        while self.running:
            start_t = time.time()
            
            with self.lock:
                # Add sinusoidal respiratory sinus arrhythmia (RSA) + tiny noise
                tick += 1
                rsa = 2.0 * math.sin(tick * 0.15)
                noise = random.uniform(-0.3, 0.3)
                
                # Smoothly shift current HR towards target
                self.current_hr = (self.current_hr * 0.9) + (self.target_hr * 0.1)
                hr_val = self.current_hr + rsa + noise
                
            self.hr_outlet.push_sample([float(hr_val)])
            
            # Wait precisely 1.0s
            elapsed = time.time() - start_t
            time.sleep(max(0.01, 1.0 - elapsed))

    def _gaze_loop(self):
        """Simulate Eye Gaze & Pupil Tracking @ 50Hz"""
        tick = 0
        gx, gy = 800.0, 600.0
        
        while self.running:
            start_t = time.time()
            tick += 1
            
            with self.lock:
                # Smooth brownian motion for eye gaze
                gx += random.uniform(-3, 3)
                gy += random.uniform(-2, 2)
                gx = max(200, min(1400, gx))
                gy = max(150, min(1050, gy))
                
                # Smoothly shift pupil diameter towards target
                self.current_pupil = (self.current_pupil * 0.95) + (self.target_pupil * 0.05)
                pup_l = self.current_pupil + random.uniform(-0.04, 0.04)
                pup_r = pup_l + random.uniform(-0.02, 0.02)
                
            # Full 16-channel Neon Companion Gaze simulation sample
            sample = [
                float(gx), float(gy),  # x, y gaze
                float(pup_l),          # pupil left diameter
                0.12, -0.04, 52.3,     # eye position
                0.01, 0.02, 0.99,      # eye direction
                float(pup_r),          # pupil right diameter
                -0.12, -0.04, 52.1,
                -0.01, 0.02, 0.99
            ]
            self.gaze_outlet.push_sample(sample)
            
            # Wait precisely 20ms (50Hz)
            elapsed = time.time() - start_t
            time.sleep(max(0.001, 0.02 - elapsed))

    # ── Timeline Script ────────────────────────────────────────────────────────

    def set_physio_targets(self, hr, pupil):
        with self.lock:
            self.target_hr = hr
            self.target_pupil = pupil
            print(f" {C_MAGENTA}[Physio Spike]{C_RESET} Stress response changed: Target HR={hr} BPM, Pupil={pupil} mm")

    def build_timeline(self):
        # 2s: session metadata broadcast
        self.events.append((2.0, self.push_session_info))

        # 10s: Scenario Started — user has ~8s to navigate + click Start
        self.events.append((10.0, lambda: [
            self.set_physio_targets(74.0, 3.6),
            self.push_event(
                "Scenario Started",
                "Scenario V-300 Behavior Discrimination started",
                "SS - Scenario Started",
                [["Scenario", "BehDisc_RealTime_Test"], ["SessionID", self.session_name]]
            )
        ]))

        # ── Engagement 1 (Hostile - Correct Decision) ─────────────────────────
        # Trainee fires at threat actor and hits. (Hit, correct)
        self.events.append((13.0, lambda: [
            self.push_event("Event Triggered", "Screen_1 was triggered", "ET - Event Triggered")
        ]))
        self.events.append((14.5, lambda: [
            self.set_physio_targets(82.0, 4.1),
            self.push_event("Actor Event", "A1_ADM_MEMC_M_1_1_S1 First Appearance event", "FA - First Appearance")
        ]))
        self.events.append((16.0, lambda: [
            self.set_physio_targets(115.0, 5.4),
            self.push_event("Actor Event", "A1_ADM_MEMC_M_1_1_S1 First Movement Hostile event", "FMH - First Movement Hostile")
        ]))
        self.events.append((17.2, lambda: [
            self.push_event("Shot Fired", "Trainee 1 fired LID3_P1", "TFW - Trainee Fires Weapon", [["ShotID", "101"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((17.4, lambda: [
            self.push_event("Shot Hit", "Trainee 1 hit Actor A1_ADM_MEMC_M_1_1_S1", "TFW - Trainee Fires Weapon", [["ShotID", "101"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((19.0, lambda: [
            self.set_physio_targets(80.0, 3.8),
            self.push_event("Event Triggered", "Training_Event_Completed was triggered", "ET - Event Triggered")
        ]))

        # ── Engagement 2 (Non-Hostile - Correct Decision) ─────────────────────
        # Bystander appears. Trainee holds fire. (No Shoot, correct)
        self.events.append((23.0, lambda: [
            self.push_event("Event Triggered", "Screen_2 was triggered", "ET - Event Triggered")
        ]))
        self.events.append((24.2, lambda: [
            self.set_physio_targets(81.0, 4.0),
            self.push_event("Actor Event", "B2_ADM_BMC_F_1_2_S1 First Appearance event", "FA - First Appearance")
        ]))
        self.events.append((25.8, lambda: [
            self.set_physio_targets(85.0, 4.2),
            self.push_event("Actor Event", "B2_ADM_BMC_F_1_2_S1 First Movement Non-Hostile event", "FMNH - First Movement Non-Hostile")
        ]))
        self.events.append((30.0, lambda: [
            self.set_physio_targets(74.0, 3.6),
            self.push_event("Event Triggered", "Training_Event_Completed was triggered", "ET - Event Triggered")
        ]))

        # ── Engagement 3 (Hostile - Incorrect Decision) ───────────────────────
        # Hostile actor appears, trainee shoots and MISSES! (Miss, incorrect)
        self.events.append((34.0, lambda: [
            self.push_event("Event Triggered", "Screen_3 was triggered", "ET - Event Triggered")
        ]))
        self.events.append((35.5, lambda: [
            self.set_physio_targets(84.0, 4.2),
            self.push_event("Actor Event", "A3_ADM_MEMC_M_2_3_S1 First Appearance event", "FA - First Appearance")
        ]))
        self.events.append((37.0, lambda: [
            self.set_physio_targets(122.0, 5.6),
            self.push_event("Actor Event", "A3_ADM_MEMC_M_2_3_S1 First Movement Hostile event", "FMH - First Movement Hostile")
        ]))
        self.events.append((38.2, lambda: [
            self.push_event("Shot Fired", "Trainee 1 fired LID3_P1", "TFW - Trainee Fires Weapon", [["ShotID", "102"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((38.4, lambda: [
            self.push_event("Shot Miss", "Trainee 1 missed", "TFW - Trainee Fires Weapon", [["ShotID", "102"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((41.0, lambda: [
            self.set_physio_targets(95.0, 4.4),
            self.push_event("Event Triggered", "Training_Event_Completed was triggered", "ET - Event Triggered")
        ]))

        # ── Engagement 4 (Non-Hostile - Incorrect Decision) ───────────────────
        # Bystander appears. Trainee panics and shoots non-hostile! (incorrect)
        self.events.append((45.0, lambda: [
            self.push_event("Event Triggered", "Screen_4 was triggered", "ET - Event Triggered")
        ]))
        self.events.append((46.2, lambda: [
            self.set_physio_targets(83.0, 4.1),
            self.push_event("Actor Event", "B4_ADM_BMC_M_1_4_S1 First Appearance event", "FA - First Appearance")
        ]))
        self.events.append((47.8, lambda: [
            self.set_physio_targets(98.0, 4.8),
            self.push_event("Actor Event", "B4_ADM_BMC_M_1_4_S1 First Movement Non-Hostile event", "FMNH - First Movement Non-Hostile")
        ]))
        self.events.append((49.2, lambda: [
            self.push_event("Shot Fired", "Trainee 1 fired LID3_P1", "TFW - Trainee Fires Weapon", [["ShotID", "103"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((49.4, lambda: [
            self.push_event("Shot Miss", "Trainee 1 missed B4_ADM_BMC_M_1_4_S1", "TFW - Trainee Fires Weapon", [["ShotID", "103"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((52.0, lambda: [
            self.set_physio_targets(85.0, 4.0),
            self.push_event("Event Triggered", "Training_Event_Completed was triggered", "ET - Event Triggered")
        ]))

        # ── Engagement 5 (Hostile w/ Instructor Shot - Trainee Correct) ───────
        # Instructor fires (excluded), trainee fires and hits threat. (correct)
        self.events.append((56.0, lambda: [
            self.push_event("Event Triggered", "Screen_5 was triggered", "ET - Event Triggered")
        ]))
        self.events.append((57.2, lambda: [
            self.set_physio_targets(85.0, 4.2),
            self.push_event("Actor Event", "A5_ADM_MEMC_M_3_5_S1 First Appearance event", "FA - First Appearance")
        ]))
        self.events.append((58.8, lambda: [
            self.set_physio_targets(118.0, 5.5),
            self.push_event("Actor Event", "A5_ADM_MEMC_M_3_5_S1 First Movement Hostile event", "FMH - First Movement Hostile")
        ]))
        self.events.append((59.8, lambda: [
            self.push_event("Shot Fired", "Instructor fired LID3_P2", "IFW - Instructor Fires Weapon", [["ShotID", "201"], ["IsInstructorShot", "True"]])
        ]))
        self.events.append((60.5, lambda: [
            self.push_event("Shot Fired", "Trainee 1 fired LID3_P1", "TFW - Trainee Fires Weapon", [["ShotID", "104"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((60.7, lambda: [
            self.push_event("Shot Hit", "Trainee 1 hit Actor A5_ADM_MEMC_M_3_5_S1", "TFW - Trainee Fires Weapon", [["ShotID", "104"], ["IsInstructorShot", "False"]])
        ]))
        self.events.append((63.0, lambda: [
            self.set_physio_targets(76.0, 3.7),
            self.push_event("Event Triggered", "Training_Event_Completed was triggered", "ET - Event Triggered")
        ]))

        # ── Scenario teardown ──────────────────────────────────────────────────
        self.events.append((65.0, lambda: [
            self.push_event(
                "Scenario Stopped",
                "Scenario stopped in VirTra",
                "ST - Scenario Stopped",
                []
            )
        ]))

        self.events.append((67.0, self.stop_sim))

    def stop_sim(self):
        print(f"\n{C_BOLD}{C_RED}[BehDiscSim]{C_RESET} Shutting down outlets...")
        self.running = False

    def run(self):
        self.initialize()
        self.build_timeline()
        
        # Start physio threads
        threading.Thread(target=self._physio_loop, daemon=True).start()
        threading.Thread(target=self._gaze_loop, daemon=True).start()
        
        print(f"\n{C_BG_BLUE}  BEHDISC LSL SIMULATOR IS ACTIVE  {C_RESET}")
        print(f"Please open: {C_BOLD}http://localhost:8080{C_RESET}")
        print("Navigate to the 'Real-Time Human Performance' page.")
        print(f"Press the green {C_GREEN}▶ Start{C_RESET} button to begin listening to the live streams!")
        print("This simulated run will execute a 60-second scenario automatically...")
        print("------------------------------------------------------------------------")
        
        self.start_mono_time = time.time()
        
        # Process events sequentially
        while self.running and self.events:
            elapsed = time.time() - self.start_mono_time
            # Get next event
            self.events.sort(key=lambda x: x[0])
            next_t, next_fn = self.events[0]
            
            if elapsed >= next_t:
                self.events.pop(0)
                next_fn()
            else:
                time.sleep(0.05)
                
        print(f"\n{C_GREEN}✓ Scenario simulation finished successfully! All streams disposed.{C_RESET}\n")


if __name__ == "__main__":
    sim = BehDiscSimulator()
    sim.run()
