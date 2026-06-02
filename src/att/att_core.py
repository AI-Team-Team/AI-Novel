import uuid
import logging
import time
import json
from typing import List, Dict, Optional, Any, Tuple
import config

class Agent:
    def __init__(self, name: str, role: str, llm_client: Optional[Any] = None):
        self.name = name
        self.role = role
        self.llm_client = llm_client

    def launch_att(self, manager: 'ATTManager', member_count: int = 3, roles_and_presets: Optional[List[Tuple[str, str]]] = None, system_instructions: str = "", team_purpose: str = "Unspecified team purpose") -> 'AgentTeam':
        """Allows this agent to launch a dynamic sub-team (Level 2+)."""
        child = manager.create_agent_team(creator=self, member_count=member_count, roles_and_presets=roles_and_presets, system_instructions=system_instructions, team_purpose=team_purpose)
        for team in manager.teams.values():
            if self in team.members:
                child.chapter_num = team.chapter_num
                break
        return child


class AgentTeam:
    def __init__(self, creator: Any, preset_name: str, team_purpose: str = "Unspecified team purpose"):
        self.team_id = f"AT-{uuid.uuid4().hex[:6]}"
        self.creator = creator
        self.preset_name = preset_name
        self.team_purpose = team_purpose
        self.chapter_num: Optional[int] = None
        self.status_map: Dict[str, str] = {}
        self.members: List[Agent] = []
        
        self.child_teams: List['AgentTeam'] = []
        self.communication_rules: Dict[str, Any] = {
            "allow_sibling_talk": False,
            "rules": []
        }
        self.logger = logging.getLogger(f"AgentTeam:{self.team_id}")
        self.message_inbox: List[Dict[str, Any]] = []
        self.tools: Dict[str, Any] = {}

    @property
    def parent_team(self) -> Optional['AgentTeam']:
        if isinstance(self.creator, AgentTeam):
            return self.creator
        elif isinstance(self.creator, Agent):
            # If the creator is an agent, traverse up to find its team if registered in manager
            return None
        return None

    @property
    def depth(self) -> int:
        parent = self.parent_team
        return (parent.depth + 1) if parent else 1

    def add_child_team(self, child: 'AgentTeam'):
        self.child_teams.append(child)

    def launch_att(self, manager: 'ATTManager', member_count: int = 3, roles_and_presets: Optional[List[Tuple[str, str]]] = None, system_instructions: str = "", team_purpose: str = "Unspecified team purpose") -> 'AgentTeam':
        """Allows any active team to recursively launch their own child AT."""
        child = manager.create_agent_team(creator=self, member_count=member_count, roles_and_presets=roles_and_presets, system_instructions=system_instructions, team_purpose=team_purpose)
        child.chapter_num = self.chapter_num
        return child

    def receive_message(self, message: Dict[str, Any]):
        self.message_inbox.append(message)
        self.logger.info(f"Team {self.team_id} received message of type '{message.get('type')}' from '{message.get('from')}'")

    def execute_react_step(self, agent: Agent, prompt: str, system_instruction: str, max_steps: int = 5, manager: Optional['ATTManager'] = None) -> str:
        """Executes a ReAct loop for a single agent inside the AT."""
        if not agent.llm_client:
            return "Error: Agent has no LLM client configured."

        if not hasattr(self, "status_map"):
            self.status_map = {}
        self.status_map[agent.name] = "Thinking..."
        if manager and hasattr(manager, "dashboard") and manager.dashboard:
            manager.dashboard.refresh()

        peer_context = ""
        if manager:
            peer_lines = []
            for tid, t in manager.teams.items():
                if tid != self.team_id:
                    peer_lines.append(f"  - {tid} (Purpose: {t.team_purpose})")
            if peer_lines:
                peer_context = f"\n### ACTIVE PEER TEAMS (Global Registry)\n" + "\n".join(peer_lines) + "\n"

        model_options = "\n".join([f"  - {k}: {v.get('ai_note', 'No description')}" for k, v in config.MODEL_REGISTRY.items()])
        identity_header = (
            f"## AGENT IDENTITY PROFILE\n"
            f"- **Role Name**: {agent.role}\n"
            f"- **Agent Name**: {agent.name}\n"
            f"- **Parent Team**: {self.team_id} (Preset: {self.preset_name})\n"
            f"- **Team Purpose**: {self.team_purpose}\n"
            f"- **Current Objective**: Cooperate in team tasks.\n"
            f"- **AT Delegation Depth**: {self.depth} / {config.MAX_DELEGATION_DEPTH}\n"
            f"{peer_context}"
            f"### AUTONOMY RULES\n"
            f"1. You can dynamically spawn child ATs using the `dispatch_subagent` tool to solve sub-problems.\n"
            f"2. You MUST NOT spawn child ATs if your Delegation Depth is already at the maximum ({config.MAX_DELEGATION_DEPTH}). If you need help at max depth, use `delegate_escalation` to ask your parent.\n"
            f"3. A valid AT MUST have at least {getattr(config, 'MIN_SUBAGENT_TEAM_SIZE', 3)} members.\n"
            f"4. When creating an AT, you can assign models based on task complexity. Available models:\n"
            f"{model_options}\n"
        )

        try:
            # Check if we have active tools to formulate the ReAct system prompt
            if getattr(self, "tools", None):
                tools_desc = []
                for t_name, tool in self.tools.items():
                    tools_desc.append(f"- **{t_name}**: {tool.description}")
                tools_list_str = "\n".join(tools_desc)

                react_system_instruction = (
                    f"{system_instruction}\n\n"
                    f"{identity_header}\n"
                    f"### AVAILABLE TOOLS\n"
                    f"{tools_list_str}\n\n"
                    f"### REACT FORMAT INSTRUCTIONS\n"
                    f"When executing your task, you can reason and use tools step-by-step. Use the following format:\n"
                    f"Thought: <your reasoning about the next step>\n"
                    f"Action: <tool_name>(<arguments_separated_by_commas_or_kwargs>)\n"
                    f"Observation: <the tool output will appear here>\n\n"
                    f"You can repeat the Thought/Action/Observation loop multiple times if needed. "
                    f"Once you have all the necessary information, or if you do not need to use any tools, output exactly:\n"
                    f"Final Answer: <your final answer here>"
                )

                # ReAct Execution Loop
                current_prompt = prompt
                react_history = []
                
                for step in range(max_steps):
                    try:
                        self.status_map[agent.name] = f"Thinking (Step {step+1}/{max_steps})..."
                        if manager and hasattr(manager, "dashboard") and manager.dashboard:
                            manager.dashboard.refresh()

                        full_prompt = current_prompt
                        if react_history:
                            full_prompt = (
                                f"{current_prompt}\n\n"
                                f"--- ReAct Iteration History ---\n"
                                f"{chr(10).join(react_history)}\n"
                                f"Next step:"
                            )

                        response = agent.llm_client.generate(
                            prompt=full_prompt,
                            system_instruction=react_system_instruction,
                            temperature=0.3
                        ).strip()

                        self.logger.info(f"Agent {agent.name} ReAct step {step+1} response:\n{response}")

                        # Log ReAct LLM step to dynamic ATT split log
                        if manager and hasattr(manager, "discussion_logger") and manager.discussion_logger:
                            log_content = (
                                f"AGENT: {agent.name}\n"
                                f"ROLE: {agent.role}\n"
                                f"STEP: {step+1}\n"
                                f"--- SYSTEM INSTRUCTION BEGIN ---\n"
                                f"{react_system_instruction}\n"
                                f"--- SYSTEM INSTRUCTION END ---\n"
                                f"--- PROMPT BEGIN ---\n"
                                f"{full_prompt}\n"
                                f"--- PROMPT END ---\n"
                                f"--- RESPONSE BEGIN ---\n"
                                f"{response}\n"
                                f"--- RESPONSE END ---\n"
                            )
                            manager.discussion_logger.append_att(
                                team_id=self.team_id,
                                title=f"ReAct LLM Step | {agent.name} ({agent.role}) Step {step+1}",
                                content=log_content,
                                chapter_num=self.chapter_num
                            )

                        # Extract Thought / Action / Final Answer for Dashboard
                        if "Final Answer:" in response:
                            final_ans_content = response.split("Final Answer:", 1)[1].strip()
                            if manager and hasattr(manager, "dashboard") and manager.dashboard:
                                manager.dashboard.add_activity(agent.name, "Final Answer", final_ans_content)
                            
                            parts = response.split("Final Answer:", 1)
                            return parts[1].strip()

                        import re
                        thought_match = re.search(r"Thought:\s*(.*)", response, re.IGNORECASE)
                        if thought_match:
                            thought_content = thought_match.group(1).split("Action:")[0].strip()
                            if manager and hasattr(manager, "dashboard") and manager.dashboard:
                                manager.dashboard.add_activity(agent.name, "Thought", thought_content)

                        # Parse and Execute Action
                        action_match = re.search(r"Action:\s*(\w+)\((.*)\)", response, re.IGNORECASE)
                        if action_match:
                            tool_name = action_match.group(1).strip()
                            tool_args_str = action_match.group(2).strip()

                            # Parse arguments safely
                            def parse_args(args_str):
                                if not args_str:
                                    return [], {}
                                import ast
                                try:
                                    parsed = ast.literal_eval(f"({args_str})")
                                    if isinstance(parsed, tuple):
                                        args = list(parsed)
                                    else:
                                        args = [parsed]
                                    return args, {}
                                except Exception:
                                    args = []
                                    kwargs = {}
                                    parts = args_str.split(",")
                                    for p in parts:
                                        p = p.strip()
                                        if "=" in p:
                                            k, v = p.split("=", 1)
                                            kwargs[k.strip()] = v.strip().strip("'\"")
                                        else:
                                            args.append(p.strip().strip("'\""))
                                    return args, kwargs

                            args, kwargs = parse_args(tool_args_str)

                            if tool_name in self.tools:
                                tool_obj = self.tools[tool_name]
                                self.logger.info(f"Executing tool: {tool_name} with args={args}, kwargs={kwargs}")
                                
                                self.status_map[agent.name] = f"Executing Tool: {tool_name}"
                                if manager and hasattr(manager, "dashboard") and manager.dashboard:
                                    manager.dashboard.add_activity(agent.name, "Action", f"{tool_name}({tool_args_str})")
                                    manager.dashboard.refresh()

                                observation = tool_obj(*args, **kwargs)
                                
                                self.status_map[agent.name] = "Thinking..."
                                if manager and hasattr(manager, "dashboard") and manager.dashboard:
                                    # Show truncated observation
                                    obs_summary = str(observation)
                                    if len(obs_summary) > 80:
                                        obs_summary = obs_summary[:77] + "..."
                                    manager.dashboard.add_activity(agent.name, "Observation", obs_summary)
                                    manager.dashboard.refresh()
                            else:
                                observation = f"Error: Tool '{tool_name}' is not registered."
                                if manager and hasattr(manager, "dashboard") and manager.dashboard:
                                    manager.dashboard.add_activity(agent.name, "Observation", observation)

                            self.logger.info(f"Tool {tool_name} observation: {observation}")
                            
                            # Log tool call and observation to split logger
                            if manager and hasattr(manager, "discussion_logger") and manager.discussion_logger:
                                log_content = (
                                    f"AGENT: {agent.name}\n"
                                    f"ROLE: {agent.role}\n"
                                    f"ACTION: {tool_name}({tool_args_str})\n"
                                    f"OBSERVATION:\n{observation}\n"
                                )
                                manager.discussion_logger.append_att(
                                    team_id=self.team_id,
                                    title=f"ReAct Tool Call | {agent.name} ({agent.role})",
                                    content=log_content,
                                    chapter_num=self.chapter_num
                                )

                            # Add step to history
                            react_history.append(f"Thought: Analyzing task.")
                            react_history.append(f"Action: {tool_name}({tool_args_str})")
                            react_history.append(f"Observation: {observation}")
                        else:
                            # LLM didn't use Action format or Final Answer format
                            if step == max_steps - 1:
                                return response
                            react_history.append(response)
                            react_history.append("Observation: Please output either 'Action: tool_name(args)' or 'Final Answer: <content>'.")
                    except Exception as e:
                        self.logger.error(f"Error in ReAct step {step+1} for agent {agent.name}: {e}")
                        return f"Error executing task during ReAct loop: {e}"

                return "Error: ReAct loop exceeded maximum steps without producing a Final Answer."

            else:
                # Fallback to standard single step call if no tools are bound
                full_system_instruction = (
                    f"{system_instruction}\n\n"
                    f"{identity_header}\n"
                    f"Output exactly 'Final Answer: <content>' when complete."
                )

                try:
                    response = agent.llm_client.generate(
                        prompt=prompt,
                        system_instruction=full_system_instruction,
                        temperature=0.3
                    ).strip()
                    
                    if manager and hasattr(manager, "discussion_logger") and manager.discussion_logger:
                        log_content = (
                            f"AGENT: {agent.name}\n"
                            f"ROLE: {agent.role}\n"
                            f"--- SYSTEM INSTRUCTION BEGIN ---\n"
                            f"{full_system_instruction}\n"
                            f"--- SYSTEM INSTRUCTION END ---\n"
                            f"--- PROMPT BEGIN ---\n"
                            f"{prompt}\n"
                            f"--- PROMPT END ---\n"
                            f"--- RESPONSE BEGIN ---\n"
                            f"{response}\n"
                            f"--- RESPONSE END ---\n"
                        )
                        manager.discussion_logger.append_att(
                            team_id=self.team_id,
                            title=f"Direct LLM Call | {agent.name} ({agent.role})",
                            content=log_content,
                            chapter_num=self.chapter_num
                        )

                    if "Final Answer:" in response:
                        final_ans_content = response.split("Final Answer:", 1)[1].strip()
                        if manager and hasattr(manager, "dashboard") and manager.dashboard:
                            manager.dashboard.add_activity(agent.name, "Final Answer", final_ans_content)
                        return response.split("Final Answer:", 1)[1].strip()
                    return response
                except Exception as e:
                    self.logger.error(f"Agent {agent.name} execution error: {e}")
                    return f"Error executing task: {e}"
        finally:
            self.status_map[agent.name] = "Idle"
            if manager and hasattr(manager, "dashboard") and manager.dashboard:
                manager.dashboard.refresh()


