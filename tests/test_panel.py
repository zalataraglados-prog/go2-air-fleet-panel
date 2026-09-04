from __future__ import annotations

import asyncio
from typing import Any

import pytest

from go2.config import load_config
from go2.connection import FailureKind, Go2ConnectionError, TransportState
from go2.motion import MotionError
from go2.panel import (
    RobotPanelSession,
    action_response_timeout,
    create_panel_app,
    euler_effect_observed,
    remaining_step_delay,
    validate_choreography_steps,
    validate_robot_ids,
)
from go2.state import LowState, SportModeState, StateSnapshot


class FakePanelSession:
    def __init__(self) -> None:
        self.connected = False
        self.calls: list[str] = []

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "busy": False,
            "operation": None,
            "state": None,
            "motion_mode": {"name": None, "status": "not_checked", "checked_at": None},
            "fleet": {
                "configured_count": 6,
                "ready_to_connect": True,
                "connected": self.connected,
                "connected_count": 6 if self.connected else 0,
                "inspected": self.connected,
                "robots": [
                    {"id": "dog_1", "label": "机器狗 1", "connected": self.connected},
                    {"id": "dog_2", "label": "机器狗 2", "connected": self.connected},
                    {"id": "dog_3", "label": "机器狗 3", "connected": self.connected},
                    {"id": "dog_4", "label": "机器狗 4", "connected": self.connected},
                    {"id": "dog_5", "label": "机器狗 5", "connected": self.connected},
                    {"id": "dog_6", "label": "机器狗 6", "connected": self.connected},
                ],
            },
            "actions": [],
        }

    def connect(self) -> dict[str, Any]:
        self.calls.append("connect")
        self.connected = True
        return self.status()

    def disconnect(self) -> dict[str, Any]:
        self.calls.append("disconnect")
        self.connected = False
        return self.status()

    def refresh_state(self) -> dict[str, Any]:
        self.calls.append("refresh")
        return {"state": {"battery_soc": 80}}

    def refresh_motion_mode(self) -> dict[str, Any]:
        self.calls.append("motion_mode")
        return {
            "motion_mode": {
                "name": "normal",
                "status": "available",
                "checked_at": "12:00:00",
            }
        }

    def connect_fleet(self) -> dict[str, Any]:
        self.calls.append("connect_fleet")
        self.connected = True
        return self.status()

    def refresh_fleet_state(self) -> dict[str, Any]:
        self.calls.append("refresh_fleet")
        return {"fleet": self.status()["fleet"]}

    def run_posture(self, action_id: str, robot_ids: Any) -> dict[str, Any]:
        self.calls.append(f"{action_id}:{','.join(robot_ids)}")
        if action_id not in {"stand_up", "stand_down"}:
            raise ValueError("该动作未进入控制面板白名单。")
        return {"action": action_id, "robot_ids": robot_ids}

    def run_library_action(self, action_id: str, robot_ids: Any) -> dict[str, Any]:
        self.calls.append(f"library:{action_id}:{','.join(robot_ids)}")
        return {"action": action_id, "robot_ids": robot_ids}

    def run_choreography(self, steps: Any, robot_ids: Any) -> dict[str, Any]:
        self.calls.append(f"choreography:{len(steps)}:{','.join(robot_ids)}")
        return {"action": "choreography", "completed": steps, "robot_ids": robot_ids}

    def stop_move(self, robot_ids: Any) -> dict[str, Any]:
        self.calls.append(f"stop_move:{','.join(robot_ids)}")
        return {"action": "stop_move", "robot_ids": robot_ids}

    def close(self) -> None:
        self.calls.append("close")


