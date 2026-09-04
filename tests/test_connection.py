from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import go2.connection as connection_module
from go2.config import ConnectionConfig
from go2.connection import FailureKind, Go2Connection, Go2ConnectionError


class AesRequiredError(RuntimeError):
    pass


class AesRejectedError(RuntimeError):
    pass


class SignalingPortError(RuntimeError):
    pass


class NoSdpError(RuntimeError):
    pass


class RobotBusyError(RuntimeError):
    pass


class DataChannelTimeoutError(RuntimeError):
    def __init__(self, ice_state: str = "checking") -> None:
        self.ice_state = ice_state


class FakeConnection:
    def __init__(
        self,
        method: Any,
        *,
        connect_error: BaseException | None = None,
        delay: float = 0,
        ready: bool = True,
        **kwargs: Any,
    ) -> None:
        self.method = method
        self.kwargs = kwargs
        self.connect_error = connect_error
        self.delay = delay
        self.isConnected = False
        self.disconnect_count = 0
        self.pc = SimpleNamespace(
            connectionState="new",
            iceConnectionState="new",
            signalingState="stable",
        )
        self.pub_sub = SimpleNamespace(
            subscribe=lambda topic, callback: None,
            unsubscribe=lambda topic: None,
            publish_request_new=self._publish_request_new,
            publish_without_callback=self._publish_without_callback,
        )
        self.datachannel = SimpleNamespace(
            data_channel_opened=False,
            channel=SimpleNamespace(readyState="connecting"),
            pub_sub=self.pub_sub,
        )
        self.ready = ready
        self.no_reply_calls: list[tuple[str, Any, str]] = []

    async def _publish_request_new(self, topic: str, options: Any) -> Any:
        return {"topic": topic, "options": options}

    def _publish_without_callback(
        self, topic: str, data: Any, msg_type: str | None = None
    ) -> None:
        self.no_reply_calls.append((topic, data, msg_type))

    async def connect(self) -> None:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.connect_error:
            raise self.connect_error
        self.isConnected = True
        self.pc.connectionState = "connected"
        self.pc.iceConnectionState = "completed"
        if self.ready:
            self.datachannel.data_channel_opened = True
            self.datachannel.channel.readyState = "open"

    async def disconnect(self) -> None:
        self.disconnect_count += 1
        self.isConnected = False
        self.pc.connectionState = "closed"


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> Any:
    sdk = connection_module._Sdk(
        connection_class=FakeConnection,
        method_enum=SimpleNamespace(LocalAP="AP", LocalSTA="STA"),
        aes_required_error=AesRequiredError,
        aes_rejected_error=AesRejectedError,
        signaling_port_error=SignalingPortError,
        no_sdp_error=NoSdpError,
        robot_busy_error=RobotBusyError,
        data_channel_timeout_error=DataChannelTimeoutError,
    )
    monkeypatch.setattr(connection_module, "_load_sdk", lambda: sdk)
    return sdk


def settings(**overrides: Any) -> ConnectionConfig:
    values: dict[str, Any] = {
        "mode": "local_sta",
        "ip": "192.168.8.181",
        "connect_timeout": 1.0,
        "require_aes_key": True,
        "aes_128_key": "a" * 32,
    }
    values.update(overrides)
    return ConnectionConfig(**values)


