"""Reviewed GO2 sport-action catalog; no arbitrary API IDs are accepted."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class ActionSpec:
    id: str
    label: str
    api_id: int
    category: str
    risk: str
    watchdog_seconds: float
    parameter: Mapping[str, Any] | None = None
    editor_allowed: bool = False
    priority: bool = False
    required_motion_mode: str | None = None
    verified: str = "official_api_unverified_on_this_robot"
    description: str = ""

    @property
    def requires_advanced_ack(self) -> bool:
        return self.risk in {"high", "extreme"}

    def public(
        self,
        *,
        motion_mode: str | None = None,
        unsupported_actions: frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        available = True
        unavailable_reason: str | None = None
        if self.id in unsupported_actions:
            available = False
            unavailable_reason = "这台 Air 已返回 3203：当前服务未实现该 API"
        elif self.required_motion_mode and motion_mode != self.required_motion_mode:
            available = False
            actual = motion_mode or "未知"
            unavailable_reason = (
                f"需要 {self.required_motion_mode} 运动模式；当前为 {actual}"
            )
        return {
            "id": self.id,
            "label": self.label,
            "api_id": self.api_id,
            "category": self.category,
            "risk": self.risk,
            "hold_ms": 3000 if self.requires_advanced_ack else 1200,
            "watchdog_seconds": self.watchdog_seconds,
            "editor_allowed": self.editor_allowed,
            "requires_advanced_ack": self.requires_advanced_ack,
            "required_motion_mode": self.required_motion_mode,
            "available": available,
            "unavailable_reason": unavailable_reason,
            "verified": self.verified,
            "description": self.description,
        }


def _action(
    id: str,
    label: str,
    api_id: int,
    category: str,
    risk: str,
    watchdog_seconds: float,
    *,
    parameter: Mapping[str, Any] | None = None,
    editor_allowed: bool = False,
    priority: bool = False,
    required_motion_mode: str | None = None,
    verified: str = "official_api_unverified_on_this_robot",
    description: str = "",
) -> ActionSpec:
    return ActionSpec(
        id=id,
        label=label,
        api_id=api_id,
        category=category,
        risk=risk,
        watchdog_seconds=watchdog_seconds,
        parameter=MappingProxyType(dict(parameter)) if parameter is not None else None,
        editor_allowed=editor_allowed,
        priority=priority,
        required_motion_mode=required_motion_mode,
        verified=verified,
        description=description,
    )


_SPECS = (
    _action("balance_stand", "平衡站立", 1002, "posture", "medium", 5, editor_allowed=True),
    _action("stand_up", "起立", 1004, "posture", "medium", 5, editor_allowed=True, verified="real_robot"),
    _action("stand_down", "趴下", 1005, "posture", "medium", 5, verified="real_robot"),
    _action("recovery_stand", "恢复站立", 1006, "posture", "high", 8),
    _action("sit", "坐下", 1009, "posture", "medium", 6),
    _action("rise_sit", "从坐姿起身", 1010, "posture", "medium", 6),
    _action("hello", "招手", 1016, "gesture", "low", 5, editor_allowed=True),
    _action("stretch", "伸展", 1017, "gesture", "low", 6, editor_allowed=True),
    _action("content", "开心动作", 1020, "gesture", "low", 5, editor_allowed=True),
    _action("dance_1", "原厂舞蹈 1", 1022, "dance", "medium", 15, editor_allowed=True),
    _action("dance_2", "原厂舞蹈 2", 1023, "dance", "medium", 15, editor_allowed=True),
    _action("pose", "姿态模式", 1028, "mode", "medium", 6, parameter={"data": True}),
    _action("scrape", "拜年/刨地", 1029, "gesture", "low", 5, editor_allowed=True),
    _action("front_flip", "前空翻", 1030, "acrobatics", "extreme", 10, parameter={"data": True}),
    _action("front_jump", "向前跳", 1031, "acrobatics", "high", 8),
    _action("front_pounce", "前扑", 1032, "acrobatics", "high", 8),
    _action("heart", "比心", 1036, "gesture", "low", 5, editor_allowed=True),
    _action("static_walk", "静态步行", 1061, "gait", "medium", 6, parameter={"data": True}, required_motion_mode="mcf"),
    _action("trot_run", "小跑", 1062, "gait", "high", 6, parameter={"data": True}, required_motion_mode="mcf"),
    _action("economic_gait", "耐力步态", 1063, "gait", "medium", 6, parameter={"data": True}, required_motion_mode="mcf"),
    _action("left_flip", "左空翻", 2041, "acrobatics", "extreme", 10, parameter={"data": True}, required_motion_mode="mcf"),
    _action("back_flip", "后空翻", 2043, "acrobatics", "extreme", 10, parameter={"data": True}, required_motion_mode="mcf"),
    _action("handstand", "手倒立", 2044, "acrobatics", "extreme", 12, parameter={"data": True}, required_motion_mode="mcf"),
    _action("free_walk", "自由行走", 2045, "gait", "medium", 6, parameter={"data": True}, required_motion_mode="mcf"),
    _action("free_bound", "跳跃步态", 2046, "gait", "high", 8, parameter={"data": True}, required_motion_mode="mcf"),
    _action("free_jump", "连续跳跃", 2047, "gait", "high", 8, parameter={"data": True}, required_motion_mode="mcf"),
    _action("free_avoid", "自由避障", 2048, "gait", "medium", 6, parameter={"data": True}, required_motion_mode="mcf"),
    _action("classic_walk", "经典步态", 2049, "gait", "medium", 6, parameter={"data": True}, required_motion_mode="mcf"),
    _action("back_stand", "后腿直立", 2050, "acrobatics", "extreme", 12, parameter={"data": True}, required_motion_mode="mcf"),
    _action("cross_step", "交叉步", 2051, "dance", "medium", 8, parameter={"data": True}, required_motion_mode="mcf"),
    _action("auto_recovery_on", "开启自动恢复", 2054, "mode", "medium", 4, parameter={"data": True}, required_motion_mode="mcf"),
    _action("auto_recovery_off", "关闭自动恢复", 2054, "mode", "low", 4, parameter={"data": False}, required_motion_mode="mcf"),
    _action("switch_avoid", "开启避障模式", 2058, "mode", "low", 4, parameter={"data": True}, required_motion_mode="mcf"),
    _action("damp", "阻尼卸力", 1001, "safety", "extreme", 3, priority=True, description="会使机器狗失去主动支撑，可能直接倒地"),
)


ACTION_LIBRARY: Mapping[str, ActionSpec] = MappingProxyType(
    {spec.id: spec for spec in _SPECS}
)

EDITOR_ACTION_IDS = frozenset(
    spec.id for spec in _SPECS if spec.editor_allowed
)


FEATURED_CHOREOGRAPHY: dict[str, Any] = {
    "id": "flowing_light_tail",
    "name": "流光摆尾·纯姿态版",
    "description": "Air 自定义编舞：BalanceStand 只负责进入姿态控制，舞段完全由已实机接受的 Euler 组合，不依赖比心或原厂舞蹈。执行前必须先起立。",
    "steps": [
        {"action": "balance_stand", "duration": 2.0},
        {"kind": "euler", "roll": 0.12, "pitch": 0.0, "yaw": 0.18, "duration": 2.0},
        {"kind": "euler", "roll": -0.12, "pitch": 0.0, "yaw": -0.18, "duration": 2.0},
        {"kind": "euler", "roll": 0.0, "pitch": 0.18, "yaw": 0.0, "duration": 2.0},
        {"kind": "euler", "roll": 0.0, "pitch": -0.16, "yaw": 0.0, "duration": 2.0},
        {"kind": "euler", "roll": 0.10, "pitch": 0.10, "yaw": -0.22, "duration": 2.0},
        {"kind": "euler", "roll": -0.10, "pitch": 0.10, "yaw": 0.22, "duration": 2.0},
        {"kind": "euler", "roll": 0.08, "pitch": -0.12, "yaw": 0.0, "duration": 2.0},
        {"kind": "euler", "roll": 0.0, "pitch": 0.0, "yaw": 0.0, "duration": 2.0},
    ],
}


AIR_CONFIRMED_UNSUPPORTED_ACTIONS: frozenset[str] = frozenset()


def public_action_library(
    *,
    motion_mode: str | None = None,
    unsupported_actions: frozenset[str] = AIR_CONFIRMED_UNSUPPORTED_ACTIONS,
) -> list[dict[str, Any]]:
    return [
        spec.public(
            motion_mode=motion_mode,
            unsupported_actions=unsupported_actions,
        )
        for spec in _SPECS
    ]