@pytest.fixture
def panel() -> tuple[Any, FakePanelSession, str]:
    config = load_config(
        environ={
            "UNITREE_ROBOT_IP": "192.168.1.2",
            "UNITREE_SERIAL_NUMBER": "DOG1",
            "UNITREE_AES_128_KEY": "a" * 32,
            "UNITREE_ROBOT_2_IP": "192.168.1.3",
            "UNITREE_ROBOT_2_SERIAL_NUMBER": "DOG2",
            "UNITREE_ROBOT_2_AES_128_KEY": "b" * 32,
            "UNITREE_ROBOT_3_SERIAL_NUMBER": "DOG3",
            "UNITREE_ROBOT_3_AES_128_KEY": "c" * 32,
            "UNITREE_ROBOT_4_SERIAL_NUMBER": "DOG4",
            "UNITREE_ROBOT_4_AES_128_KEY": "d" * 32,
            "UNITREE_ROBOT_5_IP": "192.168.1.6",
            "UNITREE_ROBOT_5_SERIAL_NUMBER": "DOG5",
            "UNITREE_ROBOT_5_AES_128_KEY": "e" * 32,
            "UNITREE_ROBOT_6_IP": "192.168.1.7",
            "UNITREE_ROBOT_6_SERIAL_NUMBER": "DOG6",
            "UNITREE_ROBOT_6_AES_128_KEY": "f" * 32,
        },
        load_env_file=False,
    )
    session = FakePanelSession()
    app = create_panel_app(config, session=session)
    app.testing = True
    return app.test_client(), session, app.extensions["go2_panel_csrf"]


def headers(token: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Go2-Panel-Token": token,
    }


def test_index_contains_no_secret_and_has_security_headers(panel: Any) -> None:
    client, _, _ = panel
    response = client.get("/")
    assert response.status_code == 200
    assert b"a" * 32 not in response.data
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "框选执行对象" in response.get_data(as_text=True)
    assert 'href="/settings"' in response.get_data(as_text=True)
    assert "连接 / 重试可用机器狗" not in response.get_data(as_text=True)
    assert "转圈" not in response.get_data(as_text=True)


def test_settings_page_separates_connection_controls_and_hides_secrets(
    panel: Any,
) -> None:
    client, _, _ = panel
    response = client.get("/settings")
    text = response.get_data(as_text=True)
    assert response.status_code == 200
    assert b"a" * 32 not in response.data
    assert "连接 / 重试离线设备" in text
    assert "动作台偏好" in text
    assert "自动发现并连接" in text
    assert 'href="/"' in text
    assert response.headers["Cache-Control"] == "no-store"


def test_mutating_request_requires_panel_token(panel: Any) -> None:
    client, session, _ = panel
    response = client.post("/api/connect", json={})
    assert response.status_code == 403
    assert session.calls == []


def test_unexpected_error_does_not_falsely_claim_nothing_was_sent(panel: Any) -> None:
    client, session, token = panel

    def explode() -> dict[str, Any]:
        raise RuntimeError("private detail")

    session.connect = explode  # type: ignore[method-assign]
    response = client.post("/api/connect", headers=headers(token), json={})
    assert response.status_code == 500
    payload = response.get_json()
    assert payload["kind"] == "internal"
    assert "可能已部分发送" in payload["error"]
    assert "private detail" not in payload["error"]


@pytest.mark.asyncio
async def test_late_response_is_not_cancelled_and_updates_result() -> None:
    session = RobotPanelSession.__new__(RobotPanelSession)
    session._action_results = {}
    session._late_response_tasks = set()

    async def delayed_response() -> dict[str, Any]:
        await asyncio.sleep(0.02)
        return {"data": {"header": {"status": {"code": 0}}}}

    with pytest.raises(MotionError, match="指令已经发出"):
        await session._request_response(
            delayed_response(),
            "比心",
            "heart",
            timeout=0.001,
        )
    assert session._action_results["heart"]["status"] == "response_timeout"
    await asyncio.sleep(0.1)
    assert session._action_results["heart"]["status"] == "accepted"
    assert session._action_results["heart"]["response_ms"] >= 10
    assert session._late_response_tasks == set()


def test_action_timeout_has_completion_margin_and_step_wait_is_not_doubled() -> None:
    assert action_response_timeout(5.0) == 15.0
    assert action_response_timeout(15.0) == 25.0
    assert remaining_step_delay(4.0, 6.01) == 0.0
    assert remaining_step_delay(1.5, 0.2) == pytest.approx(1.3)


