"""GO2 Air local WebRTC integration with read-only telemetry."""

from .config import AppConfig, ConfigurationError, ConnectionConfig, load_config
from .connection import FailureKind, Go2Connection, Go2ConnectionError
from .state import (
    Go2StateReader,
    LowState,
    SportModeState,
    StateError,
    StatePayloadError,
    StateSnapshot,
    StateTimeoutError,
)
from .motion import (
    MotionError,
    OneShotStandController,
    OneShotStandDownController,
    StandResult,
)
from .safety import StandSafetyError, StandSafetyLimits, validate_stand_preflight

__all__ = [
    "AppConfig",
    "ConfigurationError",
    "ConnectionConfig",
    "FailureKind",
    "Go2Connection",
    "Go2ConnectionError",
    "Go2StateReader",
    "LowState",
    "MotionError",
    "OneShotStandController",
    "OneShotStandDownController",
    "SportModeState",
    "StateError",
    "StatePayloadError",
    "StateSnapshot",
    "StateTimeoutError",
    "StandResult",
    "StandSafetyError",
    "StandSafetyLimits",
    "load_config",
    "validate_stand_preflight",
]
