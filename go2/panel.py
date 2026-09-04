"""Local-only, single-entry safety panel for the GO2 Air."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import asdict
import logging
import secrets
import threading
import time
from typing import Any, Callable, Coroutine, Mapping, Protocol

from flask import Flask, jsonify, render_template, request

from .actions import (
    ACTION_LIBRARY,
    EDITOR_ACTION_IDS,
    FEATURED_CHOREOGRAPHY,
    public_action_library,
)
from .config import AppConfig, PROJECT_ROOT
from .connection import Go2Connection, Go2ConnectionError
from .custom_motion import public_custom_motion_limits, validate_custom_motion_step
from .motion import MotionError
from .protocol import (
    CommandOutcome,
    CommandProtocolError,
    parse_command_outcome,
    parse_motion_mode,
)
from .safety import (
    StandSafetyError,
    validate_action_preflight,
    validate_stand_preflight,
)
from .state import Go2StateReader, StateError, StateSnapshot


MAX_CHOREOGRAPHY_STEPS = 12
MAX_CHOREOGRAPHY_SECONDS = 40.0
LOGGER = logging.getLogger(__name__)


def action_response_timeout(watchdog_seconds: float) -> float:
    """Allow for firmware that resolves preset-action RPCs on completion."""

    return min(max(watchdog_seconds + 10.0, 15.0), 35.0)


def remaining_step_delay(duration: float, elapsed: float) -> float:
    """Keep a step active for at least ``duration`` without double waiting."""

    return max(0.0, duration - elapsed)


def euler_effect_observed(
    baseline_rpy: tuple[float, float, float],
    observed_rpy: tuple[float, float, float],
    target_rpy: tuple[float, float, float],
) -> bool:
    """Require a visible attitude change or a close roll/pitch target match."""

    roll_pitch_change = max(
        abs(observed_rpy[0] - baseline_rpy[0]),
        abs(observed_rpy[1] - baseline_rpy[1]),
    )
    yaw_change = abs(
        ((observed_rpy[2] - baseline_rpy[2] + 3.141592653589793)
        % (2 * 3.141592653589793))
        - 3.141592653589793
    )
    target_error = max(
        abs(observed_rpy[0] - target_rpy[0]),
        abs(observed_rpy[1] - target_rpy[1]),
    )
    if max(abs(target_rpy[0]), abs(target_rpy[1])) >= 0.03:
        return target_error <= 0.055 or roll_pitch_change >= 0.025
    if abs(target_rpy[2]) >= 0.03:
        return yaw_change >= 0.025
    return target_error <= 0.055 or roll_pitch_change >= 0.025


class PanelBusyError(RuntimeError):
    """Raised when a second mutating operation overlaps the current one."""


class PanelNotConnectedError(RuntimeError):
    """Raised when an operation requires an active robot connection."""


def validate_robot_ids(value: Any, configured_ids: tuple[str, ...]) -> list[str]:
    """Validate an explicit, non-empty RTS target selection."""

    if not isinstance(value, list) or not value:
        raise ValueError("请至少选择一只机器狗作为执行对象。")
    if any(not isinstance(item, str) for item in value):
        raise ValueError("执行对象列表格式无效。")
    if len(set(value)) != len(value):
        raise ValueError("执行对象列表不能包含重复机器狗。")
    unknown = [item for item in value if item not in configured_ids]
    if unknown:
        raise ValueError(f"执行对象不存在：{', '.join(unknown)}。")
    selected = set(value)
    return [robot_id for robot_id in configured_ids if robot_id in selected]


class PanelSessionProtocol(Protocol):
    def status(self) -> dict[str, Any]: ...

    def connect(self) -> dict[str, Any]: ...

    def disconnect(self) -> dict[str, Any]: ...

    def refresh_state(self) -> dict[str, Any]: ...

    def refresh_motion_mode(self) -> dict[str, Any]: ...

    def connect_fleet(self) -> dict[str, Any]: ...

    def refresh_fleet_state(self) -> dict[str, Any]: ...

    def run_posture(
        self,
        action_id: str,
        robot_ids: Any,
    ) -> dict[str, Any]: ...

    def run_library_action(
        self,
        action_id: str,
        robot_ids: Any,
    ) -> dict[str, Any]: ...

    def run_choreography(
        self,
        steps: Any,
        robot_ids: Any,
    ) -> dict[str, Any]: ...

    def stop_move(self, robot_ids: Any) -> dict[str, Any]: ...

    def close(self) -> None: ...


def _snapshot_payload(snapshot: StateSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    low = snapshot.low_state
    sport = snapshot.sport_mode_state
    return {
        "battery_soc": round(low.battery_soc, 1),
        "power_voltage": round(low.power_voltage, 2),
        "rpy": [round(value, 4) for value in low.rpy],
        "foot_force": [round(value, 1) for value in low.foot_force],
        "motor_count": low.motor_count,
        "error_code": sport.error_code,
        "mode": sport.mode,
        "gait_type": sport.gait_type,
        "velocity": [round(value, 4) for value in sport.velocity],
        "yaw_speed": round(sport.yaw_speed, 4),
    }


def validate_choreography_steps(value: Any) -> list[dict[str, Any]]:
    """Validate the visual editor's bounded, allowlisted sequence format."""

    if not isinstance(value, list) or not 1 <= len(value) <= MAX_CHOREOGRAPHY_STEPS:
        raise ValueError(
            f"编舞必须包含 1 到 {MAX_CHOREOGRAPHY_STEPS} 个动作。"
        )
    normalized: list[dict[str, Any]] = []
    total = 0.0
    for index, raw_step in enumerate(value, start=1):
        if not isinstance(raw_step, Mapping):
            raise ValueError(f"第 {index} 步格式无效。")
        duration = raw_step.get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError(f"第 {index} 步等待时间必须是数字。")
        seconds = round(float(duration), 2)
        if not 0.5 <= seconds <= 8.0:
            raise ValueError(f"第 {index} 步等待时间必须在 0.5 到 8 秒之间。")
        total += seconds

        if "action" in raw_step and "kind" not in raw_step:
            if set(raw_step) != {"action", "duration"}:
                raise ValueError(f"第 {index} 步包含不允许的字段。")
            action_id = raw_step.get("action")
            if not isinstance(action_id, str) or action_id not in EDITOR_ACTION_IDS:
                raise ValueError(f"第 {index} 步动作不在编舞白名单中。")
            normalized.append({"action": action_id, "duration": seconds})
            continue

        custom = validate_custom_motion_step(raw_step, step_index=index)
        normalized.append({**custom, "duration": seconds})
    if total > MAX_CHOREOGRAPHY_SECONDS:
        raise ValueError(f"编舞总时长不能超过 {MAX_CHOREOGRAPHY_SECONDS:.0f} 秒。")
    return normalized