def test_euler_effect_requires_telemetry_change_or_target_match() -> None:
    baseline = (0.0, 0.0, 3.13)
    assert euler_effect_observed(baseline, (0.09, 0.0, 3.13), (0.12, 0.0, 0.0))
    assert not euler_effect_observed(baseline, baseline, (0.12, 0.0, 0.0))
    assert euler_effect_observed(baseline, (0.0, 0.0, -3.10), (0.0, 0.0, 0.2))


def test_3203_is_reported_but_does_not_permanently_disable_action() -> None:
    session = RobotPanelSession.__new__(RobotPanelSession)
    session._motion_mode = "mcf"
    session._action_results = {}
    response = {"data": {"header": {"status": {"code": 3203}}}}
    with pytest.raises(MotionError, match="3203"):
        session._command_outcome(response, "后空翻", "back_flip")
    assert session._action_results["back_flip"]["status"] == "unsupported"
    session._validate_action_availability("back_flip")


def test_connect_and_stop_are_single_panel_actions(panel: Any) -> None:
    client, session, token = panel
    assert client.post("/api/connect", headers=headers(token), json={}).status_code == 200
    assert client.post(
        "/api/stop-move",
        headers=headers(token),
        json={"robot_ids": ["dog_2"]},
    ).status_code == 200
    assert session.calls == ["connect", "stop_move:dog_2"]


def test_motion_mode_diagnostic_is_read_only_panel_action(panel: Any) -> None:
    client, session, token = panel
    response = client.post(
        "/api/diagnostics/motion-mode",
        headers=headers(token),
        json={},
    )
    assert response.status_code == 200
    assert response.get_json()["motion_mode"]["name"] == "normal"
    assert session.calls == ["motion_mode"]


def test_fleet_connect_and_refresh_have_dedicated_routes(panel: Any) -> None:
    client, session, token = panel
    assert client.post(
        "/api/fleet/connect", headers=headers(token), json={}
    ).status_code == 200
    assert client.post(
        "/api/fleet/state/refresh", headers=headers(token), json={}
    ).status_code == 200
    assert session.calls == ["connect_fleet", "refresh_fleet"]


def test_fleet_connect_keeps_healthy_sessions_when_one_robot_is_offline() -> None:
    config = load_config(
        environ={
            "UNITREE_ROBOT_IP": "192.168.1.2",
            "UNITREE_SERIAL_NUMBER": "DOG1",
            "UNITREE_AES_128_KEY": "a" * 32,
            "UNITREE_ROBOT_2_IP": "192.168.1.3",
            "UNITREE_ROBOT_2_SERIAL_NUMBER": "DOG2",
            "UNITREE_ROBOT_2_AES_128_KEY": "b" * 32,
        },
        load_env_file=False,
    )
    connections: dict[str, Any] = {}

    class FleetConnection:
        def __init__(self, serial: str) -> None:
            self.serial = serial
            self.is_connected = False
            self.data_channel_ready = False
            self.disconnect_calls = 0

        @property
        def transport_state(self) -> TransportState:
            return TransportState(
                peer="connected" if self.is_connected else "closed",
                ice="completed" if self.is_connected else "closed",
                signaling="stable" if self.is_connected else "closed",
                data_channel="open" if self.data_channel_ready else "closed",
                data_channel_validated=self.data_channel_ready,
            )

        async def connect(self) -> None:
            if self.serial == "DOG2":
                raise Go2ConnectionError(FailureKind.ROBOT_NOT_FOUND)
            self.is_connected = True
            self.data_channel_ready = True

        async def disconnect(self) -> None:
            self.disconnect_calls += 1
            self.is_connected = False
            self.data_channel_ready = False

        async def request_motion_mode(self) -> dict[str, Any]:
            return {
                "data": {
                    "header": {"status": {"code": 0}},
                    "data": {"name": "mcf"},
                }
            }

    class FleetStateReader:
        def __init__(self, connection: Any) -> None:
            self.connection = connection

        async def collect(self, *, samples: int, timeout: float) -> StateSnapshot:
            return StateSnapshot(
                low_state=LowState(
                    rpy=(0.0, 0.0, 0.0),
                    battery_soc=80.0,
                    power_voltage=28.0,
                    motor_count=12,
                    foot_force=(1.0, 1.0, 1.0, 1.0),
                ),
                sport_mode_state=SportModeState(
                    error_code=0,
                    mode=1,
                    gait_type=0,
                    position=(0.0, 0.0, 0.0),
                    velocity=(0.0, 0.0, 0.0),
                    yaw_speed=0.0,
                ),
                low_state_samples=1,
                sport_mode_samples=1,
            )

    def connection_factory(settings: Any) -> FleetConnection:
        connection = FleetConnection(settings.serial_number)
        connections[settings.serial_number] = connection
        return connection

    session = RobotPanelSession(
        config,
        connection_factory=connection_factory,
        state_reader_factory=FleetStateReader,
    )
    try:
        result = session.connect_fleet()
        robots = {item["id"]: item for item in result["fleet"]["robots"]}
        assert result["fleet"]["connected_count"] == 1
        assert robots["dog_1"]["connected"] is True
        assert robots["dog_2"]["connected"] is False
        assert robots["dog_2"]["connect_error"]["kind"] == "robot_not_found"
        assert connections["DOG1"].disconnect_calls == 0
    finally:
        session.close()


