#!/usr/bin/env python3
"""Send one guarded StandUp request after explicit physical-area confirmation."""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(errors="backslashreplace")

from go2.config import ConfigurationError, load_config  # noqa: E402
from go2.connection import Go2Connection, Go2ConnectionError  # noqa: E402
from go2.motion import MotionError, OneShotStandController  # noqa: E402
from go2.safety import StandSafetyError  # noqa: E402
from go2.state import Go2StateReader, StateError  # noqa: E402


class _NoRawDataChannelPayloads(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            record.name == "root"
            and (
                message.startswith("Received message on data channel:")
                or message.startswith("> message sent:")
            )
        )


async def run(config_path: str | None, confirmed: bool) -> None:
    if not confirmed:
        raise StandSafetyError(
            "Physical clearance was not confirmed; no connection or command was attempted."
        )
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_NoRawDataChannelPayloads())

    connection = Go2Connection(config.connection)
    try:
        await connection.connect()
        preflight = await Go2StateReader(connection).collect(samples=1, timeout=10.0)
        low = preflight.low_state
        print(
            "Stand preflight: "
            f"soc={low.battery_soc:.0f}% voltage={low.power_voltage:.2f}V "
            f"roll={low.rpy[0]:.4f} pitch={low.rpy[1]:.4f} "
            f"foot_force_total={sum(low.foot_force):.1f}"
        )
        result = await OneShotStandController(connection).execute(
            preflight,
            guard_seconds=5.0,
            request_timeout=3.0,
        )
        post = await Go2StateReader(connection).collect(samples=1, timeout=10.0)
        sport = post.sport_mode_state
        print("GUARDED_STAND=PASS")
        print(
            f"Commands: StandUp status={result.stand_status}; "
            f"StopMove status={result.stop_status}; guard={result.guard_seconds:.1f}s"
        )
        print(
            "Post-state: "
            f"sport_code={sport.error_code} mode={sport.mode} gait={sport.gait_type} "
            f"velocity={sport.velocity} yaw_speed={sport.yaw_speed:.4f}"
        )
    finally:
        await connection.disconnect()
        print("Connection cleanup complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One guarded GO2 Air StandUp action; this physically moves the robot."
    )
    parser.add_argument("--config", help="Path to YAML configuration")
    parser.add_argument(
        "--confirm-clearance",
        action="store_true",
        help="Confirm a clear, dry, level area with people away from the robot",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args.config, args.confirm_clearance))
    except (ConfigurationError, StandSafetyError) as exc:
        print(f"Safety refusal: {exc}", file=sys.stderr)
        return 2
    except Go2ConnectionError as exc:
        print(f"Connection failed [{exc.kind.value}]: {exc}", file=sys.stderr)
        return 1
    except (StateError, MotionError) as exc:
        print(f"Guarded stand failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; StopMove and connection cleanup requested.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
