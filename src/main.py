import sys
import os
import json
import logging
from typing import Optional, Tuple
import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table
from rich.panel import Panel

# Setup global rich console
console = Console()

# Configure root logger with RichHandler
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    datefmt="[%X]",
    handlers=[RichHandler(rich_tracebacks=True, show_path=False)]
)

def print_custom_help():
    console.print("[bold]Usage:[/bold] main.py [bold cyan][OPTIONS][/bold cyan]\n")
    console.print("AI Novelist CLI\n")
    console.print("[bold yellow]Options:[/bold yellow]")
    
    options = [
        ("--init", "Initialize only the novel workspace and create novel/Novel_Overview.md"),
        ("--start", "Start creation from novel/Novel_Overview.md"),
        ("--plan [bold magenta]INTEGER[/bold magenta]", "Generate a guide for a specific chapter number"),
        ("--write [bold magenta]INTEGER[/bold magenta]", "Write a specific chapter number (requires guide)"),
        ("--scan [bold magenta]INTEGER[/bold magenta]", "Scan a chapter for facts and update memory"),
        ("--auto [bold magenta]START_CHAPTER[/bold magenta] [bold magenta]COUNT[/bold magenta]", "Continuously generate COUNT chapters starting from START_CHAPTER."),
        ("--conflicts", "List pending conflicts in the DB queue"),
        ("--conflicts-json", "List pending conflicts with machine-readable diagnostics JSON"),
        ("--conflicts-triage", "List pending conflicts with priority and suggested actions"),
        ("--level [bold magenta]TEXT[/bold magenta]", "Optional conflict level filter for --conflicts/--conflicts-json/--conflicts-triage"),
        ("--resolve-conflict [bold magenta]CONFLICT_ID[/bold magenta] [bold magenta]ACTION[/bold magenta]", "Resolve one conflict with ACTION in {keep_existing, apply_incoming}"),
        ("--resolve-note [bold magenta]TEXT[/bold magenta]", "Optional note for conflict resolution"),
        ("--failed-commits", "List failed chapter commits"),
        ("--replay-commit [bold magenta]TEXT[/bold magenta]", "Replay a failed chapter commit by COMMIT_ID"),
        ("--triage-batch [bold magenta]LIMIT[/bold magenta]", "Resolve up to LIMIT NON_BLOCKING conflicts via keep_existing"),
        ("--rebuild-vectors", "Rebuild FAISS index from vector_metadata deterministically"),
        ("--help, -h", "Show this message and exit.")
    ]
    
    for opt, desc in options:
        console.print(f"  [bold cyan]{opt}[/bold cyan]")
        console.print(f"      {desc}\n")

app = typer.Typer(context_settings={"help_option_names": []}, add_completion=False, pretty_exceptions_enable=False)

