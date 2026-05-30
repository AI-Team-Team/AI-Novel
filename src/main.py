import sys
import argparse
import os
import json

def main():
    try:
        from workflow import WorkflowManager

        parser = argparse.ArgumentParser(description="AI Novelist CLI")
        parser.add_argument(
            "--init",
            action="store_true",
            help="Initialize only the novel workspace and create novel/Novel_Overview.md",
        )
        parser.add_argument(
            "--start",
            action="store_true",
            help="Start creation from novel/Novel_Overview.md",
        )
        parser.add_argument("--plan", type=int, help="Generate a guide for a specific chapter number")
        parser.add_argument("--write", type=int, help="Write a specific chapter number (requires guide)")
        parser.add_argument("--scan", type=int, help="Scan a chapter for facts and update memory")
        parser.add_argument(
            "--auto",
            type=int,
            nargs=2,
            metavar=("START_CHAPTER", "COUNT"),
            help=(
                "Continuously generate COUNT chapters starting from START_CHAPTER. "
                "Supports strict resume: re-running the same range validates chapter integrity, "
                "skips completed chapters, and discards/regenerates incomplete chapter artifacts."
            ),
        )
        parser.add_argument("--conflicts", action="store_true", help="List pending conflicts in the DB queue")
        parser.add_argument(
            "--conflicts-json",
            action="store_true",
            help="List pending conflicts with machine-readable diagnostics JSON",
        )
        parser.add_argument(
            "--conflicts-triage",
            action="store_true",
            help="List pending conflicts with priority and suggested actions",
        )
        parser.add_argument(
            "--level",
            choices=["BLOCKING", "NON_BLOCKING"],
            help="Optional conflict level filter for --conflicts/--conflicts-json/--conflicts-triage",
        )
        parser.add_argument(
            "--resolve-conflict",
            nargs=2,
            metavar=("CONFLICT_ID", "ACTION"),
            help="Resolve one conflict with ACTION in {keep_existing, apply_incoming}",
        )
        parser.add_argument("--resolve-note", type=str, default="", help="Optional note for conflict resolution")
        parser.add_argument("--failed-commits", action="store_true", help="List failed chapter commits")
        parser.add_argument("--replay-commit", type=str, help="Replay a failed chapter commit by COMMIT_ID")
        parser.add_argument("--triage-batch", type=int, metavar="LIMIT", help="Resolve up to LIMIT NON_BLOCKING conflicts via keep_existing")
        parser.add_argument("--rebuild-vectors", action="store_true", help="Rebuild FAISS index from vector_metadata deterministically")
        
        args = parser.parse_args()
        
        workflow = WorkflowManager()
        
        if args.init:
            print("\n=== Initializing Novel Workspace ===\n")
            overview_path = workflow.initialize_novel_workspace()
            print("\n--- Initialization Complete ---")
            print(f"Novel overview template: {overview_path}")
            print("Next step: fill Novel_Overview.md, then run --start")

        elif args.start:
            print("\n=== Starting From Novel Overview ===\n")
            overview_text = workflow.load_novel_overview()
            bible_path = workflow.start_new_project(overview_text)
            print("\n--- World Setup Complete ---")
            print(f"World Bible: {bible_path}")
            print("Critique: novel/process/critiques/critique.md")

            print("\n=== Planning Chapter 1 ===")
            guide = workflow.generate_chapter_guide(1)
            print("Guide generated.")

            print("\n=== Writing Chapter 1 ===")
            chapter_text = workflow.write_chapter(1, guide)
            print("\n=== Reviewing + Scanning Chapter 1 ===")
            workflow.review_revise_and_scan(1, guide, chapter_text)
            print("\nSuccess! Chapter 001 saved to novel/main_text/chapters/chapter_001.md")

        elif args.plan:
            print(f"Generating guide for Chapter {args.plan}...")
            workflow.generate_chapter_guide(args.plan)
            print("Done.")

        elif args.write:
            print(f"Writing Chapter {args.write}...")
            # Read the guide first
            guide_path = workflow.get_guide_path(args.write)
            if not os.path.exists(guide_path):
                print(f"Error: Guide for chapter {args.write} not found at {guide_path}. Run --plan {args.write} first.")
                sys.exit(1)
                
            with open(guide_path, "r", encoding="utf-8") as f:
                guide = f.read()
                
            chapter_text = workflow.write_chapter(args.write, guide)
            print("Running review + scan...")
            workflow.review_revise_and_scan(args.write, guide, chapter_text)
            print("Done.")

        elif args.scan:
            print(f"Scanning Chapter {args.scan}...")
            workflow.scan_chapter(args.scan)
            print("Done.")
                 
        elif args.auto:
            start_chap, count = args.auto
            print(f"=== Auto-Generating {count} chapters starting from Chapter {start_chap} ===")
            workflow.run_continuous_loop(start_chap, count)
            print("\nBatch generation complete.")

        elif args.conflicts_json:
            rows = workflow.list_pending_conflicts_detailed(limit=200, level=args.level)
            if not rows:
                print("[]")
                return
            print(json.dumps(rows, ensure_ascii=False, indent=2))

        elif args.conflicts_triage:
            rows = workflow.list_pending_conflict_triage(limit=200, level=args.level)
            if not rows:
                print("No pending conflicts.")
                return
            print("Pending conflicts (triage):")
            for row in rows:
                print(
                    f"- id={row['id']} level={row.get('blocking_level')} priority={row.get('priority')} "
                    f"type={row['conflict_type']} entity={row['entity_type']}:{row['entity_key']} "
                    f"suggested_action={row.get('suggested_action')} reason={row.get('reason_label')} "
                    f"chapter={row.get('chapter_num')}"
                )

        elif args.conflicts:
            rows = workflow.list_pending_conflicts(limit=200, level=args.level)
            if not rows:
                print("No pending conflicts.")
                return
            print("Pending conflicts:")
            for row in rows:
                blocking_level = row[7] if len(row) > 7 else "BLOCKING"
                priority = row[8] if len(row) > 8 else 2
                suggested_action = row[9] if len(row) > 9 else "manual_review"
                print(
                    f"- id={row[0]} type={row[3]} entity={row[1]}:{row[2]} "
                    f"source={row[4]} chapter={row[5]} created_at={row[6]} "
                    f"level={blocking_level} priority={priority} suggested_action={suggested_action}"
                )

        elif args.resolve_conflict:
            conflict_id_text, action = args.resolve_conflict
            try:
                conflict_id = int(conflict_id_text)
            except ValueError:
                print(f"Invalid CONFLICT_ID: {conflict_id_text}")
                sys.exit(1)
            ok = workflow.resolve_pending_conflict(conflict_id, action, note=args.resolve_note)
            if ok:
                print(f"Resolved conflict {conflict_id} with action={action}")
            else:
                print(
                    f"Failed to resolve conflict {conflict_id}. "
                    f"Check id/action and ensure conflict is still pending."
                )
                sys.exit(1)

        elif args.failed_commits:
            rows = workflow.list_failed_chapter_commits(limit=50)
            if not rows:
                print("No failed chapter commits.")
                return
            print("Failed chapter commits:")
            for row in rows:
                print(
                    f"- commit_id={row[0]} chapter={row[1]} source={row[2]} status={row[3]} "
                    f"conflicts={row[4]} replay_count={row[6]} created_at={row[7]} error={row[5]}"
                )

        elif args.replay_commit:
            ok = workflow.replay_chapter_commit(args.replay_commit)
            if ok:
                print(f"Replay succeeded for commit {args.replay_commit}")
            else:
                print(f"Replay failed for commit {args.replay_commit}")
                sys.exit(1)

        elif args.triage_batch is not None:
            resolved = workflow.batch_triage_non_blocking(limit=max(0, args.triage_batch))
            print(f"Batch triage resolved {resolved} NON_BLOCKING conflicts.")

        elif args.rebuild_vectors:
            stats = workflow.rebuild_vector_index()
            print(f"Vector rebuild done. rebuilt={stats.get('rebuilt', 0)} skipped={stats.get('skipped', 0)}")

        else:
            parser.print_help()
            
    except KeyboardInterrupt:
        print("\n[INFO] Operation interrupted by user.")
        sys.exit(130)
    except Exception as e:
        print(f"\n[ERROR] {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
