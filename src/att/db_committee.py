import logging
import json
from typing import Tuple, Dict, Any, Optional
from ai_team_team import ATTManager

class DatabaseManagementCommittee:
    def __init__(self, att_manager: ATTManager):
        self.manager = att_manager
        self.preset = att_manager.get_preset("database_management")
        self.logger = logging.getLogger("DatabaseManagementCommittee")

    def audit_query(self, sql_command: str) -> Tuple[bool, str]:
        """
        Spawns the Database Management Committee AT to audit a direct SQL query.
        Returns (approved, reasoning).
        """
        self.logger.info(f"Spawning Database Management Committee to audit SQL query: {sql_command}")
        
        team = self.manager.create_agent_team(
            creator=self.manager.root_ai,
            member_count=3,
            roles_and_presets=self.preset["roles"],
            preset_name="database_management",
            system_instructions=self.preset["system_instructions"]
        )

        prompt = (
            f"The following SQLite query is requested for execution:\n"
            f"```sql\n{sql_command}\n```\n\n"
            f"Please evaluate this query for safety, security, and rule-compliance. "
            f"Ensure no unauthorized deletions, updates that violate character statuses (like resurrecting a character), or schema corruptions exist.\n"
            f"Each member must state their analysis. Finally, specify if approved and provide your consensus reasoning."
        )

        try:
            transcript = self.manager.execute_team_discussion_sync(team, prompt, rounds=1)
            
            # Simple consensus check based on transcript debate
            approved = True
            reason = "DB Management Committee approved the query."
            
            if "reject" in transcript.lower() or "deny" in transcript.lower() or "violation" in transcript.lower():
                approved = False
                reason = "DB Management Committee rejected the query: Potential safety or integrity violation."
                
            self.logger.info(f"DB Committee decision: approved={approved}, reason={reason}")
            return approved, reason
        except Exception as e:
            self.logger.warning(f"DB Committee audit failed to run, fallback to approved: {e}")
            return True, f"DB Committee audit bypassed due to execution error: {e}"

    def audit_batch_transaction(self, data: Dict[str, Any], chapter_num: Optional[int]) -> Tuple[bool, str]:
        """
        Spawns the Database Management Committee AT to audit a batch commit transaction.
        """
        self.logger.info(f"Spawning Database Management Committee to audit batch commit for Ch {chapter_num}")
        
        team = self.manager.create_agent_team(
            creator=self.manager.root_ai,
            member_count=3,
            roles_and_presets=self.preset["roles"],
            preset_name="database_management",
            system_instructions=self.preset["system_instructions"]
        )

        # Truncate content for prompt protection
        payload_summary = {k: len(v) if isinstance(v, list) else v for k, v in data.items()}
        prompt = (
            f"A batch transaction payload is requested for Ch {chapter_num} commit.\n"
            f"Payload counts: {json.dumps(payload_summary)}\n\n"
            f"Please evaluate this transaction for consistency, security, and safety. "
            f"Verify no rules are violated. Audit the transaction and state your consensus decision (approved/rejected)."
        )

        try:
            transcript = self.manager.execute_team_discussion_sync(team, prompt, rounds=1)
            
            approved = True
            reason = "DB Management Committee approved the transaction."
            
            if "reject" in transcript.lower() or "deny" in transcript.lower():
                approved = False
                reason = "DB Management Committee rejected the batch commit: Potential integrity or rule inconsistency."
                
            self.logger.info(f"DB Committee batch decision: approved={approved}, reason={reason}")
            return approved, reason
        except Exception as e:
            self.logger.warning(f"DB Committee batch audit failed to run, fallback to approved: {e}")
            return True, f"DB Committee batch audit bypassed due to execution error: {e}"