def test_turn_and_march_test_routes_are_removed(panel: Any) -> None:
    client, session, token = panel
    for path in (
        "/api/fleet/march/prepare",
        "/api/fleet/march/run",
        "/api/fleet/turn/prepare",
        "/api/fleet/turn/run",
    ):
        assert client.post(path, headers=headers(token), json={}).status_code == 404
    assert session.calls == []


def test_robot_selection_requires_known_unique_targets() -> None:
    configured = ("dog_1", "dog_2")
    assert validate_robot_ids(["dog_2", "dog_1"], configured) == [
        "dog_1",
        "dog_2",
    ]
    with pytest.raises(ValueError, match="至少选择"):
        validate_robot_ids([], configured)
    with pytest.raises(ValueError, match="重复"):
        validate_robot_ids(["dog_1", "dog_1"], configured)
    with pytest.raises(ValueError, match="不存在"):
        validate_robot_ids(["dog_3"], configured)


def test_posture_requires_fresh_clearance_confirmation(panel: Any) -> None:
    client, session, token = panel
    rejected = client.post(
        "/api/actions/stand_up",
        headers=headers(token),
        json={"confirm_clearance": False, "robot_ids": ["dog_1"]},
    )
    assert rejected.status_code == 403
    assert session.calls == []

    accepted = client.post(
        "/api/actions/stand_up",
        headers=headers(token),
        json={"confirm_clearance": True, "robot_ids": ["dog_2", "dog_1"]},
    )
    assert accepted.status_code == 200
    assert session.calls == ["stand_up:dog_1,dog_2"]


@pytest.mark.parametrize(
    "robot_ids",
    [None, [], ["dog_9"], ["dog_1", "dog_1"]],
)
def test_motion_routes_reject_invalid_rts_selection(
    panel: Any,
    robot_ids: Any,
) -> None:
    client, session, token = panel
    response = client.post(
        "/api/actions/stand_up",
        headers=headers(token),
        json={"confirm_clearance": True, "robot_ids": robot_ids},
    )
    assert response.status_code == 400
    assert session.calls == []


def test_unknown_action_is_rejected_by_server_allowlist(panel: Any) -> None:
    client, session, token = panel
    response = client.post(
        "/api/actions/front_flip",
        headers=headers(token),
        json={"confirm_clearance": True, "robot_ids": ["dog_1"]},
    )
    assert response.status_code == 400
    assert session.calls == ["front_flip:dog_1"]


def test_low_risk_library_action_requires_clearance(panel: Any) -> None:
    client, session, token = panel
    rejected = client.post(
        "/api/library/hello",
        headers=headers(token),
        json={"confirm_clearance": False, "robot_ids": ["dog_1"]},
    )
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/library/hello",
        headers=headers(token),
        json={"confirm_clearance": True, "robot_ids": ["dog_2"]},
    )
    assert accepted.status_code == 200
    assert session.calls == ["library:hello:dog_2"]


