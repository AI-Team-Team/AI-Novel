import uuid
import logging
import time
import json
from typing import List, Dict, Optional, Any, Tuple

class Agent:
    def __init__(self, name: str, role: str, llm_client: Optional[Any] = None):
        self.name = name
        self.role = role
        self.llm_client = llm_client

    def launch_att(self, manager: 'ATTManager', member_count: int = 3, roles_and_presets: Optional[List[Tuple[str, str]]] = None) -> 'AgentTeam':
        """Allows any active agent to recursively launch their own ATT structure (a child AT)."""
        return manager.create_agent_team(creator=self, member_count=member_count, roles_and_presets=roles_and_presets)


class AgentTeam:
    def __init__(
        self, 
        team_id: str, 
        creator: Any, 
        members: List[Agent], 
        preset_name: str = "generic",
        system_instructions: str = ""
    ):
        self.team_id = team_id
        self.creator = creator  # Can be an Agent or another AgentTeam (Parent)
        self.members = members
        self.preset_name = preset_name
        self.system_instructions = system_instructions
        
        self.child_teams: List['AgentTeam'] = []
        self.communication_rules: Dict[str, Any] = {
            "allow_sibling_talk": False,
            "rules": []
        }
        self.logger = logging.getLogger(f"AgentTeam:{team_id}")
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

    def add_child_team(self, child: 'AgentTeam'):
        self.child_teams.append(child)

    def launch_att(self, manager: 'ATTManager', member_count: int = 3, roles_and_presets: Optional[List[Tuple[str, str]]] = None) -> 'AgentTeam':
        """Allows any active team to recursively launch their own child AT."""
        return manager.create_agent_team(creator=self, member_count=member_count, roles_and_presets=roles_and_presets)

    def receive_message(self, message: Dict[str, Any]):
        self.message_inbox.append(message)
        self.logger.info(f"Team {self.team_id} received message of type '{message.get('type')}' from '{message.get('from')}'")

    def execute_react_step(self, agent: Agent, prompt: str, system_instruction: str, max_steps: int = 5) -> str:
        """Executes a ReAct loop for a single agent inside the AT."""
        if not agent.llm_client:
            return "Error: Agent has no LLM client configured."

        identity_header = (
            f"## AGENT IDENTITY PROFILE\n"
            f"- **Role Name**: {agent.role}\n"
            f"- **Agent Name**: {agent.name}\n"
            f"- **Parent Team**: {self.team_id} (Preset: {self.preset_name})\n"
            f"- **Current Objective**: Cooperate in team tasks.\n"
        )

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

                    # Check for Final Answer
                    if "Final Answer:" in response:
                        parts = response.split("Final Answer:", 1)
                        return parts[1].strip()

                    # Parse and Execute Action
                    import re
                    # Look for "Action: tool_name(args)"
                    action_match = re.search(r"Action:\s*(\w+)\((.*)\)", response, re.IGNORECASE)
                    if action_match:
                        tool_name = action_match.group(1).strip()
                        tool_args_str = action_match.group(2).strip()

                        # Parse arguments safely (splitting by comma, stripping quotes, evaluating structures)
                        def parse_args(args_str):
                            if not args_str:
                                return [], {}
                            import ast
                            try:
                                # Wrap in tuple and evaluate using ast.literal_eval
                                parsed = ast.literal_eval(f"({args_str})")
                                if isinstance(parsed, tuple):
                                    args = list(parsed)
                                else:
                                    args = [parsed]
                                return args, {}
                            except Exception:
                                # Fallback split if literal_eval fails
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
                            observation = tool_obj(*args, **kwargs)
                        else:
                            observation = f"Error: Tool '{tool_name}' is not registered."

                        self.logger.info(f"Tool {tool_name} observation: {observation}")
                        
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
                if "Final Answer:" in response:
                    return response.split("Final Answer:", 1)[1].strip()
                return response
            except Exception as e:
                self.logger.error(f"Agent {agent.name} execution error: {e}")
                return f"Error executing task: {e}"


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
        Escalates anomaly up the lineage tree until a healthy parent is found.
        If all ancestors fail, reports directly to the root AI (Level 0).
        """
        self.logger.error(f"[SUPERVISOR ALERT] Anomaly detected in team {failed_team.team_id}: {reason}")
        
        current_parent = failed_team.parent_team or manager.find_parent_team(failed_team)
        failed_lineage = [failed_team]
        
        while current_parent is not None:
            # Audit the parent itself
            is_healthy, parent_reason = self.audit_team_dialog(current_parent, "Audit check during escalation")
            if is_healthy:
                self.logger.info(f"[SUPERVISOR] Parent team {current_parent.team_id} is healthy. Reporting failure of child {failed_team.team_id} to parent.")
                # Route the alert to the parent team inbox
                current_parent.receive_message({
                    "type": "child_failure_escalation",
                    "from": "Supervisor",
                    "failed_team_id": failed_team.team_id,
                    "reason": reason
                })
                return
            else:
                self.logger.warning(f"[SUPERVISOR] Parent team {current_parent.team_id} is ALSO broken: {parent_reason}. Climbing higher...")
                failed_lineage.append(current_parent)
                current_parent = current_parent.parent_team or manager.find_parent_team(current_parent)

        # All ancestors are broken: escalate directly to the root AI (Level 0)
        self.logger.critical("[SUPERVISOR CRITICAL] Lineage collapse! Escalating directly to Root AI Level 0.")
        if self.root_ai.llm_client:
            alert_msg = (
                f"CRITICAL SYSTEM FAILURE: Anomaly in child team lineage.\n"
                f"Failed teams: {[t.team_id for t in failed_lineage]}\n"
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

    def create_agent_team(
        self, 
        creator: Any, 
        member_count: int = 3,
        roles_and_presets: Optional[List[Tuple[str, str]]] = None,
        preset_name: str = "generic",
        system_instructions: str = ""
    ) -> AgentTeam:
        """Spawns a new AT, enforcing size >= 3."""
        assert member_count >= 3, f"An Agent Team (AT) must contain at least 3 members (got {member_count})."
        
        team_id = f"AT-{uuid.uuid4().hex[:6]}"
        members = []
        
        if roles_and_presets:
            for name, role in roles_and_presets:
                members.append(Agent(name=name, role=role, llm_client=self.critic_client))
        else:
            # Fallback default members
            for i in range(member_count):
                members.append(Agent(name=f"{team_id}_member_{i+1}", role="Specialist", llm_client=self.critic_client))

        team = AgentTeam(
            team_id=team_id,
            creator=creator,
            members=members,
            preset_name=preset_name,
            system_instructions=system_instructions
        )
        
        # Bind tools from context if registered
        from att.tools import get_default_tools
        if hasattr(self, "tools_context") and self.tools_context:
            team.tools = get_default_tools(self.tools_context, team)
            
        self.teams[team_id] = team
        
        # If creator is another team, register it as child
        if isinstance(creator, AgentTeam):
            creator.add_child_team(team)
            
        self.logger.info(f"Successfully spawned Agent Team {team_id} (N={len(members)}, Preset: {preset_name}) spawned by {creator.name if hasattr(creator, 'name') else creator.team_id}")
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
            inbox_context = (
                f"\n\n### UNRESOLVED INBOX ALERTS & ESCALATIONS\n"
                f"Your team has received the following signals from your descendants or supervisor:\n"
                f"{chr(10).join(inbox_lines)}\n"
                f"Please address or incorporate these alerts into your decision-making."
            )
            # Clear processed messages
            team.message_inbox = []

        dialog_history = []
        full_prompt = f"{prompt}{inbox_context}"
        
        for r in range(1, rounds + 1):
            for agent in team.members:
                agent_prompt = (
                    f"Task: {full_prompt}\n\n"
                    f"--- Discussion History ---\n"
                    f"{chr(10).join(dialog_history) if dialog_history else '(None)'}\n\n"
                    f"Your Turn (Speak in your role. Keep it concise):"
                )
                response = team.execute_react_step(agent, agent_prompt, team.system_instructions)
                dialog_history.append(f"[{agent.role} - {agent.name}]: {response}")

        transcript = "\n".join(dialog_history)
        
        # Run supervisory audit
        is_healthy, reason = self.supervisor.audit_team_dialog(team, transcript)
        if not is_healthy:
            self.supervisor.report_anomaly(team, reason, self)
            
        return transcript