class RobotPanelSession:
    """Own one event loop and one reusable WebRTC connection for the panel."""

    def __init__(
        self,
        config: AppConfig,
        *,
        connection_factory: Callable[[Any], Go2Connection] = Go2Connection,
        state_reader_factory: Callable[[Any], Go2StateReader] = Go2StateReader,
    ) -> None:
        self._config = config
        self._connection_factory = connection_factory
        self._state_reader_factory = state_reader_factory
        self._connection: Go2Connection | None = None
        self._robot_profiles = config.robots
        self._fleet_connections: dict[str, Go2Connection] = {}
        self._fleet_snapshots: dict[str, StateSnapshot] = {}
        self._fleet_motion_modes: dict[str, str] = {}
        self._fleet_connect_errors: dict[str, dict[str, str]] = {}
        self._last_snapshot: StateSnapshot | None = None
        self._action_results: dict[str, dict[str, Any]] = {}
        self._response_times_ms: dict[str, int] = {}
        self._late_response_tasks: set[asyncio.Task[Any]] = set()
        self._motion_mode: str | None = None
        self._motion_mode_status = "not_checked"
        self._motion_mode_checked_at: str | None = None
        self._operation: str | None = None
        self._operation_guard = threading.Lock()
        self._closed = False
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="go2-panel-asyncio",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=5.0):
            raise RuntimeError("The GO2 panel runtime did not start.")

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(
                asyncio.gather(*pending, return_exceptions=True)
            )
        self._loop.close()

    def _submit(self, coroutine: Coroutine[Any, Any, dict[str, Any]]) -> dict[str, Any]:
        if self._closed:
            coroutine.close()
            raise RuntimeError("The GO2 panel session is closed.")
        future: Future[dict[str, Any]] = asyncio.run_coroutine_threadsafe(
            coroutine, self._loop
        )
        return future.result(timeout=150.0)

    def _run_exclusive(
        self,
        name: str,
        operation: Callable[[], Coroutine[Any, Any, dict[str, Any]]],
    ) -> dict[str, Any]:
        if not self._operation_guard.acquire(blocking=False):
            raise PanelBusyError(f"当前正在执行：{self._operation or '其他操作'}")
        self._operation = name
        try:
            return self._submit(operation())
        finally:
            self._operation = None
            self._operation_guard.release()

    def status(self) -> dict[str, Any]:
        return self._submit(self._status())

    async def _status(self) -> dict[str, Any]:
        connection = self._connection
        transport = connection.transport_state if connection else None
        return {
            "connected": bool(
                connection
                and connection.is_connected
                and connection.data_channel_ready
            ),
            "busy": self._operation is not None,
            "operation": self._operation,
            "connection_mode": self._config.connection.mode,
            "target_ip": self._config.connection.target_ip,
            "motion_mode": {
                "name": self._motion_mode,
                "status": self._motion_mode_status,
                "checked_at": self._motion_mode_checked_at,
            },
            "transport": asdict(transport) if transport else None,
            "state": _snapshot_payload(self._last_snapshot),
            "actions": public_action_library(
                motion_mode=self._motion_mode,
                unsupported_actions=frozenset(),
            ),
            "action_results": dict(self._action_results),
            "custom_motion_limits": public_custom_motion_limits(),
            "featured_choreography": FEATURED_CHOREOGRAPHY,
            "fleet": self._fleet_status_payload(),
        }

    def _fleet_status_payload(self) -> dict[str, Any]:
        robots: list[dict[str, Any]] = []
        for index, profile in enumerate(self._robot_profiles):
            connection = (
                self._connection
                if index == 0
                else self._fleet_connections.get(profile.id)
            )
            snapshot = (
                self._last_snapshot
                if index == 0
                else self._fleet_snapshots.get(profile.id)
            )
            motion_mode = (
                self._motion_mode
                if index == 0
                else self._fleet_motion_modes.get(profile.id)
            )
            missing: list[str] = []
            if not profile.connection.serial_number:
                missing.append("SN")
            if profile.connection.require_aes_key and not profile.connection.aes_128_key:
                missing.append("AES key")
            robots.append(
                {
                    "id": profile.id,
                    "label": profile.label,
                    "target_ip": (
                        getattr(connection, "effective_target_ip", None)
                        if connection is not None
                        else None
                    )
                    or profile.connection.target_ip,
                    "serial_suffix": (
                        profile.connection.serial_number[-6:]
                        if profile.connection.serial_number
                        else None
                    ),
                    "credentials_ready": not missing,
                    "missing": missing,
                    "connected": bool(
                        connection
                        and connection.is_connected
                        and connection.data_channel_ready
                    ),
                    "motion_mode": motion_mode,
                    "state": _snapshot_payload(snapshot),
                    "connect_error": self._fleet_connect_errors.get(profile.id),
                }
            )
        connected_count = sum(bool(item["connected"]) for item in robots)
        connected = bool(robots) and connected_count == len(robots)
        inspected = connected and all(
            item["motion_mode"] and item["state"] for item in robots
        )
        return {
            "configured_count": len(robots),
            "connected_count": connected_count,
            "ready_to_connect": bool(robots)
            and any(item["credentials_ready"] for item in robots),
            "fully_configured": bool(robots)
            and all(item["credentials_ready"] for item in robots),
            "connected": connected,
            "inspected": inspected,
            "robots": robots,
        }

    def connect(self) -> dict[str, Any]:
        return self._run_exclusive("连接机器狗", self._connect)

    async def _connect(self) -> dict[str, Any]:
        if self._connection is None:
            self._connection = self._connection_factory(self._config.connection)
        await self._connection.connect()
        await self._query_motion_mode(suppress_errors=True)
        return await self._status()

    def connect_fleet(self) -> dict[str, Any]:
        return self._run_exclusive("连接全部机器狗", self._connect_fleet)

    async def _connect_fleet(self) -> dict[str, Any]:
        fleet_status = self._fleet_status_payload()
        if not fleet_status["ready_to_connect"]:
            details = "; ".join(
                f"{item['label']} 缺少 {', '.join(item['missing'])}"
                for item in fleet_status["robots"]
                if item["missing"]
            )
            raise ValueError(f"机器狗身份/凭据尚未就绪：{details}")

        async def connection_for(index: int) -> Go2Connection:
            profile = self._robot_profiles[index]
            if index == 0:
                if self._connection is None:
                    self._connection = self._connection_factory(profile.connection)
                return self._connection
            connection = self._fleet_connections.get(profile.id)
            if connection is None:
                connection = self._connection_factory(profile.connection)
                self._fleet_connections[profile.id] = connection
            return connection

        async def inspect_profile(index: int, connection: Go2Connection) -> None:
            profile = self._robot_profiles[index]
            response = await asyncio.wait_for(connection.request_motion_mode(), timeout=5.0)
            mode = parse_motion_mode(response)
            snapshot = await self._state_reader_factory(connection).collect(samples=1, timeout=10.0)
            if index == 0:
                self._motion_mode = mode
                self._motion_mode_status = "available"
                self._motion_mode_checked_at = time.strftime("%H:%M:%S")
                self._last_snapshot = snapshot
            else:
                self._fleet_motion_modes[profile.id] = mode
                self._fleet_snapshots[profile.id] = snapshot

        # aiortc handshakes must remain sequential, but one unavailable robot
        # must not tear down sessions that are already healthy.
        for index, profile in enumerate(self._robot_profiles):
            status_item = fleet_status["robots"][index]
            if not status_item["credentials_ready"]:
                self._fleet_connect_errors[profile.id] = {
                    "kind": "configuration",
                    "message": "身份或 AES 凭据不完整。",
                }
                continue
            connection = await connection_for(index)
            try:
                await connection.connect()
            except asyncio.CancelledError:
                raise
            except Go2ConnectionError as exc:
                self._fleet_connect_errors[profile.id] = {
                    "kind": exc.kind.value,
                    "message": str(exc),
                }
                await connection.disconnect()
                continue
            except Exception:
                LOGGER.exception("Fleet connection failed: robot=%s", profile.id)
                self._fleet_connect_errors[profile.id] = {
                    "kind": "internal",
                    "message": "连接过程发生内部异常。",
                }
                await connection.disconnect()
                continue

            self._fleet_connect_errors.pop(profile.id, None)
            try:
                await inspect_profile(index, connection)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("Fleet inspection failed: robot=%s", profile.id)
                self._fleet_connect_errors[profile.id] = {
                    "kind": "inspection_failed",
                    "message": "WebRTC 已连接，但运动模式或状态读取失败。",
                }

        result = await self._status()
        failures = result["fleet"]["robots"]
        failed_labels = [
            item["label"]
            for item in failures
            if not item["connected"]
        ]
        result["connection_results"] = {
            "connected": [
                item["id"] for item in failures if item["connected"]
            ],
            "failed": [item["id"] for item in failures if not item["connected"]],
        }
        if failed_labels:
            result["warning"] = (
                "以下机器狗未连接，可保持其他会话并单独重试："
                + "、".join(failed_labels)
                + "。"
            )
        return result

    def refresh_fleet_state(self) -> dict[str, Any]:
        return self._run_exclusive("刷新全部机器狗", self._refresh_fleet_state)

    async def _refresh_fleet_state(self) -> dict[str, Any]:
        async def collect_profile(index: int) -> None:
            profile = self._robot_profiles[index]
            connection = (
                self._connection
                if index == 0
                else self._fleet_connections.get(profile.id)
            )
            if not connection or not connection.is_connected or not connection.data_channel_ready:
                return
            snapshot = await self._state_reader_factory(connection).collect(
                samples=1,
                timeout=10.0,
            )
            if index == 0:
                self._last_snapshot = snapshot
            else:
                self._fleet_snapshots[profile.id] = snapshot

        if self._fleet_status_payload()["connected_count"] == 0:
            raise PanelNotConnectedError("当前没有已连接的机器狗。")
        outcomes = await asyncio.gather(
            *(collect_profile(index) for index in range(len(self._robot_profiles))),
            return_exceptions=True,
        )
        failed = [
            self._robot_profiles[index].label
            for index, outcome in enumerate(outcomes)
            if isinstance(outcome, BaseException)
        ]
        result: dict[str, Any] = {"fleet": self._fleet_status_payload()}
        if failed:
            result["warning"] = "状态读取失败：" + "、".join(failed) + "。"
        return result

    def _selected_runtime_pairs(
        self,
        robot_ids: Any,
    ) -> list[tuple[Any, Go2Connection]]:
        configured_ids = tuple(profile.id for profile in self._robot_profiles)
        selected_ids = validate_robot_ids(robot_ids, configured_ids)
        profiles = {profile.id: profile for profile in self._robot_profiles}
        pairs: list[tuple[Any, Go2Connection]] = []
        for robot_id in selected_ids:
            profile = profiles[robot_id]
            connection = (
                self._connection
                if robot_id == configured_ids[0]
                else self._fleet_connections.get(robot_id)
            )
            if (
                not connection
                or not connection.is_connected
                or not connection.data_channel_ready
            ):
                raise PanelNotConnectedError(f"{profile.label} 尚未连接。")
            pairs.append((profile, connection))
        return pairs

    def _motion_mode_for(self, robot_id: str) -> str | None:
        if robot_id == self._robot_profiles[0].id:
            return self._motion_mode
        return self._fleet_motion_modes.get(robot_id)

    def _store_snapshot(self, robot_id: str, snapshot: StateSnapshot) -> None:
        if robot_id == self._robot_profiles[0].id:
            self._last_snapshot = snapshot
        else:
            self._fleet_snapshots[robot_id] = snapshot

    async def _collect_snapshot_map(
        self,
        pairs: list[tuple[Any, Go2Connection]],
    ) -> dict[str, StateSnapshot]:
        snapshots = await asyncio.gather(
            *(
                self._state_reader_factory(connection).collect(
                    samples=1,
                    timeout=10.0,
                )
                for _, connection in pairs
            )
        )
        result: dict[str, StateSnapshot] = {}
        for (profile, _), snapshot in zip(pairs, snapshots):
            result[profile.id] = snapshot
            self._store_snapshot(profile.id, snapshot)
        return result

    async def _selected_checked_command(
        self,
        pairs: list[tuple[Any, Go2Connection]],
        command: str,
        request_factory: Callable[[Go2Connection], Coroutine[Any, Any, Any]],
        *,
        timeout: float,
        result_id: str,
    ) -> dict[str, Any]:
        gate = asyncio.Event()
        sent_at: dict[str, float] = {}

        async def send_one(profile: Any, connection: Go2Connection) -> CommandOutcome:
            await gate.wait()
            sent_at[profile.id] = time.monotonic()
            response = await self._request_response(
                request_factory(connection),
                f"{profile.label} {command}",
                result_id,
                timeout=timeout,
                robot_id=profile.id,
            )
            return self._command_outcome(
                response,
                f"{profile.label} {command}",
                result_id,
                robot_id=profile.id,
            )

        tasks = [
            asyncio.create_task(send_one(profile, connection))
            for profile, connection in pairs
        ]
        await asyncio.sleep(0)
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            first = failures[0]
            if isinstance(first, Exception):
                raise first
            raise MotionError(f"{command} 被中断。")
        outcomes = {
            profile.id: results[index]
            for index, (profile, _) in enumerate(pairs)
        }
        timestamps = list(sent_at.values())
        return {
            "codes": {
                robot_id: outcome.code
                for robot_id, outcome in outcomes.items()
            },
            "accepted": {
                robot_id: outcome.accepted
                for robot_id, outcome in outcomes.items()
            },
            "response_shapes": {
                robot_id: outcome.shape
                for robot_id, outcome in outcomes.items()
            },
            "start_skew_ms": round(
                (max(timestamps) - min(timestamps)) * 1000,
                2,
            ) if timestamps else 0.0,
        }

    async def _selected_best_effort_stop(
        self,
        pairs: list[tuple[Any, Go2Connection]],
        *,
        accepted_codes: frozenset[int] = frozenset({0}),
    ) -> tuple[dict[str, int | None], list[str]]:
        codes: dict[str, int | None] = {}
        warnings: list[str] = []

        async def stop_one(profile: Any, connection: Go2Connection) -> None:
            try:
                response = await asyncio.wait_for(
                    asyncio.shield(asyncio.create_task(connection.request_stop_move())),
                    timeout=5.0,
                )
                outcome = parse_command_outcome(
                    response,
                    accepted_codes=accepted_codes,
                )
                codes[profile.id] = outcome.code
                if outcome.accepted is False:
                    warnings.append(
                        f"{profile.label} StopMove 被拒绝（code {outcome.code}），请立即人工确认。"
                    )
                elif outcome.accepted is None:
                    warnings.append(
                        f"{profile.label} StopMove 已应答但无显式状态码，请人工确认。"
                    )
            except BaseException:
                codes[profile.id] = None
                warnings.append(
                    f"{profile.label} StopMove 未得到有效应答，请立即人工确认。"
                )
                LOGGER.error(
                    "Selected StopMove did not confirm: robot=%s",
                    profile.id,
                )

        await asyncio.gather(
            *(stop_one(profile, connection) for profile, connection in pairs),
            return_exceptions=True,
        )
        return codes, warnings

    async def _disconnect_all_connections(self) -> None:
        connections = [
            connection
            for connection in [self._connection, *self._fleet_connections.values()]
            if connection is not None
        ]
        if connections:
            await asyncio.gather(
                *(connection.disconnect() for connection in connections),
                return_exceptions=True,
            )
        self._last_snapshot = None
        self._fleet_snapshots.clear()
        self._fleet_motion_modes.clear()

    def disconnect(self) -> dict[str, Any]:
        return self._run_exclusive("断开连接", self._disconnect)

    async def _disconnect(self) -> dict[str, Any]:
        await self._disconnect_all_connections()
        self._motion_mode = None
        self._motion_mode_status = "not_checked"
        self._motion_mode_checked_at = None
        self._fleet_connect_errors.clear()
        return await self._status()

    def refresh_state(self) -> dict[str, Any]:
        return self._run_exclusive("刷新状态", self._refresh_state)

    async def _require_connection(self) -> Go2Connection:
        connection = self._connection
        if not connection or not connection.is_connected or not connection.data_channel_ready:
            raise PanelNotConnectedError("机器狗尚未连接。")
        return connection

    async def _collect_state(self) -> StateSnapshot:
        connection = await self._require_connection()
        snapshot = await self._state_reader_factory(connection).collect(
            samples=1,
            timeout=10.0,
        )
        self._last_snapshot = snapshot
        return snapshot

    async def _refresh_state(self) -> dict[str, Any]:
        snapshot = await self._collect_state()
        return {"state": _snapshot_payload(snapshot)}

    def refresh_motion_mode(self) -> dict[str, Any]:
        return self._run_exclusive("检测运动模式", self._refresh_motion_mode)

    async def _refresh_motion_mode(self) -> dict[str, Any]:
        await self._query_motion_mode(suppress_errors=False)
        return {
            "motion_mode": {
                "name": self._motion_mode,
                "status": self._motion_mode_status,
                "checked_at": self._motion_mode_checked_at,
            }
        }

    async def _query_motion_mode(self, *, suppress_errors: bool) -> None:
        connection = await self._require_connection()
        started = time.monotonic()
        try:
            response = await asyncio.wait_for(
                connection.request_motion_mode(),
                timeout=5.0,
            )
            mode = parse_motion_mode(response)
        except Exception as exc:
            self._motion_mode = None
            self._motion_mode_status = "unavailable"
            self._motion_mode_checked_at = time.strftime("%H:%M:%S")
            LOGGER.warning(
                "GO2 read-only motion mode query failed: error=%s elapsed_ms=%d",
                type(exc).__name__,
                round((time.monotonic() - started) * 1000),
            )
            if suppress_errors:
                return
            raise MotionError("读取运动模式失败；未切换模式，也未发送动作。") from exc
        self._motion_mode = mode
        self._motion_mode_status = "available"
        self._motion_mode_checked_at = time.strftime("%H:%M:%S")
        LOGGER.info(
            "GO2 motion mode detected: name=%s elapsed_ms=%d",
            mode,
            round((time.monotonic() - started) * 1000),
        )

    def _record_outcome(
        self,
        result_id: str,
        outcome: CommandOutcome | None,
        *,
        status: str | None = None,
        robot_id: str | None = None,
    ) -> None:
        if status is None:
            status = (
                "unsupported"
                if outcome and outcome.code == 3203
                else "accepted"
                if outcome and outcome.accepted is True
                else "acknowledged"
                if outcome and outcome.accepted is None
                else "rejected"
            )
        self._action_results[result_id] = {
            "status": status,
            "code": outcome.code if outcome else None,
            "response_shape": outcome.shape if outcome else None,
            "response_ms": getattr(self, "_response_times_ms", {}).get(result_id),
            "updated_at": time.strftime("%H:%M:%S"),
        }
        LOGGER.info(
            "GO2 command outcome: action=%s status=%s code=%s shape=%s response_ms=%s",
            result_id,
            status,
            outcome.code if outcome else None,
            outcome.shape if outcome else "invalid",
            getattr(self, "_response_times_ms", {}).get(result_id),
        )

    def _command_outcome(
        self,
        response: Any,
        command: str,
        result_id: str,
        *,
        accepted_codes: frozenset[int] = frozenset({0}),
        robot_id: str | None = None,
    ) -> CommandOutcome:
        try:
            outcome = parse_command_outcome(response, accepted_codes=accepted_codes)
        except CommandProtocolError as exc:
            self._record_outcome(
                result_id,
                None,
                status="invalid_response",
                robot_id=robot_id,
            )
            raise MotionError(f"{command} 回包无法识别；未据此判定动作失败。") from exc
        self._record_outcome(result_id, outcome, robot_id=robot_id)
        if outcome.accepted is False:
            raise MotionError(f"{command} 被机器狗明确拒绝，状态码 {outcome.code}。")
        return outcome

    async def _request_response(
        self,
        awaitable: Coroutine[Any, Any, Any],
        command: str,
        result_id: str,
        *,
        timeout: float,
        robot_id: str | None = None,
    ) -> Any:
        """Wait without cancelling the SDK future when firmware replies late."""

        started = time.monotonic()
        task = asyncio.create_task(awaitable)
        try:
            response = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            elapsed_ms = round((time.monotonic() - started) * 1000)
            if not hasattr(self, "_response_times_ms"):
                self._response_times_ms = {}
            self._response_times_ms[result_id] = elapsed_ms
            LOGGER.info(
                "GO2 matched response received: action=%s elapsed_ms=%d timeout_s=%g",
                result_id,
                elapsed_ms,
                timeout,
            )
            return response
        except asyncio.TimeoutError as exc:
            if not hasattr(self, "_response_times_ms"):
                self._response_times_ms = {}
            self._response_times_ms[result_id] = round(
                (time.monotonic() - started) * 1000
            )
            self._record_outcome(
                result_id,
                None,
                status="response_timeout",
                robot_id=robot_id,
            )
            self._late_response_tasks.add(task)

            def consume_late_response(completed: asyncio.Task[Any]) -> None:
                self._late_response_tasks.discard(completed)
                try:
                    response = completed.result()
                    self._response_times_ms[result_id] = round(
                        (time.monotonic() - started) * 1000
                    )
                    outcome = parse_command_outcome(response)
                    self._record_outcome(
                        result_id,
                        outcome,
                        robot_id=robot_id,
                    )
                    LOGGER.info(
                        "Late GO2 response resolved: action=%s elapsed_ms=%d",
                        result_id,
                        self._response_times_ms[result_id],
                    )
                except BaseException as late_exc:
                    LOGGER.warning(
                        "Late GO2 response failed: action=%s error=%s",
                        result_id,
                        type(late_exc).__name__,
                    )

            task.add_done_callback(consume_late_response)
            raise MotionError(
                f"{command} 指令已经发出，但 {timeout:g} 秒内未收到匹配回包；"
                "系统已中止后续步骤并尝试 StopMove。"
            ) from exc

    def run_posture(
        self,
        action_id: str,
        robot_ids: Any,
    ) -> dict[str, Any]:
        labels = {"stand_up": "起立", "stand_down": "趴下"}
        if action_id not in labels:
            raise ValueError("该动作未进入控制面板白名单。")
        selected = validate_robot_ids(
            robot_ids,
            tuple(profile.id for profile in self._robot_profiles),
        )
        return self._run_exclusive(
            f"{labels[action_id]} · {len(selected)} 个对象",
            lambda: self._run_posture(action_id, selected),
        )

    async def _run_posture(
        self,
        action_id: str,
        robot_ids: list[str],
    ) -> dict[str, Any]:
        pairs = self._selected_runtime_pairs(robot_ids)
        preflight = await self._collect_snapshot_map(pairs)
        for profile, _ in pairs:
            try:
                validate_stand_preflight(preflight[profile.id])
            except StandSafetyError as exc:
                raise StandSafetyError(
                    f"{profile.label} 未通过姿态安全预检：{exc}"
                ) from exc

        command = "StandUp" if action_id == "stand_up" else "StandDown"
        request_factory = (
            (lambda connection: connection.request_stand_up())
            if action_id == "stand_up"
            else (lambda connection: connection.request_stand_down())
        )
        started = time.monotonic()
        command_result: dict[str, Any] | None = None
        stop_codes: dict[str, int | None] = {}
        warnings: list[str] = []
        try:
            command_result = await self._selected_checked_command(
                pairs,
                command,
                request_factory,
                timeout=3.0,
                result_id=action_id,
            )
            await asyncio.sleep(remaining_step_delay(5.0, time.monotonic() - started))
        finally:
            stop_codes, stop_warnings = await self._selected_best_effort_stop(
                pairs,
                accepted_codes=(
                    frozenset({0, -1})
                    if action_id == "stand_down"
                    else frozenset({0})
                ),
            )
            warnings.extend(stop_warnings)

        states: dict[str, StateSnapshot] = {}
        try:
            states = await self._collect_snapshot_map(pairs)
        except StateError:
            warnings.append("动作后状态刷新失败，请人工确认姿态。")

        assert command_result is not None
        aggregate_status = (
            "accepted"
            if all(value is True for value in command_result["accepted"].values())
            else "acknowledged"
        )
        self._action_results[action_id] = {
            "status": aggregate_status,
            "code": None,
            "codes": command_result["codes"],
            "robots": list(robot_ids),
            "response_shape": "multi_target_posture",
            "response_ms": None,
            "updated_at": time.strftime("%H:%M:%S"),
        }
        return {
            "action": action_id,
            "robot_ids": list(robot_ids),
            "start_skew_ms": command_result["start_skew_ms"],
            "guard_seconds": 5.0,
            "robots": {
                profile.id: {
                    "command_status": command_result["codes"][profile.id],
                    "stop_status": stop_codes.get(profile.id),
                    "state": _snapshot_payload(states.get(profile.id)),
                }
                for profile, _ in pairs
            },
            "warning": " ".join(dict.fromkeys(warnings)) or None,
        }

    def run_library_action(
        self,
        action_id: str,
        robot_ids: Any,
    ) -> dict[str, Any]:
        spec = ACTION_LIBRARY.get(action_id)
        if spec is None:
            raise ValueError("该动作未进入控制面板白名单。")
        if action_id in {"stand_up", "stand_down"}:
            return self.run_posture(action_id, robot_ids)
        selected = validate_robot_ids(
            robot_ids,
            tuple(profile.id for profile in self._robot_profiles),
        )
        return self._run_exclusive(
            f"{spec.label} · {len(selected)} 个对象",
            lambda: self._run_library_action(action_id, selected),
        )

    def _validate_action_availability(
        self,
        action_id: str,
        robot_id: str | None = None,
    ) -> None:
        spec = ACTION_LIBRARY[action_id]
        motion_mode = (
            self._motion_mode
            if robot_id is None
            else self._motion_mode_for(robot_id)
        )
        if spec.required_motion_mode and motion_mode != spec.required_motion_mode:
            current = motion_mode or "未知"
            raise MotionError(
                f"{spec.label} 需要 {spec.required_motion_mode} 运动模式，当前为 {current}；未发送动作。"
            )

    async def _run_library_action(
        self,
        action_id: str,
        robot_ids: list[str],
    ) -> dict[str, Any]:
        spec = ACTION_LIBRARY[action_id]
        pairs = self._selected_runtime_pairs(robot_ids)
        preflight = await self._collect_snapshot_map(pairs)
        for profile, _ in pairs:
            self._validate_action_availability(action_id, profile.id)
            try:
                validate_action_preflight(preflight[profile.id])
            except StandSafetyError as exc:
                raise StandSafetyError(
                    f"{profile.label} 未通过动作安全预检：{exc}"
                ) from exc

        action_started = time.monotonic()
        command_result: dict[str, Any] | None = None
        stop_codes: dict[str, int | None] = {}
        warnings: list[str] = []
        try:
            command_result = await self._selected_checked_command(
                pairs,
                spec.label,
                lambda connection: connection.request_library_action(action_id),
                timeout=action_response_timeout(spec.watchdog_seconds),
                result_id=action_id,
            )
            await asyncio.sleep(
                remaining_step_delay(
                    spec.watchdog_seconds,
                    time.monotonic() - action_started,
                )
            )
        finally:
            stop_codes, stop_warnings = await self._selected_best_effort_stop(pairs)
            warnings.extend(stop_warnings)

        assert command_result is not None
        if any(
            value is None for value in command_result["accepted"].values()
        ):
            warnings.append(
                f"{spec.label} 至少有一个对象已应答，但固件未返回显式状态码，请结合姿态确认。"
            )
        aggregate_status = (
            "accepted"
            if all(value is True for value in command_result["accepted"].values())
            else "acknowledged"
        )
        self._action_results[action_id] = {
            "status": aggregate_status,
            "code": None,
            "codes": command_result["codes"],
            "robots": list(robot_ids),
            "response_shape": "multi_target_action",
            "response_ms": None,
            "updated_at": time.strftime("%H:%M:%S"),
        }
        return {
            "action": action_id,
            "robot_ids": list(robot_ids),
            "codes": command_result["codes"],
            "accepted_by_robot": command_result["accepted"],
            "stop_status": stop_codes,
            "start_skew_ms": command_result["start_skew_ms"],
            "guard_seconds": spec.watchdog_seconds,
            "warning": " ".join(dict.fromkeys(warnings)) or None,
        }

    def run_choreography(
        self,
        steps: Any,
        robot_ids: Any,
    ) -> dict[str, Any]:
        normalized = validate_choreography_steps(steps)
        selected = validate_robot_ids(
            robot_ids,
            tuple(profile.id for profile in self._robot_profiles),
        )
        return self._run_exclusive(
            f"执行自定义编舞 · {len(selected)} 个对象",
            lambda: self._run_choreography(normalized, selected),
        )

    async def _run_choreography(
        self,
        steps: list[dict[str, Any]],
        robot_ids: list[str],
    ) -> dict[str, Any]:
        pairs = self._selected_runtime_pairs(robot_ids)
        preflight = await self._collect_snapshot_map(pairs)
        for profile, _ in pairs:
            try:
                validate_action_preflight(preflight[profile.id])
            except StandSafetyError as exc:
                raise StandSafetyError(
                    f"{profile.label} 未通过编舞安全预检：{exc}"
                ) from exc

        completed: list[dict[str, Any]] = []
        stop_codes: dict[str, int | None] = {}
        warnings: list[str] = []
        try:
            for index, step in enumerate(steps, start=1):
                step_started = time.monotonic()
                command_result: dict[str, Any] | None = None
                if "action" in step:
                    spec = ACTION_LIBRARY[step["action"]]
                    for profile, _ in pairs:
                        self._validate_action_availability(spec.id, profile.id)
                    label = spec.label
                    self._operation = (
                        f"编舞 {index}/{len(steps)} · {label} · "
                        f"{len(pairs)} 个对象"
                    )
                    command_result = await self._selected_checked_command(
                        pairs,
                        f"第 {index} 步 {label}",
                        lambda connection, action_id=spec.id: (
                            connection.request_library_action(action_id)
                        ),
                        timeout=action_response_timeout(spec.watchdog_seconds),
                        result_id=spec.id,
                    )
                    completed.append(
                        {
                            "action": spec.id,
                            "label": label,
                            "duration": step["duration"],
                            "codes": command_result["codes"],
                            "accepted_by_robot": command_result["accepted"],
                            "start_skew_ms": command_result["start_skew_ms"],
                        }
                    )
                elif step["kind"] == "wait":
                    label = "停顿"
                    self._operation = (
                        f"编舞 {index}/{len(steps)} · {label} · "
                        f"{len(pairs)} 个对象"
                    )
                    completed.append(
                        {
                            "kind": "wait",
                            "label": label,
                            "duration": step["duration"],
                            "accepted": True,
                            "response_shape": "local_wait",
                        }
                    )
                else:
                    kind = step["kind"]
                    label = "自定义姿态"
                    result_id = f"custom:{kind}"
                    parameters = {
                        key: value
                        for key, value in step.items()
                        if key not in {"kind", "duration"}
                    }
                    baseline = (
                        await self._collect_snapshot_map(pairs)
                        if kind == "euler"
                        else {}
                    )
                    self._operation = (
                        f"编舞 {index}/{len(steps)} · {label} · "
                        f"{len(pairs)} 个对象"
                    )
                    command_result = await self._selected_checked_command(
                        pairs,
                        f"第 {index} 步 {label}",
                        lambda connection: connection.request_custom_motion(
                            kind,
                            parameters,
                        ),
                        timeout=4.0,
                        result_id=result_id,
                    )
                    effects: dict[str, bool | None] = {
                        profile.id: None for profile, _ in pairs
                    }
                    observed_rpy: dict[str, list[float]] = {}
                    delta_rpy: dict[str, list[float]] = {}
                    if kind == "euler":
                        observation_delay = min(
                            0.8,
                            max(0.35, step["duration"] * 0.55),
                        )
                        await asyncio.sleep(observation_delay)
                        try:
                            observations = await self._collect_snapshot_map(pairs)
                        except StateError:
                            warnings.append(
                                f"第 {index} 步回包成功，但实时姿态采样失败，无法验证目视效果。"
                            )
                        else:
                            target_rpy = (
                                float(parameters["roll"]),
                                float(parameters["pitch"]),
                                float(parameters["yaw"]),
                            )
                            for profile, _ in pairs:
                                baseline_rpy = baseline[profile.id].low_state.rpy
                                current_rpy = observations[profile.id].low_state.rpy
                                effects[profile.id] = euler_effect_observed(
                                    baseline_rpy,
                                    current_rpy,
                                    target_rpy,
                                )
                                observed_rpy[profile.id] = [
                                    round(value, 4) for value in current_rpy
                                ]
                                delta_rpy[profile.id] = [
                                    round(
                                        current_rpy[axis] - baseline_rpy[axis],
                                        4,
                                    )
                                    for axis in range(3)
                                ]
                                LOGGER.info(
                                    "GO2 Euler effect check: step=%d robot=%s "
                                    "target=%s observed=%s effect=%s",
                                    index,
                                    profile.id,
                                    target_rpy,
                                    tuple(observed_rpy[profile.id]),
                                    effects[profile.id],
                                )
                    completed.append(
                        {
                            "kind": kind,
                            "label": label,
                            "duration": step["duration"],
                            "codes": command_result["codes"],
                            "accepted_by_robot": command_result["accepted"],
                            "effect_observed": effects,
                            "observed_rpy": observed_rpy,
                            "delta_rpy": delta_rpy,
                            "start_skew_ms": command_result["start_skew_ms"],
                        }
                    )
                    invisible = [
                        profile.label
                        for profile, _ in pairs
                        if effects.get(profile.id) is False
                    ]
                    if invisible:
                        raise MotionError(
                            f"第 {index} 步 {label} 虽返回 code 0，但"
                            f"{'、'.join(invisible)} 的实时 RPY 未出现预期变化；"
                            "已中止后续步骤并尝试 StopMove。"
                        )

                if command_result and any(
                    value is None
                    for value in command_result["accepted"].values()
                ):
                    warnings.append(
                        f"第 {index} 步至少有一个对象已应答，但无显式状态码。"
                    )
                elapsed = time.monotonic() - step_started
                delay = remaining_step_delay(step["duration"], elapsed)
                LOGGER.info(
                    "GO2 choreography step timing: step=%d label=%s "
                    "elapsed_ms=%d remaining_ms=%d targets=%s",
                    index,
                    label,
                    round(elapsed * 1000),
                    round(delay * 1000),
                    ",".join(robot_ids),
                )
                await asyncio.sleep(delay)
        finally:
            stop_codes, stop_warnings = await self._selected_best_effort_stop(pairs)
            warnings.extend(stop_warnings)

        self._action_results["choreography"] = {
            "status": "accepted",
            "code": None,
            "robots": list(robot_ids),
            "response_shape": "multi_target_choreography",
            "response_ms": None,
            "updated_at": time.strftime("%H:%M:%S"),
        }
        return {
            "action": "choreography",
            "robot_ids": list(robot_ids),
            "completed": completed,
            "stop_status": stop_codes,
            "warning": " ".join(dict.fromkeys(warnings)) or None,
        }

    def stop_move(self, robot_ids: Any) -> dict[str, Any]:
        selected = validate_robot_ids(
            robot_ids,
            tuple(profile.id for profile in self._robot_profiles),
        )
        return self._run_exclusive(
            f"StopMove · {len(selected)} 个对象",
            lambda: self._stop_move(selected),
        )

    async def _stop_move(self, robot_ids: list[str]) -> dict[str, Any]:
        pairs = self._selected_runtime_pairs(robot_ids)
        result = await self._selected_checked_command(
            pairs,
            "StopMove",
            lambda connection: connection.request_stop_move(),
            timeout=3.0,
            result_id="stop_move",
        )
        warnings = [
            f"{profile.label} StopMove 已应答但无显式状态码，请人工确认。"
            for profile, _ in pairs
            if result["accepted"][profile.id] is None
        ]
        return {
            "action": "stop_move",
            "robot_ids": list(robot_ids),
            "codes": result["codes"],
            "accepted_by_robot": result["accepted"],
            "start_skew_ms": result["start_skew_ms"],
            "warning": " ".join(warnings) or None,
        }

    def close(self) -> None:
        if self._closed:
            return
        if self._operation_guard.acquire(timeout=10.0):
            try:
                if self._connection is not None:
                    try:
                        self._submit(self._disconnect())
                    except Exception:
                        pass
            finally:
                self._operation_guard.release()
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5.0)


