# Database Schema Migration & Compatibility Mechanism

This document records the original database versioning and incremental migration mechanism used in the **AI-Novel** project. This documentation serves as a reference for re-implementing backward-compatible schema updates once the project stabilizes.

## 1. Core Architecture

The schema versioning and migration pipeline is encapsulated within `MemorySchemaMixin` (`src/memory_components/schema_mixin.py`). It consists of three components:

1. **Schema Version Identifier**: A class constant `SCHEMA_VERSION` representing the latest schema code version.
2. **Metadata Table (`schema_meta`)**: A persistent key-value store in SQLite containing database-level metadata, including the active schema version under the key `'schema_version'`.
3. **Migration Runner (`_run_migrations`)**: A dispatcher that determines which migration steps need to run to upgrade an older database file to the latest schema version.

## 2. Legacy Migration Logic

Below is the original implementation of the versioning checks and migration runner:

```python
class MemorySchemaMixin:
    # Class constant representing the latest version of the database schema code.
    SCHEMA_VERSION = 6

    def _ensure_schema_meta_table(self):
        """Creates the key-value schema metadata table if it does not exist."""
        self.cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

    def _table_exists(self, table_name: str) -> bool:
        self.cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ? LIMIT 1",
            (table_name,),
        )
        return self.cursor.fetchone() is not None

    def _has_non_meta_tables(self) -> bool:
        """Checks if there are other tables in SQLite (legacy check)."""
        self.cursor.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
              AND name != 'schema_meta'
            LIMIT 1
            """
        )
        return self.cursor.fetchone() is not None

    def _get_schema_version(self) -> int:
        """Retrieves current schema version stored in the SQLite file."""
        self.cursor.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version' LIMIT 1"
        )
        row = self.cursor.fetchone()
        if row:
            try:
                return int(row[0])
            except (TypeError, ValueError):
                return 0
        
        # If schema_version is missing, check if this is an unversioned legacy database
        if self._has_non_meta_tables():
            raise RuntimeError(
                "Detected unsupported legacy database without schema version metadata. "
                "Please back up and re-initialize the database."
            )
        return 0

    def _set_schema_version(self, version: int):
        """Saves active schema version to SQLite."""
        self.cursor.execute(
            """
            INSERT INTO schema_meta (key, value) VALUES ('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(version),),
        )

    def _run_migrations(self):
        """Dispatches and runs incremental migrations sequentially in a transaction."""
        migrations = {
            1: self._migration_001_initial_schema,
            2: self._migration_002_add_indexes,
            3: self._migration_003_conflict_blocking_levels,
            4: self._migration_004_commit_replay_fields,
            5: self._migration_005_conflict_triage_fields,
            6: self._migration_006_audit_fields_for_fact_tables,
        }
        current_version = self._get_schema_version()
        
        # Iterate from the next version up to SCHEMA_VERSION
        for version in range(current_version + 1, self.SCHEMA_VERSION + 1):
            migration = migrations.get(version)
            if migration is None:
                raise RuntimeError(f"Missing migration for schema version {version}")
            migration()
            self._set_schema_version(version)
            self.conn.commit()

    def _init_sqlite(self):
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self._ensure_schema_meta_table()
        self._run_migrations()
```

## 3. Writing Custom Migrations

When a schema modification (e.g. adding a new table, adding columns, renaming columns/tables) is required:

1. **Increment Version**: Increase `SCHEMA_VERSION` by 1.
2. **Define Migration Method**: Write a method `_migration_XXX` performing the SQL statements (e.g., `ALTER TABLE`, `CREATE INDEX`).
3. **Register Migration**: Add the version key and the method reference to the `migrations` dictionary in `_run_migrations()`.

### Example (Table Renaming Migration)

```python
    def _migration_007_rename_timeline_to_timeline_events(self):
        # Rename table
        if self._table_exists("timeline"):
            self.cursor.execute("ALTER TABLE timeline RENAME TO timeline_events")
        
        # Recreate dependent indexes
        self.cursor.execute("DROP INDEX IF EXISTS idx_timeline_active")
        self.cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_timeline_active "
            "ON timeline_events(is_deleted, event_name, timestamp_str, id)"
        )
```
