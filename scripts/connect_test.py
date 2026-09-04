#!/usr/bin/env python3
"""Connect, verify DataChannel, wait briefly, and disconnect without movement."""

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


def hold_seconds(value: str) -> float:
    number = float(value)
    if not 5.0 <= number <= 10.0:
        raise argparse.ArgumentTypeError("hold time must be between 5 and 10 seconds")
    return number


def repeat_count(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 20:
        raise argparse.ArgumentTypeError("repeat count must be between 1 and 20")
    return number


async def run(config_path: str | None, hold: float, repeat: int) -> None:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    for attempt in range(1, repeat + 1):
        print(f"Attempt {attempt}/{repeat}: connecting (no motion commands)...")
        connection = Go2Connection(config.connection)
        try:
            await connection.connect()
            state = connection.transport_state
            print(
                "Connected: "
                f"peer={state.peer}, ice={state.ice}, "
                f"data_channel={state.data_channel}, "
                f"validated={state.data_channel_validated}"
            )
            await asyncio.sleep(hold)
        finally:
            await connection.disconnect()
            print("Connection cleanup complete.")
        if attempt < repeat:
            await asyncio.sleep(1.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Movement-free GO2 Air WebRTC connection test."
    )
    parser.add_argument("--config", help="Path to YAML configuration")
    parser.add_argument(
        "--hold-seconds",
        type=hold_seconds,
        default=5.0,
        help="Connected dwell time, 5-10 seconds (default: 5)",
    )
    parser.add_argument(
        "--repeat",
        type=repeat_count,
        default=1,
        help="Number of complete connect/disconnect cycles (default: 1)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        asyncio.run(run(args.config, args.hold_seconds, args.repeat))
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except Go2ConnectionError as exc:
        print(f"Connection failed [{exc.kind.value}]: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted; connection cleanup requested.")
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
