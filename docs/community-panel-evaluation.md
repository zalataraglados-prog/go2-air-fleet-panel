# Community Control Panel Evaluation

Audit date: 2026-09-02.

The goal was to replace one-script-per-action operation with a persistent local console while preserving the safety boundaries already validated on GO2 Air hardware.

## Decision

Community projects were used as interaction references only. The existing Python control layer remains the only command gateway.

No reviewed community panel was connected directly to the robots. Several candidates stored device keys in browser storage or logs, listened on all network interfaces, enabled actions without live-state gates, or lacked a clear focus/network-loss watchdog.

## Candidates

### legion1581/unitree_ui

- Repository: <https://github.com/legion1581/unitree_ui>
- Audited revision: `3eb378b7adcf06a7773724efcbb4f58a8df98e11`
- License: MIT
- Strengths: maintained alongside `unitree_webrtc_connect`; supports GO2 Air, local AP/STA, AES, camera, joysticks, actions, telemetry, and error display.
- Blocking concerns: AES values can reach browser logs; device/session data is stored in `localStorage`; the default server listens on all interfaces; action availability has limited live-state gating; no explicit forced neutral/StopMove behavior was found for page blur, tab hide, or network loss.
- Use in this project: interaction and information-architecture reference only.

### hfarmai/unitree-go2-control

- Repository: <https://github.com/hfarmai/unitree-go2-control>
- License: Apache-2.0
- Strengths: a Python/Flask architecture that resembles this project's stack.
- Blocking concerns: broad unverified action exposure and motion ranges; weaker device-coverage evidence than the primary candidate.
- Use in this project: Flask panel organization reference only.

### go2_dashboard and go2-webrtc

- `go2_dashboard` primarily targets DDS/Ethernet and does not match the Windows-to-Wi-Fi-to-WebRTC path.
- `go2-webrtc` did not provide enough maintenance or capability evidence to replace the already validated connection layer.

## Production boundaries adopted here

1. Bind the panel to `127.0.0.1` only.
2. Read AES keys only in the Python backend; never send them to the browser or normal logs.
3. Provide no arbitrary API/RPC endpoint.
4. Require connection, live telemetry preflight, fresh clearance confirmation, and hold-to-run interaction for posture actions.
5. Retain bounded StopMove protection after posture commands and exceptional paths.
6. Treat StopMove as a software guard, not a physical emergency stop.
7. Add continuous movement only after direction, low-speed, release, blur, network-loss, and process-exit watchdog validation.

## Capability admission order

1. Validate offline panel startup and browser behavior with robots powered off.
2. Validate connection, telemetry, and disconnect without movement.
3. Repeat already validated stand-up and stand-down operations from the panel.
4. Keep the action library behind a backend allowlist and elevated confirmation for high-risk actions.
5. Limit choreography length, duration, action risk, and custom primitives; attempt StopMove on completion or failure.
6. Introduce low-speed continuous movement only with a server-side watchdog.
7. Prefer read-only camera and LiDAR integration before additional acrobatics.

This review records engineering rationale, not an endorsement or security certification of any third-party project.
