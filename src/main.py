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
from workflow_components.bootstrap_messages import (
    ConfigurationError,
    get_bootstrap_message,
    load_project_language,
)


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI_LANGUAGE = load_project_language(PROJECT_ROOT)


def get_message(key: str, **kwargs) -> str:
    """Resolve CLI text without importing the fully validated runtime config."""

    return get_bootstrap_message(PROJECT_ROOT, CLI_LANGUAGE, key, **kwargs)

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
    console.print(f"[bold]{get_message('cli.usage')}[/bold]\n")
    console.print(f"{get_message('cli.title')}\n")
    console.print(f"[bold yellow]{get_message('cli.options')}[/bold yellow]")
    
    options = [
        ("--init", get_message("cli.option.init")),
        ("--start", get_message("cli.option.start")),
        ("--plan [bold magenta]INTEGER[/bold magenta]", get_message("cli.option.plan")),
        ("--write [bold magenta]INTEGER[/bold magenta]", get_message("cli.option.write")),
        ("--scan [bold magenta]INTEGER[/bold magenta]", get_message("cli.option.scan")),
        ("--auto [bold magenta]START_CHAPTER COUNT[/bold magenta]", get_message("cli.option.auto")),
        ("--conflicts", get_message("cli.option.conflicts")),
        ("--conflicts-json", get_message("cli.option.conflicts_json")),
        ("--conflicts-triage", get_message("cli.option.conflicts_triage")),
        ("--level [bold magenta]TEXT[/bold magenta]", get_message("cli.option.level")),
        ("--resolve-conflict [bold magenta]CONFLICT_ID ACTION[/bold magenta]", get_message("cli.option.resolve_conflict")),
        ("--resolve-note [bold magenta]TEXT[/bold magenta]", get_message("cli.option.resolve_note")),
        ("--failed-commits", get_message("cli.option.failed_commits")),
        ("--replay-commit [bold magenta]TEXT[/bold magenta]", get_message("cli.option.replay_commit")),
        ("--replay-failed-bulk", get_message("cli.option.replay_bulk")),
        ("--replay-dry-run", get_message("cli.option.replay_dry_run")),
        ("--replay-limit [bold magenta]INTEGER[/bold magenta]", get_message("cli.option.replay_limit")),
        ("--replay-max-attempts [bold magenta]INTEGER[/bold magenta]", get_message("cli.option.replay_attempts")),
        ("--replay-policy [bold magenta]TEXT[/bold magenta]", get_message("cli.option.replay_policy")),
        ("--triage-batch [bold magenta]LIMIT[/bold magenta]", get_message("cli.option.triage_batch")),
        ("--rebuild-vectors", get_message("cli.option.rebuild_vectors")),
        ("--ai-resolve-conflicts", get_message("cli.option.ai_resolve")),
        ("--help, -h", get_message("cli.option.help"))
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
        help=get_message("cli.option.init"),
    ),
    start: bool = typer.Option(
        False,
        "--start",
        help=get_message("cli.option.start"),
    ),
    plan: Optional[int] = typer.Option(
        None,
        "--plan",
        help=get_message("cli.option.plan"),
    ),
    write: Optional[int] = typer.Option(
        None,
        "--write",
        help=get_message("cli.option.write"),
    ),
    scan: Optional[int] = typer.Option(
        None,
        "--scan",
        help=get_message("cli.option.scan"),
    ),
    auto: Optional[Tuple[int, int]] = typer.Option(
        None,
        "--auto",
        help=get_message("cli.option.auto"),
    ),
    conflicts: bool = typer.Option(
        False,
        "--conflicts",
        help=get_message("cli.option.conflicts"),
    ),
    conflicts_json: bool = typer.Option(
        False,
        "--conflicts-json",
        help=get_message("cli.option.conflicts_json"),
    ),
    conflicts_triage: bool = typer.Option(
        False,
        "--conflicts-triage",
        help=get_message("cli.option.conflicts_triage"),
    ),
    level: Optional[str] = typer.Option(
        None,
        "--level",
        help=get_message("cli.option.level"),
    ),
    resolve_conflict: Optional[Tuple[str, str]] = typer.Option(
        None,
        "--resolve-conflict",
        metavar="CONFLICT_ID ACTION",
        help=get_message("cli.option.resolve_conflict"),
    ),
    resolve_note: str = typer.Option(
        "",
        "--resolve-note",
        help=get_message("cli.option.resolve_note"),
    ),
    failed_commits: bool = typer.Option(
        False,
        "--failed-commits",
        help=get_message("cli.option.failed_commits"),
    ),
    replay_commit: Optional[str] = typer.Option(
        None,
        "--replay-commit",
        help=get_message("cli.option.replay_commit"),
    ),
    replay_failed_bulk: bool = typer.Option(
        False, "--replay-failed-bulk", help=get_message("cli.option.replay_bulk")
    ),
    replay_dry_run: bool = typer.Option(
        False, "--replay-dry-run", help=get_message("cli.option.replay_dry_run")
    ),
    replay_limit: int = typer.Option(
        50, "--replay-limit", help=get_message("cli.option.replay_limit")
    ),
    replay_max_attempts: int = typer.Option(
        3, "--replay-max-attempts", help=get_message("cli.option.replay_attempts")
    ),
    replay_policy: str = typer.Option(
        "continue", "--replay-policy", help=get_message("cli.option.replay_policy")
    ),
    triage_batch: Optional[int] = typer.Option(
        None,
        "--triage-batch",
        metavar="LIMIT",
        help=get_message("cli.option.triage_batch"),
    ),
    rebuild_vectors: bool = typer.Option(
        False,
        "--rebuild-vectors",
        help=get_message("cli.option.rebuild_vectors"),
    ),
    ai_resolve_conflicts: bool = typer.Option(
        False,
        "--ai-resolve-conflicts",
        help=get_message("cli.option.ai_resolve"),
    ),
    help: bool = typer.Option(
        False,
        "--help",
        "-h",
        help=get_message("cli.option.help"),
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
        replay_failed_bulk,
        replay_dry_run,
        triage_batch is not None,
        rebuild_vectors,
        ai_resolve_conflicts,
    ])

    if not has_args or help:
        print_custom_help()
        raise typer.Exit()

    workflow = None
    try:
        from workflow import WorkflowManager

        workflow = WorkflowManager()
        if ai_resolve_conflicts:
            workflow.ai_resolve_conflicts = True

        if init:
            console.print(Panel(get_message("cli.init_title"), border_style="cyan"))
            overview_path = workflow.initialize_novel_workspace()
            console.print(get_message("cli.init_done"))
            console.print(get_message("cli.overview_path", path=overview_path))
            console.print(get_message("cli.init_next"))

        elif start:
            console.print(Panel(get_message("cli.start_title"), border_style="cyan"))
            
            def run_start():
                overview_text = workflow.load_novel_overview()
                bible_path = workflow.start_new_project(overview_text)
                guide = workflow.generate_chapter_guide(1)
                chapter_text = workflow.write_chapter(1, guide)
                workflow.review_revise_and_scan(1, guide, chapter_text)
                return bible_path

            bible_path = workflow.run_with_dashboard(run_start)
            console.print(get_message("cli.start_done"))
            console.print(get_message("cli.world_path", path=bible_path))
            console.print(get_message("cli.chapter_one_path", path="novel/main_text/chapters/chapter_001.md"))

        elif plan is not None:
            console.print(get_message("cli.generating_guide", chapter=plan))
            workflow.run_with_dashboard(workflow.generate_chapter_guide, plan)
            console.print(get_message("cli.done"))

        elif write is not None:
            console.print(get_message("cli.writing_chapter", chapter=write))
            # Read the guide first
            guide_path = workflow.get_guide_path(write)
            if not os.path.exists(guide_path):
                console.print(get_message(
                    "cli.guide_missing",
                    chapter=write,
                    path=guide_path,
                ))
                raise typer.Exit(code=1)

            with open(guide_path, "r", encoding="utf-8") as f:
                guide = f.read()

            def run_write():
                chapter_text = workflow.write_chapter(write, guide)
                workflow.review_revise_and_scan(write, guide, chapter_text)

            workflow.run_with_dashboard(run_write)
            console.print(get_message("cli.done"))

        elif scan is not None:
            console.print(get_message("cli.scanning_chapter", chapter=scan))
            workflow.run_with_dashboard(workflow.scan_chapter, scan)
            console.print(get_message("cli.done"))

        elif auto is not None:
            start_chap, count = auto
            console.print(Panel(
                get_message("cli.auto_title", count=count, chapter=start_chap),
                border_style="cyan"
            ))
            workflow.run_with_dashboard(workflow.run_continuous_loop, start_chap, count)
            console.print(get_message("cli.auto_done"))

        elif conflicts_json:
            rows = workflow.list_pending_conflicts_detailed(limit=200, level=level)
            if not rows:
                console.print("[]")
                return
            console.print_json(data=rows)

        elif conflicts_triage:
            rows = workflow.list_pending_conflict_triage(limit=200, level=level)
            if not rows:
                console.print(get_message("cli.no_conflicts"))
                return

            table = Table(title=get_message("cli.title.triage"), show_header=True, header_style="bold magenta")
            table.add_column(get_message("cli.column.id"), style="dim", width=6)
            table.add_column(get_message("cli.column.level"))
            table.add_column(get_message("cli.column.priority"), justify="center")
            table.add_column(get_message("cli.column.type"), style="cyan")
            table.add_column(get_message("cli.column.entity"), style="green")
            table.add_column(get_message("cli.column.action"))
            table.add_column(get_message("cli.column.reason"), style="yellow")
            table.add_column(get_message("cli.column.chapter"), justify="right")

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
                console.print(get_message("cli.no_conflicts"))
                return

            table = Table(title=get_message("cli.title.pending"), show_header=True, header_style="bold magenta")
            table.add_column(get_message("cli.column.id"), style="dim", width=6)
            table.add_column(get_message("cli.column.type"), style="cyan")
            table.add_column(get_message("cli.column.entity"), style="green")
            table.add_column(get_message("cli.column.source"), style="dim")
            table.add_column(get_message("cli.column.chapter"), justify="right")
            table.add_column(get_message("cli.column.created"), style="dim")
            table.add_column(get_message("cli.column.level"))
            table.add_column(get_message("cli.column.priority"), justify="center")
            table.add_column(get_message("cli.column.action"))

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
                console.print(get_message("cli.invalid_conflict_id", conflict_id=conflict_id_text))
                raise typer.Exit(code=1)
            ok = workflow.resolve_pending_conflict(conflict_id, action, note=resolve_note)
            if ok:
                console.print(get_message("cli.resolve_success", conflict_id=conflict_id, action=action))
            else:
                console.print(get_message("cli.resolve_failure", conflict_id=conflict_id))
                raise typer.Exit(code=1)

        elif failed_commits:
            rows = workflow.list_failed_chapter_commits(limit=50)
            if not rows:
                console.print(get_message("cli.no_failed_commits"))
                return

            table = Table(title=get_message("cli.title.failed_commits"), show_header=True, header_style="bold magenta")
            table.add_column(get_message("cli.column.commit_id"), style="dim")
            table.add_column(get_message("cli.column.chapter"), justify="right")
            table.add_column(get_message("cli.column.source"), style="cyan")
            table.add_column(get_message("cli.column.status"), style="bold red")
            table.add_column(get_message("cli.column.conflicts"), justify="center")
            table.add_column(get_message("cli.column.replays"), justify="center")
            table.add_column(get_message("cli.column.created"), style="dim")
            table.add_column(get_message("cli.column.error"), style="yellow")

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
                console.print(get_message("cli.replay_success", commit_id=replay_commit))
            else:
                console.print(get_message("cli.replay_failure", commit_id=replay_commit))
                raise typer.Exit(code=1)

        elif replay_failed_bulk:
            report = workflow.bulk_replay_failed_commits(
                limit=replay_limit,
                dry_run=replay_dry_run,
                max_attempts=replay_max_attempts,
                retry_policy=replay_policy,
            )
            table = Table(
                title=get_message("cli.bulk.title"),
                show_header=True,
                header_style="bold magenta",
            )
            for column in (
                get_message("cli.column.commit_id"),
                get_message("cli.column.chapter"),
                get_message("cli.column.eligible"),
                get_message("cli.column.attempts"),
                get_message("cli.column.outcome"),
                get_message("cli.column.error"),
            ):
                table.add_column(column)
            for item in report["commits"]:
                table.add_row(
                    str(item["commit_id"]),
                    str(item["chapter_num"]),
                    str(item["can_replay"]),
                    str(item.get("attempts", 0)),
                    str(item.get("outcome", "preview")),
                    "; ".join(item.get("validation_errors", [])) or str(item.get("error_after", "")),
                )
            console.print(table)
            console.print(get_message("cli.bulk.summary", **report))

        elif triage_batch is not None:
            resolved = workflow.batch_triage_non_blocking(limit=max(0, triage_batch))
            console.print(get_message("cli.triage_done", count=resolved))

        elif rebuild_vectors:
            stats = workflow.rebuild_vector_index()
            console.print(get_message("cli.rebuild_done", rebuilt=stats.get('rebuilt', 0), skipped=stats.get('skipped', 0)))

    except ConfigurationError as e:
        console.print(get_message("cli.configuration_error", error=e))
        sys.exit(2)
    except KeyboardInterrupt:
        console.print(get_message("cli.interrupted"))
        raise typer.Exit(code=130)
    except typer.Exit as e:
        sys.exit(e.code)
    except Exception as e:
        console.print(get_message("cli.error", error=e))
        sys.exit(1)
    finally:
        if workflow is not None:
            workflow.close()

if __name__ == "__main__":
    app()