@pytest.mark.asyncio
async def test_local_sta_connects_and_disconnects(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(settings(), connection_factory=factory)
    await connection.connect()
    assert connection.is_connected
    assert connection.data_channel_ready
    assert created[0].method == "STA"
    assert created[0].kwargs["ip"] == "192.168.8.181"
    assert created[0].kwargs["device_type"] == "Go2"
    await connection.disconnect()
    await connection.disconnect()
    assert created[0].disconnect_count == 1


@pytest.mark.asyncio
async def test_local_ap_does_not_pass_an_ip(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(
        settings(mode="local_ap", ip=None), connection_factory=factory
    )
    async with connection:
        assert created[0].method == "AP"
        assert "ip" not in created[0].kwargs


@pytest.mark.asyncio
async def test_missing_key_fails_before_sdk_connection(fake_sdk: Any) -> None:
    connection = Go2Connection(settings(aes_128_key=None))
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.AES_KEY_MISSING


@pytest.mark.asyncio
async def test_typed_sdk_errors_are_sanitized_and_cleanup_runs(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, connect_error=AesRejectedError("secret"), **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(settings(), connection_factory=factory)
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.AES_KEY_REJECTED
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__ is True
    assert created[0].disconnect_count == 1


@pytest.mark.asyncio
async def test_data_channel_must_be_validated(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, ready=False, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(settings(), connection_factory=factory)
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.DATA_CHANNEL_FAILED
    assert created[0].disconnect_count == 1


@pytest.mark.asyncio
async def test_outer_timeout_is_bounded_and_cleanup_runs(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, delay=0.2, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(
        settings(connect_timeout=0.01), connection_factory=factory
    )
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.TIMEOUT
    assert created[0].disconnect_count == 1


@pytest.mark.asyncio
async def test_data_channel_timeout_with_failed_ice_is_classified(fake_sdk: Any) -> None:
    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(
            method, connect_error=DataChannelTimeoutError("failed"), **kwargs
        )
        instance.pc.iceConnectionState = "failed"
        return instance

    connection = Go2Connection(settings(), connection_factory=factory)
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.ICE_FAILED


@pytest.mark.asyncio
async def test_serial_discovery_failure_is_robot_not_found(fake_sdk: Any) -> None:
    connection = Go2Connection(
        settings(ip=None, serial_number="TEST-SN"),
        discovery_function=lambda serial: (None, "192.168.1.10"),
    )
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.ROBOT_NOT_FOUND


@pytest.mark.asyncio
async def test_serial_discovery_exposes_live_ip_without_cached_ip(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(
        settings(ip=None, serial_number="TEST-SN"),
        connection_factory=factory,
        discovery_function=lambda serial: ("192.168.50.42", "192.168.50.10"),
    )
    await connection.connect()
    assert connection.effective_target_ip == "192.168.50.42"
    assert created[0].kwargs["ip"] == "192.168.50.42"
    await connection.disconnect()


@pytest.mark.asyncio
async def test_serial_discovery_overrides_stale_cached_ip(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(
        settings(ip="192.168.8.181", serial_number="TEST-SN"),
        connection_factory=factory,
        discovery_function=lambda serial: ("192.168.50.42", "192.168.50.10"),
    )
    await connection.connect()
    assert connection.effective_target_ip == "192.168.50.42"
    assert created[0].kwargs["ip"] == "192.168.50.42"
    await connection.disconnect()


@pytest.mark.asyncio
async def test_cached_ip_is_fallback_when_discovery_is_blocked(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(
        settings(ip="192.168.8.181", serial_number="TEST-SN"),
        connection_factory=factory,
        discovery_function=lambda serial: (None, "192.168.8.10"),
    )
    await connection.connect()
    assert connection.effective_target_ip == "192.168.8.181"
    assert created[0].kwargs["ip"] == "192.168.8.181"
    await connection.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (SignalingPortError(), FailureKind.ROBOT_NOT_FOUND),
        (NoSdpError(), FailureKind.SIGNALING_FAILED),
        (RobotBusyError(), FailureKind.ROBOT_BUSY),
    ],
)
async def test_typed_transport_errors_are_classified(
    fake_sdk: Any, error: BaseException, expected: FailureKind
) -> None:
    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        return FakeConnection(method, connect_error=error, **kwargs)

    connection = Go2Connection(settings(), connection_factory=factory)
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is expected


@pytest.mark.asyncio
async def test_connect_cancellation_still_disconnects(fake_sdk: Any) -> None:
    created: list[FakeConnection] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, delay=60, **kwargs)
        created.append(instance)
        return instance

    connection = Go2Connection(settings(), connection_factory=factory)
    task = asyncio.create_task(connection.connect())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert created[0].disconnect_count == 1


@pytest.mark.asyncio
async def test_unicode_value_error_is_not_robot_not_found(fake_sdk: Any) -> None:
    unicode_error = UnicodeEncodeError("gbk", "\U0001f552", 0, 1, "unsupported")

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        return FakeConnection(method, connect_error=unicode_error, **kwargs)

    connection = Go2Connection(
        settings(ip=None, serial_number="TEST-SN"),
        connection_factory=factory,
        discovery_function=lambda serial: ("192.168.1.20", "192.168.1.10"),
    )
    with pytest.raises(Go2ConnectionError) as caught:
        await connection.connect()
    assert caught.value.kind is FailureKind.UNKNOWN


@pytest.mark.asyncio
async def test_read_only_subscription_is_allowlisted(fake_sdk: Any) -> None:
    calls: list[tuple[str, str]] = []

    def factory(method: Any, **kwargs: Any) -> FakeConnection:
        instance = FakeConnection(method, **kwargs)
        instance.pub_sub.subscribe = lambda topic, callback: calls.append(
            ("subscribe", topic)
        )
        instance.pub_sub.unsubscribe = lambda topic: calls.append(("unsubscribe", topic))
        return instance

    connection = Go2Connection(settings(), connection_factory=factory)
    await connection.connect()
    connection.subscribe_read_only_state("rt/lf/lowstate", lambda message: None)
    connection.unsubscribe_read_only_state("rt/lf/lowstate")
    with pytest.raises(ValueError):
        connection.subscribe_read_only_state("rt/api/sport/request", lambda message: None)
    await connection.disconnect()
    assert calls == [
        ("subscribe", "rt/lf/lowstate"),
        ("unsubscribe", "rt/lf/lowstate"),
    ]


@pytest.mark.asyncio
async def test_only_allowlisted_action_requests_are_exposed(fake_sdk: Any) -> None:
    connection = Go2Connection(settings())
    await connection.connect()
    stand = await connection.request_stand_up()
    stand_down = await connection.request_stand_down()
    stop = await connection.request_stop_move()
    motion_mode = await connection.request_motion_mode()
    static_walk_on = await connection.request_static_walk(True)
    static_walk_off = await connection.request_static_walk(False)
    hello = await connection.request_library_action("hello")
    handstand = await connection.request_library_action("handstand")
    euler = await connection.request_custom_motion(
        "euler", {"roll": 0.1, "pitch": 0.0, "yaw": -0.1}
    )
    with pytest.raises(ValueError):
        await connection.request_library_action("arbitrary_9999")
    with pytest.raises(ValueError):
        await connection.request_custom_motion("euler", {"roll": 1, "pitch": 0, "yaw": 0})
    with pytest.raises(ValueError):
        await connection.request_custom_motion("body_height", {"height": -0.04})
    await connection.disconnect()
    assert stand == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1004},
    }
    assert stop == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1003, "priority": 1},
    }
    assert motion_mode == {
        "topic": "rt/api/motion_switcher/request",
        "options": {"api_id": 1001},
    }
    assert static_walk_on == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1061, "parameter": {"data": True}},
    }
    assert static_walk_off == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1061, "parameter": {"data": False}},
    }
    assert stand_down == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1005},
    }
    assert hello == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1016},
    }
    assert handstand == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 2044, "parameter": {"data": True}},
    }
    assert euler == {
        "topic": "rt/api/sport/request",
        "options": {"api_id": 1007, "parameter": {"x": 0.1, "y": 0.0, "z": -0.1}},
    }
