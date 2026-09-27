from pathlib import Path

import pytest
from alembic.script import Script, ScriptDirectory


MIGRATIONS = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))


@pytest.mark.parametrize(
    "migration",
    list(MIGRATIONS.walk_revisions()),
    ids=lambda migration: migration.revision,
)
def test_revision_fits_alembic_version_column(migration: Script) -> None:
    assert len(migration.revision) <= 32, (
        f"Revision {migration.revision!r} exceeds alembic_version.version_num VARCHAR(32)"
    )
