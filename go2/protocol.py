"""Normalize the response envelopes returned by GO2 WebRTC firmware variants."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping, Sequence


class CommandProtocolError(RuntimeError):
    """Raised when a command response is not a recognizable acknowledgement."""


class MotionModeProtocolError(CommandProtocolError):
    """Raised when a successful motion-switcher reply has no usable mode name."""


@dataclass(frozen=True)
class CommandOutcome:
    """A sanitized command result; the raw robot payload is never retained."""

    acknowledged: bool
    accepted: bool | None
    code: int | None
    shape: str

    def public(self) -> dict[str, Any]:
        return {
            "acknowledged": self.acknowledged,
            "accepted": self.accepted,
            "status": self.code,
            "response_shape": self.shape,
        }


_CODE_PATHS: tuple[tuple[str, ...], ...] = (
    ("data", "header", "status", "code"),
    ("data", "status", "code"),
    ("header", "status", "code"),
    ("status", "code"),
    ("data", "code"),
    ("code",),
)
_INTEGER = re.compile(r"^-?\d+$")


def _coerce_code(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
        return int(value.strip())
    return None


def _at_path(value: Mapping[str, Any], path: Sequence[str]) -> tuple[bool, Any]:
    current: Any = value
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            return False, None
        current = current[part]
    return True, current


def _decode_json_container(response: Any) -> tuple[Any, str]:
    if isinstance(response, bytes):
        try:
            response = response.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CommandProtocolError("Command returned non-UTF-8 response bytes.") from exc
    if isinstance(response, str):
        stripped = response.strip()
        direct_code = _coerce_code(stripped)
        if direct_code is not None:
            return direct_code, "direct_numeric_string"
        try:
            return json.loads(stripped), "json_string"
        except (json.JSONDecodeError, TypeError) as exc:
            raise CommandProtocolError("Command returned an invalid response string.") from exc
    return response, "native"


def parse_command_outcome(
    response: Any,
    *,
    accepted_codes: frozenset[int] = frozenset({0}),
) -> CommandOutcome:
    """Parse known response variants without treating a code-less ``res`` as failure.

    ``unitree_webrtc_connect`` resolves a request only after its matching response
    reaches the future resolver. Some GO2 firmware builds include an explicit
    status code while others return only the matched ``res`` envelope.
    """

    decoded, container_shape = _decode_json_container(response)
    direct_code = _coerce_code(decoded)
    if direct_code is not None:
        return CommandOutcome(
            acknowledged=True,
            accepted=direct_code in accepted_codes,
            code=direct_code,
            shape=f"{container_shape}:code",
        )
    if not isinstance(decoded, Mapping):
        raise CommandProtocolError("Command returned an invalid response envelope.")

    # A few firmware versions JSON-encode the inner data object.
    inner_data = decoded.get("data")
    if isinstance(inner_data, (str, bytes)):
        try:
            parsed_inner, _ = _decode_json_container(inner_data)
        except CommandProtocolError:
            parsed_inner = None
        if isinstance(parsed_inner, Mapping):
            decoded = dict(decoded)
            decoded["data"] = parsed_inner
            container_shape = f"{container_shape}:json_data"

    for path in _CODE_PATHS:
        found, raw_code = _at_path(decoded, path)
        if not found:
            continue
        code = _coerce_code(raw_code)
        if code is None:
            raise CommandProtocolError(
                f"Command returned a non-numeric status at {'.'.join(path)}."
            )
        return CommandOutcome(
            acknowledged=True,
            accepted=code in accepted_codes,
            code=code,
            shape=f"{container_shape}:{'.'.join(path)}",
        )

    response_type = decoded.get("type")
    data = decoded.get("data")
    header = data.get("header") if isinstance(data, Mapping) else None
    top_header = decoded.get("header")
    has_matched_identity = (
        isinstance(header, Mapping) and isinstance(header.get("identity"), Mapping)
    ) or (
        isinstance(top_header, Mapping)
        and isinstance(top_header.get("identity"), Mapping)
    )
    is_response_envelope = response_type in {"res", "response"}
    if is_response_envelope or has_matched_identity:
        marker = "response_envelope" if is_response_envelope else "matched_identity"
        return CommandOutcome(
            acknowledged=True,
            accepted=None,
            code=None,
            shape=f"{container_shape}:{marker}:no_code",
        )

    raise CommandProtocolError("Command returned an unrecognized response envelope.")


def require_command_accepted(
    response: Any,
    command: str,
    *,
    accepted_codes: frozenset[int] = frozenset({0}),
) -> CommandOutcome:
    """Return an acknowledgement or raise only for malformed/explicit rejection."""

    try:
        outcome = parse_command_outcome(response, accepted_codes=accepted_codes)
    except CommandProtocolError as exc:
        raise CommandProtocolError(f"{command}: {exc}") from exc
    if outcome.accepted is False:
        raise CommandProtocolError(
            f"{command} was rejected with status code {outcome.code}."
        )
    return outcome


def parse_motion_mode(response: Any) -> str:
    """Return the mode name from the read-only motion-switcher CheckMode reply.

    The WebRTC bridge places a JSON string in ``data.data`` while test tools and
    some firmware variants may already decode that value to a mapping.
    """

    outcome = parse_command_outcome(response)
    if outcome.accepted is not True:
        if outcome.accepted is False:
            raise MotionModeProtocolError(
                f"Motion mode query was rejected with status code {outcome.code}."
            )
        raise MotionModeProtocolError(
            "Motion mode query returned no explicit success status."
        )
    decoded, _ = _decode_json_container(response)
    if not isinstance(decoded, Mapping):
        raise MotionModeProtocolError("Motion mode reply is not an object.")
    outer_data = decoded.get("data")
    if not isinstance(outer_data, Mapping):
        raise MotionModeProtocolError("Motion mode reply has no data envelope.")
    mode_data: Any = outer_data.get("data")
    if isinstance(mode_data, (str, bytes)):
        try:
            mode_data, _ = _decode_json_container(mode_data)
        except CommandProtocolError as exc:
            raise MotionModeProtocolError(
                "Motion mode reply contains invalid JSON data."
            ) from exc
    if not isinstance(mode_data, Mapping):
        raise MotionModeProtocolError("Motion mode reply has no mode object.")
    name = mode_data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise MotionModeProtocolError("Motion mode reply has no mode name.")
    normalized = name.strip().lower()
    if len(normalized) > 32 or not re.fullmatch(r"[a-z0-9_-]+", normalized):
        raise MotionModeProtocolError("Motion mode reply contains an invalid mode name.")
    return normalized
