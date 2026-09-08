"""Configurable Database Management Committee governance."""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional, Tuple

import config
from att.compat import ATTManager
from att.runtime import (
    ATTEventLoopRunner,
    run_att_sync,
    run_team_discussion,
    select_designated_answer,
)
from workflow_components.parsing import extract_json_payload
from workflow_components.resources import get_ai_resource, get_message


class DatabaseManagementCommittee:
    """Audit selected database operations before they mutate story state."""

    def __init__(
        self,
        att_manager: ATTManager,
        *,
        enabled: Optional[bool] = None,
        scopes: Optional[Dict[str, bool]] = None,
        failure_policy: Optional[str] = None,
        failure_policies: Optional[Dict[str, str]] = None,
        runner: Optional[ATTEventLoopRunner] = None,
        stable_agent_ids: Optional[Dict[str, str]] = None,
    ):
        self.manager = att_manager
        self.preset = att_manager.get_preset("database_management")
        self.enabled = (
            config.DATABASE_AUDIT_ENABLED if enabled is None else bool(enabled)
        )
        self.scopes = dict(config.DATABASE_AUDIT_SCOPES)
        if scopes:
            self.scopes.update(scopes)
        self.failure_policy = (
            failure_policy or config.DATABASE_AUDIT_FAILURE_POLICY
        ).strip().lower()
        if self.failure_policy not in {"allow", "deny"}:
            raise ValueError(get_message("error.database_audit_policy"))
        self.failure_policies = dict(config.DATABASE_AUDIT_FAILURE_POLICIES)
        if failure_policies:
            self.failure_policies.update(
                {key: str(value).strip().lower() for key, value in failure_policies.items()}
            )
        invalid = {
            key: value
            for key, value in self.failure_policies.items()
            if key not in self.scopes or value not in {"allow", "deny"}
        }
        if invalid:
            raise ValueError(get_message("error.database_audit_policy"))
        self.logger = logging.getLogger("DatabaseManagementCommittee")
        self.runner = runner
        self.stable_agent_ids = dict(stable_agent_ids or {})
        self._team = None

    def should_audit(self, scope: str) -> bool:
        return self.enabled and bool(self.scopes.get(scope, False))

    def _failure_result(self, scope: str, message: str) -> Tuple[bool, str]:
        policy = self.failure_policies.get(scope, self.failure_policy)
        return policy == "allow", message

    def _create_team(self):
        return run_att_sync(self._create_team_on_runner, self.runner)

    def _create_team_on_runner(self):
        if self._team is not None and not getattr(self.manager, "_closing", False):
            if getattr(self._team, "tools", None) is not None:
                self._team.tools.clear()
            return self._team
        role_names = [name for name, _ in self.preset["roles"]]
        registry = getattr(self.manager.config, "model_registry", {}) or {}
        if self.stable_agent_ids:
            expected_ids = {self.stable_agent_ids[name] for name in role_names}
            for team in getattr(self.manager, "teams", {}).values():
                if (
                    getattr(team, "preset_name", None) == "database_management"
                    and {member.agent_id for member in team.members} == expected_ids
                ):
                    self._team = team
                    if getattr(team, "tools", None) is not None:
                        team.tools.clear()
                    return team
            self._team = self.manager.create_agent_team(
                creator=self.manager.root_ai,
                member_count=len(role_names),
                existing_member_ids=[self.stable_agent_ids[name] for name in role_names],
                preset_name="database_management",
                system_instructions=self.preset["system_instructions"],
            )
        else:
            # Without episodic memory, committee Agents are intentionally
            # session-local; do not revive an older persisted committee team.
            self._team = self.manager.create_agent_team(
                creator=self.manager.root_ai,
                member_count=len(role_names),
                roles_and_presets=self.preset["roles"],
                roles_and_models={
                    name: registry[name]
                    for name in role_names
                    if registry.get(name)
                },
                preset_name="database_management",
                system_instructions=self.preset["system_instructions"],
            )
        # The governance team decides from the submitted full payload. It must
        # not call the governed SQL tool (or delegation tools) and recursively
        # trigger its own auditor while its discussion lock is held.
        if getattr(self._team, "tools", None) is not None:
            self._team.tools.clear()
        return self._team

    def audit_operation(
        self,
        scope: str,
        operation: str,
        payload: Any,
        chapter_num: Optional[int] = None,
    ) -> Tuple[bool, str]:
        if not self.should_audit(scope):
            return True, get_ai_resource("status.database_audit_disabled", scope=scope)

        team = self._create_team()
        prompt = get_ai_resource(
            "prompt.att.database",
            scope=scope,
            operation=operation,
            chapter_num=chapter_num,
            payload=json.dumps(payload, ensure_ascii=False, default=str),
        )
        try:
            if self.runner is None:
                discussion_result = run_team_discussion(
                    self.manager, team, prompt, rounds=1
                )
            else:
                discussion_result = run_team_discussion(
                    self.manager,
                    team,
                    prompt,
                    rounds=1,
                    runner=self.runner,
                )
            answer = select_designated_answer(
                discussion_result,
                team,
                "Transaction_Planner",
                "database_management",
                getattr(config, "COMMITTEE_PARTIAL_POLICIES", {}).get(
                    "database_management", "reject"
                ),
            )
            decision = extract_json_payload(answer or "")
            if not isinstance(decision, dict) or not isinstance(
                decision.get("approved"), bool
            ):
                return self._failure_result(
                    scope,
                    get_ai_resource("error.database_audit_invalid")
                )
            reason = str(decision.get("reason") or get_ai_resource("status.no_reason"))
            approved = bool(decision["approved"])
            self.logger.info(get_message(
                "status.database_audit_decision",
                scope=scope,
                operation=operation,
                approved=approved,
                reason=reason,
            ))
            return approved, reason
        except Exception as exc:
            self.logger.warning(get_message(
                "status.database_audit_failed",
                scope=scope,
                operation=operation,
                error=exc,
            ))
            return self._failure_result(
                scope,
                get_ai_resource("error.database_audit_execution", error=exc)
            )

    def audit_query(self, sql_command: str) -> Tuple[bool, str]:
        return self.audit_operation("att_sql", "direct_sql", {"sql": sql_command})

    def audit_batch_transaction(
        self,
        data: Dict[str, Any],
        chapter_num: Optional[int],
        *,
        scope: str = "chapter_fact_batches",
        operation: str = "chapter_fact_batch",
    ) -> Tuple[bool, str]:
        return self.audit_operation(scope, operation, data, chapter_num)