def create_panel_app(
    config: AppConfig,
    *,
    session: PanelSessionProtocol | None = None,
) -> Flask:
    """Create the loopback-only Flask application."""

    app = Flask(
        "go2_panel",
        template_folder=str(PROJECT_ROOT / "panel" / "templates"),
        static_folder=str(PROJECT_ROOT / "panel" / "static"),
        static_url_path="/static",
    )
    app.config.update(
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH=16 * 1024,
        TRUSTED_HOSTS=["127.0.0.1", "localhost"],
    )
    panel_session = session or RobotPanelSession(config)
    csrf_token = secrets.token_urlsafe(32)
    configured_robot_ids = tuple(profile.id for profile in config.robots)
    app.extensions["go2_panel_session"] = panel_session
    app.extensions["go2_panel_csrf"] = csrf_token

    @app.after_request
    def security_headers(response: Any) -> Any:
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'none'"
        )
        return response

    @app.get("/")
    def index() -> str:
        return render_template("workbench.html", csrf_token=csrf_token)

    @app.get("/settings")
    def settings() -> str:
        return render_template("settings.html", csrf_token=csrf_token)

    @app.get("/api/status")
    def status() -> Any:
        return jsonify(panel_session.status())

    def require_panel_request() -> None:
        if request.headers.get("X-Go2-Panel-Token") != csrf_token:
            raise PermissionError("控制请求校验失败，请刷新面板后重试。")
        if not request.is_json:
            raise ValueError("控制请求必须使用 JSON。")

    @app.post("/api/connect")
    def connect() -> Any:
        require_panel_request()
        return jsonify(panel_session.connect())

    @app.post("/api/fleet/connect")
    def connect_fleet() -> Any:
        require_panel_request()
        return jsonify(panel_session.connect_fleet())

    @app.post("/api/disconnect")
    def disconnect() -> Any:
        require_panel_request()
        return jsonify(panel_session.disconnect())

    @app.post("/api/state/refresh")
    def refresh_state() -> Any:
        require_panel_request()
        return jsonify(panel_session.refresh_state())

    @app.post("/api/fleet/state/refresh")
    def refresh_fleet_state() -> Any:
        require_panel_request()
        return jsonify(panel_session.refresh_fleet_state())

    @app.post("/api/diagnostics/motion-mode")
    def refresh_motion_mode() -> Any:
        require_panel_request()
        return jsonify(panel_session.refresh_motion_mode())

    @app.post("/api/actions/<action_id>")
    def posture_action(action_id: str) -> Any:
        require_panel_request()
        body = request.get_json(silent=True)
        if not isinstance(body, Mapping) or body.get("confirm_clearance") is not True:
            raise PermissionError("必须重新确认现场净空后才能执行姿态动作。")
        robot_ids = validate_robot_ids(body.get("robot_ids"), configured_robot_ids)
        return jsonify(panel_session.run_posture(action_id, robot_ids))

    @app.post("/api/library/<action_id>")
    def library_action(action_id: str) -> Any:
        require_panel_request()
        spec = ACTION_LIBRARY.get(action_id)
        if spec is None:
            raise ValueError("该动作未进入控制面板白名单。")
        body = request.get_json(silent=True)
        if not isinstance(body, Mapping) or body.get("confirm_clearance") is not True:
            raise PermissionError("必须重新确认现场净空后才能执行动作。")
        if spec.requires_advanced_ack and body.get("risk_ack") != "GO2 HIGH RISK":
            raise PermissionError("高风险动作需要输入指定确认短语。")
        robot_ids = validate_robot_ids(body.get("robot_ids"), configured_robot_ids)
        return jsonify(panel_session.run_library_action(action_id, robot_ids))

    @app.post("/api/choreographies/validate")
    def validate_choreography() -> Any:
        require_panel_request()
        body = request.get_json(silent=True)
        if not isinstance(body, Mapping):
            raise ValueError("编舞请求格式无效。")
        steps = validate_choreography_steps(body.get("steps"))
        return jsonify(
            {
                "valid": True,
                "steps": steps,
                "duration": round(sum(step["duration"] for step in steps), 2),
            }
        )

    @app.post("/api/choreographies/run")
    def run_choreography() -> Any:
        require_panel_request()
        body = request.get_json(silent=True)
        if not isinstance(body, Mapping) or body.get("confirm_clearance") is not True:
            raise PermissionError("执行编舞前必须重新确认现场净空。")
        robot_ids = validate_robot_ids(body.get("robot_ids"), configured_robot_ids)
        return jsonify(panel_session.run_choreography(body.get("steps"), robot_ids))

    @app.post("/api/stop-move")
    def stop_move() -> Any:
        require_panel_request()
        body = request.get_json(silent=True)
        if not isinstance(body, Mapping):
            raise ValueError("StopMove 请求格式无效。")
        robot_ids = validate_robot_ids(body.get("robot_ids"), configured_robot_ids)
        return jsonify(panel_session.stop_move(robot_ids))

    @app.errorhandler(PanelBusyError)
    def handle_busy(exc: PanelBusyError) -> Any:
        return jsonify({"error": str(exc), "kind": "busy"}), 409

    @app.errorhandler(PanelNotConnectedError)
    def handle_not_connected(exc: PanelNotConnectedError) -> Any:
        return jsonify({"error": str(exc), "kind": "not_connected"}), 409

    @app.errorhandler(PermissionError)
    def handle_permission(exc: PermissionError) -> Any:
        return jsonify({"error": str(exc), "kind": "permission"}), 403

    @app.errorhandler(ValueError)
    def handle_value(exc: ValueError) -> Any:
        return jsonify({"error": str(exc), "kind": "invalid_request"}), 400

    @app.errorhandler(Go2ConnectionError)
    def handle_connection(exc: Go2ConnectionError) -> Any:
        return jsonify({"error": str(exc), "kind": exc.kind.value}), 503

    def handle_robot_operation(exc: Exception) -> Any:
        return jsonify({"error": str(exc), "kind": "robot_operation"}), 422

    for operation_error in (MotionError, StandSafetyError, StateError):
        app.register_error_handler(operation_error, handle_robot_operation)

    @app.errorhandler(Exception)
    def handle_unexpected(exc: Exception) -> Any:
        if getattr(exc, "code", None) is not None:
            return exc
        LOGGER.exception("Unhandled panel error: %s", type(exc).__name__)
        return jsonify(
            {
                "error": "面板内部异常；指令可能已部分发送，系统已尝试 StopMove。",
                "kind": "internal",
            }
        ), 500

    return app
