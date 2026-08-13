"""Synchronous lifecycle helpers for ATT's asynchronous public API."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Coroutine, TypeVar

from workflow_components.resources import get_message


T = TypeVar("T")


def run_att_async(factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
    """Run an ATT coroutine from synchronous code, including inside an active loop.

    The coroutine is created in the loop that executes it.  This avoids binding
    ATT's asyncio primitives to the caller's already-running event loop.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    result: list[T] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            result.append(asyncio.run(factory()))
        except BaseException as exc:  # Propagate ATT cancellation and failures.
            errors.append(exc)

    thread = threading.Thread(target=worker, name="ai-novel-att-bridge")
    thread.start()
    thread.join()
    if errors:
        raise errors[0]
    return result[0]


def run_team_discussion(manager: Any, team: Any, prompt: str, rounds: int) -> str:
    if rounds < 1:
        raise ValueError(get_message("validation.att_rounds"))
    return run_att_async(
        lambda: manager.execute_team_discussion(team, prompt, rounds=rounds)
    )


def close_att_manager(manager: Any) -> None:
    if manager is None or getattr(manager, "_closed", False):
        return

    async def save_and_close() -> None:
        save_error = None
        try:
            await manager.save_state(full=True)
        except BaseException as exc:
            save_error = exc
        try:
            await manager.close()
        except BaseException:
            if save_error is None:
                raise
        if save_error is not None:
            raise save_error

    run_att_async(save_and_close)
