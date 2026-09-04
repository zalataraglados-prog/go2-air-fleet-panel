"""Project configuration with environment-only handling for secrets."""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import logging
import os
from pathlib import Path
import re
from typing import Any, Mapping

from dotenv import load_dotenv
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "config.example.yaml"
LOCAL_AP_IP = "192.168.12.1"


class ConfigurationError(ValueError):
    """Raised when project configuration is invalid."""


@dataclass(frozen=True)
class ConnectionConfig:
    """All parameters needed to create one local GO2 WebRTC connection."""

    mode: str = "local_sta"
    ip: str | None = None
    serial_number: str | None = None
    region: str = "cn"
    connect_timeout: float = 30.0
    require_aes_key: bool = True
    aes_128_key: str | None = field(default=None, repr=False)

    @property
    def target_ip(self) -> str | None:
        return LOCAL_AP_IP if self.mode == "local_ap" else self.ip

    def preflight_errors(self) -> list[str]:
        errors: list[str] = []
        if self.mode == "local_sta" and not (self.ip or self.serial_number):
            errors.append(
                "local_sta requires UNITREE_ROBOT_IP or "
                "UNITREE_SERIAL_NUMBER"
            )
        if self.require_aes_key and not self.aes_128_key:
            errors.append(
                "UNITREE_AES_128_KEY is required for GO2 firmware >= 1.1.15"
            )
        return errors


@dataclass(frozen=True)
class SafetyConfig:
    """Reserved bounds for the later motion phase; unused in phase 0/1."""

    max_vx: float = 0.25
    max_vy: float = 0.20
    max_yaw: float = 0.50
    watchdog_timeout: float = 0.40


@dataclass(frozen=True)
class LoggingConfig:
    level: str = "INFO"


@dataclass(frozen=True)
class RobotProfile:
    """One named robot with an isolated connection and per-device key."""

    id: str
    label: str
    connection: ConnectionConfig


@dataclass(frozen=True)
class AppConfig:
    connection: ConnectionConfig
    safety: SafetyConfig
    logging: LoggingConfig
    source: Path
    robots: tuple[RobotProfile, ...] = ()


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigurationError(f"{label} must be a YAML mapping")
    return value