class NegotiationBroker:
    """Coordinates sibling and cross-lineage communication permissions."""
    def __init__(self, manager: 'ATTManager'):
        self.manager = manager
        self.logger = logging.getLogger("NegotiationBroker")

    def negotiate_communication(
        self, 
        sender: AgentTeam, 
        recipient: AgentTeam, 
        mode: str = "proxied"
    ) -> bool:
        """
        Negotiates communication between two ATs.
        If sibling: controlled by their common parent's rules.
        If cross-lineage: requires negotiation between their respective parents.
        """
        sender_parent = sender.parent_team or self.manager.find_parent_team(sender)
        recipient_parent = recipient.parent_team or self.manager.find_parent_team(recipient)

        # 1. Sibling Communication check
        if sender_parent and recipient_parent and sender_parent.team_id == recipient_parent.team_id:
            parent = sender_parent
            allow = parent.communication_rules.get("allow_sibling_talk", False)
            self.logger.info(f"Sibling negotiation between {sender.team_id} and {recipient.team_id}: Parent {parent.team_id} decision={allow}")
            return allow

        # 2. Cross-lineage communication: requires negotiation between their respective parents
        if not sender_parent or not recipient_parent:
            self.logger.warning(f"Lineage incomplete. Cannot negotiate communication between {sender.team_id} and {recipient.team_id}.")
            return False

        self.logger.info(f"Cross-lineage negotiation requested between {sender.team_id} and {recipient.team_id} (via parents {sender_parent.team_id} and {recipient_parent.team_id}).")
        return self._run_parent_negotiation_loop(sender_parent, recipient_parent, mode)

    def _run_parent_negotiation_loop(self, p1: AgentTeam, p2: AgentTeam, mode: str) -> bool:
        """Runs a simulated agreement debate between two parent ATs to approve the tunnel."""
        self.logger.info(f"Parents {p1.team_id} and {p2.team_id} are negotiating communication channel (mode: {mode})...")
        # In this implementation, we auto-approve standard proxied/indirect rules
        if mode in {"proxied", "indirect", "rule_gated"}:
            self.logger.info("Negotiation loop succeeded: communication contract established.")
            return True
        self.logger.warning(f"Negotiation loop rejected: mode '{mode}' is unsupported or unsafe.")
        return False


