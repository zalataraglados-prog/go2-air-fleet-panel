"""Conservative, fixed custom-motion primitives for the choreography editor."""

from __future__ import annotations

from typing import Any, Mapping


CUSTOM_MOTION_LIMITS: dict[str, dict[str, Any]] = {
    "euler": {
        "label": "自定义姿态",
        "api_id": 1007,
        "bounds": {
            "roll": (-0.12, 0.12),
            "pitch": (-0.20, 0.20),
            "yaw": (-0.30, 0.30),
        },
    },
    "wait": {
        "label": "停顿",
        "api_id": None,
        "bounds": {},
    },
}


def _bounded_number(
    value: Any,
    *,
    field: str,
    low: float,
    high: float,
    step_index: int,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"第 {step_index} 步 {field} 必须是数字。")
    normalized = round(float(value), 4)
    if not low <= normalized <= high:
        raise ValueError(
            f"第 {step_index} 步 {field} 必须在 {low:.2f} 到 {high:.2f} 之间。"
        )
    return normalized


def validate_custom_motion_step(
    raw_step: Mapping[str, Any],
    *,
    step_index: int,
) -> dict[str, Any]:
    kind = raw_step.get("kind")
    if kind not in CUSTOM_MOTION_LIMITS:
        raise ValueError(f"第 {step_index} 步自定义动作类型无效。")

    if kind == "euler":
        allowed = {"kind", "roll", "pitch", "yaw", "duration"}
        if set(raw_step) != allowed:
            raise ValueError(f"第 {step_index} 步自定义姿态字段不完整或包含额外字段。")
        bounds = CUSTOM_MOTION_LIMITS[kind]["bounds"]
        return {
            "kind": kind,
            "roll": _bounded_number(
                raw_step.get("roll"), field="roll", low=bounds["roll"][0],
                high=bounds["roll"][1], step_index=step_index,
            ),
            "pitch": _bounded_number(
                raw_step.get("pitch"), field="pitch", low=bounds["pitch"][0],
                high=bounds["pitch"][1], step_index=step_index,
            ),
            "yaw": _bounded_number(
                raw_step.get("yaw"), field="yaw", low=bounds["yaw"][0],
                high=bounds["yaw"][1], step_index=step_index,
            ),
        }

    if set(raw_step) != {"kind", "duration"}:
        raise ValueError(f"第 {step_index} 步停顿包含不允许的字段。")
    return {"kind": "wait"}


def build_custom_motion_request(
    kind: str,
    parameters: Mapping[str, Any],
) -> tuple[int, dict[str, float]]:
    """Build only fixed, reviewed sport requests; arbitrary IDs are impossible."""

    raw = {"kind": kind, "duration": 1.0, **dict(parameters)}
    normalized = validate_custom_motion_step(raw, step_index=1)
    if kind == "euler":
        return 1007, {
            "x": normalized["roll"],
            "y": normalized["pitch"],
            "z": normalized["yaw"],
        }
    raise ValueError("Wait is local-only and does not produce a robot request.")


def public_custom_motion_limits() -> dict[str, dict[str, Any]]:
    return {
        kind: {
            "label": spec["label"],
            "bounds": {
                field: list(bound) for field, bound in spec["bounds"].items()
            },
        }
        for kind, spec in CUSTOM_MOTION_LIMITS.items()
    }
