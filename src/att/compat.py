"""Validated imports for the AI-Novel/ATT public API contract."""

from __future__ import annotations

import config
from workflow_components.bootstrap_messages import (
    ConfigurationError,
    get_bootstrap_message,
)


try:
    from ai_team_team import (
        ATTConfig,
        ATTManager,
        Agent,
        AgentTurnStatus,
        DiscussionResult,
        DiscussionStatus,
        GatedFileReader,
        LLMResponse,
        ToolCall,
        TurnFailurePolicyConfig,
    )
    from ai_team_team.core import ManagerDefaultClientAdapter
except (ImportError, AttributeError) as exc:
    raise ConfigurationError(
        get_bootstrap_message(
            config.project_root,
            config.LANGUAGE,
            "config.att_dependency_outdated",
        )
    ) from exc


__all__ = [
    "ATTConfig",
    "ATTManager",
    "Agent",
    "AgentTurnStatus",
    "DiscussionResult",
    "DiscussionStatus",
    "GatedFileReader",
    "LLMResponse",
    "ManagerDefaultClientAdapter",
    "ToolCall",
    "TurnFailurePolicyConfig",
]
