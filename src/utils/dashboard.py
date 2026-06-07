from collections import deque
import logging
import os
import time
from typing import Optional
from rich.panel import Panel
from rich.tree import Tree
from rich.text import Text
from rich.columns import Columns
from rich.console import Group
from rich.align import Align
from rich.spinner import Spinner
from rich.table import Table
from rich.layout import Layout

class DashboardLogHandler(logging.Handler):
    def __init__(self, dashboard: 'ConsoleDashboard'):
        super().__init__()
        self.dashboard = dashboard

    def emit(self, record):
        try:
            level = record.levelname
            msg = record.getMessage()
            
            # Remove raw prompt/response logs from real-time display to keep it elegant
            if "ReAct step" in msg or "generate_content" in msg or "Response:" in msg or "PROMPT BEGIN" in msg or "RESPONSE BEGIN" in msg:
                first_line = msg.split("\n")[0]
                if len(first_line) > 100:
                    first_line = first_line[:97] + "..."
                msg = f"{first_line} [dim](detailed log stored to file)[/dim]"
            
            timestamp = time.strftime("%H:%M:%S")
            if level == "WARNING":
                log_fmt = f"[dim]{timestamp}[/dim] ⚠️  [bold yellow]WARNING[/bold yellow]: {msg}"
            elif level in ("ERROR", "CRITICAL"):
                log_fmt = f"[dim]{timestamp}[/dim] ❌ [bold red]ERROR[/bold red]: {msg}"
            else:
                if "Successfully" in msg or "Success" in msg or "✔" in msg:
                    log_fmt = f"[dim]{timestamp}[/dim] [bold green]✔ {msg}[/bold green]"
                elif "Spawning" in msg or "spawned" in msg:
                    log_fmt = f"[dim]{timestamp}[/dim] [cyan]👥 {msg}[/cyan]"
                elif "Saved" in msg:
                    log_fmt = f"[dim]{timestamp}[/dim] [dim]💾 {msg}[/dim]"
                else:
                    log_fmt = f"[dim]{timestamp}[/dim] [white]{msg}[/white]"
            
            self.dashboard.add_log(log_fmt)
        except Exception:
            self.handleError(record)

class DashboardRenderable:
    def __init__(self, dashboard: 'ConsoleDashboard', layout: Layout):
        self.dashboard = dashboard
        self.layout = layout

    def __rich_console__(self, console, options):
        # Prevent terminal scrolling by rendering 1 line less than the viewport height.
        # This leaves exactly 1 line for the trailing newline, preventing overflow.
        target_height = max(10, console.height - 1)
        options.height = target_height
        yield from console.render(self.layout, options)

