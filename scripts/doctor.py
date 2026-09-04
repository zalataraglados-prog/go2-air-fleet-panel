#!/usr/bin/env python3
"""Read-only environment, network, WebRTC, and DataChannel diagnostics."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
from importlib import metadata
import logging
import platform
from pathlib import Path
import socket
import sys
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Upstream status lines contain emoji. Preserve the Windows console encoding
# but escape characters it cannot represent instead of aborting the connection.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")

from go2.config import AppConfig, ConfigurationError, load_config  # noqa: E402
from go2.connection import Go2Connection, Go2ConnectionError  # noqa: E402


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool | None
    detail: str
    required: bool = True

    @property
    def status(self) -> str:
        if self.ok is True:
            return "PASS"
        if self.ok is False:
            return "FAIL"
        return "SKIP"


def _version(distribution: str) -> str | None:
    try:
        return metadata.version(distribution)
    except metadata.PackageNotFoundError:
        return None


def dependency_checks() -> list[Check]:
    checks: list[Check] = []
    for distribution in ("unitree_webrtc_connect", "aiortc", "aioice"):
        version = _version(distribution)
        checks.append(
            Check(
                f"dependency:{distribution}",
                version is not None,
                version or "not installed",
            )
        )
    return checks


def network_checks() -> list[Check]:
    try:
        import psutil
    except ImportError:
        return [Check("network interfaces", False, "psutil not installed")]

    interfaces: list[str] = []
    addresses: list[str] = []
    for name, entries in psutil.net_if_addrs().items():
        ipv4 = sorted(
            {
                entry.address
                for entry in entries
                if entry.family == socket.AF_INET and entry.address
            }
        )
        if ipv4:
            interfaces.append(name)
            addresses.extend(ipv4)

    return [
        Check(
            "network interfaces",
            bool(interfaces),
            ", ".join(interfaces) if interfaces else "none with IPv4",
        ),
        Check(
            "IPv4 addresses",
            bool(addresses),
            ", ".join(sorted(set(addresses))) if addresses else "none",
        ),
    ]


async def _port_open(ip: str, port: int, timeout: float) -> bool:
    writer: asyncio.StreamWriter | None = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
        return True
    except (asyncio.TimeoutError, ConnectionError, OSError):
        return False
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass


async def signaling_probe(ip: str, timeout: float = 1.5) -> Check:
    ports = (9991, 8081)
    states = await asyncio.gather(*(_port_open(ip, port, timeout) for port in ports))
    open_ports = [str(port) for port, opened in zip(ports, states) if opened]
    return Check(
        "robot signaling reachability",
        bool(open_ports),
        f"{ip}; open port(s): {', '.join(open_ports)}"
        if open_ports
        else f"{ip}; ports 9991/8081 unreachable",
    )


def configuration_checks(config: AppConfig) -> list[Check]:
    connection = config.connection
    return [
        Check("configuration", True, str(config.source)),
        Check("connection mode", True, connection.mode),
        Check(
            "UNITREE_AES_128_KEY",
            bool(connection.aes_128_key) or not connection.require_aes_key,
            "configured (value hidden)" if connection.aes_128_key else "not configured",
        ),
        Check(
            "UNITREE_ROBOT_IP",
            True if connection.target_ip else None,
            connection.target_ip or "not configured; serial discovery requested",
            required=False,
        ),
    ]


async def webrtc_checks(config: AppConfig, skip_webrtc: bool) -> list[Check]:
    if skip_webrtc:
        return [
            Check("WebRTC", None, "disabled by --skip-webrtc"),
            Check("DataChannel", None, "disabled by --skip-webrtc"),
        ]

    preflight = config.connection.preflight_errors()
    if preflight:
        detail = "; ".join(preflight)
        return [
            Check("WebRTC", False, detail),
            Check("DataChannel", False, "WebRTC preflight failed"),
        ]

    connection = Go2Connection(config.connection)
    try:
        await connection.connect()
        state = connection.transport_state
        return [
            Check("WebRTC", connection.is_connected, f"peer={state.peer}; ice={state.ice}"),
            Check(
                "DataChannel",
                connection.data_channel_ready,
                f"channel={state.data_channel}; validated={state.data_channel_validated}",
            ),
        ]
    except Go2ConnectionError as exc:
        state = exc.state or connection.transport_state
        peer_ok = state.peer == "connected"
        return [
            Check(
                "WebRTC",
                peer_ok,
                f"failure={exc.kind.value}; peer={state.peer}; ice={state.ice}",
            ),
            Check(
                "DataChannel",
                False,
                f"failure={exc.kind.value}; channel={state.data_channel}",
            ),
        ]
    finally:
        await connection.disconnect()


def print_report(checks: Iterable[Check]) -> bool:
    checks = list(checks)
    width = max((len(check.name) for check in checks), default=0)
    for check in checks:
        print(f"[{check.status:4}] {check.name:<{width}}  {check.detail}")
    required_checks = [check for check in checks if check.required]
    passed = bool(required_checks) and all(
        check.ok is True for check in required_checks
    )
    print(f"\nFINAL: {'PASS' if passed else 'FAIL'}")
    return passed


async def run(config_path: str | None, skip_webrtc: bool) -> bool:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    checks: list[Check] = [
        Check("Python", sys.version_info >= (3, 10), platform.python_version()),
        Check("operating system", True, platform.platform()),
    ]
    checks.extend(dependency_checks())
    checks.extend(network_checks())
    checks.extend(configuration_checks(config))
    target_ip = config.connection.target_ip
    if target_ip:
        checks.append(await signaling_probe(target_ip))
    else:
        checks.append(
            Check(
                "robot signaling reachability",
                None,
                "no explicit IP; multicast discovery occurs during WebRTC connect",
                required=False,
            )
        )
    checks.extend(await webrtc_checks(config, skip_webrtc))
    return print_report(checks)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose GO2 Air environment and perform a movement-free connection test."
    )
    parser.add_argument("--config", help="Path to YAML configuration")
    parser.add_argument(
        "--skip-webrtc",
        action="store_true",
        help="Run local diagnostics only; WebRTC/DataChannel are reported as SKIP",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        passed = asyncio.run(run(args.config, args.skip_webrtc))
    except ConfigurationError as exc:
        print(f"[FAIL] configuration  {exc}")
        print("\nFINAL: FAIL")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; connection cleanup requested.")
        return 130
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