def _reject_unknown_keys(
    values: Mapping[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ConfigurationError(
            f"Unknown {label} option(s): {', '.join(unknown)}"
        )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _validate_ipv4(
    value: str | None,
    label: str = "UNITREE_ROBOT_IP",
) -> str | None:
    if value is None:
        return None
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ConfigurationError(f"{label} must be a valid IP address") from exc
    if address.version != 4:
        raise ConfigurationError(f"{label} must be an IPv4 address")
    return str(address)


def _validate_aes_key(value: str | None, label: str) -> str | None:
    key = _optional_text(value)
    if key is None:
        return None
    key = key.lower()
    if re.fullmatch(r"[0-9a-f]{32}", key) is None:
        raise ConfigurationError(
            f"{label} must contain exactly 32 hexadecimal characters"
        )
    return key


def _positive_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} must be a number") from exc
    if number <= 0:
        raise ConfigurationError(f"{label} must be greater than zero")
    return number


def _non_negative_float(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{label} must be a number") from exc
    if number < 0:
        raise ConfigurationError(f"{label} must be zero or greater")
    return number


def _bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    raise ConfigurationError(f"{label} must be true or false")


def load_config(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    load_env_file: bool = True,
) -> AppConfig:
    """Load non-sensitive YAML settings and secret environment variables.

    OS environment values take precedence over a local ``.env`` file. Tests can
    supply ``environ`` to avoid reading or mutating the process environment.
    """

    if environ is None:
        if load_env_file:
            load_dotenv(PROJECT_ROOT / ".env", override=False)
        env: Mapping[str, str] = os.environ
    else:
        env = environ

    if path is None:
        config_path = (
            DEFAULT_CONFIG_PATH
            if DEFAULT_CONFIG_PATH.exists()
            else EXAMPLE_CONFIG_PATH
        )
    else:
        config_path = Path(path).expanduser().resolve()

    if not config_path.is_file():
        raise ConfigurationError(f"Configuration file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigurationError(f"Cannot read {config_path}: {exc}") from exc

    root = _mapping(raw, "configuration")
    _reject_unknown_keys(root, {"connection", "safety", "logging"}, "top-level")
    connection_raw = _mapping(root.get("connection"), "connection")
    safety_raw = _mapping(root.get("safety"), "safety")
    logging_raw = _mapping(root.get("logging"), "logging")
    _reject_unknown_keys(
        connection_raw,
        {"mode", "ip", "connect_timeout", "require_aes_key"},
        "connection",
    )
    _reject_unknown_keys(
        safety_raw,
        {"max_vx", "max_vy", "max_yaw", "watchdog_timeout"},
        "safety",
    )
    _reject_unknown_keys(logging_raw, {"level"}, "logging")

    mode = str(connection_raw.get("mode", "local_sta")).strip().lower()
    if mode not in {"local_ap", "local_sta"}:
        raise ConfigurationError(
            "connection.mode must be 'local_ap' or 'local_sta'"
        )

    env_ip = _optional_text(env.get("UNITREE_ROBOT_IP"))
    yaml_ip = _optional_text(connection_raw.get("ip"))
    ip = _validate_ipv4(env_ip if env_ip is not None else yaml_ip)
    serial_number = _optional_text(env.get("UNITREE_SERIAL_NUMBER"))
    region = _optional_text(env.get("UNITREE_REGION")) or "cn"
    if region not in {"cn", "global"}:
        raise ConfigurationError("UNITREE_REGION must be 'cn' or 'global'")

    aes_key = _validate_aes_key(
        env.get("UNITREE_AES_128_KEY"),
        "UNITREE_AES_128_KEY",
    )

    connection = ConnectionConfig(
        mode=mode,
        ip=ip,
        serial_number=serial_number,
        region=region,
        connect_timeout=_positive_float(
            connection_raw.get("connect_timeout", 30.0),
            "connection.connect_timeout",
        ),
        require_aes_key=_bool(
            connection_raw.get("require_aes_key", True),
            "connection.require_aes_key",
        ),
        aes_128_key=aes_key,
    )

    safety = SafetyConfig(
        max_vx=_non_negative_float(safety_raw.get("max_vx", 0.25), "safety.max_vx"),
        max_vy=_non_negative_float(safety_raw.get("max_vy", 0.20), "safety.max_vy"),
        max_yaw=_non_negative_float(
            safety_raw.get("max_yaw", 0.50), "safety.max_yaw"
        ),
        watchdog_timeout=_positive_float(
            safety_raw.get("watchdog_timeout", 0.40),
            "safety.watchdog_timeout",
        ),
    )

    level = str(logging_raw.get("level", "INFO")).strip().upper()
    if not isinstance(getattr(logging, level, None), int):
        raise ConfigurationError(f"Unknown logging.level: {level}")

    robots: list[RobotProfile] = [
        RobotProfile(id="dog_1", label="机器狗 1", connection=connection)
    ]
    for index in range(2, 17):
        prefix = f"UNITREE_ROBOT_{index}"
        values = {
            "ip": _optional_text(env.get(f"{prefix}_IP")),
            "serial": _optional_text(env.get(f"{prefix}_SERIAL_NUMBER")),
            "key": _optional_text(env.get(f"{prefix}_AES_128_KEY")),
            "label": _optional_text(env.get(f"{prefix}_LABEL")),
        }
        if not any(values.values()):
            continue
        profile_connection = ConnectionConfig(
            mode=mode,
            ip=_validate_ipv4(values["ip"], f"{prefix}_IP"),
            serial_number=values["serial"],
            region=region,
            connect_timeout=connection.connect_timeout,
            require_aes_key=connection.require_aes_key,
            aes_128_key=_validate_aes_key(
                values["key"],
                f"{prefix}_AES_128_KEY",
            ),
        )
        robots.append(
            RobotProfile(
                id=f"dog_{index}",
                label=values["label"] or f"机器狗 {index}",
                connection=profile_connection,
            )
        )

    ips = [profile.connection.target_ip for profile in robots]
    duplicate_ips = sorted({item for item in ips if item and ips.count(item) > 1})
    if duplicate_ips:
        raise ConfigurationError("Robot profiles must use distinct IP addresses")
    serials = [profile.connection.serial_number for profile in robots]
    duplicate_serials = sorted(
        {item for item in serials if item and serials.count(item) > 1}
    )
    if duplicate_serials:
        raise ConfigurationError("Robot profiles must use distinct serial numbers")

    return AppConfig(
        connection=connection,
        safety=safety,
        logging=LoggingConfig(level=level),
        source=config_path,
        robots=tuple(robots),
    )
