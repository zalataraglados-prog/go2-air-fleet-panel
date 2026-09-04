from __future__ import annotations

from typing import Any

import pytest

from go2.motion import MotionError, OneShotStandController, OneShotStandDownController
from go2.safety import (
    StandSafetyError,
    is_prone_standby,
    validate_action_preflight,
    validate_stand_preflight,
)
from go2.state import LowState, SportModeState, StateSnapshot


def snapshot(
    *,
    soc: float = 80.0,
    voltage: float = 31.0,
    rpy: tuple[float, float, float] = (0.0, 0.0, 0.0),
    foot_force: tuple[float, float, float, float] = (50, 50, 50, 50),
    error_code: int = 1001,
    mode: int = 0,
) -> StateSnapshot:
    return StateSnapshot(
        low_state=LowState(
            rpy=rpy,
            battery_soc=soc,
            power_voltage=voltage,
            motor_count=20,
            foot_force=foot_force,
        ),
        sport_mode_state=SportModeState(
            error_code=error_code,
            mode=mode,
            gait_type=0,
            position=(0, 0, 0),
            velocity=(0, 0, 0),
            yaw_speed=0,
        ),
        low_state_samples=1,
        sport_mode_samples=1,
    )


def response(code: int = 0) -> dict[str, Any]:
    return {"data": {"header": {"status": {"code": code}}}}


class FakeMotionConnection:
    def __init__(
        self, *, stand_code: int = 0, stand_down_code: int = 0, stop_code: int = 0
    ) -> None:
        self.stand_code = stand_code
        self.stand_down_code = stand_down_code
        self.stop_code = stop_code
        self.calls: list[str] = []

    async def request_stand_up(self) -> Any:
        self.calls.append("stand_up")
        return response(self.stand_code)

    async def request_stand_down(self) -> Any:
        self.calls.append("stand_down")
        return response(self.stand_down_code)

    async def request_stop_move(self) -> Any:
        self.calls.append("stop_move")
        return response(self.stop_code)


@pytest.mark.parametrize(
    "unsafe",
    [
        snapshot(soc=10),
        snapshot(voltage=24),
        snapshot(rpy=(0.8, 0, 0)),
        snapshot(rpy=(0, -0.8, 0)),
        snapshot(foot_force=(0, 0, 0, 0)),
    ],
)
def test_stand_preflight_rejects_unsafe_state(unsafe: StateSnapshot) -> None:
    with pytest.raises(StandSafetyError):
        validate_stand_preflight(unsafe)


def test_action_preflight_requires_completed_standup_state() -> None:
    with pytest.raises(StandSafetyError, match="请先用面板执行一次起立"):
        validate_action_preflight(snapshot(error_code=1001, mode=0))
    validate_action_preflight(snapshot(error_code=1002, mode=0))
    validate_action_preflight(snapshot(error_code=1001, mode=1))


def test_prone_standby_uses_observed_go2_air_state_pair() -> None:
    assert is_prone_standby(snapshot(error_code=1001, mode=0)) is True
    assert is_prone_standby(snapshot(error_code=1001, mode=5)) is False
    assert is_prone_standby(snapshot(error_code=0, mode=0)) is False


@pytest.mark.asyncio
async def test_stand_is_followed_by_stop_watchdog() -> None:
    connection = FakeMotionConnection()
    result = await OneShotStandController(connection).execute(
        snapshot(), guard_seconds=0.01, request_timeout=0.1
    )
    assert result.stand_status == 0
    assert result.stop_status == 0
    assert connection.calls == ["stand_up", "stop_move"]


@pytest.mark.asyncio
async def test_code_less_matched_response_is_acknowledged_not_failed() -> None:
    connection = FakeMotionConnection()

    async def code_less_stand() -> Any:
        connection.calls.append("stand_up")
        return {"type": "res", "topic": "rt/api/sport/request", "data": {}}

    connection.request_stand_up = code_less_stand  # type: ignore[method-assign]
    result = await OneShotStandController(connection).execute(
        snapshot(), guard_seconds=0.01, request_timeout=0.1
    )
    assert result.stand_status is None
    assert result.stop_status == 0
    assert connection.calls == ["stand_up", "stop_move"]


@pytest.mark.asyncio
async def test_stand_down_is_followed_by_stop_watchdog() -> None:
    connection = FakeMotionConnection()
    result = await OneShotStandDownController(connection).execute(
        snapshot(), guard_seconds=0.01, request_timeout=0.1
    )
    assert result.stand_status == 0
    assert result.stop_status == 0
    assert connection.calls == ["stand_down", "stop_move"]


@pytest.mark.asyncio
async def test_stand_down_reports_terminal_stop_rejection_without_hiding_success() -> None:
    connection = FakeMotionConnection(stop_code=-1)
    result = await OneShotStandDownController(connection).execute(
        snapshot(), guard_seconds=0.01, request_timeout=0.1
    )
    assert result.stand_status == 0
    assert result.stop_status == -1
    assert connection.calls == ["stand_down", "stop_move"]


@pytest.mark.asyncio
async def test_rejected_stand_still_requests_stop() -> None:
    connection = FakeMotionConnection(stand_code=4202)
    with pytest.raises(MotionError, match="4202"):
        await OneShotStandController(connection).execute(
            snapshot(), guard_seconds=0.05, request_timeout=0.1
        )
    assert connection.calls == ["stand_up", "stop_move"]


@pytest.mark.asyncio
async def test_stop_watchdog_failure_is_reported() -> None:
    connection = FakeMotionConnection(stop_code=4202)
    with pytest.raises(MotionError, match="4202"):
        await OneShotStandController(connection).execute(
            snapshot(), guard_seconds=0.01, request_timeout=0.1
        )
    assert connection.calls == ["stand_up", "stop_move"]


@pytest.mark.asyncio
async def test_unsafe_state_sends_no_commands() -> None:
    connection = FakeMotionConnection()
    with pytest.raises(StandSafetyError):
        await OneShotStandController(connection).execute(
            snapshot(soc=5), guard_seconds=0.01, request_timeout=0.1
        )
    assert connection.calls == []
