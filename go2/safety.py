"""Conservative preflight checks for the explicitly allowed one-shot stand."""

from __future__ import annotations

from dataclasses import dataclass

from .state import StateSnapshot


class StandSafetyError(RuntimeError):
    """Raised before motion when the current state is not safe enough to stand."""


@dataclass(frozen=True)
class StandSafetyLimits:
    min_battery_soc: float = 20.0
    min_power_voltage: float = 26.0
    max_abs_roll_pitch: float = 0.70
    min_total_foot_force: float = 20.0


def is_prone_standby(snapshot: StateSnapshot) -> bool:
    """Return whether this GO2 reports the observed pre-StandUp standby state."""

    sport = snapshot.sport_mode_state
    # Verified on both local GO2 Air units. This is the same state in which
    # action preflight below tells the operator to StandUp first; ``5`` in the
    # action catalogue is a post-command delay, not a SportModeState value.
    return sport.mode == 0 and sport.error_code == 1001


def validate_stand_preflight(
    snapshot: StateSnapshot, limits: StandSafetyLimits | None = None
) -> None:
    """Reject a stand command unless basic power and orientation checks pass."""

    limits = limits or StandSafetyLimits()
    low = snapshot.low_state
    problems: list[str] = []
    if low.battery_soc < limits.min_battery_soc:
        problems.append("battery state of charge is below the stand threshold")
    if low.power_voltage < limits.min_power_voltage:
        problems.append("battery voltage is below the stand threshold")
    if abs(low.rpy[0]) > limits.max_abs_roll_pitch:
        problems.append("absolute roll exceeds the stand threshold")
    if abs(low.rpy[1]) > limits.max_abs_roll_pitch:
        problems.append("absolute pitch exceeds the stand threshold")
    if sum(max(force, 0.0) for force in low.foot_force) < limits.min_total_foot_force:
        problems.append("insufficient foot contact force")
    if problems:
        raise StandSafetyError("Stand preflight rejected: " + "; ".join(problems) + ".")


def validate_action_preflight(
    snapshot: StateSnapshot, limits: StandSafetyLimits | None = None
) -> None:
    """Require both physical safety and a controller state ready for actions."""

    validate_stand_preflight(snapshot, limits)
    sport = snapshot.sport_mode_state
    # Observed on this GO2 Air and independently reported by current Go2 users:
    # mode=0/error_code=1001 is the pre-StandUp state. Sport actions return -1
    # immediately here, so stop before sending and direct the operator to StandUp.
    if sport.mode == 0 and sport.error_code == 1001:
        raise StandSafetyError(
            "动作控制器尚未起立就绪（mode=0, error_code=1001）；"
            "请先用面板执行一次起立，确认稳定后再运行动作或编舞。未发送动作。"
        )
