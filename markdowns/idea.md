# LabReplay Architecture and Event-Centered Replay Design

## Mission

LabReplay is not just a physiological dashboard.

The mission of the system is:

“To reconstruct, replay, and interpret human cognitive and behavioral performance during high-stress simulation scenarios using synchronized multimodal data streams.”

The system should behave more like an investigative replay workstation, a behavioral analysis engine, and a cognitive after-action review platform rather than a traditional telemetry dashboard.

It must help researchers and trainers answer questions such as:

* Why did performance degrade here?
* What was the trainee experiencing cognitively?
* Did stress impair situational awareness?
* Was there attentional narrowing?
* How long did recovery take?
* Was this overload, panic, adaptation, or controlled engagement?

The key idea is that physiological signals alone are not meaningful. Meaning emerges only when physiology, behavior, gaze, movement, and simulation events are synchronized and interpreted together across time.

---

## Core Architecture

The platform consists of synchronized multimodal streams:

**Internal State**

* Heart rate
* HRV
* Respiration
* Pupil dilation
* Autonomic arousal

**Behavior**

* Gaze tracking
* Fixation behavior
* Scan patterns
* Motion
* Trigger cadence
* Shooting performance

**Environment**

* VirTra events
* Threat appearance
* Hostage appearance
* Actor state changes
* Hits/misses
* Simulation triggers

LSL provides temporal synchronization, and SQLite stores replay sessions. Everything must align on a unified timeline.

---

## The Most Important Concept

Do not design this as charts, logs, and metrics.

Design it as an event-centered behavioral replay system.

The replay system should revolve around:

* Events
* Episodes
* Transitions
* Interpretation

Not standalone signals.

---

## The Main Design Shift

**Old model:**
Streams → Charts

**New model:**
Event → Physiological reaction → Attentional shift → Behavioral response → Performance outcome → Recovery/adaptation

The replay engine reconstructs this chain.

---

## Replay Philosophy

Replay is not a secondary feature — replay is the product.

Researchers cannot meaningfully analyze overload, stress, panic, adaptation, or attentional collapse in real time. Meaning emerges during replay, rewind, slow motion, event jumping, and contextual inspection.

The system should feel closer to:

* Aviation black-box review
* Sports replay analysis
* Tactical after-action review
* Surgical training replay

---

## Event-Centered Replay

Every replay interaction starts from an event anchor.

Examples of anchor events:

* Threat appearance
* First miss
* Rapid firing sequence
* Hostage exposure
* Overload detection
* HR spike
* Gaze collapse
* Respiration dysregulation

When a user clicks an event, do not replay the entire session linearly. Instead, center the replay around that event and reconstruct surrounding context while synchronizing all streams around that moment.

---

## Fixed Windows Are Not Enough

Do not hardcode replay windows like -5 seconds or +10 seconds.

This fails because:

* Events cluster tightly
* Physiology has delayed responses
* Stress responses overlap
* Cognitive state evolves continuously

Instead, use:

**EVENT-FIXED, CONTEXT-MOVABLE**

The event remains anchored, but the surrounding context is adjustable.

Researchers must be able to:

* Zoom out
* Zoom in
* Expand backward
* Expand forward
* Dynamically shift context

---

## Recommended Replay Model

**Event Anchor (Immutable Center Point)**
Example:
00:01:17 — Miss Cluster Begins

**Adaptive Context Window**
Automatically chosen based on event type:

* Rapid gunshot response: -1s to +4s
* Overload buildup: -15s to +30s

**User-Controlled Expansion**

* Timeline zoom
* Drag-to-expand
* Scroll-wheel scaling
* Event neighborhood inspection

---

## Episodes

Single events are too granular. The system should automatically identify higher-level behavioral episodes.

Examples:

* Panic firing episode
* Overload episode
* Attentional collapse
* Recovery episode
* Controlled engagement period

Episodes are psychologically meaningful analysis units.

Example:
Episode: 00:01:17 → 00:01:31

Characteristics:

* 7 misses
* Increased trigger cadence
* Destabilized respiration
* Elevated HR
* Reduced gaze scanning

Interpretation:
Possible overload response with attentional narrowing.

---

## UI Architecture

The UI should be timeline-first, with everything synchronized horizontally.

Suggested structure:

* Simulation Timeline
* Threats / Hits / Misses
* Behavioral Episodes
* Cognitive State Estimation
* Performance Metrics
* Physiological Signals
* Gaze + Motion

All rows share:

* The same cursor
* The same replay position
* The same zoom context

Moving anywhere updates everything simultaneously.

---

## Do Not Prioritize Raw Signals

Raw physiology is secondary.

Researchers care more about:

* Transitions
* Relationships
* Stress-performance coupling
* Overload states
* Recovery patterns
* Attentional behavior

The system should infer higher-level meaning rather than expose raw data alone.

---

## Interpretive Layer

Instead of showing:

> “HR = 110”

Prefer:

> “Elevated autonomic arousal after threat appearance”

---

If you want, I can also convert this into a **product spec (PRD)**, **system architecture diagram**, or **frontend component structure (React)**.
