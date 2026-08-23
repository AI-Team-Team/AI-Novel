"""Synchronous lifecycle helpers for ATT's asynchronous public API."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Coroutine, TypeVar

from att.compat import AgentTurnStatus, DiscussionResult, DiscussionStatus
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


class ATTDiscussionPolicyError(RuntimeError):
    """Raised when a structured ATT result cannot authorize workflow output."""


def run_team_discussion(
    manager: Any,
    team: Any,
    prompt: str,
    rounds: int,
) -> DiscussionResult:
    if rounds < 1:
        raise ValueError(get_message("validation.att_rounds"))
    return run_att_async(
        lambda: manager.execute_team_discussion_detailed(
            team, prompt, rounds=rounds
        )
    )


def select_designated_answer(
    result: DiscussionResult,
    team: Any,
    member_name: str,
    committee: str,
    policy: str,
) -> str:
    """Select the designated final-round answer under a partial-result policy."""

    normalized_policy = str(policy).strip().lower()
    if normalized_policy not in {"reject", "accept_designated_member"}:
        raise ATTDiscussionPolicyError(
            get_message(
                "runtime.att_partial_policy_invalid",
                committee=committee,
                policy=normalized_policy,
            )
        )
    if result.status == DiscussionStatus.PARTIAL and normalized_policy == "reject":
        raise ATTDiscussionPolicyError(
            get_message(
                "runtime.att_partial_rejected",
                committee=committee,
                policy=normalized_policy,
            )
        )

    target_ids = {
        str(getattr(member, "agent_id", ""))
        for member in list(getattr(team, "members", []) or [])
        if (
            str(getattr(member, "name", "")) == member_name
            or str(getattr(member, "name", "")).startswith(f"{member_name}_")
            or str(getattr(member, "role", "")) == member_name
        )
    }
    final_round = result.rounds[-1] if result.rounds else None
    if final_round is not None:
        for turn in reversed(final_round.turns):
            if str(turn.agent_id) not in target_ids:
                continue
            if turn.status == AgentTurnStatus.COMPLETED and (turn.answer or "").strip():
                return str(turn.answer).strip()

    raise ATTDiscussionPolicyError(
        get_message(
            "runtime.att_designated_incomplete",
            committee=committee,
            member=member_name,
        )
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
