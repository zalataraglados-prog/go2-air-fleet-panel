#!/usr/bin/env python3
"""Run the local-only GO2 Air control panel."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import threading


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from go2.config import ConfigurationError, load_config  # noqa: E402
from go2.autoconnect import auto_connect_until_ready  # noqa: E402
from go2.panel import RobotPanelSession, create_panel_app  # noqa: E402


class _RawTransportFrameFilter(logging.Filter):
    """Keep the panel log compact without echoing complete WebRTC payloads."""

    _PREFIXES = (
        "> message sent:",
        "Received message on data channel:",
        "Heartbeat response received.",
    )

    def filter(self, record: logging.LogRecord) -> bool:
        return not (
            record.name == "root"
            and any(record.getMessage().startswith(prefix) for prefix in self._PREFIXES)
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the loopback-only GO2 Air safety panel."
    )
    parser.add_argument("--config", help="Optional YAML configuration path")
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1024 <= args.port <= 65535:
        print("--port must be between 1024 and 65535", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    for handler in logging.getLogger().handlers:
        handler.addFilter(_RawTransportFrameFilter())
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    session = RobotPanelSession(config)
    app = create_panel_app(config, session=session)
    auto_connect_stop = threading.Event()
    auto_connect_thread = threading.Thread(
        target=auto_connect_until_ready,
        args=(session, auto_connect_stop),
        kwargs={"retry_seconds": 10.0, "max_attempts": 12},
        name="go2-panel-auto-connect",
        daemon=True,
    )
    print(f"GO2_PANEL=http://127.0.0.1:{args.port}")
    print("The panel is local-only. Fleet auto-connect uses read-only discovery.")
    try:
        auto_connect_thread.start()
        app.run(
            host="127.0.0.1",
            port=args.port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except KeyboardInterrupt:
        pass
    finally:
        auto_connect_stop.set()
        auto_connect_thread.join(timeout=2.0)
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
