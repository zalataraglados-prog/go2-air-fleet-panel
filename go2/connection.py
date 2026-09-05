"""Safe WebRTC lifecycle with an explicit state-and-posture allowlist."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import Enum
import ipaddress
import json
import logging
import time
from typing import Any, Callable, Mapping

from .actions import ACTION_LIBRARY
from .custom_motion import build_custom_motion_request
from .config import ConnectionConfig
from .discovery import discover_robot_ip, local_ipv4_candidates


LOGGER = logging.getLogger(__name__)

_READ_ONLY_STATE_TOPICS = frozenset(
    {
        "rt/lf/lowstate",
        "rt/lf/sportmodestate",
    }
)


def cached_ip_matches_lan(cached_ip: str | None, local_interfaces: str) -> bool:
    """Accept cached robot addresses only on a discovered physical /24 LAN."""

    if not cached_ip:
        return False
    try:
        cached = ipaddress.ip_address(cached_ip)
    except ValueError:
        return False
    if (
        cached.version != 4
        or not cached.is_private
        or cached.is_link_local
        or cached.is_loopback
    ):
        return False
    for value in local_interfaces.split(","):
        value = value.strip()
        try:
            local = ipaddress.ip_address(value)
        except ValueError:
            continue
        if local.version != 4 or local.is_link_local or local.is_loopback:
            continue
        if cached in ipaddress.ip_network(f"{local}/24", strict=False):
            return True
    return False


def constrain_ice_host_candidates(addresses: tuple[str, ...]) -> tuple[str, ...]:
    """Limit aioice host candidates to the active private IPv4 LAN."""

    validated: list[str] = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if (
            address.version == 4
            and address.is_private
            and not address.is_link_local
            and not address.is_loopback
            and value not in validated
        ):
            validated.append(value)
    if not validated:
        raise Go2ConnectionError(
            FailureKind.ROBOT_NOT_FOUND,
            detail="No usable physical private-LAN address is available for ICE.",
        )

    try:
        import aioice.ice as aioice_ice
    except ImportError as exc:
        raise Go2ConnectionError(FailureKind.DEPENDENCY_MISSING) from exc

    selected = tuple(validated)

    def selected_host_addresses(use_ipv4: bool, use_ipv6: bool) -> list[str]:
        del use_ipv6
        return list(selected) if use_ipv4 else []

    aioice_ice.get_host_addresses = selected_host_addresses
    LOGGER.info("ICE host candidates constrained: addresses=%s", ",".join(selected))
    return selected


class FailureKind(str, Enum):
    CONFIGURATION = "configuration"
    DEPENDENCY_MISSING = "dependency_missing"
    ROBOT_NOT_FOUND = "robot_not_found"
    AES_KEY_MISSING = "aes_key_missing"
    AES_KEY_REJECTED = "aes_key_rejected"
    SIGNALING_FAILED = "signaling_failed"
    ICE_FAILED = "ice_failed"
    DATA_CHANNEL_FAILED = "data_channel_failed"
    ROBOT_BUSY = "robot_busy"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


_SAFE_MESSAGES = {
    FailureKind.CONFIGURATION: "Connection configuration is incomplete or invalid.",
    FailureKind.DEPENDENCY_MISSING: "unitree_webrtc_connect is not installed.",
    FailureKind.ROBOT_NOT_FOUND: "Robot or local signaling ports are unreachable.",
    FailureKind.AES_KEY_MISSING: "This robot requires a per-device AES-128 key.",
    FailureKind.AES_KEY_REJECTED: "The robot rejected the AES-128 key; verify its serial number.",
    FailureKind.SIGNALING_FAILED: "WebRTC signaling returned no valid SDP answer.",
    FailureKind.ICE_FAILED: "WebRTC ICE/DTLS negotiation failed.",
    FailureKind.DATA_CHANNEL_FAILED: "The DataChannel did not open and validate.",
    FailureKind.ROBOT_BUSY: "Another WebRTC client is using the robot.",
    FailureKind.TIMEOUT: "Connection did not finish before the configured timeout.",
    FailureKind.UNKNOWN: "An unclassified connection error occurred.",
}


class Go2ConnectionError(RuntimeError):
    """A sanitized, actionable connection failure."""

    def __init__(
        self,
        kind: FailureKind,
        *,
        detail: str | None = None,
        state: "TransportState | None" = None,
    ) -> None:
        self.kind = kind
        self.detail = detail
        self.state = state
        message = _SAFE_MESSAGES[kind]
        if detail:
            message = f"{message} {detail}"
        super().__init__(message)


@dataclass(frozen=True)
class TransportState:
    peer: str = "unavailable"
    ice: str = "unavailable"
    signaling: str = "unavailable"
    data_channel: str = "unavailable"
    data_channel_validated: bool = False


@dataclass(frozen=True)
class _Sdk:
    connection_class: type
    method_enum: type
    aes_required_error: type[BaseException]
    aes_rejected_error: type[BaseException]
    signaling_port_error: type[BaseException]
    no_sdp_error: type[BaseException]
    robot_busy_error: type[BaseException]
    data_channel_timeout_error: type[BaseException]


class _DisabledMediaChannel:
    """Placeholder that prevents unused audio/video RTP transceivers."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        del args, kwargs