def test_high_risk_library_action_requires_exact_phrase(panel: Any) -> None:
    client, session, token = panel
    rejected = client.post(
        "/api/library/front_flip",
        headers=headers(token),
        json={"confirm_clearance": True, "risk_ack": "yes", "robot_ids": ["dog_1"]},
    )
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/library/front_flip",
        headers=headers(token),
        json={"confirm_clearance": True, "risk_ack": "GO2 HIGH RISK", "robot_ids": ["dog_1", "dog_2"]},
    )
    assert accepted.status_code == 200
    assert session.calls == ["library:front_flip:dog_1,dog_2"]


def test_choreography_rejects_raw_api_or_high_risk_action(panel: Any) -> None:
    client, session, token = panel
    raw_api = client.post(
        "/api/choreographies/validate",
        headers=headers(token),
        json={"steps": [{"action": "hello", "duration": 1, "api_id": 9999}]},
    )
    assert raw_api.status_code == 400
    high_risk = client.post(
        "/api/choreographies/validate",
        headers=headers(token),
        json={"steps": [{"action": "front_flip", "duration": 2}]},
    )
    assert high_risk.status_code == 400
    assert session.calls == []


def test_choreography_run_requires_clearance_and_accepts_safe_steps(panel: Any) -> None:
    client, session, token = panel
    steps = [{"action": "hello", "duration": 1.5}]
    rejected = client.post(
        "/api/choreographies/run",
        headers=headers(token),
        json={"confirm_clearance": False, "steps": steps, "robot_ids": ["dog_1"]},
    )
    assert rejected.status_code == 403
    accepted = client.post(
        "/api/choreographies/run",
        headers=headers(token),
        json={"confirm_clearance": True, "steps": steps, "robot_ids": ["dog_1", "dog_2"]},
    )
    assert accepted.status_code == 200
    assert session.calls == ["choreography:1:dog_1,dog_2"]


def test_choreography_accepts_bounded_custom_primitives(panel: Any) -> None:
    client, session, token = panel
    steps = [
        {"kind": "euler", "roll": 0.1, "pitch": 0, "yaw": -0.08, "duration": 1.5},
        {"kind": "velocity", "direction": "clockwise", "speed": 0.35, "duration": 2},
        {"kind": "wait", "duration": 1},
    ]
    response = client.post(
        "/api/choreographies/run",
        headers=headers(token),
        json={"confirm_clearance": True, "steps": steps, "robot_ids": ["dog_2"]},
    )
    assert response.status_code == 200
    assert session.calls == ["choreography:3:dog_2"]


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "euler", "roll": 0.13, "pitch": 0, "yaw": 0, "duration": 1},
        {"kind": "body_height", "height": -0.04, "duration": 1},
        {"kind": "euler", "roll": 0, "pitch": 0, "yaw": 0, "api_id": 1007, "duration": 1},
        {"kind": "wait", "api_id": 1003, "duration": 1},
        {"kind": "velocity", "direction": "forward", "speed": 0.3, "duration": 1},
        {"kind": "velocity", "direction": "clockwise", "speed": 0.3, "duration": 3.5},
    ],
)
def test_choreography_rejects_unsafe_custom_parameters_or_api_injection(
    panel: Any, step: dict[str, Any]
) -> None:
    client, session, token = panel
    response = client.post(
        "/api/choreographies/validate",
        headers=headers(token),
        json={"steps": [step]},
    )
    assert response.status_code == 400
    assert session.calls == []


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [{"action": "hello", "duration": 0.1}],
        [{"action": "hello", "duration": 9}],
        [{"action": "unknown", "duration": 1}],
        [{"action": "hello", "duration": 4}] * 11,
    ],
)
def test_choreography_bounds_are_enforced(steps: Any) -> None:
    with pytest.raises(ValueError):
        validate_choreography_steps(steps)
