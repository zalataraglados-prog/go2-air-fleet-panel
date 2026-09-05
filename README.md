# GO2 Air Fleet Panel

A local-only, safety-oriented multi-robot control panel for Unitree GO2 Air robots over Wi-Fi and WebRTC.

The application manages up to 16 independent robots, provides RTS-style target selection, displays read-only telemetry, exposes a reviewed action allowlist, and includes a bounded choreography editor. The browser never receives device AES keys or account credentials.

> **Safety warning:** This software can command physical robots. Keep every selected robot on a level, dry, non-slip surface with a clear perimeter. Maintain immediate access to physical power controls. `StopMove` is not a physical emergency stop.

## Highlights

- Local STA and local AP WebRTC connectivity through `unitree_webrtc_connect`.
- Serial-number discovery and WebRTC ICE host candidates are constrained to the primary physical private-LAN subnet; VPN, overlay, host-only, link-local, and unrelated subnets are excluded.
- Automatic recovery after Wi-Fi changes; cached addresses are optional, live discovery takes precedence, and cross-subnet or `169.254.*` caches are rejected.
- Startup auto-connect with bounded exponential backoff and no motion commands.
- Partial fleet tolerance: an offline robot does not tear down healthy sessions or block explicitly selected online robots.
- RTS-style single, additive, select-all, clear, and drag-box selection, with no robot selected by default.
- Per-robot read-only state, motion-mode detection, response classification, and timing.
- Explicit backend action allowlist; the browser cannot submit arbitrary API IDs.
- DataChannel-only WebRTC sessions omit unused audio/video RTP transceivers, reducing each robot to one ICE transport and a smaller UDP footprint.
- Separate action workbench and settings/connection center.
- Local browser preferences for automatic connection, polling interval, and compact cards.
- A visual choreography editor limited to 12 steps and 40 seconds, including bounded body-relative forward, backward, lateral, and in-place rotation steps.
- `163` offline tests covering configuration, discovery, connection lifecycle, safety gates, protocol parsing, fleet behavior, and auto-connect retries.

The current interface is localized in Simplified Chinese. Repository documentation, Git history, and release notes are maintained in English.

## Architecture

```text
Windows or Linux PC
        |
        | Wi-Fi / Ethernet LAN
        v
unitree_webrtc_connect
        |
        | WebRTC + validated DataChannel
        v
GO2 Air fleet
```

The project does not use SDK2, direct CycloneDDS/ROS 2 DDS access, SSH, LowCmd, or firmware modification.

## Requirements

- Python 3.11 or 3.12
- GO2 Air robots connected to the same LAN as the host
- One serial number and per-device AES-128 key for each robot
- Windows PowerShell for the supplied desktop launcher

Versions tested by this project are pinned in `requirements.txt`, including `unitree_webrtc_connect==2.2.0`.

## Installation

Windows PowerShell:

```powershell
git clone https://github.com/zalataraglados-prog/go2-air-fleet-panel.git
cd go2-air-fleet-panel
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
Copy-Item config\config.example.yaml config\config.yaml
```

Linux:

```bash
git clone https://github.com/zalataraglados-prog/go2-air-fleet-panel.git
cd go2-air-fleet-panel
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-dev.txt
cp .env.example .env
cp config/config.example.yaml config/config.yaml
```

## Configuration

Non-sensitive settings belong in `config/config.yaml`. Secrets belong only in environment variables or the Git-ignored `.env` file.

```dotenv
UNITREE_REGION=cn
UNITREE_ROBOT_IP=
UNITREE_AES_128_KEY=<32-hex-character-device-key>
UNITREE_SERIAL_NUMBER=<device-serial-number>

UNITREE_ROBOT_2_LABEL=Robot 2
UNITREE_ROBOT_2_IP=
UNITREE_ROBOT_2_SERIAL_NUMBER=<device-serial-number>
UNITREE_ROBOT_2_AES_128_KEY=<32-hex-character-device-key>
```

Continue with `UNITREE_ROBOT_3_...` through `UNITREE_ROBOT_16_...` as needed.

Leave all `*_IP` values blank for network-independent operation. On every Local STA connection, the panel discovers each robot by serial number on the primary physical private-LAN subnet. VPN, overlay, host-only, link-local, and unrelated subnets are excluded. If an optional cached address exists, a live discovery result takes precedence; the cache is used only when multicast discovery is unavailable and the address remains on the current `/24` LAN.

For Local AP mode, connect the PC directly to the robot hotspot and set `connection.mode` to `local_ap`. The robot address is fixed at `192.168.12.1`.

Never commit `.env`, real AES keys, robot serial numbers, or account credentials.

### Secure key import

The importer reads a local two-line email/password file, retrieves the assigned device key, and writes only to the ignored `.env` file. It never prints the password, full serial number, or AES key.

```powershell
python scripts\fetch_key_to_env.py `
  --credentials-file "C:\secure\unitree-credentials.txt" `
  --slot 1 --region cn

python scripts\fetch_key_to_env.py `
  --credentials-file "C:\secure\unitree-credentials.txt" `
  --slot 2 --candidate-index 1 --region cn
```

