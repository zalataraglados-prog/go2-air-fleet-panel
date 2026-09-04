from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from go2.state import (
    Go2StateReader,
    LOW_STATE_TOPIC,
    SPORT_MODE_STATE_TOPIC,
    StatePayloadError,
    StateTimeoutError,
    parse_low_state,
    parse_sport_mode_state,
)


def low_message(*, soc: float = 75) -> dict[str, Any]:
    return {
        "data": {
            "imu_state": {"rpy": [0.1, -0.2, 0.3]},
            "motor_state": [{} for _ in range(12)],
            "bms_state": {"soc": soc},
            "foot_force": [10, 20, 30, 40],
            "power_v": 31.8,
        }
    }


def sport_message() -> dict[str, Any]:
    return {
        "data": {
            "error_code": 1001,
            "mode": 1,
            "gait_type": 0,
            "position": [1.0, 2.0, 0.3],
            "velocity": [0.0, 0.0, 0.0],
            "yaw_speed": 0.0,
        }
    }


class FakeReadOnlyConnection:
    def __init__(self) -> None:
        self.callbacks: dict[str, Any] = {}
        self.subscribe_calls: list[str] = []
        self.unsubscribe_calls: list[str] = []

    def subscribe_read_only_state(self, topic: str, callback: Any) -> None:
        self.subscribe_calls.append(topic)
        self.callbacks[topic] = callback

    def unsubscribe_read_only_state(self, topic: str) -> None:
        self.unsubscribe_calls.append(topic)

    def emit(self, topic: str, message: Any) -> None:
        self.callbacks[topic](message)


def test_parsers_reduce_valid_payloads() -> None:
    low = parse_low_state(low_message())
    sport = parse_sport_mode_state(
        {"data": json.dumps(sport_message()["data"])}
    )
    assert low.battery_soc == 75
    assert low.motor_count == 12
    assert low.rpy == (0.1, -0.2, 0.3)
    assert sport.position == (1.0, 2.0, 0.3)
    assert sport.velocity == (0.0, 0.0, 0.0)
    assert sport.error_code == 1001


@pytest.mark.parametrize("soc", [-1, 101, float("nan")])
def test_low_state_rejects_invalid_battery_soc(soc: float) -> None:
    with pytest.raises(StatePayloadError):
        parse_low_state(low_message(soc=soc))


@pytest.mark.asyncio
async def test_reader_collects_both_topics_and_unsubscribes() -> None:
    connection = FakeReadOnlyConnection()
    task = asyncio.create_task(
        Go2StateReader(connection).collect(samples=2, timeout=1.0)
    )
    await asyncio.sleep(0)
    for _ in range(2):
        connection.emit(LOW_STATE_TOPIC, low_message())
        connection.emit(SPORT_MODE_STATE_TOPIC, sport_message())
    snapshot = await task
    assert snapshot.low_state_samples == 2
    assert snapshot.sport_mode_samples == 2
    assert connection.subscribe_calls == [LOW_STATE_TOPIC, SPORT_MODE_STATE_TOPIC]
    assert connection.unsubscribe_calls == [SPORT_MODE_STATE_TOPIC, LOW_STATE_TOPIC]


@pytest.mark.asyncio
async def test_reader_payload_error_still_unsubscribes() -> None:
    connection = FakeReadOnlyConnection()
    task = asyncio.create_task(
        Go2StateReader(connection).collect(samples=1, timeout=1.0)
    )
    await asyncio.sleep(0)
    connection.emit(LOW_STATE_TOPIC, {"data": {}})
    with pytest.raises(StatePayloadError):
        await task
    assert connection.unsubscribe_calls == [SPORT_MODE_STATE_TOPIC, LOW_STATE_TOPIC]


@pytest.mark.asyncio
async def test_reader_timeout_still_unsubscribes() -> None:
    connection = FakeReadOnlyConnection()
    with pytest.raises(StateTimeoutError):
        await Go2StateReader(connection).collect(samples=1, timeout=1.0)
    assert connection.unsubscribe_calls == [SPORT_MODE_STATE_TOPIC, LOW_STATE_TOPIC]
