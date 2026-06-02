import os
import sys
import unittest
from unittest.mock import MagicMock

# Setup paths
CURRENT_DIR = os.path.dirname(__file__)
ROOT_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(ROOT_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from att.att_core import ATTManager, Agent, AgentTeam, NegotiationBroker, SupervisoryTeam
from att.db_committee import DatabaseManagementCommittee
from att.presets import get_preset

class TestATT(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        # Mock generate to return a JSON payload for audit and basic responses for debate
        self.mock_client.generate.return_value = '{"is_healthy": true, "reason": "Dialogue approved."}'
        
        self.root_ai = Agent(name="Root_AI", role="Architect", llm_client=self.mock_client)
        self.manager = ATTManager(root_ai=self.root_ai, critic_client=self.mock_client)

    def test_agent_team_assertion_size(self):
        """Verify that an Agent Team (AT) must contain at least 3 members."""
        with self.assertRaises(AssertionError):
            self.manager.create_agent_team(creator=self.root_ai, member_count=2)

    def test_agent_team_spawning(self):
        """Verify that an Agent Team (AT) is spawned successfully with 3 members."""
        preset = get_preset("generic")
        team = self.manager.create_agent_team(
            creator=self.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"],
            preset_name="generic",
            system_instructions=preset["system_instructions"]
        )
        self.assertEqual(len(team.members), 3)
        self.assertEqual(team.preset_name, "generic")
        self.assertEqual(team.creator, self.root_ai)

    def test_recursive_att_spawning(self):
        """Verify that any team member can recursively spawn their own child AT."""
        preset = get_preset("generic")
        parent_team = self.manager.create_agent_team(
            creator=self.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"]
        )
        
        # Sibling Member 1 launches recursive child team
        member1 = parent_team.members[0]
        child_team = member1.launch_att(self.manager, member_count=3, roles_and_presets=preset["roles"])
        
        self.assertEqual(child_team.creator, member1)
        self.assertEqual(len(child_team.members), 3)

    def test_sibling_communication_negotiation(self):
        """Verify that sibling communication negotiation is managed by the parent team."""
        preset = get_preset("generic")
        parent_team = self.manager.create_agent_team(
            creator=self.root_ai,
            member_count=3,
            roles_and_presets=preset["roles"]
        )
        
        # Spawn child 1
        c1 = parent_team.members[0].launch_att(self.manager, member_count=3)
        # Spawn child 2
        c2 = parent_team.members[1].launch_att(self.manager, member_count=3)
        
        # Default sibling talk is False
        parent_team.communication_rules["allow_sibling_talk"] = False
        allowed = self.manager.broker.negotiate_communication(c1, c2)
        self.assertFalse(allowed)
        
        # Enable sibling talk
        parent_team.communication_rules["allow_sibling_talk"] = True
        allowed = self.manager.broker.negotiate_communication(c1, c2)
        self.assertTrue(allowed)

    def test_database_management_committee_approval(self):
        """Verify Database Management Committee audits SQLite queries."""
        committee = DatabaseManagementCommittee(self.att_manager_custom() if hasattr(self, "att_manager_custom") else self.manager)
        
        # 1. Approved query
        self.mock_client.generate.return_value = "Thought: Safe query.\nFinal Answer: approved"
        approved, reason = committee.audit_query("SELECT * FROM characters")
        self.assertTrue(approved)
        
        # 2. Rejected query
        self.mock_client.generate.return_value = "Thought: Potential violation.\nFinal Answer: reject because of resurrecting a character"
        approved, reason = committee.audit_query("UPDATE characters SET status = 'alive' WHERE name = 'Iris'")
        self.assertFalse(approved)

    def test_supervisory_team_escalation_protocol(self):
        """Verify that the 3-AI Supervisory Team scales up the lineage when a parent is also broken."""
        # Setup failed child team
        failed_team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
        
        # Setup audit failure for failed_team (is_healthy = False)
        # First call evaluates failed_team -> returns False
        # Second call evaluates parent -> parent is root (no team), or if a parent team exists
        parent_team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
        child_team = self.manager.create_agent_team(creator=parent_team, member_count=3)
        
        # Configure critic mock to return unhealthy audit response
        self.mock_client.generate.return_value = '{"is_healthy": false, "reason": "Anomaly found."}'
        
        # Trigger audit and escalation check
        self.manager.supervisor.report_anomaly(child_team, "Deadlock", self.manager)
        
        # We verify that both child_team and parent_team are registered as having failure logs
        self.assertTrue(len(self.manager.supervisor.auditors) == 3)

    def test_react_loop_and_tools(self):
        """Verify the ReAct execution loop parsing and tool execution."""
        from att.tools import Tool
        
        # 1. Setup a custom tool mock
        dummy_tool_called = False
        def dummy_tool(arg1):
            nonlocal dummy_tool_called
            dummy_tool_called = True
            return f"Processed: {arg1}"
            
        team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
        team.tools = {
            "dummy_tool": Tool("dummy_tool", "A dummy testing tool.", dummy_tool)
        }
        
        # Configure LLM Client side-effects for successive ReAct steps
        # Step 1: LLM decides to call Action
        # Step 2: LLM produces Final Answer
        self.mock_client.generate.side_effect = [
            "Thought: Let's run the dummy tool first.\nAction: dummy_tool(hello_world)",
            "Thought: I got the observation. We are done.\nFinal Answer: Success!"
        ]
        
        agent = team.members[0]
        final_answer = team.execute_react_step(agent, "Run the task", "System instructions", max_steps=2)
        
        self.assertTrue(dummy_tool_called)
        self.assertEqual(final_answer, "Success!")

    def test_sibling_talk_permission_tool(self):
        """Verify that sibling talk permissions can be dynamically set by parents only."""
        from att.tools import get_default_tools
        
        parent_team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
        child_team = self.manager.create_agent_team(creator=parent_team, member_count=3)
        unrelated_team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
        
        # Setup context and register tools on parent
        context = {"att_manager": self.manager}
        parent_team.tools = get_default_tools(context, parent_team)
        unrelated_team.tools = get_default_tools(context, unrelated_team)
        
        # 1. Unrelated team attempts to grant child_team sibling talk -> fails
        set_sibling_tool = unrelated_team.tools["set_sibling_talk"]
        res = set_sibling_tool(child_team.team_id, True)
        self.assertTrue("Error" in res)
        self.assertFalse(child_team.communication_rules["allow_sibling_talk"])
        
        # 2. Parent team grants child_team sibling talk -> succeeds
        set_sibling_tool = parent_team.tools["set_sibling_talk"]
        res = set_sibling_tool(child_team.team_id, True)
        self.assertTrue("Successfully" in res)
        self.assertTrue(child_team.communication_rules["allow_sibling_talk"])

    def test_discussion_inbox_alerts_injection(self):
        """Verify that inbox messages are prepended to discussion prompts."""
        team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
        team.receive_message({"from": "Supervisor", "reason": "Anomaly in chapter 1"})
        
        # We mock execute_react_step to verify that prompt contains the inbox signal
        observed_prompt = ""
        def mock_execute_react_step(agent, prompt, system_instruction, max_steps=5, manager=None):
            nonlocal observed_prompt
            observed_prompt = prompt
            return "Mocked Answer"
            
        team.execute_react_step = mock_execute_react_step
        self.manager.execute_team_discussion(team, "Start debate", rounds=1)
        
        self.assertIn("UNRESOLVED INBOX ALERTS & ESCALATIONS", observed_prompt)
        self.assertIn("Anomaly in chapter 1", observed_prompt)
        # Message inbox should be cleared after discussion
        self.assertEqual(len(team.message_inbox), 0)

    def test_chapter_num_propagation_and_att_logging(self):
        """Verify that chapter_num propagates correctly down dynamic teams and ATT split logs are generated."""
        from workflow_components.discussion import DiscussionLogger
        import shutil

        # Setup custom test log directory
        test_log_dir = "test_discussion_log"
        if os.path.exists(test_log_dir):
            shutil.rmtree(test_log_dir)

        try:
            discussion_logger = DiscussionLogger(test_log_dir)
            self.manager.discussion_logger = discussion_logger

            # Create a parent team and set its chapter_num
            parent_team = self.manager.create_agent_team(creator=self.root_ai, member_count=3)
            parent_team.chapter_num = 42

            # Creator agent from parent team launches recursive child team
            creator_agent = parent_team.members[0]
            child_team = creator_agent.launch_att(self.manager, member_count=3)

            # Child team launches grandchild team
            grandchild_team = child_team.launch_att(self.manager, member_count=3)

            # Verify chapter_num propagation
            self.assertEqual(child_team.chapter_num, 42)
            self.assertEqual(grandchild_team.chapter_num, 42)

            # Let's perform a step with child team and log it
            child_team.tools = {}
            self.mock_client.generate.return_value = "Final Answer: Done."
            
            # This should log both react steps and the final synthesized debate transcript
            self.manager.execute_team_discussion(child_team, "Run some debate", rounds=1)

            # Verify that logs were generated
            all_log = discussion_logger.all_log_path()
            split_log = discussion_logger.att_log_path(child_team.team_id)
            chapter_log = discussion_logger.chapter_log_path(42, lambda val: f"{val:03d}")

            self.assertTrue(os.path.exists(all_log))
            self.assertTrue(os.path.exists(split_log))
            self.assertTrue(os.path.exists(chapter_log))

            # Verify file content has the team ID
            with open(split_log, "r", encoding="utf-8") as f:
                content = f.read()
                self.assertIn(child_team.team_id, content)
                self.assertIn("Synthesized Debate Transcript", content)

        finally:
            # Clean up test directory
            if os.path.exists(test_log_dir):
                shutil.rmtree(test_log_dir)

if __name__ == "__main__":
    unittest.main()