When multiple unassigned devices remain, `--candidate-index` selects a candidate using stable serial-number ordering. Add `--discover-ip` only if you want an optional address cache; it is not required for automatic Wi-Fi recovery.

## Automatic Wi-Fi recovery

The normal workflow after changing routers is:

1. Join the PC and every robot to the new LAN.
2. Start the panel from the desktop shortcut or run `python scripts/panel.py`.
3. The panel enumerates active IPv4 interfaces, discovers each configured serial number, and connects the fleet automatically.
4. Partial failures are retried with a bounded 10, 20, 40, then 60-second backoff while healthy sessions remain connected.

No `.env` edit is required. Discovery and auto-connect do not send movement commands.

The settings page includes an **automatic discovery and connection** preference. Manually disconnecting the fleet disables that browser preference so the page does not immediately reconnect against the operator's intent.

## Running the panel

```powershell
python scripts\panel.py
```

Open:

- Workbench: <http://127.0.0.1:8765/>
- Settings and connection center: <http://127.0.0.1:8765/settings>

The server binds only to `127.0.0.1`. Starting the service launches a bounded background connection pass, while either browser page can retry any remaining offline devices with exponential backoff.

Runtime diagnostics are written as UTF-8 to `logs/panel.log`. The file rotates at 5 MiB and retains five backups. It records per-robot discovery targets, sanitized failure categories, ICE/DataChannel transitions, connection retry intervals, operation timing, and matched response latency; device keys and raw transport frames are excluded.

On Windows, `scripts/start_panel.ps1` reuses an existing service or starts it in the background and opens the browser. A desktop shortcut can target:

```text
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "<project>\scripts\start_panel.ps1"
```

## Diagnostics

Run environment, dependency, interface, signaling-port, WebRTC, and DataChannel checks:

```powershell
python scripts\doctor.py
```

Run only host-side checks:

```powershell
python scripts\doctor.py --skip-webrtc
```

Run a movement-free connection cycle:

```powershell
python scripts\connect_test.py --repeat 5 --hold-seconds 5
```

Run allowlisted read-only telemetry checks:

```powershell
python scripts\state_test.py --samples 3 --timeout 10
```

## Connection error categories

| Category | Meaning |
| --- | --- |
| `robot_not_found` | No targeted discovery reply or no reachable local signaling port |
| `aes_key_missing` | The per-device AES-128 key is missing |
| `aes_key_rejected` | The key does not match the target robot |
| `signaling_failed` | Local signaling did not produce a valid SDP answer |
| `ice_failed` | ICE/DTLS negotiation failed |
| `data_channel_failed` | The DataChannel did not open and validate |
| `robot_busy` | Another WebRTC client is using the robot |
| `timeout` | The bounded connection timeout expired |

Exceptions are sanitized before they reach the browser or normal logs.

## Safety model

- The Flask server listens only on loopback.
- Device secrets remain in the Python process.
- Only reviewed state topics can be subscribed to.
- Motion requests use fixed backend definitions; arbitrary browser-supplied API IDs are rejected.
- Each command requires an explicit non-empty robot target set.
- Each motion requires a fresh clearance confirmation.
- High and extreme risk actions require the exact phrase `GO2 HIGH RISK` and a three-second hold.
- Choreographies accept only low/medium-risk allowlisted actions and bounded Euler/wait primitives.
- Custom Euler limits are roll `±0.12`, pitch `±0.20`, and yaw `±0.30` radians.
- Choreography steps compare accepted RPC responses with live RPY telemetry and stop when the expected posture change is not visible.
- Completion and exceptional paths attempt `StopMove`, but physical power control remains the final safety mechanism.

The built-in **Luminous Tail** choreography uses conservative, already accepted Euler primitives. It does not expose joint-level trajectories.

Choreography locomotion streams the official high-level `Move(vx, vy, vyaw)` RPC at 8 Hz so firmware command expiry cannot turn a held direction into a short nudge. The editor exposes only six single-axis directions, caps forward/backward speed at `0.25 m/s`, lateral speed at `0.30 m/s`, yaw rate at `0.50 rad/s`, and each locomotion step at three seconds. Consecutive velocity steps switch directly and issue one `StopMove` only at the end of the contiguous chain. Transitions to non-velocity steps also stop first, and **STOP SELECTED** remains available while a choreography is running. The checked first-frame acknowledgement allows 1.2 seconds for multi-robot response jitter without pausing the 8 Hz stream.

These locomotion steps are open-loop and relative to each robot's current body heading. Duration does not guarantee an exact distance or angle. They do not provide shared-map localization, obstacle-aware navigation, or closed-loop formation keeping.

## Tests

```powershell
python -m pytest -q
```

The test suite uses fake robot transports and does not connect to hardware or send motion commands.

## Community UI review

The design rationale and the reviewed community projects are documented in [`docs/community-panel-evaluation.md`](docs/community-panel-evaluation.md).

## Responsible use

Use the project only on robots you own or are authorized to operate. Verify every new firmware version, action, and physical environment with conservative bounds and direct supervision before routine use.
