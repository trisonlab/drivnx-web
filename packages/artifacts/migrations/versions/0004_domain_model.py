"""The rest of the M1 domain model (Section 12 of the direction doc, ADR 0002 and ADR 0003)."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TABLES = (
    "provenance_event",
    "contract_rule_revision",
    "contract_rule_field",
    "contract_rule",
    "contract_revision",
    "contract",
    "dictionary_entry_revision",
    "dictionary_entry",
    "finding_observation",
    "finding",
    "observation",
    "profile_run",
)


INDEXES = {
    "provenance_event_observation": "provenance_event (observation_id)",
    "provenance_event_entry_revision": "provenance_event (dictionary_entry_revision_id)",
    "provenance_event_rule_revision": "provenance_event (contract_rule_revision_id)",
    "provenance_event_finding": "provenance_event (finding_id)",
    "observation_profile_run": "observation (profile_run_id)",
}


def upgrade() -> None:
    # What the composite foreign keys below point at: a field in the same dataset as its owner.
    op.execute("CREATE UNIQUE INDEX field_dataset_and_id ON field (dataset_id, id)")
    op.execute(
        """
        CREATE TABLE profile_run (
            id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            mode TEXT NOT NULL,
            sample_size INTEGER,
            strategy TEXT,
            seed INTEGER,
            status TEXT NOT NULL
                CHECK (status IN ('running', 'completed', 'cancelled', 'failed', 'interrupted')),
            started_at TEXT NOT NULL,
            ended_at TEXT
        )
        """
    )
    op.execute(
        """
        CREATE TABLE observation (
            id TEXT PRIMARY KEY,
            profile_run_id TEXT NOT NULL REFERENCES profile_run (id),
            dataset_id TEXT NOT NULL REFERENCES dataset (id),
            field_id TEXT,
            metric TEXT NOT NULL,
            value TEXT NOT NULL,
            scope TEXT NOT NULL CHECK (scope IN ('full', 'sample', 'metadata')),
            method TEXT NOT NULL,
            precision TEXT NOT NULL CHECK (precision IN ('exact', 'approximate')),
            FOREIGN KEY (dataset_id, field_id) REFERENCES field (dataset_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finding (
            id TEXT PRIMARY KEY,
            profile_run_id TEXT NOT NULL REFERENCES profile_run (id),
            interpretation TEXT NOT NULL,
            why_it_matters TEXT NOT NULL,
            recommended_action TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE finding_observation (
            finding_id TEXT NOT NULL REFERENCES finding (id),
            observation_id TEXT NOT NULL REFERENCES observation (id),
            position INTEGER NOT NULL,
            PRIMARY KEY (finding_id, observation_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE dictionary_entry (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES dataset (id),
            field_id TEXT,
            FOREIGN KEY (dataset_id, field_id) REFERENCES field (dataset_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE dictionary_entry_revision (
            id TEXT PRIMARY KEY,
            entry_id TEXT NOT NULL REFERENCES dictionary_entry (id),
            revision INTEGER NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('inferred', 'confirmed')),
            content TEXT NOT NULL,
            UNIQUE (entry_id, revision)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL UNIQUE REFERENCES dataset (id),
            current_version INTEGER NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract_revision (
            contract_id TEXT NOT NULL REFERENCES contract (id),
            version INTEGER NOT NULL,
            purpose TEXT NOT NULL,
            limitations TEXT NOT NULL,
            PRIMARY KEY (contract_id, version)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract_rule (
            id TEXT PRIMARY KEY,
            contract_id TEXT NOT NULL REFERENCES contract (id),
            rule_type TEXT NOT NULL CHECK (rule_type IN (
                'required', 'type', 'unique', 'accepted_values', 'range', 'identifier',
                'relationship'
            ))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract_rule_field (
            rule_id TEXT NOT NULL REFERENCES contract_rule (id),
            role TEXT NOT NULL CHECK (role IN ('subject', 'target')),
            position INTEGER NOT NULL,
            field_id TEXT NOT NULL REFERENCES field (id),
            PRIMARY KEY (rule_id, role, position)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract_rule_revision (
            id TEXT PRIMARY KEY,
            rule_id TEXT NOT NULL REFERENCES contract_rule (id),
            revision INTEGER NOT NULL,
            contract_version INTEGER,
            state TEXT NOT NULL CHECK (state IN ('inferred', 'confirmed', 'defined')),
            is_tombstone INTEGER NOT NULL DEFAULT 0 CHECK (is_tombstone IN (0, 1)),
            params TEXT NOT NULL,
            UNIQUE (rule_id, revision)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE provenance_event (
            id TEXT PRIMARY KEY,
            author TEXT NOT NULL CHECK (author IN ('system', 'ai', 'user')),
            occurred_at TEXT NOT NULL,
            action TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            previous_value TEXT,
            ai_provider TEXT,
            ai_model TEXT,
            prompt_version TEXT,
            approved_context_categories TEXT NOT NULL,
            evidence_references TEXT NOT NULL,
            reviewer TEXT,
            observation_id TEXT REFERENCES observation (id),
            dictionary_entry_revision_id TEXT REFERENCES dictionary_entry_revision (id),
            contract_rule_revision_id TEXT REFERENCES contract_rule_revision (id),
            finding_id TEXT REFERENCES finding (id),
            CHECK (
                (observation_id IS NOT NULL)
                + (dictionary_entry_revision_id IS NOT NULL)
                + (contract_rule_revision_id IS NOT NULL)
                + (finding_id IS NOT NULL) = 1
            )
        )
        """
    )
    for name, columns in INDEXES.items():
        op.execute(f"CREATE INDEX {name} ON {columns}")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TABLE {table}")
    op.execute("DROP INDEX field_dataset_and_id")
