# Interruption Resume Mechanism

This document defines how `--auto START_CHAPTER COUNT` resumes after interruption.

## Goal

Resume safely, not optimistically:

1. Validate generated runtime artifacts before continuing.
2. Skip only chapters with complete and valid outputs.
3. Discard and regenerate corrupted/incomplete chapter outputs.
4. Block resume if critical global frame files are invalid.

## Validation Scope

The runtime validator scans generated files under:

* `novel/frame/world`
* `novel/frame/plot`
* `novel/frame/chapter_guides`
* `novel/frame/archives`
* `novel/main_text/chapters`
* `novel/process/critiques`
* `novel/process/discussions`
* `novel/process/facts`
* `novel/process/reviews`
* `novel/process/revisions`
* `novel/Discussion_Log`

It validates file integrity by type:

* `.json`: parseable JSON; chapter facts payloads must also pass fact schema validation.
* `.jsonl`: each non-empty line must be valid JSON.
* `.md/.txt/.log`: UTF-8 readable text; chapter-scoped files and critical global files must be non-empty.

Binary stores (`.db`, `.faiss`) are not file-decoded by this layer; database integrity is checked through commit/state consistency for each chapter.

* **Automatic FAISS Recovery**: During startup, if the FAISS index file is missing or corrupt but the SQLite database has active records in the `vector_metadata` table, the system automatically rebuilds the vector index from database metadata.

## Chapter Completion Contract

A chapter is treated as complete only when all of the following are valid:

1. Chapter text file exists and is non-empty.
2. Chapter facts summary exists and is non-empty.
3. Chapter facts JSON exists, parses, and passes schema validation.
4. Latest `chapter_commits` row for `source='scan_chapter'` has:
   * `status='COMPLETED'`
   * valid `payload_json` parse and schema.

If any condition fails, the chapter is considered incomplete.

## Recovery Actions

### Chapter-Scoped Corruption / Incompleteness

If a chapter is incomplete or any chapter-scoped generated artifact is invalid:

1. Remove chapter artifacts:
   * guide, chapter text, facts JSON/summary/raw/invalid
   * chapter review and revision files
   * chapter guide/text discussion files
2. Remove incomplete scan commit traces (`STARTED` / `FAILED`) for that chapter.
3. Regenerate that chapter from:
   * `Plan -> Write -> Review/Revise -> Scan`

### Global Artifact Corruption

* `discussion_index.jsonl`: discard file and continue; it will be recreated as new entries are appended.
* compact archives (`characters_compact.md`, `world_rules_compact.md`): discard and regenerate from DB snapshot.
* critical frame files:
  * `world_bible.md`
  * `plot_outline.md`
  * `detailed_plot_outline.md`

If a critical frame file is invalid, resume is blocked after discard and a runtime error is raised. Operator must regenerate project frames (for example, rerun project start flow) before auto-resume.

## Execution Order in `run_continuous_loop`

1. Validate discussion index JSONL.
2. Validate all runtime artifacts and classify invalid targets:
   * invalid chapter-scoped
   * invalid global-scoped
3. Handle invalid global artifacts (repair or block).
4. For each chapter in range:
   * pre-discard chapter if pre-scan artifact validation flagged it
   * validate chapter completion contract
   * if complete: skip
   * else: discard remaining chapter traces if needed, then regenerate

## Why This Design

* Prevents silent continuation from partial writes.
* Keeps the database and filesystem artifacts aligned by commit-level checks.
* Keeps recovery deterministic at chapter granularity.
* Avoids compounding corruption by blocking on invalid global planning frames.