class ConsoleDashboard:
    def __init__(self, workflow_manager=None):
        self.workflow_manager = workflow_manager
        self.active_stage = "Ready to start"
        self.recent_activities = deque(maxlen=15)
        self.recent_logs = deque(maxlen=15)
        self.current_auto_chapter = 0
        self.total_auto_chapters = 0
        self.live = None
        self.handler = None
        self.old_levels = {}
        self.old_tty_attrs = None

    def set_live(self, live):
        self.live = live
        try:
            import sys
            import termios
            import threading
            if sys.__stdout__ and sys.__stdout__.isatty() and sys.__stdin__ and sys.__stdin__.isatty():
                # Save original TTY settings to restore later
                self.old_tty_attrs = termios.tcgetattr(sys.__stdin__)
                
                # Disable ECHO and ICANON so mouse tracking/scroll sequences are not visually echoed and are passed immediately
                new_attrs = termios.tcgetattr(sys.__stdin__)
                new_attrs[3] = new_attrs[3] & ~termios.ECHO & ~termios.ICANON
                new_attrs[6][termios.VMIN] = 1
                new_attrs[6][termios.VTIME] = 0
                termios.tcsetattr(sys.__stdin__, termios.TCSADRAIN, new_attrs)

                # Enable mouse click/scroll tracking to lock viewport scrolling.
                # (We do NOT use \033[3J here to preserve the primary screen's scrollback history).
                sys.__stdout__.write("\033[?1000h\033[?1006h")
                sys.__stdout__.flush()

                # Start background thread to drain stdin and prevent OS input buffer overflow beeps/lag
                self.stop_drain_event = threading.Event()
                
                def drain_stdin(stop_event):
                    import select
                    import os
                    import time
                    while not stop_event.is_set():
                        try:
                            r, _, _ = select.select([sys.__stdin__], [], [], 0.05)
                            if r:
                                os.read(sys.__stdin__.fileno(), 1024)
                        except Exception:
                            time.sleep(0.05)

                self.drain_thread = threading.Thread(
                    target=drain_stdin,
                    args=(self.stop_drain_event,),
                    daemon=True
                )
                self.drain_thread.start()
        except Exception:
            pass

    def add_activity(self, agent_name: str, activity_type: str, content: str):
        """Appends a ReAct loop thought, action, or observation to the dashboard."""
        timestamp = time.strftime("%H:%M:%S")
        clean_content = " ".join(content.split())
        if len(clean_content) > 120:
            clean_content = clean_content[:117] + "..."
            
        icon = "🤖"
        color = "white"
        if activity_type == "Thought":
            icon = "🧠"
            color = "bright_black"
        elif activity_type == "Action":
            icon = "⚙️"
            color = "yellow"
        elif activity_type == "Observation":
            icon = "👁️"
            color = "cyan"
        elif activity_type == "Final Answer":
            icon = "✨"
            color = "green"

        activity_str = f"[dim]{timestamp}[/dim] {icon} [bold magenta]{agent_name}[/bold magenta]: [{color}]{activity_type}: {clean_content}[/{color}]"
        self.recent_activities.append(activity_str)
        self.refresh()

    def add_log(self, log_msg: str):
        """Appends intercepted logs to the scrolling dashboard view."""
        self.recent_logs.append(log_msg)
        self.refresh()

    def refresh(self):
        if self.live:
            try:
                self.live.update(self.render(), refresh=True)
            except Exception:
                pass

    def start_capture(self):
        self.handler = DashboardLogHandler(self)
        self.handler.setFormatter(logging.Formatter("[dim]%(asctime)s[/dim] %(message)s", datefmt="%H:%M:%S"))
        self.handler.setLevel(logging.INFO)
        logging.getLogger().addHandler(self.handler)
        self.silence_console_handlers()

    def stop_capture(self):
        # 1. Stop background drain thread
        try:
            if getattr(self, "stop_drain_event", None) is not None:
                self.stop_drain_event.set()
            if getattr(self, "drain_thread", None) is not None:
                self.drain_thread.join(timeout=0.2)
        except Exception:
            pass

        # 2. Flush standard input to discard any remaining mouse reporting sequences
        try:
            import sys
            import termios
            if sys.__stdin__ and sys.__stdin__.isatty():
                termios.tcflush(sys.__stdin__, termios.TCIFLUSH)
        except (ImportError, AttributeError, ValueError, IOError):
            pass

        # 2. Restore TTY settings (re-enable ECHO)
        try:
            import sys
            import termios
            if sys.__stdin__ and sys.__stdin__.isatty() and getattr(self, "old_tty_attrs", None) is not None:
                termios.tcsetattr(sys.__stdin__, termios.TCSADRAIN, self.old_tty_attrs)
                self.old_tty_attrs = None
        except Exception:
            pass

        # 3. Disable mouse tracking
        try:
            import sys
            if sys.__stdout__ and sys.__stdout__.isatty():
                sys.__stdout__.write("\033[?1006l\033[?1000l")
                sys.__stdout__.flush()
        except Exception:
            pass

        self.restore_console_handlers()

    def silence_console_handlers(self):
        self.old_levels = {}
        for h in list(logging.getLogger().handlers):
            if h != self.handler:
                self.old_levels[h] = h.level
                h.setLevel(logging.CRITICAL + 1)

    def restore_console_handlers(self):
        for h, lvl in self.old_levels.items():
            h.setLevel(lvl)
        if self.handler in logging.getLogger().handlers:
            logging.getLogger().removeHandler(self.handler)

    def render(self) -> 'DashboardRenderable':
        # Create root layout
        layout = Layout()
        
        # Split into header and body
        layout.split(
            Layout(name="header", size=3),
            Layout(name="body", ratio=1),
        )
        
        # Split body into left (ATT tree) and right (Agent Terminal + logs)
        layout["body"].split_row(
            Layout(name="left", ratio=1),
            Layout(name="right", ratio=2),
        )
        
        # Split right into agent activities and system logs
        layout["right"].split(
            Layout(name="activities", ratio=1),
            Layout(name="logs", ratio=1)
        )

        # 1. Header with title, active stage and spinner/progress
        header_text = Text.assemble(
            ("✨ AI NOVEL WRITER WORKSPACE ", "bold cyan"),
            ("✨", "bold yellow")
        )
        
        progress_text = ""
        if self.total_auto_chapters > 0:
            progress_text = f" | [bold yellow]Progress: Chapter {self.current_auto_chapter} of {self.total_auto_chapters}[/bold yellow]"
            
        stage_text = Text.assemble(
            ("Current Stage: ", "bold white"),
            (f"{self.active_stage}", "bold green"),
            (progress_text, "white")
        )

        # Spinner - only animate spinner if running, hide if finished/error/ready
        spinner = None
        if "Finished" not in self.active_stage and "Error" not in self.active_stage and "Ready" not in self.active_stage:
            spinner = Spinner("dots", style="green")

        header_table = Table.grid(expand=True)
        header_table.add_column(justify="left", ratio=1)
        header_table.add_column(justify="right", ratio=1)
        
        if spinner:
            right_content = Columns([stage_text, spinner])
            header_table.add_row(header_text, right_content)
        else:
            header_table.add_row(header_text, stage_text)
        
        layout["header"].update(Panel(header_table, border_style="grey37"))

        # 2. Dynamic ATT Tree Lineage
        tree = Tree("[bold royal_blue1]📁 Root AI Level 0 (Architect)[/bold royal_blue1]")
        
        if self.workflow_manager and hasattr(self.workflow_manager, "att_manager"):
            manager = self.workflow_manager.att_manager
            
            # Find and display active dynamic Agent Teams
            active_teams = list(manager.teams.values())
            
            # Group teams by parent lineage (level 1 has no parent team)
            level_1_teams = []
            for team in active_teams:
                parent = team.parent_team or manager.find_parent_team(team)
                if parent is None:
                    level_1_teams.append(team)

            for team in level_1_teams:
                # Level 1 Tree Node
                team_desc = f"[bold cyan]👥 {team.team_id} ({team.preset_name})[/bold cyan] - [dim]{team.team_purpose}[/dim]"
                if team.chapter_num is not None:
                    team_desc += f" [yellow](Ch {team.chapter_num})[/yellow]"
                team_tree = tree.add(team_desc)
                
                # Show active agents inside the team
                for member in team.members:
                    status = getattr(team, "status_map", {}).get(member.name, "Idle")
                    status_color = "green" if status == "Idle" else "yellow"
                    team_tree.add(f"[bold white]👤 {member.role}[/bold white] ({member.name}): [{status_color}]{status}[/{status_color}]")
                
                # Check for Level 2 children spawned by members of this team
                for child in active_teams:
                    child_parent = child.parent_team or manager.find_parent_team(child)
                    if child_parent and child_parent.team_id == team.team_id:
                        child_desc = f"[bold magenta]└── 👥 {child.team_id} ({child.preset_name})[/bold magenta] - [dim]{child.team_purpose}[/dim]"
                        child_tree = team_tree.add(child_desc)
                        for member in child.members:
                            status = getattr(child, "status_map", {}).get(member.name, "Idle")
                            status_color = "green" if status == "Idle" else "yellow"
                            child_tree.add(f"[bold white]👤 {member.role}[/bold white] ({member.name}): [{status_color}]{status}[/{status_color}]")

        tree_panel = Panel(
            tree,
            title="[bold blue]Lineage Tree of Active ATTs[/bold blue]",
            title_align="left",
            border_style="grey37",
            expand=True
        )
        layout["left"].update(tree_panel)

        # 3. Agent ReAct Activities & Thoughts Panel
        activities_text = Text()
        if self.recent_activities:
            for act in self.recent_activities:
                activities_text.append_text(Text.from_markup(act + "\n"))
        else:
            activities_text.append_text(Text("[dim]Waiting for agent activities...[/dim]\n"))

        activities_panel = Panel(
            activities_text,
            title="[bold yellow]Real-Time ReAct Agent Loop[/bold yellow]",
            title_align="left",
            border_style="grey37",
            expand=True
        )
        layout["activities"].update(activities_panel)

        # 4. System Logs Panel
        logs_text = Text()
        if self.recent_logs:
            for log in self.recent_logs:
                logs_text.append_text(Text.from_markup(log + "\n"))
        else:
            logs_text.append_text(Text("[dim]No system logs yet...[/dim]\n"))

        logs_panel = Panel(
            logs_text,
            title="[bold green]System & Memory Log[/bold green]",
            title_align="left",
            border_style="grey37",
            expand=True
        )
        layout["logs"].update(logs_panel)

        return DashboardRenderable(self, layout)