def _configure_datachannel_only_driver(driver_module: Any) -> None:
    """Keep the control panel on one SCTP/ICE transport per robot."""

    driver_module.WebRTCAudioChannel = _DisabledMediaChannel
    driver_module.WebRTCVideoChannel = _DisabledMediaChannel


def _load_sdk() -> _Sdk:
    try:
        from unitree_webrtc_connect import (
            AesKeyRejectedError,
            AesKeyRequiredError,
            DataChannelTimeoutError,
            LocalSignalingPortError,
            NoSdpAnswerError,
            RobotBusyError,
            UnitreeWebRTCConnection,
            WebRTCConnectionMethod,
        )
        from unitree_webrtc_connect import webrtc_driver
    except ImportError as exc:
        raise Go2ConnectionError(
            FailureKind.DEPENDENCY_MISSING,
            detail="请在项目虚拟环境中执行 pip install -r requirements.txt。",
        ) from exc
    _configure_datachannel_only_driver(webrtc_driver)
    return _Sdk(
        connection_class=UnitreeWebRTCConnection,
        method_enum=WebRTCConnectionMethod,
        aes_required_error=AesKeyRequiredError,
        aes_rejected_error=AesKeyRejectedError,
        signaling_port_error=LocalSignalingPortError,
        no_sdp_error=NoSdpAnswerError,
        robot_busy_error=RobotBusyError,
        data_channel_timeout_error=DataChannelTimeoutError,
    )


