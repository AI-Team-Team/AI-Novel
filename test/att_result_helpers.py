"""Factories for ATT structured-result test fixtures."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Mapping, Sequence

from ai_team_team import (
    AgentTurnResult,
    AgentTurnStatus,
    AuditResult,
    AuditStatus,
    DiscussionResult,
    DiscussionRoundResult,
    DiscussionStatus,
    OperationalStatus,
)


def make_team(*member_names: str, team_id: str = "AT-test"):
    return SimpleNamespace(
        team_id=team_id,
        members=[
            SimpleNamespace(agent_id=f"agent-{index}", name=name, role=name)
            for index, name in enumerate(member_names, start=1)
        ],
    )


def make_discussion_result(
    team,
    answers: Mapping[str, str],
    *,
    status: DiscussionStatus = DiscussionStatus.COMPLETED,
    incomplete: Sequence[str] = (),
) -> DiscussionResult:
    incomplete_names = set(incomplete)
    turns = []
    transcript_lines = []
    for member in team.members:
        answer = answers.get(member.name)
        is_incomplete = member.name in incomplete_names
        turns.append(
            AgentTurnResult(
                agent_id=member.agent_id,
                team_id=team.team_id,
                discussion_id="discussion-test",
                round_number=1,
                status=(
                    AgentTurnStatus.INCOMPLETE
                    if is_incomplete
                    else AgentTurnStatus.COMPLETED
                ),
                answer=None if is_incomplete else answer,
                error_kind="test_incomplete" if is_incomplete else None,
                reason="Test fixture incomplete turn." if is_incomplete else None,
            )
        )
        if answer is not None:
            transcript_lines.append(f"{member.name}: Final Answer: {answer}")

    degraded = status is DiscussionStatus.PARTIAL or bool(incomplete_names)
    return DiscussionResult(
        team_id=team.team_id,
        discussion_id="discussion-test",
        status=status,
        transcript="\n".join(transcript_lines),
        rounds=[DiscussionRoundResult(round_number=1, turns=turns)],
        audit=AuditResult(
            status=AuditStatus.UNKNOWN,
            reason="Test fixture does not run an audit.",
            operational_status=(
                OperationalStatus.DEGRADED if degraded else OperationalStatus.HEALTHY
            ),
            operational_reason=(
                "Fixture contains incomplete turns."
                if degraded
                else "All fixture turns completed."
            ),
        ),
    )
