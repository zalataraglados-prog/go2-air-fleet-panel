"""Bounded StandUp/StandDown actions with a mandatory StopMove watchdog."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from .protocol import CommandProtocolError, require_command_accepted
from .safety import validate_stand_preflight
from .state import StateSnapshot


class MotionError(RuntimeError):
    """Sanitized motion request or safety-guard failure."""


class _StandConnection(Protocol):
    async def request_stand_up(self) -> Any: ...

    async def request_stand_down(self) -> Any: ...

    async def request_stop_move(self) -> Any: ...


@dataclass(frozen=True)
class StandResult:
    stand_status: int | None
    stop_status: int | None
    guard_seconds: float


def _status_code(
    response: Any,
    command: str,
    *,
    accepted_codes: frozenset[int] = frozenset({0}),
) -> int | None:
    try:
        return require_command_accepted(
            response,
            command,
            accepted_codes=accepted_codes,
        ).code
    except CommandProtocolError as exc:
        raise MotionError(str(exc)) from exc


class OneShotStandController:
    """Permit only StandUp and guarantee a bounded StopMove request."""

    def __init__(self, connection: _StandConnection) -> None:
        self._connection = connection

    async def _stop(
        self,
        request_timeout: float,
        *,
        accepted_codes: frozenset[int] = frozenset({0}),
    ) -> int | None:
        try:
            response = await asyncio.wait_for(
                self._connection.request_stop_move(), timeout=request_timeout
            )
        except asyncio.TimeoutError as exc:
            raise MotionError("StopMove watchdog response timed out.") from exc
        return _status_code(
            response,
            "StopMove watchdog",
            accepted_codes=accepted_codes,
        )

    async def _execute_action(
        self,
        preflight_state: StateSnapshot,
        request_action: Callable[[], Awaitable[Any]],
        action_name: str,
        *,
        guard_seconds: float = 5.0,
        request_timeout: float = 3.0,
        accepted_stop_codes: frozenset[int] = frozenset({0}),
    ) -> StandResult:
        if not 0.01 <= guard_seconds <= 8.0:
            raise ValueError("guard_seconds must be between 0.01 and 8 seconds")
        if not 0.1 <= request_timeout <= 5.0:
            raise ValueError("request_timeout must be between 0.1 and 5 seconds")
        validate_stand_preflight(preflight_state)

        async def watchdog() -> int | None:
            await asyncio.sleep(guard_seconds)
            return await self._stop(
                request_timeout,
                accepted_codes=accepted_stop_codes,
            )

        watchdog_task = asyncio.create_task(watchdog())
        try:
            try:
                response = await asyncio.wait_for(
                    request_action(), timeout=request_timeout
                )
            except asyncio.TimeoutError as exc:
                raise MotionError(f"{action_name} response timed out.") from exc
            stand_status = _status_code(response, action_name)
            stop_status = await watchdog_task
            return StandResult(
                stand_status=stand_status,
                stop_status=stop_status,
                guard_seconds=guard_seconds,
            )
        except BaseException:
            if not watchdog_task.done():
                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)
                # An early error or Ctrl+C requests StopMove immediately.
                await self._stop(
                    request_timeout,
                    accepted_codes=accepted_stop_codes,
                )
            raise

    async def execute(
        self,
        preflight_state: StateSnapshot,
        *,
        guard_seconds: float = 5.0,
        request_timeout: float = 3.0,
    ) -> StandResult:
        return await self._execute_action(
            preflight_state,
            self._connection.request_stand_up,
            "StandUp",
            guard_seconds=guard_seconds,
            request_timeout=request_timeout,
        )


class OneShotStandDownController(OneShotStandController):
    """Permit only StandDown and guarantee a bounded StopMove request.

    GO2 Air can answer ``-1`` to the watchdog after it has already entered the
    terminal crouched posture. The watchdog is still sent; that one observed
    terminal response is reported in ``StandResult`` instead of incorrectly
    turning an accepted StandDown into a failed action.
    """

    async def execute(
        self,
        preflight_state: StateSnapshot,
        *,
        guard_seconds: float = 5.0,
        request_timeout: float = 3.0,
    ) -> StandResult:
        return await self._execute_action(
            preflight_state,
            self._connection.request_stand_down,
            "StandDown",
            guard_seconds=guard_seconds,
            request_timeout=request_timeout,
            accepted_stop_codes=frozenset({0, -1}),
        )