class SupervisoryTeam:
    """Composed of exactly 3 AIs. Audits intra-team and inter-team dialog effectiveness, and triggers recursive parent escalation."""
    def __init__(self, root_ai: Agent, critic_client: Any):
        self.root_ai = root_ai
        self.critic_client = critic_client
        self.auditors = [
            Agent(name=f"Auditor_Integrity_01", role="Integrity_Auditor", llm_client=critic_client),
            Agent(name=f"Auditor_Continuity_02", role="Continuity_Auditor", llm_client=critic_client),
            Agent(name=f"Auditor_Deadlock_03", role="Deadlock_Auditor", llm_client=critic_client),
        ]
        self.logger = logging.getLogger("SupervisoryTeam")

    def audit_team_dialog(self, team: AgentTeam, dialog_transcript: str) -> Tuple[bool, str]:
        """
        Evaluates dialogue transcript efficiency inside an AT.
        Returns (is_healthy, reason).
        """
        audit_prompt = (
            f"Audit the following multi-agent discussion transcript for efficiency and logic.\n"
            f"Check if there are deadlocks, repetitive arguments, or deviations from roles.\n\n"
            f"--- TRANSCRIPT BEGIN ---\n"
            f"{dialog_transcript}\n"
            f"--- TRANSCRIPT END ---\n\n"
            f"Output exactly a JSON payload:\n"
            f"{{\n"
            f"  \"is_healthy\": true | false,\n"
            f"  \"reason\": \"Reasoning for your audit...\"\n"
            f"}}"
        )

        try:
            # We use one of the auditor's LLM clients to perform the JSON audit
            response = self.critic_client.generate(
                prompt=audit_prompt,
                system_instruction="You are a strict, objective Supervisory Auditor. Evaluate communication effectiveness.",
                temperature=0.2,
                require_json=True
            )
            # Simple JSON parse
            if "```" in response:
                response = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(response)
            is_healthy = bool(data.get("is_healthy", True))
            reason = str(data.get("reason", "No reason provided."))
            self.logger.info(f"Audit for team {team.team_id}: healthy={is_healthy}, reason={reason}")
            return is_healthy, reason
        except Exception as e:
            self.logger.warning(f"Supervisory audit failed, defaulting to healthy: {e}")
            return True, f"Audit failed: {e}"

    def report_anomaly(self, failed_team: AgentTeam, reason: str, manager: 'ATTManager'):
        """
        Escalates anomaly up the lineage tree by reporting to the direct parent.
        If the parent is not found, escalates directly to the root AI (Level 0).
        """
        self.logger.error(f"[SUPERVISOR ALERT] Anomaly detected in team {failed_team.team_id}: {reason}")
        
        current_parent = failed_team.parent_team or manager.find_parent_team(failed_team)
        
        if current_parent is not None:
            self.logger.info(f"[SUPERVISOR] Escalating failure of child {failed_team.team_id} to parent team {current_parent.team_id}.")
            # Route the alert to the parent team inbox
            current_parent.receive_message({
                "type": "child_failure_escalation",
                "from": "Supervisor",
                "failed_team_id": failed_team.team_id,
                "reason": reason
            })
            return
            
        # No parent found: escalate directly to the root AI (Level 0)
        self.logger.critical("[SUPERVISOR CRITICAL] Root-level failure or lineage collapse! Escalating directly to Root AI Level 0.")
        if self.root_ai.llm_client:
            alert_msg = (
                f"CRITICAL SYSTEM FAILURE: Anomaly in team {failed_team.team_id}.\n"
                f"Original anomaly reason: {reason}"
            )
            # In an actual system, we can inject this alert into the main prompt or log it
            print(f"!!! ROOT ALERT !!! {alert_msg}")


