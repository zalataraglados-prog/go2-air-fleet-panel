#!/usr/bin/env python3
"""Run the local-only GO2 Air control panel."""

from __future__ import annotations

import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import socket
import sys
import threading
import time
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from go2.config import ConfigurationError, DATA_ROOT, load_config  # noqa: E402
from go2.autoconnect import auto_connect_until_ready  # noqa: E402
from go2.panel import RobotPanelSession, create_panel_app  # noqa: E402


LOGGER = logging.getLogger(__name__)
DEFAULT_LOG_PATH = DATA_ROOT / "logs" / "panel.log"


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


def configure_logging(level: str, log_path: Path = DEFAULT_LOG_PATH) -> Path:
    """Configure UTF-8 console and bounded persistent diagnostics."""

    resolved = log_path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s"
    )
    console = logging.StreamHandler()
    rotating = RotatingFileHandler(
        resolved,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
        delay=True,
    )
    frame_filter = _RawTransportFrameFilter()
    for handler in (console, rotating):
        handler.setFormatter(formatter)
        handler.addFilter(frame_filter)
    logging.basicConfig(
        level=getattr(logging, level),
        handlers=[console, rotating],
        force=True,
    )
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the loopback-only GO2 Air safety panel."
    )
    parser.add_argument("--config", help="Optional YAML configuration path")
    parser.add_argument("--port", type=int, default=8765, help="Local port (default: 8765)")
    parser.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the workbench in the default browser after startup",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def panel_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def panel_is_listening(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def open_panel_in_browser(port: int) -> None:
    """Open the panel through Windows ShellExecute with a portable fallback."""

    url = panel_url(port)
    if sys.platform == "win32":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return
        except OSError:
            LOGGER.warning("Windows could not open the panel URL; using fallback")
    webbrowser.open(url)


def open_browser_when_ready(port: int) -> None:
    for _ in range(40):
        if panel_is_listening(port):
            open_panel_in_browser(port)
            return
        time.sleep(0.25)


def runtime_self_test() -> bool:
    """Load the native runtime and the real SDK without opening a connection."""

    try:
        import wasmtime
        from go2.connection import _load_sdk

        wasmtime.Engine()
        _load_sdk()
    except Exception:
        return False
    return True


def show_startup_error(message: str) -> None:
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        ctypes.windll.user32.MessageBoxW(  # type: ignore[attr-defined]
            0,
            message,
            "GO2 Air Fleet Panel",
            0x10,
        )
    else:
        print(message, file=sys.stderr)


def main() -> int:
    args = parse_args()
    if args.self_test:
        return 0 if runtime_self_test() else 1
    if not 1024 <= args.port <= 65535:
        show_startup_error("--port must be between 1024 and 65535")
        return 2
    if panel_is_listening(args.port):
        if args.open_browser:
            open_panel_in_browser(args.port)
        return 0
    try:
        config = load_config(args.config)
    except ConfigurationError as exc:
        show_startup_error(f"Configuration error: {exc}")
        return 2

    log_path = configure_logging(config.logging.level)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    LOGGER.info("GO2 panel starting: log_file=%s", log_path)
    session = RobotPanelSession(config)
    app = create_panel_app(config, session=session)
    auto_connect_stop = threading.Event()
    auto_connect_thread = threading.Thread(
        target=auto_connect_until_ready,
        args=(session, auto_connect_stop),
        kwargs={
            "retry_seconds": 10.0,
            "max_retry_seconds": 60.0,
            "max_attempts": 12,
        },
        name="go2-panel-auto-connect",
        daemon=True,
    )
    print(f"GO2_PANEL={panel_url(args.port)}")
    print("The panel is local-only. Fleet auto-connect uses read-only discovery.")
    try:
        auto_connect_thread.start()
        if args.open_browser:
            threading.Thread(
                target=open_browser_when_ready,
                args=(args.port,),
                name="go2-panel-browser",
                daemon=True,
            ).start()
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
