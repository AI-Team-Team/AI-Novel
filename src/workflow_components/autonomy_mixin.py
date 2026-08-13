import logging
import os
import sqlite3
from typing import Dict, Any, Optional, Tuple, List, Union

import config
from ai_team_team import ATTManager, Agent, ATTConfig, GatedFileReader
from ai_team_team.core import ManagerDefaultClientAdapter
from att.db_committee import DatabaseManagementCommittee
from att.runtime import close_att_manager, run_att_async, run_team_discussion
from llm_client import LLMClient
from workflow_components.resources import get_ai_resource, get_message

class AutonomyWorkflowMixin:
    """
    Mixin integrating ATTManager and DatabaseManagementCommittee
    into the WorkflowManager, providing the new AI Team Team (ATT)
    topology routing and tool execution.
    """
    def initialize_autonomy(self):
        """Initializes the ATT core manager, gated reader, and DB committee."""
        self.gated_reader = GatedFileReader(
            large_threshold_kb=getattr(config, "LARGE_FILE_THRESHOLD_KB", 50),
            max_chunk=getattr(config, "MAX_CHUNK_LINES", 100)
        )
        
        # 1. Build role-to-model registry mapping role names to config model names
        role_to_model = {
            "Architect": config.models_section.get("architect_model"),
            "Planner": config.models_section.get("planner_model"),
            "Writer": config.models_section.get("writer_model"),
            "Critic": config.models_section.get("critic_model"),
            "Scanner": config.models_section.get("scanner_model"),
            
            # Conflict Resolution
            "Historian_Critic": config.models_section.get("critic_model"),
            "Prose_Scanner": config.models_section.get("scanner_model"),
            "Consensus_Planner": config.models_section.get("planner_model"),
            
            # Database Management
            "Security_Officer": config.models_section.get("critic_model"),
            "Schema_Auditor": config.models_section.get("critic_model"),
            "Transaction_Planner": config.models_section.get("planner_model"),
            
            # Planning
            "Continuity_Auditor": config.models_section.get("critic_model"),
            "Structural_Planner": config.models_section.get("planner_model"),
            "Reviewer_Arbitrator": config.models_section.get("planner_model"),
            
            # Editorial
            "Style_Critic": config.models_section.get("critic_model"),
            "Creative_Writer": config.models_section.get("writer_model"),
            "Editor_In_Chief": config.models_section.get("critic_model"),
            
            # World Bible
            "Lore_Architect": config.models_section.get("architect_model"),
            "Narrative_Critic": config.models_section.get("critic_model"),
            "World_Arbitrator": config.models_section.get("architect_model"),
            
            # Plot Outline
            "Narrative_Arc_Planner": config.models_section.get("planner_model"),
            "Continuity_Critic": config.models_section.get("critic_model"),
            "Arc_Arbitrator": config.models_section.get("planner_model"),
        }
        self.att_roles_and_models = {
            role: model for role, model in role_to_model.items() if model
        }

        # 1. Build configuration object
        enable_autonomy = getattr(config, "ENABLE_AUTONOMY_SUITE", True)
        config_obj = ATTConfig(
            enable_dynamic_delegation=getattr(config, "ENABLE_DYNAMIC_DELEGATION", False) if enable_autonomy else False,
            max_delegation_depth=getattr(config, "MAX_DELEGATION_DEPTH", 2),
            min_subagent_team_size=getattr(config, "MIN_SUBAGENT_TEAM_SIZE", 3),
            subagent_discussion_rounds=getattr(config, "SUBAGENT_DISCUSSION_ROUNDS", 1),
            react_max_steps=getattr(config, "REACT_MAX_STEPS", 5),
            inbox_summarize_threshold_chars=getattr(config, "INBOX_SUMMARIZE_THRESHOLD_CHARS", 1500),
            model_registry=role_to_model,
            enable_memory_compression=getattr(config, "ENABLE_MEMORY_COMPRESSION", True),
            max_memory_turns=getattr(config, "MAX_MEMORY_TURNS", 20),
            failover_policy=getattr(config, "FAILOVER_POLICY", "auto"),
            enable_emergency_wakeup=getattr(config, "ENABLE_EMERGENCY_WAKEUP", True) if enable_autonomy else False,
            emergency_discussion_rounds=getattr(config, "EMERGENCY_DISCUSSION_ROUNDS", 1),
            tool_calling_mode=getattr(config, "TOOL_CALLING_MODE", "auto"),
            max_tool_rounds=getattr(config, "MAX_TOOL_ROUNDS", 5),
            workspace_root=os.path.dirname(os.path.abspath(config.DB_PATH))
        )

        # 2. Instantiate root agent and ATTManager
        root_agent = Agent(
            name="Root_AI_Level_0",
            role="Architect",
        )
        att_db_path = getattr(
            config,
            "ATT_STATE_DB_PATH",
            os.path.join(config.PROCESS_DIR, "att_state_v6.db"),
        )
        restore_existing = os.path.exists(att_db_path) and os.path.getsize(att_db_path) > 0
        self.att_manager = ATTManager(root_ai=root_agent, config=config_obj, db_path=att_db_path)

        # Cache LLM Client mapping by registered model config name and by simple role name
        self.llm_clients = {
            config.models_section.get("architect_model"): getattr(self, "architect_client", None),
            config.models_section.get("planner_model"): getattr(self, "planner_client", None),
            config.models_section.get("writer_model"): getattr(self, "writer_client", None),
            config.models_section.get("critic_model"): getattr(self, "critic_client", None),
            config.models_section.get("scanner_model"): getattr(self, "scanner_client", None),

            "architect": getattr(self, "architect_client", None),
            "planner": getattr(self, "planner_client", None),
            "writer": getattr(self, "writer_client", None),
            "critic": getattr(self, "critic_client", None),
            "scanner": getattr(self, "scanner_client", None),
        }

        # Register a global generator callback handler
        async def generator_handler(
            model_name: str,
            prompt: Union[str, List[Dict[str, Any]]],
            system_instruction: Optional[str] = None,
            temperature: float = 0.3,
            require_json: bool = False,
            **generation_options: Any,
        ) -> str:
            client = None
            instr = (system_instruction or "").lower()
            if instr:
                if "architect" in instr or "lore" in instr:
                    client = self.llm_clients.get("architect")
                elif any(k in instr for k in ["planner", "arbitrator", "arc", "consensus", "transaction", "reviewer"]):
                    client = self.llm_clients.get("planner")
                elif "writer" in instr or "creative" in instr:
                    client = self.llm_clients.get("writer")
                elif any(k in instr for k in ["critic", "auditor", "security", "schema", "historian", "style", "editor", "chief"]):
                    client = self.llm_clients.get("critic")
                elif "scanner" in instr or "prose" in instr:
                    client = self.llm_clients.get("scanner")

            if not client:
                client = self.llm_clients.get(model_name)
            if not client:
                lower_name = model_name.lower()
                if "architect" in lower_name or "lore" in lower_name:
                    client = self.llm_clients.get("architect")
                elif any(k in lower_name for k in ["planner", "arbitrator", "arc", "consensus", "transaction", "reviewer"]):
                    client = self.llm_clients.get("planner")
                elif "writer" in lower_name or "creative" in lower_name:
                    client = self.llm_clients.get("writer")
                elif any(k in lower_name for k in ["critic", "auditor", "security", "schema", "historian", "style", "editor", "chief"]):
                    client = self.llm_clients.get("critic")
                elif "scanner" in lower_name or "prose" in lower_name:
                    client = self.llm_clients.get("scanner")
                
                if not client:
                    client = self.llm_clients.get("critic") or \
                             self.llm_clients.get("planner") or \
                             self.llm_clients.get("architect") or \
                             self.llm_clients.get("writer") or \
                             self.llm_clients.get("scanner")
                
                if client:
                    self.llm_clients[model_name] = client

            if not client:
                model_info = config.MODEL_REGISTRY.get(model_name)
                if not model_info:
                    default_key = config.models_section.get("default_model")
                    model_info = config.MODEL_REGISTRY.get(default_key)
                if model_info:
                    client = LLMClient(model_config=model_info)
                    self.llm_clients[model_name] = client
                else:
                    raise ValueError(get_message("validation.model_registry", model_name=model_name))

            # Inspect signature to construct correct arguments for client.generate safely
            import inspect
            sig = inspect.signature(client.generate)
            has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
            
            kwargs = {}
            if "system_instruction" in sig.parameters or has_var_keyword:
                kwargs["system_instruction"] = system_instruction
            if "temperature" in sig.parameters or has_var_keyword:
                kwargs["temperature"] = temperature
            if "require_json" in sig.parameters or has_var_keyword:
                kwargs["require_json"] = require_json
            for name, value in generation_options.items():
                if name in sig.parameters or has_var_keyword:
                    kwargs[name] = value
            
            try:
                return client.generate(prompt, **kwargs)
            except TypeError:
                return client.generate(prompt)

        self.att_manager.register_generator_handler(generator_handler)
        self.att_manager.root_ai.llm_client = ManagerDefaultClientAdapter(
            self.att_manager
        )
        self.att_manager.root_ai._model_alias = "default"

        if restore_existing:
            run_att_async(lambda: self.att_manager.load_state(att_db_path))

        # Refresh model metadata after restoration so configuration changes apply.
        for key, model_info in config.MODEL_REGISTRY.items():
            self.att_manager.register_model(key, model_info)

        # 3. Register custom presets
        PRESETS = {
            "conflict_resolution": {
                "description": get_ai_resource("committee.conflict.description"),
                "system_instructions": get_ai_resource("committee.conflict.system"),
                "roles": [
                    ("Historian_Critic", get_ai_resource("committee.conflict.role.historian")),
                    ("Prose_Scanner", get_ai_resource("committee.conflict.role.scanner")),
                    ("Consensus_Planner", get_ai_resource("committee.conflict.role.planner")),
                ],
            },
            "database_management": {
                "description": get_ai_resource("committee.database.description"),
                "system_instructions": get_ai_resource("committee.database.system"),
                "roles": [
                    ("Security_Officer", get_ai_resource("committee.database.role.security")),
                    ("Schema_Auditor", get_ai_resource("committee.database.role.schema")),
                    ("Transaction_Planner", get_ai_resource("committee.database.role.transaction")),
                ],
            },
            "planning": {
                "description": get_ai_resource("committee.planning.description"),
                "system_instructions": get_ai_resource("committee.planning.system"),
                "roles": [
                    ("Continuity_Auditor", get_ai_resource("committee.planning.role.continuity")),
                    ("Structural_Planner", get_ai_resource("committee.planning.role.structure")),
                    ("Reviewer_Arbitrator", get_ai_resource("committee.planning.role.arbitrator")),
                ],
            },
            "editorial": {
                "description": get_ai_resource("committee.editorial.description"),
                "system_instructions": get_ai_resource("committee.editorial.system"),
                "roles": [
                    ("Style_Critic", get_ai_resource("committee.editorial.role.style")),
                    ("Creative_Writer", get_ai_resource("committee.editorial.role.writer")),
                    ("Editor_In_Chief", get_ai_resource("committee.editorial.role.chief")),
                ],
            },
            "world_bible": {
                "description": get_ai_resource("committee.world.description"),
                "system_instructions": get_ai_resource("committee.world.system"),
                "roles": [
                    ("Lore_Architect", get_ai_resource("committee.world.role.architect")),
                    ("Narrative_Critic", get_ai_resource("committee.world.role.critic")),
                    ("World_Arbitrator", get_ai_resource("committee.world.role.arbitrator")),
                ],
            },
            "plot_outline": {
                "description": get_ai_resource("committee.plot.description"),
                "system_instructions": get_ai_resource("committee.plot.system"),
                "roles": [
                    ("Narrative_Arc_Planner", get_ai_resource("committee.plot.role.planner")),
                    ("Continuity_Critic", get_ai_resource("committee.plot.role.critic")),
                    ("Arc_Arbitrator", get_ai_resource("committee.plot.role.arbitrator")),
                ],
            },
        }
        for name, preset_data in PRESETS.items():
            self.att_manager.register_preset(
                name=name,
                description=preset_data["description"],
                system_instructions=preset_data["system_instructions"],
                roles=preset_data["roles"]
            )

        # 4. Establish the 3-AI Database Management Committee
        self.db_committee = DatabaseManagementCommittee(self.att_manager)
        
        # Register the Database Management Committee on MemoryManager safely
        memory = getattr(self, "memory", None)
        if memory is not None:
            memory.set_db_committee(self.db_committee)

        # 5. Register the centralized tools context
        self.att_manager.register_tools_context({
            "memory": memory,
            "embedding_client": getattr(self, "embedding_client", None),
            "gated_reader": self.gated_reader,
            "att_manager": self.att_manager,
            "db_committee": self.db_committee
        })

        # 6. Register custom tools
        def query_sqlite(sql_command: str) -> str:
            """Queries the SQLite database directly. Arguments: sql_command (str)"""
            if not self.memory:
                return get_ai_resource("tool.database_unavailable")
            try:
                # ATT executes synchronous tools in worker threads. Use an
                # operation-local connection instead of MemoryManager's
                # thread-bound cursor, and let the context manager commit or
                # roll back the standalone statement deterministically.
                with sqlite3.connect(self.memory.db_path) as connection:
                    cursor = connection.execute(sql_command)
                    if cursor.description is not None:
                        return str(cursor.fetchall())
                    affected = max(0, int(cursor.rowcount or 0))
                    return get_ai_resource(
                        "tool.sqlite_write_result",
                        rows=affected,
                    )
            except Exception as e:
                return get_ai_resource("tool.sqlite_error", error=e)

        def search_faiss(query_text: str, limit: int = 3) -> str:
            """Performs semantic vector search on FAISS indices. Arguments: query_text (str), limit (int)"""
            if not self.embedding_client:
                return get_ai_resource("tool.embedding_unavailable")
            if not self.memory:
                return get_ai_resource("tool.database_unavailable")
            try:
                emb = self.embedding_client.get_embedding(query_text)
                if not emb:
                    return get_ai_resource("tool.embedding_failed")
                hits = self.memory.search_semantic(emb, k=int(limit))
                return str(hits)
            except Exception as e:
                return get_ai_resource("tool.faiss_error", error=e)

        def read_file_chunk(path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
            """Reads a specific paginated chunk of a file. Arguments: path (str), start_line (int), end_line (int)"""
            try:
                start_line = int(start_line)
                if end_line is not None:
                    end_line = int(end_line)
                return self.gated_reader.read_file(path, start_line, end_line)
            except Exception as e:
                return get_ai_resource("tool.file_chunk_error", error=e)

        def read_file_tail(path: str, line_count: int = 50) -> str:
            """Reads the last line_count lines of a file or log. Arguments: path (str), line_count (int)"""
            try:
                line_count = int(line_count)
                return self.gated_reader.read_file_tail(path, line_count)
            except Exception as e:
                return get_ai_resource("tool.file_tail_error", error=e)

        tools_enabled = enable_autonomy and getattr(
            config, "ENABLE_AUTONOMOUS_QUERIES", False
        )
        if tools_enabled:
            self.att_manager.register_tool("query_sqlite", get_ai_resource("tool.query_sqlite.description"), query_sqlite)
            self.att_manager.register_tool("search_faiss", get_ai_resource("tool.search_faiss.description"), search_faiss)
            self.att_manager.register_tool("read_file_chunk", get_ai_resource("tool.read_chunk.description"), read_file_chunk)
            self.att_manager.register_tool("read_file_tail", get_ai_resource("tool.read_tail.description"), read_file_tail)

        # 7. Register the Tool Auditor hook for query_sqlite
        def audit_sqlite_query(*args, **kwargs) -> Tuple[bool, str]:
            sql_command = args[0] if args else kwargs.get("sql_command")
            if not sql_command:
                return False, get_ai_resource("tool.sql_missing")
            return self.db_committee.audit_query(sql_command)

        if tools_enabled:
            self.att_manager.register_tool_auditor("query_sqlite", audit_sqlite_query)

        # 8. Wire status change and activity handlers to update local ConsoleDashboard screen
        dashboard = getattr(self, "dashboard", None)
        if dashboard is not None:
            self.att_manager.on_status_change = lambda name, status: dashboard.refresh()
            self.att_manager.on_activity_added = lambda name, act_type, content: dashboard.add_activity(name, act_type, content)

        # 9. Logger callback to write logs to files via DiscussionLogger
        def handle_log_append(team_id, title, content, chapter_num):
            discussion_logger = self._discussion_logger()
            num3_func = getattr(self, "num3", None)
            discussion_logger.append_att(
                team_id=team_id,
                title=title,
                content=content,
                chapter_num=chapter_num,
                num3_func=num3_func
            )
        self.att_manager.on_log_append = handle_log_append

    def _create_att_team(self, preset_name: str, chapter_num: Optional[int] = None):
        """Create a committee using ATT's current explicit role/model routing."""

        preset = self.att_manager.get_preset(preset_name)
        role_names = [role_name for role_name, _ in preset["roles"]]
        team = self.att_manager.create_agent_team(
            creator=self.att_manager.root_ai,
            member_count=len(role_names),
            roles_and_presets=preset["roles"],
            roles_and_models={
                role_name: self.att_roles_and_models[role_name]
                for role_name in role_names
                if role_name in self.att_roles_and_models
            },
            preset_name=preset_name,
            system_instructions=preset["system_instructions"],
        )
        if chapter_num is not None:
            team.chapter_num = chapter_num
        return team

    def _execute_att_discussion(self, team: Any, prompt: str, rounds: int) -> str:
        return run_team_discussion(self.att_manager, team, prompt, rounds)

    def _audit_database_batch(
        self,
        scope: str,
        operation: str,
        payload: Dict[str, Any],
        chapter_num: Optional[int],
    ) -> None:
        committee = getattr(self, "db_committee", None)
        if committee is None or not committee.should_audit(scope):
            return
        approved, reason = committee.audit_batch_transaction(
            payload,
            chapter_num,
            scope=scope,
            operation=operation,
        )
        if not approved:
            raise PermissionError(
                get_message(
                    "error.database_audit_denied", operation=operation, reason=reason
                )
            )

    def close_autonomy(self) -> None:
        close_att_manager(getattr(self, "att_manager", None))

    def get_autonomy_tools(self, caller_node: Any) -> Dict[str, Any]:
        """Assembles the tools map bound to a specific AgentTeam or Member."""
        if hasattr(caller_node, "tools"):
            return caller_node.tools
        return {}
