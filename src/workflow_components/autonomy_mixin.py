import logging
import os
from typing import Dict, Any, Optional, Tuple

import config
from ai_team_team import ATTManager, Agent, ATTConfig, GatedFileReader
from att.db_committee import DatabaseManagementCommittee
from llm_client import LLMClient

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

        # 1. Build configuration object
        config_obj = ATTConfig(
            enable_dynamic_delegation=getattr(config, "ENABLE_DYNAMIC_DELEGATION", False),
            max_delegation_depth=getattr(config, "MAX_DELEGATION_DEPTH", 2),
            min_subagent_team_size=getattr(config, "MIN_SUBAGENT_TEAM_SIZE", 3),
            subagent_discussion_rounds=getattr(config, "SUBAGENT_DISCUSSION_ROUNDS", 1),
            react_max_steps=getattr(config, "REACT_MAX_STEPS", 5),
            inbox_summarize_threshold_chars=getattr(config, "INBOX_SUMMARIZE_THRESHOLD_CHARS", 1500),
            model_registry=role_to_model
        )

        # 2. Instantiate root agent and ATTManager
        root_agent = Agent(name="Root_AI_Level_0", role="Architect")
        att_db_path = os.path.join(config.PROCESS_DIR, "att_state.db")
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

        # Register models on the ATTManager
        for key, model_info in config.MODEL_REGISTRY.items():
            self.att_manager.register_model(key, model_info)

        # Register a global generator callback handler
        async def generator_handler(
            model_name: str,
            prompt: str,
            system_instruction: Optional[str] = None,
            temperature: float = 0.3,
            require_json: bool = False
        ) -> str:
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
                    raise ValueError(f"Model key '{model_name}' not found in registry.")

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
            
            try:
                return client.generate(prompt, **kwargs)
            except TypeError:
                return client.generate(prompt)

        self.att_manager.register_generator_handler(generator_handler)

        def execute_team_discussion_sync(team, prompt, rounds=2):
            import asyncio
            coro = self.att_manager.execute_team_discussion(team, prompt, rounds=rounds)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                import threading
                result = []
                exception = []
                def _run():
                    try:
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        res = new_loop.run_until_complete(coro)
                        result.append(res)
                    except Exception as e:
                        exception.append(e)
                    finally:
                        new_loop.close()
                t = threading.Thread(target=_run)
                t.start()
                t.join()
                if exception:
                    raise exception[0]
                return result[0]
            else:
                return asyncio.run(coro)

        self.att_manager.execute_team_discussion_sync = execute_team_discussion_sync

        
        # 3. Register custom presets
        PRESETS = {
            "conflict_resolution": {
                "description": "Multi-Agent Narrative Consensus Panel to resolve blocking database fact contradictions.",
                "system_instructions": (
                    "You are a member of the Multi-Agent Narrative Consensus Panel.\n"
                    "Your collective goal is to debate and resolve the contradiction between the incoming scanned facts "
                    "and the existing database continuity facts. In the final round, you must decide whether to 'keep_existing' "
                    "or 'apply_incoming' and provide a narrative compromise to bridge the gap."
                ),
                "roles": [
                    ("Historian_Critic", "Defends database continuity, world rule integrity, and established timeline facts."),
                    ("Prose_Scanner", "Defends the newly generated prose's creative choices, pacing, and new details."),
                    ("Consensus_Planner", "Moderates the debate and synthesizes the final JSON decision choosing exactly one action.")
                ]
            },
            
            "database_management": {
                "description": "Database Management Committee to audit all direct SQL queries and transactions.",
                "system_instructions": (
                    "You are a member of the Database Management Committee (数据库管理委员会).\n"
                    "Your collective goal is to audit direct SQLite queries or proposed batch commits against the novel's global rules "
                    "and structural constraints. You must ensure no rules are broken, no invalid character resurrections occur, "
                    "and no SQL injections or destructive operations are executed. You must approve or reject the query/transaction."
                ),
                "roles": [
                    ("Security_Officer", "Audits queries for security, safety, and unauthorized modifications."),
                    ("Schema_Auditor", "Checks queries for structural consistency, table constraints, and field integrity."),
                    ("Transaction_Planner", "Evaluates the overall transaction intent, timeline consistency, and makes the final decision.")
                ]
            },

            "planning": {
                "description": "Chapter Planning Committee to outline and write chapter guides.",
                "system_instructions": (
                    "You are a member of the Chapter Planning Committee.\n"
                    "Your objective is to outline the scenes, character focus points, spatiotemporal constraints, and "
                    "detailed directives for the next chapter based on the World Bible and Plot Outline."
                ),
                "roles": [
                    ("Continuity_Auditor", "Ensures the proposed plan matches all previous timeline events and character statuses."),
                    ("Structural_Planner", "Lays out the scene breakdown, pacing, emotional beats, and writing guidelines."),
                    ("Reviewer_Arbitrator", "Reviews the plan for completeness, ensures no foresight leaks exist, and builds final guide.")
                ]
            },

            "editorial": {
                "description": "Chapter Editorial Committee to write, review, and revise chapter prose.",
                "system_instructions": (
                    "You are a member of the Chapter Editorial Committee.\n"
                    "Your goal is to collaborate on writing, criticizing, and refining the chapter's prose. "
                    "You must ensure consistent voice, emotional resonance, show-dont-tell technique, and strict rule continuity."
                ),
                "roles": [
                    ("Style_Critic", "Reviews the generated draft for pacing, vocabulary flow, show-dont-tell depth, and language guard rules."),
                    ("Creative_Writer", "Generates and rewrites text segments incorporating editorial reviews."),
                    ("Editor_In_Chief", "Synthesizes comments and signs off on the final chapter text draft.")
                ]
            },

            "world_bible": {
                "description": "World Bible Committee to draft and critique the World rules and characters profiles.",
                "system_instructions": (
                    "You are a member of the World Bible Committee.\n"
                    "Your objective is to draft, review, and refine the core constraints, character profiles, species, "
                    "and unchangeable laws of the novel's world."
                ),
                "roles": [
                    ("Lore_Architect", "Drafts the system elements, geography, rules, magic systems or technology laws."),
                    ("Narrative_Critic", "Scrutinizes the world laws for logical holes, inconsistencies, or pacing bottlenecks."),
                    ("World_Arbitrator", "Synthesizes the finalized World Bible ready for the timeline scanner to seed.")
                ]
            },

            "plot_outline": {
                "description": "Plot Outline Committee to design the novel's high-level narrative arcs.",
                "system_instructions": (
                    "You are a member of the Plot Outline Committee.\n"
                    "Your goal is to design the high-level progression, major milestones, character growth, and turning points "
                    "for the entire novel trajectory."
                ),
                "roles": [
                    ("Narrative_Arc_Planner", "Drafts the major narrative turning points, chapters splits, and character goals."),
                    ("Continuity_Critic", "Checks the outline for logical consistency, causality, and progression pacing."),
                    ("Arc_Arbitrator", "Finalizes the plot roadmap to guide specific chapter planning committees.")
                ]
            }
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
                return "Error: Database memory manager is not available in tools context."
            try:
                self.memory.cursor.execute(sql_command)
                rows = self.memory.cursor.fetchall()
                return str(rows)
            except Exception as e:
                return f"SQLite Error: {e}"

        def search_faiss(query_text: str, limit: int = 3) -> str:
            """Performs semantic vector search on FAISS indices. Arguments: query_text (str), limit (int)"""
            if not self.embedding_client:
                return "Error: Embedding client not available in tools context."
            if not self.memory:
                return "Error: Database memory manager is not available in tools context."
            try:
                emb = self.embedding_client.get_embedding(query_text)
                if not emb:
                    return "Error: Could not generate embedding."
                hits = self.memory.search_semantic(emb, k=int(limit))
                return str(hits)
            except Exception as e:
                return f"FAISS Search Error: {e}"

        def read_file_chunk(path: str, start_line: int = 1, end_line: Optional[int] = None) -> str:
            """Reads a specific paginated chunk of a file. Arguments: path (str), start_line (int), end_line (int)"""
            try:
                start_line = int(start_line)
                if end_line is not None:
                    end_line = int(end_line)
                return self.gated_reader.read_file(path, start_line, end_line)
            except Exception as e:
                return f"Error reading file chunk: {e}"

        def read_file_tail(path: str, line_count: int = 50) -> str:
            """Reads the last line_count lines of a file or log. Arguments: path (str), line_count (int)"""
            try:
                line_count = int(line_count)
                return self.gated_reader.read_file_tail(path, line_count)
            except Exception as e:
                return f"Error reading file tail: {e}"

        self.att_manager.register_tool("query_sqlite", "Queries the SQLite database directly with sql_command (str).", query_sqlite)
        self.att_manager.register_tool("search_faiss", "Performs semantic vector search on FAISS indices using query_text (str) and limit (int).", search_faiss)
        self.att_manager.register_tool("read_file_chunk", "Reads a specific paginated chunk of a file using path (str), start_line (int), and optionally end_line (int).", read_file_chunk)
        self.att_manager.register_tool("read_file_tail", "Reads the last line_count (int) lines of a file using path (str).", read_file_tail)

        # 7. Register the Tool Auditor hook for query_sqlite
        def audit_sqlite_query(*args, **kwargs) -> Tuple[bool, str]:
            sql_command = args[0] if args else kwargs.get("sql_command")
            if not sql_command:
                return False, "No sql_command supplied"
            return self.db_committee.audit_query(sql_command)

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

    def get_autonomy_tools(self, caller_node: Any) -> Dict[str, Any]:
        """Assembles the tools map bound to a specific AgentTeam or Member."""
        if hasattr(caller_node, "tools"):
            return caller_node.tools
        return {}
