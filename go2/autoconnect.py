"""Bounded startup auto-connect retries for the local fleet panel."""

from __future__ import annotations

import logging
import threading
from typing import Any


LOGGER = logging.getLogger(__name__)


def retry_delay_seconds(
    retry_seconds: float,
    completed_attempts: int,
    max_retry_seconds: float,
) -> float:
    """Return a bounded exponential delay after failed connection attempts."""

    if retry_seconds < 0 or completed_attempts < 1:
        raise ValueError("retry delay inputs are invalid")
    if max_retry_seconds < retry_seconds:
        raise ValueError("max_retry_seconds must be at least retry_seconds")
    return min(
        max_retry_seconds,
        retry_seconds * (2 ** (completed_attempts - 1)),
    )


def auto_connect_until_ready(
    session: Any,
    stop_event: threading.Event,
    *,
    retry_seconds: float = 10.0,
    max_retry_seconds: float = 60.0,
    max_attempts: int = 12,
) -> bool:
    """Connect configured robots without sending motion commands.

    The first attempt runs immediately. Partial fleet connections are retained,
    and only the remaining unavailable robots need work on later attempts.
    """

    if retry_seconds < 0:
        raise ValueError("retry_seconds must not be negative")
    if max_retry_seconds < retry_seconds:
        raise ValueError("max_retry_seconds must be at least retry_seconds")
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least one")

    for attempt in range(1, max_attempts + 1):
        delay = (
            retry_delay_seconds(
                retry_seconds,
                attempt - 1,
                max_retry_seconds,
            )
            if attempt > 1
            else 0.0
        )
        if delay and stop_event.wait(delay):
            return False
        if stop_event.is_set():
            return False
        next_delay = (
            retry_delay_seconds(
                retry_seconds,
                attempt,
                max_retry_seconds,
            )
            if attempt < max_attempts
            else 0.0
        )
        try:
            result = session.connect_fleet()
            fleet = result.get("fleet", {})
            if bool(fleet.get("connected")):
                LOGGER.info("Startup auto-connect completed: fleet ready")
                return True
            LOGGER.warning(
                "Startup auto-connect incomplete: attempt=%d connected=%s "
                "configured=%s next_retry_seconds=%g",
                attempt,
                fleet.get("connected_count", 0),
                fleet.get("configured_count", 0),
                next_delay,
            )
        except Exception as exc:
            LOGGER.warning(
                "Startup auto-connect attempt failed: attempt=%d kind=%s "
                "next_retry_seconds=%g",
                attempt,
                type(exc).__name__,
                next_delay,
            )
    return False
