#!/usr/bin/env python3
"""Collect a bounded GO2 Air telemetry snapshot without motion commands."""

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
from go2.state import Go2StateReader, StateError  # noqa: E402


class _NoRawDataChannelPayloads(logging.Filter):
    """Keep the upstream root logger from dumping complete telemetry frames."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        return not (
            record.name == "root"
            and (
                message.startswith("Received message on data channel:")
                or message.startswith("> message sent:")
            )
        )


def sample_count(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("sample count must be between 1 and 20")
    return number


def timeout_seconds(value: str) -> float:
    number = float(value)
    if not 5.0 <= number <= 30.0:
        raise argparse.ArgumentTypeError("timeout must be between 5 and 30 seconds")
    return number


async def run(config_path: str | None, samples: int, timeout: float) -> None:
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
        snapshot = await Go2StateReader(connection).collect(
            samples=samples, timeout=timeout
        )
        low = snapshot.low_state
        sport = snapshot.sport_mode_state
        print("READ_ONLY_STATE=PASS")
        print(
            "Samples: "
            f"low_state={snapshot.low_state_samples}, "
            f"sport_mode_state={snapshot.sport_mode_samples}"
        )
        print(
            "Battery: "
            f"soc={low.battery_soc:.0f}% voltage={low.power_voltage:.2f}V"
        )
        print(
            "IMU RPY: "
            f"roll={low.rpy[0]:.4f} pitch={low.rpy[1]:.4f} yaw={low.rpy[2]:.4f}"
        )
        print(
            f"Low state: motors={low.motor_count} foot_force={low.foot_force}"
        )
        print(
            "Sport state: "
            f"error_code={sport.error_code} mode={sport.mode} gait={sport.gait_type} "
            f"position={sport.position} velocity={sport.velocity} "
            f"yaw_speed={sport.yaw_speed:.4f}"
        )
    finally:
        await connection.disconnect()
        print("Connection cleanup complete.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only GO2 Air low-state and sport-mode telemetry test."
    )
    parser.add_argument("--config", help="Path to YAML configuration")
    parser.add_argument(
        "--samples",
        type=sample_count,
        default=3,
        help="Valid samples required per topic, 1-20 (default: 3)",
    )
    parser.add_argument(
        "--timeout",
        type=timeout_seconds,
        default=10.0,
        help="Overall telemetry timeout, 5-30 seconds (default: 10)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args.config, args.samples, args.timeout))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Go2ConnectionError as exc:
        print(f"Connection failed [{exc.kind.value}]: {exc}", file=sys.stderr)
        return 1
    except StateError as exc:
        print(f"Read-only state validation failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; state subscription cleanup requested.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
