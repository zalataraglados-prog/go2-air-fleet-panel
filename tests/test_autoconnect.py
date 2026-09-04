from __future__ import annotations

import threading

import pytest

from go2.autoconnect import auto_connect_until_ready


class FakeSession:
    def __init__(self, connected_counts: list[int], configured: int = 2) -> None:
        self.connected_counts = connected_counts
        self.configured = configured
        self.calls = 0

    def connect_fleet(self) -> dict[str, object]:
        index = min(self.calls, len(self.connected_counts) - 1)
        connected = self.connected_counts[index]
        self.calls += 1
        return {
            "fleet": {
                "connected": connected == self.configured,
                "connected_count": connected,
                "configured_count": self.configured,
            }
        }


def test_auto_connect_retries_partial_fleet_until_ready() -> None:
    session = FakeSession([1, 2])
    assert auto_connect_until_ready(
        session,
        threading.Event(),
        retry_seconds=0,
        max_attempts=3,
    )
    assert session.calls == 2


def test_auto_connect_stops_after_bounded_attempts() -> None:
    session = FakeSession([0])
    assert not auto_connect_until_ready(
        session,
        threading.Event(),
        retry_seconds=0,
        max_attempts=2,
    )
    assert session.calls == 2


def test_auto_connect_honors_shutdown_before_first_attempt() -> None:
    session = FakeSession([2])
    stop_event = threading.Event()
    stop_event.set()
    assert not auto_connect_until_ready(
        session,
        stop_event,
        retry_seconds=0,
        max_attempts=2,
    )
    assert session.calls == 0


@pytest.mark.parametrize(
    ("retry_seconds", "max_attempts"),
    [(-1, 1), (0, 0)],
)
def test_auto_connect_rejects_invalid_bounds(
    retry_seconds: float,
    max_attempts: int,
) -> None:
    with pytest.raises(ValueError):
        auto_connect_until_ready(
            FakeSession([2]),
            threading.Event(),
            retry_seconds=retry_seconds,
            max_attempts=max_attempts,
        )
