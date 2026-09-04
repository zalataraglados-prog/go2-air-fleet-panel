from __future__ import annotations

import json
from typing import Any

import pytest

from go2.protocol import (
    CommandProtocolError,
    MotionModeProtocolError,
    parse_command_outcome,
    parse_motion_mode,
    require_command_accepted,
)


@pytest.mark.parametrize(
    ("response", "expected_code"),
    [
        ({"data": {"header": {"status": {"code": 0}}}}, 0),
        ({"data": {"status": {"code": "0"}}}, 0),
        ({"header": {"status": {"code": 0}}}, 0),
        ({"status": {"code": 0}}, 0),
        ({"data": {"code": 0}}, 0),
        ({"code": "0"}, 0),
        (0, 0),
        ("0", 0),
        (json.dumps({"type": "res", "code": 0}), 0),
        ({"type": "res", "data": json.dumps({"code": 0})}, 0),
    ],
)
def test_known_success_envelopes_are_accepted(response: Any, expected_code: int) -> None:
    outcome = parse_command_outcome(response)
    assert outcome.acknowledged is True
    assert outcome.accepted is True
    assert outcome.code == expected_code


@pytest.mark.parametrize(
    "response",
    [
        {"type": "res", "topic": "rt/api/sport/request", "data": {}},
        {"data": {"header": {"identity": {"id": 123, "api_id": 1016}}}},
    ],
)
def test_matched_code_less_response_is_acknowledged(response: Any) -> None:
    outcome = parse_command_outcome(response)
    assert outcome.acknowledged is True
    assert outcome.accepted is None
    assert outcome.code is None


def test_explicit_nonzero_code_remains_a_rejection() -> None:
    outcome = parse_command_outcome({"data": {"header": {"status": {"code": 4202}}}})
    assert outcome.accepted is False
    assert outcome.code == 4202
    with pytest.raises(CommandProtocolError, match="4202"):
        require_command_accepted(
            {"data": {"header": {"status": {"code": 4202}}}}, "Hello"
        )


@pytest.mark.parametrize(
    "response",
    [None, [], True, "not-json", {}, {"data": {"header": {"status": {"code": True}}}}],
)
def test_invalid_response_is_not_promoted_to_success(response: Any) -> None:
    with pytest.raises(CommandProtocolError):
        parse_command_outcome(response)


@pytest.mark.parametrize(
    "mode_data",
    [json.dumps({"name": "normal"}), {"name": "MCF"}],
)
def test_motion_mode_query_parses_web_rtc_variants(mode_data: Any) -> None:
    response = {
        "data": {
            "header": {"status": {"code": 0}},
            "data": mode_data,
        }
    }
    assert parse_motion_mode(response) == (
        "normal" if isinstance(mode_data, str) else "mcf"
    )


def test_motion_mode_query_requires_explicit_success_and_name() -> None:
    with pytest.raises(MotionModeProtocolError, match="3203"):
        parse_motion_mode(
            {
                "data": {
                    "header": {"status": {"code": 3203}},
                    "data": json.dumps({"name": "normal"}),
                }
            }
        )
    with pytest.raises(MotionModeProtocolError, match="mode name"):
        parse_motion_mode(
            {"data": {"header": {"status": {"code": 0}}, "data": "{}"}}
        )
