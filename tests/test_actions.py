from __future__ import annotations

from go2.actions import (
    ACTION_LIBRARY,
    EDITOR_ACTION_IDS,
    FEATURED_CHOREOGRAPHY,
    public_action_library,
)


def test_action_ids_and_api_ids_are_unique() -> None:
    assert len(ACTION_LIBRARY) == len(set(ACTION_LIBRARY))
    # A few firmware aliases legitimately share an API ID, but every editor ID
    # still resolves through the fixed catalog rather than accepting raw IDs.
    assert all(spec.api_id > 0 for spec in ACTION_LIBRARY.values())


def test_high_risk_actions_require_advanced_ack() -> None:
    for action_id in ("front_flip", "back_flip", "handstand", "damp"):
        assert ACTION_LIBRARY[action_id].requires_advanced_ack


def test_editor_excludes_high_risk_actions() -> None:
    assert EDITOR_ACTION_IDS
    assert all(
        ACTION_LIBRARY[action_id].risk not in {"high", "extreme"}
        for action_id in EDITOR_ACTION_IDS
    )


def test_featured_dance_is_custom_and_editor_safe() -> None:
    steps = FEATURED_CHOREOGRAPHY["steps"]
    official_ids = [step["action"] for step in steps if "action" in step]
    assert "dance_1" not in official_ids
    assert "dance_2" not in official_ids
    assert "heart" not in official_ids
    assert official_ids == ["balance_stand"]
    assert all(action_id in EDITOR_ACTION_IDS for action_id in official_ids)
    assert {step.get("kind") for step in steps} >= {"euler"}
    assert all(step.get("kind") != "body_height" for step in steps)


def test_removed_or_misidentified_apis_are_not_exposed() -> None:
    assert "wallow" not in ACTION_LIBRARY
    assert "lead_follow" not in ACTION_LIBRARY
    assert "walk_upright" not in ACTION_LIBRARY
    assert ACTION_LIBRARY["back_stand"].api_id == 2050


def test_parameter_shapes_match_webrtc_mcf_transport_example() -> None:
    for action_id in (
        "front_flip", "static_walk", "trot_run", "economic_gait",
        "left_flip", "back_flip", "free_walk",
    ):
        assert dict(ACTION_LIBRARY[action_id].parameter or {}) == {"data": True}


def test_mcf_actions_are_mode_gated_and_real_robot_3203_is_blocked() -> None:
    disconnected = {item["id"]: item for item in public_action_library()}
    mcf = {
        item["id"]: item
        for item in public_action_library(motion_mode="mcf")
    }
    assert disconnected["handstand"]["available"] is False
    assert mcf["handstand"]["available"] is True
    assert mcf["back_flip"]["available"] is True
    assert mcf["cross_step"]["available"] is True
