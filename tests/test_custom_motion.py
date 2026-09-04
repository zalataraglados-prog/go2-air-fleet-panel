from __future__ import annotations

import pytest

from go2.custom_motion import build_custom_motion_request, validate_custom_motion_step


def test_euler_request_uses_fixed_official_api_and_axes() -> None:
    assert build_custom_motion_request(
        "euler", {"roll": 0.1, "pitch": -0.05, "yaw": 0.12}
    ) == (1007, {"x": 0.1, "y": -0.05, "z": 0.12})


@pytest.mark.parametrize(
    "step",
    [
        {"kind": "euler", "roll": 0.121, "pitch": 0, "yaw": 0, "duration": 1},
        {"kind": "euler", "roll": 0, "pitch": -0.201, "yaw": 0, "duration": 1},
        {"kind": "body_height", "height": -0.04, "duration": 1},
        {"kind": "wait", "api_id": 1003, "duration": 1},
    ],
)
def test_custom_motion_rejects_out_of_bounds_or_extra_fields(step: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate_custom_motion_step(step, step_index=1)