class Go2Connection:
    """Own one SDK connection and guarantee bounded connect/disconnect calls.

    This class intentionally exposes no generic publish/RPC surface. It allows
    only two read-only telemetry topics and the three explicitly reviewed
    posture-guard commands (StandUp, StandDown, StopMove). Camera, LiDAR,
    locomotion, and arbitrary action IDs remain unavailable.

    ``unitree_webrtc_connect.connect()`` returns only after its data channel has
    opened and completed the validation handshake.
    """

    def __init__(
        self,
        settings: ConnectionConfig,
        *,
        connection_factory: Callable[..., Any] | None = None,
        discovery_function: Callable[..., tuple[str | None, str]] | None = None,
    ) -> None:
        self.settings = settings
        self._connection_factory = connection_factory
        self._discovery_function = discovery_function or discover_robot_ip
        self._connection: Any | None = None
        self._resolved_ip: str | None = None
        self._request_id = int(time.time() * 1000) % 2_147_483_648
        self._lock = asyncio.Lock()

    @property
    def is_connected(self) -> bool:
        return bool(
            self._connection is not None
            and getattr(self._connection, "isConnected", False)
        )

    @property
    def data_channel_ready(self) -> bool:
        if self._connection is None:
            return False
        datachannel = getattr(self._connection, "datachannel", None)
        return bool(
            datachannel is not None
            and getattr(datachannel, "data_channel_opened", False)
        )

    @property
    def effective_target_ip(self) -> str | None:
        """Return the live discovery result before any optional cached address."""

        return self._resolved_ip or self.settings.target_ip

    @property
    def transport_state(self) -> TransportState:
        if self._connection is None:
            return TransportState()
        peer = getattr(self._connection, "pc", None)
        datachannel = getattr(self._connection, "datachannel", None)
        channel = getattr(datachannel, "channel", None)
        return TransportState(
            peer=str(getattr(peer, "connectionState", "unavailable")),
            ice=str(getattr(peer, "iceConnectionState", "unavailable")),
            signaling=str(getattr(peer, "signalingState", "unavailable")),
            data_channel=str(getattr(channel, "readyState", "unavailable")),
            data_channel_validated=bool(
                getattr(datachannel, "data_channel_opened", False)
            ),
        )

    def _preflight(self) -> None:
        errors = self.settings.preflight_errors()
        if not errors:
            return
        if not self.settings.aes_128_key and self.settings.require_aes_key:
            kind = FailureKind.AES_KEY_MISSING
        else:
            kind = FailureKind.CONFIGURATION
        raise Go2ConnectionError(kind, detail="; ".join(errors))

    def _validated_pub_sub(self) -> Any:
        if not self.is_connected or not self.data_channel_ready:
            raise Go2ConnectionError(
                FailureKind.DATA_CHANNEL_FAILED,
                detail="A validated DataChannel is required for state subscription.",
                state=self.transport_state,
            )
        datachannel = getattr(self._connection, "datachannel", None)
        pub_sub = getattr(datachannel, "pub_sub", None)
        if pub_sub is None:
            raise Go2ConnectionError(
                FailureKind.DATA_CHANNEL_FAILED,
                detail="The SDK state subscription interface is unavailable.",
                state=self.transport_state,
            )
        return pub_sub

    def subscribe_read_only_state(
        self, topic: str, callback: Callable[[Any], None]
    ) -> None:
        """Subscribe to one explicitly allowlisted, read-only telemetry topic."""

        if topic not in _READ_ONLY_STATE_TOPICS:
            raise ValueError(f"Topic is not allowlisted for read-only state: {topic}")
        self._validated_pub_sub().subscribe(topic, callback)

    def unsubscribe_read_only_state(self, topic: str) -> None:
        """Unsubscribe from an explicitly allowlisted telemetry topic."""

        if topic not in _READ_ONLY_STATE_TOPICS:
            raise ValueError(f"Topic is not allowlisted for read-only state: {topic}")
        self._validated_pub_sub().unsubscribe(topic)

    async def request_stand_up(self) -> Any:
        """Send the single allowlisted posture command: StandUp (API 1004)."""

        return await self._validated_pub_sub().publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1004},
        )

    async def request_stand_down(self) -> Any:
        """Send the single allowlisted lowering command: StandDown (API 1005)."""

        return await self._validated_pub_sub().publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1005},
        )

    async def request_stop_move(self) -> Any:
        """Send the mandatory movement guard command: StopMove (API 1003)."""

        return await self._validated_pub_sub().publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1003, "priority": 1},
        )

    async def request_motion_mode(self) -> Any:
        """Read the active motion-switcher profile without changing robot state."""

        return await self._validated_pub_sub().publish_request_new(
            "rt/api/motion_switcher/request",
            {"api_id": 1001},
        )

    async def request_static_walk(self, enabled: bool) -> Any:
        """Enter or leave MCF StaticWalk using a fixed, non-arbitrary API."""

        if not isinstance(enabled, bool):
            raise ValueError("StaticWalk enabled must be a boolean.")
        return await self._validated_pub_sub().publish_request_new(
            "rt/api/sport/request",
            {"api_id": 1061, "parameter": {"data": enabled}},
        )

    async def request_library_action(self, action_id: str) -> Any:
        """Send one reviewed action by stable ID; arbitrary API IDs are impossible."""

        spec = ACTION_LIBRARY.get(action_id)
        if spec is None:
            raise ValueError("Action is not in the reviewed GO2 allowlist.")
        options: dict[str, Any] = {"api_id": spec.api_id}
        if spec.parameter is not None:
            options["parameter"] = dict(spec.parameter)
        if spec.priority:
            options["priority"] = 1
        return await self._validated_pub_sub().publish_request_new(
            "rt/api/sport/request",
            options,
        )

    async def request_custom_motion(
        self,
        kind: str,
        parameters: Mapping[str, Any],
    ) -> Any:
        """Send one bounded custom primitive through a fixed official API."""

        api_id, parameter = build_custom_motion_request(kind, parameters)
        return await self._validated_pub_sub().publish_request_new(
            "rt/api/sport/request",
            {"api_id": api_id, "parameter": parameter},
        )

    def publish_velocity_keepalive(self, direction: str, speed: float) -> None:
        """Refresh a validated Move command without creating an ack waiter."""

        api_id, parameter = build_custom_motion_request(
            "velocity",
            {"direction": direction, "speed": speed},
        )
        self._request_id = (self._request_id + 1) % 2_147_483_648
        self._validated_pub_sub().publish_without_callback(
            "rt/api/sport/request",
            {
                "header": {
                    "identity": {
                        "id": self._request_id,
                        "api_id": api_id,
                    }
                },
                "parameter": json.dumps(parameter),
            },
            "req",
        )

    def _build_connection(self, sdk: _Sdk) -> Any:
        method = (
            sdk.method_enum.LocalAP
            if self.settings.mode == "local_ap"
            else sdk.method_enum.LocalSTA
        )
        kwargs: dict[str, Any] = {
            "region": self.settings.region,
            "device_type": "Go2",
        }
        if self.settings.aes_128_key:
            kwargs["aes_128_key"] = self.settings.aes_128_key
        if self.settings.mode == "local_sta":
            kwargs["ip"] = self.effective_target_ip
        factory = self._connection_factory or sdk.connection_class
        return factory(method, **kwargs)

    async def connect(self) -> None:
        async with self._lock:
            if self.is_connected and self.data_channel_ready:
                return
            self._preflight()
            ice_interfaces: tuple[str, ...] = ()
            if (
                self.settings.mode == "local_sta"
                and self.settings.serial_number
            ):
                LOGGER.info("Discovering GO2 Air on the primary LAN interface")
                resolved_ip, local_ip = await asyncio.to_thread(
                    self._discovery_function, self.settings.serial_number
                )
                ice_interfaces = tuple(
                    value.strip()
                    for value in local_ip.split(",")
                    if value.strip()
                )
                cached_ip_usable = cached_ip_matches_lan(
                    self.settings.ip,
                    local_ip,
                )
                if not resolved_ip and not cached_ip_usable:
                    cached_detail = (
                        " Cached address rejected because it is not on the "
                        "current physical LAN."
                        if self.settings.ip
                        else ""
                    )
                    raise Go2ConnectionError(
                        FailureKind.ROBOT_NOT_FOUND,
                        detail=(
                            f"No targeted multicast reply on local interface {local_ip}."
                            f"{cached_detail}"
                        ),
                    )
                if resolved_ip:
                    self._resolved_ip = resolved_ip
                    LOGGER.info(
                        "GO2 Air discovered: ip=%s local_interface=%s",
                        resolved_ip,
                        local_ip,
                    )
                else:
                    LOGGER.warning(
                        "Targeted discovery did not reply; using same-LAN cached ip=%s",
                        self.settings.ip,
                    )
            if self.settings.mode == "local_sta":
                constrain_ice_host_candidates(
                    ice_interfaces or local_ipv4_candidates()
                )
            sdk = _load_sdk()
            self._connection = self._build_connection(sdk)
            LOGGER.info(
                "Connecting to GO2 Air: mode=%s ip=%s",
                self.settings.mode,
                self.effective_target_ip or "discovery",
            )
            try:
                await asyncio.wait_for(
                    self._connection.connect(),
                    timeout=self.settings.connect_timeout,
                )
                if not self.is_connected:
                    raise Go2ConnectionError(
                        FailureKind.ICE_FAILED,
                        detail="PeerConnection did not reach the connected state.",
                        state=self.transport_state,
                    )
                if not self.data_channel_ready:
                    raise Go2ConnectionError(
                        FailureKind.DATA_CHANNEL_FAILED,
                        detail="The application-level validation did not complete.",
                        state=self.transport_state,
                    )
                state = self.transport_state
                LOGGER.info(
                    "GO2 Air WebRTC and DataChannel are ready: ip=%s "
                    "peer=%s ice=%s data_channel=%s validated=%s",
                    self.effective_target_ip or "discovery",
                    state.peer,
                    state.ice,
                    state.data_channel,
                    state.data_channel_validated,
                )
            except asyncio.CancelledError:
                await self._disconnect_unlocked()
                raise
            except asyncio.TimeoutError as exc:
                state = self.transport_state
                kind = (
                    FailureKind.ICE_FAILED
                    if state.ice == "failed"
                    else FailureKind.TIMEOUT
                )
                await self._disconnect_unlocked()
                LOGGER.warning(
                    "GO2 Air connection timeout: ip=%s kind=%s peer=%s "
                    "ice=%s data_channel=%s validated=%s",
                    self.effective_target_ip or "discovery",
                    kind.value,
                    state.peer,
                    state.ice,
                    state.data_channel,
                    state.data_channel_validated,
                )
                raise Go2ConnectionError(
                    kind,
                    detail=f"timeout={self.settings.connect_timeout:.1f}s",
                    state=state,
                ) from exc
            except Go2ConnectionError as exc:
                state = exc.state or self.transport_state
                await self._disconnect_unlocked()
                LOGGER.warning(
                    "GO2 Air connection failed: ip=%s kind=%s peer=%s "
                    "ice=%s data_channel=%s validated=%s",
                    self.effective_target_ip or "discovery",
                    exc.kind.value,
                    state.peer,
                    state.ice,
                    state.data_channel,
                    state.data_channel_validated,
                )
                raise
            except Exception as exc:
                state = self.transport_state
                kind = self._classify_exception(exc, sdk, state)
                await self._disconnect_unlocked()
                LOGGER.warning(
                    "GO2 Air connection failed: ip=%s kind=%s peer=%s "
                    "ice=%s data_channel=%s validated=%s",
                    self.effective_target_ip or "discovery",
                    kind.value,
                    state.peer,
                    state.ice,
                    state.data_channel,
                    state.data_channel_validated,
                )
                sanitized = Go2ConnectionError(kind, state=state)
                if kind is FailureKind.AES_KEY_REJECTED:
                    # The upstream exception includes a key prefix. Suppress its
                    # traceback context so callers cannot accidentally log it.
                    raise sanitized from None
                raise sanitized from exc

    def _classify_exception(
        self, exc: BaseException, sdk: _Sdk, state: TransportState
    ) -> FailureKind:
        if isinstance(exc, sdk.aes_required_error):
            return FailureKind.AES_KEY_MISSING
        if isinstance(exc, sdk.aes_rejected_error):
            return FailureKind.AES_KEY_REJECTED
        if isinstance(exc, sdk.signaling_port_error):
            return FailureKind.ROBOT_NOT_FOUND
        if isinstance(exc, sdk.no_sdp_error):
            return FailureKind.SIGNALING_FAILED
        if isinstance(exc, sdk.robot_busy_error):
            return FailureKind.ROBOT_BUSY
        if isinstance(exc, sdk.data_channel_timeout_error):
            if getattr(exc, "ice_state", None) == "failed" or state.ice == "failed":
                return FailureKind.ICE_FAILED
            return FailureKind.DATA_CHANNEL_FAILED
        if (
            type(exc) is ValueError
            and self.settings.mode == "local_sta"
            and not self.settings.ip
            and self.settings.serial_number
        ):
            return FailureKind.ROBOT_NOT_FOUND
        if state.ice == "failed":
            return FailureKind.ICE_FAILED
        return FailureKind.UNKNOWN

    async def _disconnect_unlocked(self) -> None:
        connection = self._connection
        if connection is None:
            return
        state = self.transport_state
        self._connection = None
        LOGGER.info(
            "Disconnecting GO2 Air WebRTC: ip=%s peer=%s ice=%s data_channel=%s",
            self.effective_target_ip or "discovery",
            state.peer,
            state.ice,
            state.data_channel,
        )
        try:
            await asyncio.wait_for(connection.disconnect(), timeout=5.0)
        except Exception:
            LOGGER.exception("WebRTC disconnect did not finish cleanly")

    async def disconnect(self) -> None:
        async with self._lock:
            await self._disconnect_unlocked()

    async def __aenter__(self) -> "Go2Connection":
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.disconnect()
