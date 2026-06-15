# LabReplay — Stream Simulator & Integration Testing

This folder contains a fully self-contained Lab Streaming Layer (LSL) physiological and behavioral stream simulator designed to facilitate offline development, testing, and debugging.

##  How to Run the Simulator

Ensure you have your Python environment activated (the workspace relies on `uv` or standard Python with `pylsl` installed).

Run the simulator directly from the workspace root directory:

```bash
uv run python Testing/lsl_simulator.py
```

---

##  Simulated Streams & Metadata Schemas

The simulator registers **7 distinct streams** on the local network that map exactly to the production definitions:

1. **`Polar Sense D851B82E_HR`** (Type: `HR`, Rate: 1 Hz)
   - Real-time simulated heart rate. Generates a sinusoidal sinus arrhythmia fluctuation under idle states, with tiny sensor noise.
2. **`Neon Companion_Neon Gaze`** (Type: `Gaze`, Rate: 200 Hz)
   - A full 16-channel eye-tracking stream. Simulates smooth sweeps and micro-movements, periodic sharp eye saccades (shifts), and highly correlated binocular pupil diameters (left/right).
3. **`HRV_Live`** (Type: `HRV`, Rate: 0.2 Hz)
   - Real-time heart rate variability parameters (`mean_hr`, `sdnn`, `rmssd`, `pnn50`, `sd1`, `sd2`).
4. **`PhoneSensor_Linear Acceleration`** (Type: `ACC`, Rate: 50 Hz)
   - 3-channel linear accelerometer data (`[ax, ay, az]` in m/s²).
5. **`PhoneSensor_Gyroscope`** (Type: `GYRO`, Rate: 50 Hz)
   - 3-channel gyroscope rotational velocity data (`[gx, gy, gz]` in rad/s).
6. **`SessionInfo`** (Type: `SessionInfo`, Irregular)
   - Broadcasts participant metadata in a JSON string block (`{ "participant_id": "...", "session_name": "...", "drill": "..." }`) that the frontend parses dynamically.
7. **`V_300_VirTraEvents`** (Type: `VirTraEvents`, Irregular)
   - Publishes simulated 4-channel string markers for tactical drill events to test the speech/event ticker.

---

## ⌨️ Interactive Keyboard Controls

While the simulator is running in your terminal, type any of the following command keys and press **Enter** to manipulate the live data streams in real time:

- **`h` — Trigger stress spike**: Heart rate ramps up rapidly to `125+ BPM` and pupil sizes dilate. Lasts for 20 seconds before settling back down.
- **`m` — Trigger high-intensity motion shake**: Generates random high-frequency acceleration bursts on `ACC` and `GYRO` for 3 seconds. Watch the **Motion Intensity** fusion strip chart on the Live Monitor react instantly!
- **`v` — Fire a random VirTra event**: Broadcasts a realistic tactical event (e.g. *Threat Engaged*, *Actor Fired*, *Trainee Shot*).
- **`s` — Regenerate SessionInfo metadata**: Randomizes the participant ID and session name, and re-broadcasts them on the network.
- **`q` — Exit cleanly**: Disposes of all LSL outlets and shuts down.