@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    init: bool = typer.Option(
        False,
        "--init",
        help="Initialize only the novel workspace and create novel/Novel_Overview.md",
    ),
    start: bool = typer.Option(
        False,
        "--start",
        help="Start creation from novel/Novel_Overview.md",
    ),
    plan: Optional[int] = typer.Option(
        None,
        "--plan",
        help="Generate a guide for a specific chapter number",
    ),
    write: Optional[int] = typer.Option(
        None,
        "--write",
        help="Write a specific chapter number (requires guide)",
    ),
    scan: Optional[int] = typer.Option(
        None,
        "--scan",
        help="Scan a chapter for facts and update memory",
    ),
    auto: Optional[Tuple[int, int]] = typer.Option(
        None,
        "--auto",
        help="Continuously generate COUNT chapters starting from START_CHAPTER.",
    ),
    conflicts: bool = typer.Option(
        False,
        "--conflicts",
        help="List pending conflicts in the DB queue",
    ),
    conflicts_json: bool = typer.Option(
        False,
        "--conflicts-json",
        help="List pending conflicts with machine-readable diagnostics JSON",
    ),
    conflicts_triage: bool = typer.Option(
        False,
        "--conflicts-triage",
        help="List pending conflicts with priority and suggested actions",
    ),
    level: Optional[str] = typer.Option(
        None,
        "--level",
        help="Optional conflict level filter for --conflicts/--conflicts-json/--conflicts-triage",
    ),
    resolve_conflict: Optional[Tuple[str, str]] = typer.Option(
        None,
        "--resolve-conflict",
        metavar="CONFLICT_ID ACTION",
        help="Resolve one conflict with ACTION in {keep_existing, apply_incoming}",
    ),
    resolve_note: str = typer.Option(
        "",
        "--resolve-note",
        help="Optional note for conflict resolution",
    ),
    failed_commits: bool = typer.Option(
        False,
        "--failed-commits",
        help="List failed chapter commits",
    ),
    replay_commit: Optional[str] = typer.Option(
        None,
        "--replay-commit",
        help="Replay a failed chapter commit by COMMIT_ID",
    ),
    triage_batch: Optional[int] = typer.Option(
        None,
        "--triage-batch",
        metavar="LIMIT",
        help="Resolve up to LIMIT NON_BLOCKING conflicts via keep_existing",
    ),
    rebuild_vectors: bool = typer.Option(
        False,
        "--rebuild-vectors",
        help="Rebuild FAISS index from vector_metadata deterministically",
    ),
    help: bool = typer.Option(
        False,
        "--help",
        "-h",
        help="Show this message and exit.",
    ),
):
    # Check if any options were explicitly passed.
    has_args = any([
        init,
        start,
        plan is not None,
        write is not None,
        scan is not None,
        auto is not None,
        conflicts,
        conflicts_json,
        conflicts_triage,
        level is not None,
        resolve_conflict is not None,
        resolve_note != "",
        failed_commits,
        replay_commit is not None,
        triage_batch is not None,
        rebuild_vectors,
    ])

    if not has_args or help:
        print_custom_help()
        raise typer.Exit()

    try:
        from workflow import WorkflowManager

        workflow = WorkflowManager()

        if init:
            console.print(Panel("[bold cyan]Initializing Novel Workspace[/bold cyan]", border_style="cyan"))
            overview_path = workflow.initialize_novel_workspace()
            console.print("\n[bold green]✔ Initialization Complete[/bold green]")
            console.print(f"Novel overview template: [bold white]{overview_path}[/bold white]")
            console.print("Next step: fill [bold green]Novel_Overview.md[/bold green], then run [bold yellow]--start[/bold yellow]")

        elif start:
            console.print(Panel("[bold cyan]Starting From Novel Overview[/bold cyan]", border_style="cyan"))
            overview_text = workflow.load_novel_overview()
            bible_path = workflow.start_new_project(overview_text)
            console.print("\n[bold green]✔ World Setup Complete[/bold green]")
            console.print(f"World Bible: [bold white]{bible_path}[/bold white]")
            console.print("Critique: [bold white]novel/process/critiques/critique.md[/bold white]")

            console.print(Panel("[bold yellow]Planning Chapter 1[/bold yellow]", border_style="yellow"))
            guide = workflow.generate_chapter_guide(1)
            console.print("[bold green]✔ Guide generated.[/bold green]")

            console.print(Panel("[bold yellow]Writing Chapter 1[/bold yellow]", border_style="yellow"))
            chapter_text = workflow.write_chapter(1, guide)
            
            console.print(Panel("[bold yellow]Reviewing + Scanning Chapter 1[/bold yellow]", border_style="yellow"))
            workflow.review_revise_and_scan(1, guide, chapter_text)
            console.print("\n[bold green]✔ Success![/bold green] Chapter 001 saved to [bold white]novel/main_text/chapters/chapter_001.md[/bold white]")

        elif plan is not None:
            console.print(f"Generating guide for Chapter [bold yellow]{plan}[/bold yellow]...")
            workflow.generate_chapter_guide(plan)
            console.print("[bold green]✔ Done.[/bold green]")

        elif write is not None:
            console.print(f"Writing Chapter [bold yellow]{write}[/bold yellow]...")
            # Read the guide first
            guide_path = workflow.get_guide_path(write)
            if not os.path.exists(guide_path):
                console.print(
                    f"[bold red]Error:[/bold red] Guide for chapter {write} not found at "
                    f"[bold white]{guide_path}[/bold white]. Run [bold yellow]--plan {write}[/bold yellow] first."
                )
                raise typer.Exit(code=1)

            with open(guide_path, "r", encoding="utf-8") as f:
                guide = f.read()

            chapter_text = workflow.write_chapter(write, guide)
            console.print("Running review + scan...")
            workflow.review_revise_and_scan(write, guide, chapter_text)
            console.print("[bold green]✔ Done.[/bold green]")

        elif scan is not None:
            console.print(f"Scanning Chapter [bold yellow]{scan}[/bold yellow]...")
            workflow.scan_chapter(scan)
            console.print("[bold green]✔ Done.[/bold green]")

        elif auto is not None:
            start_chap, count = auto
            console.print(Panel(
                f"[bold cyan]Auto-Generating {count} chapters starting from Chapter {start_chap}[/bold cyan]",
                border_style="cyan"
            ))
            workflow.run_continuous_loop(start_chap, count)
            console.print("\n[bold green]✔ Batch generation complete.[/bold green]")

        elif conflicts_json:
            rows = workflow.list_pending_conflicts_detailed(limit=200, level=level)
            if not rows:
                console.print("[]")
                return
            console.print_json(data=rows)

        elif conflicts_triage:
            rows = workflow.list_pending_conflict_triage(limit=200, level=level)
            if not rows:
                console.print("[bold yellow]No pending conflicts.[/bold yellow]")
                return

            table = Table(title="[bold yellow]Pending Conflicts (Triage)[/bold yellow]", show_header=True, header_style="bold magenta")
            table.add_column("ID", style="dim", width=6)
            table.add_column("Level")
            table.add_column("Priority", justify="center")
            table.add_column("Type", style="cyan")
            table.add_column("Entity", style="green")
            table.add_column("Suggested Action")
            table.add_column("Reason Label", style="yellow")
            table.add_column("Chapter", justify="right")

            for row in rows:
                blocking_level = row.get('blocking_level')
                priority = row.get('priority')
                level_style = "[bold red]BLOCKING[/bold red]" if blocking_level == "BLOCKING" else "[yellow]NON_BLOCKING[/yellow]"
                priority_style = f"[bold red]{priority}[/bold red]" if priority == 1 else str(priority)

                table.add_row(
                    str(row['id']),
                    level_style,
                    priority_style,
                    str(row['conflict_type']),
                    f"{row['entity_type']}:{row['entity_key']}",
                    str(row.get('suggested_action')),
                    str(row.get('reason_label')),
                    str(row.get('chapter_num'))
                )
            console.print(table)

        elif conflicts:
            rows = workflow.list_pending_conflicts(limit=200, level=level)
            if not rows:
                console.print("[bold yellow]No pending conflicts.[/bold yellow]")
                return

            table = Table(title="[bold red]Pending Conflicts[/bold red]", show_header=True, header_style="bold magenta")
            table.add_column("ID", style="dim", width=6)
            table.add_column("Type", style="cyan")
            table.add_column("Entity", style="green")
            table.add_column("Source", style="dim")
            table.add_column("Chapter", justify="right")
            table.add_column("Created At", style="dim")
            table.add_column("Level")
            table.add_column("Priority", justify="center")
            table.add_column("Suggested Action")

            for row in rows:
                blocking_level = row[7] if len(row) > 7 else "BLOCKING"
                priority = row[8] if len(row) > 8 else 2
                suggested_action = row[9] if len(row) > 9 else "manual_review"

                level_style = "[bold red]BLOCKING[/bold red]" if blocking_level == "BLOCKING" else "[yellow]NON_BLOCKING[/yellow]"
                priority_style = f"[bold red]{priority}[/bold red]" if priority == 1 else str(priority)

                table.add_row(
                    str(row[0]),
                    str(row[3]),
                    f"{row[1]}:{row[2]}",
                    str(row[4]),
                    str(row[5]),
                    str(row[6]),
                    level_style,
                    priority_style,
                    str(suggested_action)
                )
            console.print(table)

        elif resolve_conflict is not None:
            conflict_id_text, action = resolve_conflict
            try:
                conflict_id = int(conflict_id_text)
            except ValueError:
                console.print(f"[bold red]Error:[/bold red] Invalid CONFLICT_ID: {conflict_id_text}")
                raise typer.Exit(code=1)
            ok = workflow.resolve_pending_conflict(conflict_id, action, note=resolve_note)
            if ok:
                console.print(f"[bold green]✔[/bold green] Resolved conflict [bold white]{conflict_id}[/bold white] with action=[bold yellow]{action}[/bold yellow]")
            else:
                console.print(
                    f"[bold red]Error:[/bold red] Failed to resolve conflict [bold white]{conflict_id}[/bold white]. "
                    f"Check id/action and ensure conflict is still pending."
                )
                raise typer.Exit(code=1)

        elif failed_commits:
            rows = workflow.list_failed_chapter_commits(limit=50)
            if not rows:
                console.print("[bold yellow]No failed chapter commits.[/bold yellow]")
                return

            table = Table(title="[bold red]Failed Chapter Commits[/bold red]", show_header=True, header_style="bold magenta")
            table.add_column("Commit ID", style="dim")
            table.add_column("Chapter", justify="right")
            table.add_column("Source", style="cyan")
            table.add_column("Status", style="bold red")
            table.add_column("Conflicts", justify="center")
            table.add_column("Replays", justify="center")
            table.add_column("Created At", style="dim")
            table.add_column("Error Message", style="yellow")

            for row in rows:
                table.add_row(
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    str(row[3]),
                    str(row[4]),
                    str(row[6]),
                    str(row[7]),
                    str(row[5])
                )
            console.print(table)

        elif replay_commit is not None:
            ok = workflow.replay_chapter_commit(replay_commit)
            if ok:
                console.print(f"[bold green]✔[/bold green] Replay succeeded for commit [bold white]{replay_commit}[/bold white]")
            else:
                console.print(f"[bold red]Error:[/bold red] Replay failed for commit [bold white]{replay_commit}[/bold white]")
                raise typer.Exit(code=1)

        elif triage_batch is not None:
            resolved = workflow.batch_triage_non_blocking(limit=max(0, triage_batch))
            console.print(f"[bold green]✔[/bold green] Batch triage resolved [bold yellow]{resolved}[/bold yellow] NON_BLOCKING conflicts.")

        elif rebuild_vectors:
            stats = workflow.rebuild_vector_index()
            console.print(
                f"[bold green]✔[/bold green] Vector rebuild done. "
                f"rebuilt=[bold yellow]{stats.get('rebuilt', 0)}[/bold yellow] "
                f"skipped=[bold yellow]{stats.get('skipped', 0)}[/bold yellow]"
            )

    except KeyboardInterrupt:
        console.print("\n[bold yellow][INFO] Operation interrupted by user.[/bold yellow]")
        raise typer.Exit(code=130)
    except typer.Exit as e:
        sys.exit(e.code)
    except Exception as e:
        console.print(f"\n[bold red][ERROR][/bold red] {e}")
        sys.exit(1)

if __name__ == "__main__":
    app()
