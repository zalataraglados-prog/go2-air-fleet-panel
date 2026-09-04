"""Bounded, read-only telemetry collection for Unitree GO2 Air."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import math
from typing import Any, Mapping, Protocol


LOW_STATE_TOPIC = "rt/lf/lowstate"
SPORT_MODE_STATE_TOPIC = "rt/lf/sportmodestate"
READ_ONLY_STATE_TOPICS = (LOW_STATE_TOPIC, SPORT_MODE_STATE_TOPIC)


class StateError(RuntimeError):
    """Base class for sanitized state collection failures."""


class StateTimeoutError(StateError):
    """Raised when enough state samples do not arrive within the time limit."""


class StatePayloadError(StateError):
    """Raised when a telemetry payload does not match the expected safe schema."""


class _ReadOnlyConnection(Protocol):
    def subscribe_read_only_state(self, topic: str, callback: Any) -> None: ...

    def unsubscribe_read_only_state(self, topic: str) -> None: ...


@dataclass(frozen=True)
class LowState:
    rpy: tuple[float, float, float]
    battery_soc: float
    power_voltage: float
    motor_count: int
    foot_force: tuple[float, float, float, float]


@dataclass(frozen=True)
class SportModeState:
    error_code: int
    mode: int
    gait_type: int
    position: tuple[float, float, float]
    velocity: tuple[float, float, float]
    yaw_speed: float


@dataclass(frozen=True)
class StateSnapshot:
    low_state: LowState
    sport_mode_state: SportModeState
    low_state_samples: int
    sport_mode_samples: int


def _payload(message: Any) -> Mapping[str, Any]:
    if not isinstance(message, Mapping):
        raise StatePayloadError("Telemetry envelope must be a mapping.")
    data = message.get("data")
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            raise StatePayloadError("Telemetry data is not valid JSON.") from exc
    if not isinstance(data, Mapping):
        raise StatePayloadError("Telemetry data must be a mapping.")
    return data


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StatePayloadError(f"{label} must be a mapping.")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StatePayloadError(f"{label} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise StatePayloadError(f"{label} must be finite.")
    return number


def _integer(value: Any, label: str) -> int:
    number = _number(value, label)
    if not number.is_integer():
        raise StatePayloadError(f"{label} must be an integer.")
    return int(number)


def _vector(value: Any, size: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) < size:
        raise StatePayloadError(f"{label} must contain at least {size} values.")
    return tuple(_number(item, f"{label}[{index}]") for index, item in enumerate(value[:size]))


def parse_low_state(message: Any) -> LowState:
    """Validate and reduce a low-state message without retaining its raw payload."""

    data = _payload(message)
    imu = _mapping(data.get("imu_state"), "imu_state")
    bms = _mapping(data.get("bms_state"), "bms_state")
    rpy = _vector(imu.get("rpy"), 3, "imu_state.rpy")
    foot_force = _vector(data.get("foot_force"), 4, "foot_force")
    motors = data.get("motor_state")
    if not isinstance(motors, (list, tuple)) or len(motors) < 12:
        raise StatePayloadError("motor_state must contain at least 12 motors.")
    soc = _number(bms.get("soc"), "bms_state.soc")
    if not 0 <= soc <= 100:
        raise StatePayloadError("bms_state.soc must be between 0 and 100.")
    voltage = _number(data.get("power_v"), "power_v")
    if voltage <= 0:
        raise StatePayloadError("power_v must be greater than zero.")
    return LowState(
        rpy=(rpy[0], rpy[1], rpy[2]),
        battery_soc=soc,
        power_voltage=voltage,
        motor_count=len(motors),
        foot_force=(foot_force[0], foot_force[1], foot_force[2], foot_force[3]),
    )


def parse_sport_mode_state(message: Any) -> SportModeState:
    """Validate and reduce one sport-mode state message."""

    data = _payload(message)
    position = _vector(data.get("position"), 3, "position")
    velocity = _vector(data.get("velocity"), 3, "velocity")
    return SportModeState(
        error_code=_integer(data.get("error_code"), "error_code"),
        mode=_integer(data.get("mode"), "mode"),
        gait_type=_integer(data.get("gait_type"), "gait_type"),
        position=(position[0], position[1], position[2]),
        velocity=(velocity[0], velocity[1], velocity[2]),
        yaw_speed=_number(data.get("yaw_speed"), "yaw_speed"),
    )


class Go2StateReader:
    """Collect a small number of samples from two telemetry-only topics."""

    def __init__(self, connection: _ReadOnlyConnection) -> None:
        self._connection = connection

    async def collect(
        self, *, samples: int = 3, timeout: float = 10.0
    ) -> StateSnapshot:
        if not 1 <= samples <= 20:
            raise ValueError("samples must be between 1 and 20")
        if not 1.0 <= timeout <= 30.0:
            raise ValueError("timeout must be between 1 and 30 seconds")

        changed = asyncio.Event()
        low_states: list[LowState] = []
        sport_states: list[SportModeState] = []
        payload_error: StatePayloadError | None = None
        subscribed: list[str] = []

        def on_low_state(message: Any) -> None:
            nonlocal payload_error
            if len(low_states) >= samples:
                return
            try:
                low_states.append(parse_low_state(message))
            except StatePayloadError as exc:
                payload_error = exc
            changed.set()

        def on_sport_state(message: Any) -> None:
            nonlocal payload_error
            if len(sport_states) >= samples:
                return
            try:
                sport_states.append(parse_sport_mode_state(message))
            except StatePayloadError as exc:
                payload_error = exc
            changed.set()

        try:
            self._connection.subscribe_read_only_state(LOW_STATE_TOPIC, on_low_state)
            subscribed.append(LOW_STATE_TOPIC)
            self._connection.subscribe_read_only_state(
                SPORT_MODE_STATE_TOPIC, on_sport_state
            )
            subscribed.append(SPORT_MODE_STATE_TOPIC)

            async def wait_for_samples() -> None:
                while len(low_states) < samples or len(sport_states) < samples:
                    if payload_error is not None:
                        raise payload_error
                    changed.clear()
                    await changed.wait()
                if payload_error is not None:
                    raise payload_error

            try:
                await asyncio.wait_for(wait_for_samples(), timeout=timeout)
            except asyncio.TimeoutError as exc:
                raise StateTimeoutError(
                    "Timed out waiting for read-only GO2 telemetry samples."
                ) from exc

            return StateSnapshot(
                low_state=low_states[-1],
                sport_mode_state=sport_states[-1],
                low_state_samples=len(low_states),
                sport_mode_samples=len(sport_states),
            )
        finally:
            for topic in reversed(subscribed):
                self._connection.unsubscribe_read_only_state(topic)
