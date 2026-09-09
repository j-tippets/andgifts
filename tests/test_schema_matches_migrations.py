"""Asserts the model definitions and the migration chain describe the
same schema.

Why this exists
---------------
The test suite builds its database with `db.create_all()`, which reads
the SQLAlchemy models. Production builds its database by running the
Alembic migrations. Nothing has ever checked that those two agree, and
they had in fact diverged: migrations `a1c8e2f4b9d0` and `6f53c92d3337`
applied `ondelete='SET NULL'` to `campaigns.source_recipe_id` and
`suggested_actions.source_campaign_id`, and neither change was ever
reflected in the model.

That drift is not cosmetic -- it makes tests lie, in both directions:

  * Model stricter than production (what happened here): tests raise
    IntegrityError on an operation that works fine in production. You
    chase a bug that doesn't exist. Deleting a Flow Library recipe was
    reported as broken on exactly this basis; production was fine.

  * Model looser than production (the dangerous direction): tests pass
    on an operation that will 500 for a real customer. This is the same
    class of failure as running the suite without FK enforcement, just
    one level further up.

The check is deliberately narrow: tables, columns, nullability, and FK
delete behaviour. It does not compare types, indexes, or server
defaults, because SQLite and MySQL render those differently enough that
a strict comparison would be noise. If a future migration changes
something this doesn't cover, this test won't catch it -- but the
categories it does cover are the ones that silently change what the
database will and won't let the application do.
"""
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest
import sqlalchemy as sa

from app import create_app
from app.extensions import db

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def migrated_inspector():
    """Runs the full migration chain against a throwaway on-disk SQLite
    database and returns an inspector over the result.

    Driven through `flask db upgrade` in a subprocess rather than
    flask_migrate's Python API, for two reasons. It is the literal
    command `.do/app.yaml` runs pre-deploy, so this exercises the real
    path rather than an approximation of it. And a second `db.init_app`
    against an already-initialised app raises, so building an app with a
    different database URI in-process means fighting the extension.

    On-disk rather than in-memory because each Alembic step opens its
    own connection, and an in-memory SQLite database dies with the
    connection that created it. Module-scoped because replaying ~60
    migrations is the slow part of this file, and the result is
    read-only.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_path = pathlib.Path(tmp) / "migrated.db"

        env = dict(os.environ)
        # Not "testing": TestingConfig hardcodes an in-memory URI that
        # would silently override DATABASE_URL, and the migrations would
        # run against a database this fixture never sees.
        env["FLASK_ENV"] = "development"
        env["DATABASE_URL"] = f"sqlite:///{db_path}"

        result = subprocess.run(
            [sys.executable, "-m", "flask", "--app", "wsgi", "db", "upgrade"],
            cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=300,
        )
        assert result.returncode == 0, (
            "`flask db upgrade` failed against SQLite. The migration chain must stay "
            "runnable on SQLite for this comparison to be possible at all -- a bare "
            "op.alter_column is the usual cause; use batch_alter_table instead.\n"
            f"{result.stdout}\n{result.stderr}"
        )

        engine = sa.create_engine(f"sqlite:///{db_path}")
        yield sa.inspect(engine)
        engine.dispose()


@pytest.fixture(scope="module")
def model_inspector():
    """The schema db.create_all() produces from the models -- i.e. what
    every other test in this suite actually runs against."""
    application = create_app("testing")
    engine = sa.create_engine("sqlite://")
    with application.app_context():
        db.metadata.create_all(engine)
    inspector = sa.inspect(engine)
    yield inspector
    engine.dispose()


def _tables(inspector):
    # alembic_version exists only in the migrated schema, by construction.
    return {t for t in inspector.get_table_names() if t != "alembic_version"}


def _fk_delete_rules(inspector):
    """{(table, (columns,)): 'SET NULL' | 'CASCADE' | 'NO ACTION'}.

    A FK with no explicit ondelete is reported as NO ACTION, which is
    what both SQLite and MySQL/InnoDB actually enforce (i.e. RESTRICT)."""
    rules = {}
    for table in _tables(inspector):
        for fk in inspector.get_foreign_keys(table):
            key = (table, tuple(fk["constrained_columns"]))
            rules[key] = ((fk.get("options") or {}).get("ondelete") or "NO ACTION").upper()
    return rules


def test_same_tables(migrated_inspector, model_inspector):
    migrated, models = _tables(migrated_inspector), _tables(model_inspector)

    assert migrated == models, (
        f"only in migrations: {sorted(migrated - models)}; "
        f"only in models: {sorted(models - migrated)}"
    )


def test_same_columns_and_nullability(migrated_inspector, model_inspector):
    differences = []
    for table in sorted(_tables(migrated_inspector) & _tables(model_inspector)):
        migrated = {c["name"]: c["nullable"] for c in migrated_inspector.get_columns(table)}
        models = {c["name"]: c["nullable"] for c in model_inspector.get_columns(table)}

        for column in sorted(set(migrated) - set(models)):
            differences.append(f"{table}.{column}: in migrations, missing from models")
        for column in sorted(set(models) - set(migrated)):
            differences.append(f"{table}.{column}: in models, missing from migrations")
        for column in sorted(set(migrated) & set(models)):
            if migrated[column] != models[column]:
                differences.append(
                    f"{table}.{column}: migrations nullable={migrated[column]}, "
                    f"models nullable={models[column]}"
                )

    assert not differences, "schema drift:\n  " + "\n  ".join(differences)


def test_same_foreign_key_delete_behaviour(migrated_inspector, model_inspector):
    """The one that matters most for the deletion paths: whether a
    parent delete is blocked, cascaded, or nulled is decided entirely by
    this, and it is invisible in ordinary application code."""
    migrated = _fk_delete_rules(migrated_inspector)
    models = _fk_delete_rules(model_inspector)

    differences = []
    for key in sorted(set(migrated) | set(models)):
        table, columns = key
        in_migrations = migrated.get(key, "no such FK")
        in_models = models.get(key, "no such FK")
        if in_migrations != in_models:
            differences.append(
                f"{table}.{','.join(columns)}: migrations={in_migrations}, models={in_models}"
            )

    assert not differences, (
        "foreign key delete behaviour differs between the migrated schema and the models.\n"
        "Tests run against the models; production runs against the migrations, so this\n"
        "means the suite is enforcing different rules than production:\n  "
        + "\n  ".join(differences)
    )
