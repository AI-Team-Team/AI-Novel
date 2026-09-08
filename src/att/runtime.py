"""Synchronous lifecycle helpers for ATT's asynchronous public API."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Callable, Coroutine, Optional, TypeVar

from att.compat import AgentTurnStatus, DiscussionResult, DiscussionStatus
from workflow_components.resources import get_message


T = TypeVar("T")


class ATTEventLoopRunner:
    """Own one event loop for the complete lifetime of an ATT manager."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._closed = False
        self._thread = threading.Thread(
            target=self._run_loop,
            name="ai-novel-att-event-loop",
            daemon=True,
        )
        self._thread.start()
        self._started.wait()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._started.set()
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            self._loop.close()

    def _submit(self, coroutine: Coroutine[Any, Any, T]) -> Future[T]:
        if self._closed:
            coroutine.close()
            raise RuntimeError(get_message("runtime.att_runner_closed"))
        if threading.current_thread() is self._thread:
            coroutine.close()
            raise RuntimeError(get_message("runtime.att_runner_self_block"))
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    def run(self, factory: Callable[[], Coroutine[Any, Any, T]]) -> T:
        async def invoke() -> T:
            return await factory()

        return self._submit(invoke()).result()

    def call(self, callback: Callable[[], T]) -> T:
        async def invoke() -> T:
            return callback()

        return self._submit(invoke()).result()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._loop.call_soon_threadsafe(self._loop.stop)
        if threading.current_thread() is not self._thread:
            self._thread.join()


def run_att_async(
    factory: Callable[[], Coroutine[Any, Any, T]],
    runner: Optional[ATTEventLoopRunner] = None,
) -> T:
    """Run an ATT coroutine from synchronous code, including inside an active loop.

    The coroutine is created in the loop that executes it.  This avoids binding
    ATT's asyncio primitives to the caller's already-running event loop.
    """

    if runner is not None:
        return runner.run(factory)

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


def run_att_sync(
    callback: Callable[[], T],
    runner: Optional[ATTEventLoopRunner] = None,
) -> T:
    """Run an ATT state mutation on its owned event-loop thread."""

    if runner is None:
        return callback()
    return runner.call(callback)


class ATTDiscussionPolicyError(RuntimeError):
    """Raised when a structured ATT result cannot authorize workflow output."""


def run_team_discussion(
    manager: Any,
    team: Any,
    prompt: str,
    rounds: int,
    runner: Optional[ATTEventLoopRunner] = None,
) -> DiscussionResult:
    if rounds < 1:
        raise ValueError(get_message("validation.att_rounds"))

    async def execute() -> DiscussionResult:
        episodic = getattr(getattr(manager, "config", None), "episodic_memory", None)
        if getattr(episodic, "enabled", False):
            try:
                await manager.flush_memory_indexing()
            except Exception as exc:
                manager.logger.warning(
                    get_message("runtime.att_memory_flush_failed", error=exc)
                )
            else:
                failures = manager.list_memory_index_failures()
                if failures:
                    segment_ids = [str(item.segment_id) for item in failures]
                    manager.logger.warning(
                        get_message(
                            "runtime.att_memory_failures",
                            count=len(segment_ids),
                            segments=", ".join(segment_ids),
                        )
                    )
        return await manager.execute_team_discussion_detailed(
            team, prompt, rounds=rounds
        )

    return run_att_async(
        execute,
        runner,
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


def close_att_manager(
    manager: Any,
    runner: Optional[ATTEventLoopRunner] = None,
) -> None:
    if manager is None or getattr(manager, "_closed", False):
        if runner is not None:
            runner.close()
        return

    async def save_and_close() -> None:
        save_error = None
        try:
            episodic = getattr(getattr(manager, "config", None), "episodic_memory", None)
            memory_service = getattr(manager, "_memory", None)
            stop_indexing = getattr(memory_service, "close", None)
            if getattr(episodic, "enabled", False) and callable(stop_indexing):
                # Quiesce workers before the final full snapshot. Doing this
                # before ATT marks the whole manager as closing lets its worker
                # cancellation persist processing rows as pending.
                await stop_indexing()
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

    try:
        run_att_async(save_and_close, runner)
    finally:
        if runner is not None:
            runner.close()
