## GO2 Air Fleet Panel v1.1.0

> [!WARNING]
> The v1.1.0 Windows installer omitted the native Wasmtime DLL required by encrypted signaling. Install v1.1.1 or later instead. The source release remains available for historical reference.

This release focuses on multi-robot connection resilience, smoother choreography timing, reliable draft persistence, and straightforward Windows installation.

### Highlights

- Adds a real per-user Windows installer with an independent Python/WebRTC runtime, desktop and Start menu shortcuts, and a registered uninstaller.
- Preserves local `.env`, non-sensitive configuration, logs, and choreography drafts across upgrades.
- Automatically saves every choreography edit to browser storage and an atomic local panel draft, then restores the newest copy after refresh or restart.
- Fails fast when a selected robot's DataChannel closes during an action instead of waiting for the full command timeout.
- Immediately retries a previously healthy robot after an unexpected disconnect before resuming bounded backoff.
- Prevents delayed locomotion scheduler ticks from being replayed as a packet burst.
- Prevents late acknowledgement resolution from extending a velocity step beyond its configured deadline.
- Constrains discovery and ICE candidates to the primary physical private-LAN subnet and rejects stale cross-network address caches.
- Uses DataChannel-only WebRTC sessions to avoid unused audio/video RTP transports.

### Installer

Download and run `GO2-Air-Fleet-Panel-Setup-v1.1.0.exe`. It installs for the current user and does not require a separate Python installation. Use the Start menu shortcut **Open configuration folder** to edit the generated `.env` before connecting robots.

The installer is not code-signed, so Windows SmartScreen may display an unrecognized-publisher warning. Verify the accompanying SHA-256 checksum before installation.

### Validation

- 168 offline tests pass on Python 3.12.
- The packaged executable starts without a system Python dependency and serves the workbench and draft API on an isolated test port.
- The installer is built with PyInstaller 6.22.2 and Inno Setup 6.7.3.

### Safety

This software controls physical robots. Keep every selected robot in a clear, level, non-slip area, retain immediate access to physical power controls, and treat `StopMove` as a software guard rather than a physical emergency stop.