class ATTManager:
    """Master controller managing the overall ATT (AI Team Team) topology."""
    def __init__(self, root_ai: Agent, critic_client: Any):
        self.root_ai = root_ai
        self.critic_client = critic_client
        self.teams: Dict[str, AgentTeam] = {}
        self.broker = NegotiationBroker(self)
        self.supervisor = SupervisoryTeam(root_ai, critic_client)
        self.logger = logging.getLogger("ATTManager")
        self.tools_context: Dict[str, Any] = {}

    def register_tools_context(self, context: Dict[str, Any]):
        """Registers system dependencies/resources context for binding tools to AIs."""
        self.tools_context.update(context)
        from att.tools import get_default_tools
        # Update tools on any existing teams
        for team in self.teams.values():
            team.tools = get_default_tools(self.tools_context, team)

    def create_agent_team(self, creator: Any, member_count: int = 3, roles_and_presets: List[Tuple[str, str]] = None, preset_name: str = "custom", system_instructions: str = "", team_purpose: str = "Unspecified team purpose") -> AgentTeam:
        """
        Dynamically spawns a new recursive Agent Team (AT).
        Enforces minimum team size logic.
        """
        min_size = getattr(config, 'MIN_SUBAGENT_TEAM_SIZE', 3)
        assert member_count >= min_size, f"An Agent Team must contain at least {min_size} members to debate properly."
        
        team = AgentTeam(creator=creator, preset_name=preset_name, team_purpose=team_purpose)
        
        # Propagate chapter_num from creator if possible
        if isinstance(creator, AgentTeam):
            team.chapter_num = creator.chapter_num
        elif isinstance(creator, Agent):
            for t in self.teams.values():
                if creator in t.members:
                    team.chapter_num = t.chapter_num
                    break

        members = []
        
        if roles_and_presets:
            for name, role in roles_and_presets:
                members.append(Agent(name=name, role=role, llm_client=self.critic_client))
        else:
            # Fallback default members
            for i in range(member_count):
                members.append(Agent(name=f"{team.team_id}_member_{i+1}", role="Specialist", llm_client=self.critic_client))

        team.members = members
        team.system_instructions = system_instructions
        
        # Bind tools from context if registered
        from att.tools import get_default_tools
        if hasattr(self, "tools_context") and self.tools_context:
            team.tools = get_default_tools(self.tools_context, team)
            
        self.teams[team.team_id] = team
        
        # If creator is another team, register it as child
        if isinstance(creator, AgentTeam):
            creator.add_child_team(team)
            
        self.logger.info(f"Successfully spawned Agent Team {team.team_id} (N={len(members)}, Preset: {preset_name}) spawned by {creator.name if hasattr(creator, 'name') else creator.team_id}")
        return team

    def find_parent_team(self, target: AgentTeam) -> Optional[AgentTeam]:
        """Traverses the registered teams to resolve parent relationship if not explicitly linked."""
        for team in self.teams.values():
            if target in team.child_teams:
                return team
        
        # Check if creator is an agent belonging to one of the teams
        creator = target.creator
        if isinstance(creator, Agent):
            for team in self.teams.values():
                if creator in team.members:
                    return team
        return None

    def execute_team_discussion(self, team: AgentTeam, prompt: str, rounds: int = 2) -> str:
        """Executes a multi-agent debate session inside the AT, monitored by the Supervisor."""
        self.logger.info(f"Executing discussion in team {team.team_id} (rounds={rounds})...")
        
        # Inject unresolved inbox alerts & escalations if any exist
        inbox_context = ""
        if team.message_inbox:
            inbox_lines = []
            for msg in team.message_inbox:
                inbox_lines.append(f"- **From [{msg.get('from', 'Unknown')}]**: {msg.get('reason') or msg.get('objective') or str(msg)}")
            
            raw_inbox_text = chr(10).join(inbox_lines)
            threshold = getattr(config, "INBOX_SUMMARIZE_THRESHOLD_CHARS", 1500)
            if len(raw_inbox_text) > threshold and self.critic_client:
                self.logger.info("Inbox context too large, summarizing before injection...")
                summary_prompt = f"Summarize the following system alerts and escalations concisely:\n\n{raw_inbox_text}"
                try:
                    raw_inbox_text = self.critic_client.generate(summary_prompt, system_instruction="You are a strict system summarizer. Compress alerts while keeping critical facts and failures.", temperature=0.1)
                except Exception as e:
                    self.logger.warning(f"Failed to summarize inbox: {e}")
                    
            inbox_context = (
                f"\n\n### UNRESOLVED INBOX ALERTS & ESCALATIONS\n"
                f"Your team has received the following signals from your descendants or supervisor:\n"
                f"{raw_inbox_text}\n"
                f"Please address or incorporate these alerts into your decision-making."
            )
            # Clear processed messages
            team.message_inbox = []

        dialog_history = []
        full_prompt = f"{prompt}{inbox_context}"
        
        for r in range(1, rounds + 1):
            for agent in team.members:
                self.logger.info(f"Agent {agent.name} thinking...")
                final_answer = team.execute_react_step(
                    agent=agent, 
                    prompt=full_prompt, 
                    system_instruction=team.system_instructions,
                    max_steps=getattr(config, "REACT_MAX_STEPS", 5),
                    manager=self
                )
                dialog_history.append(f"{agent.name}: {final_answer}")

        transcript = "\n".join(dialog_history)
        
        # Run supervisory audit
        is_healthy, reason = self.supervisor.audit_team_dialog(team, transcript)
        if not is_healthy:
            self.supervisor.report_anomaly(team, reason, self)
            
        # Log final synthesized transcript to dynamic ATT split log
        if hasattr(self, "discussion_logger") and self.discussion_logger:
            log_title = f"Synthesized Debate Transcript | {team.team_id} ({team.preset_name}) - Rounds: {rounds}"
            log_content = (
                f"TEAM_ID: {team.team_id}\n"
                f"PRESET_NAME: {team.preset_name}\n"
                f"PURPOSE: {team.team_purpose}\n"
                f"PROMPT: {prompt}\n"
                f"--- SYNTHESIZED TRANSCRIPT BEGIN ---\n"
                f"{transcript}\n"
                f"--- SYNTHESIZED TRANSCRIPT END ---\n"
                f"AUDIT STATUS: {'Healthy' if is_healthy else 'Anomaly Detected'}\n"
                f"AUDIT REASON: {reason}\n"
            )
            self.discussion_logger.append_att(
                team_id=team.team_id,
                title=log_title,
                content=log_content,
                chapter_num=team.chapter_num
            )

        return transcript
