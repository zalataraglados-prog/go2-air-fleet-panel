## GO2 Air Fleet Panel v1.1.1

This hotfix corrects two Windows installer issues found during first deployment to a second computer.

### Fixes

- Bundles the Wasmtime 48.0.0 native runtime required by encrypted GO2 signaling. Version 1.1.0 omitted `_wasmtime.dll`, which caused a PyInstaller runtime error after successful robot discovery.
- Uses Windows ShellExecute to reopen the local workbench reliably when the panel service is already running in the background.
- Adds a packaged-runtime build gate that loads and initializes Wasmtime before an installer can be produced.

All 170 offline tests pass on Python 3.12.

### Upgrade

Install `GO2-Air-Fleet-Panel-Setup-v1.1.1.exe` over the existing installation. The installer preserves `.env`, `config/config.yaml`, logs, and choreography drafts. Do not reuse the v1.1.0 installer.

The installer is not code-signed, so Windows SmartScreen may display an unrecognized-publisher warning. Verify the accompanying SHA-256 checksum before installation.

### Network note

This hotfix resolves the missing native runtime. A `robot_not_found` result remains a separate LAN discovery condition: verify that every robot and the control computer are on the same non-isolated local network. Fixed robot IP addresses are not required.
