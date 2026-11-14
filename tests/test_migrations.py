"""PostgreSQL migration smoke test in a disposable schema."""
import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url


def _public_schema_signature(engine):
    inspector = inspect(engine)
    tables = inspector.get_table_names(schema="public")
    return {
        table: {
            "columns": tuple(
                (column["name"], str(column["type"]), column["nullable"], column["default"])
                for column in inspector.get_columns(table, schema="public")
            ),
            "indexes": tuple(sorted(
                (index["name"], tuple(index.get("column_names") or ()), index["unique"])
                for index in inspector.get_indexes(table, schema="public")
            )),
            "uniques": tuple(sorted(
                (constraint["name"], tuple(constraint.get("column_names") or ()))
                for constraint in inspector.get_unique_constraints(table, schema="public")
            )),
            "foreign_keys": tuple(sorted(
                (
                    constraint["name"],
                    tuple(constraint.get("constrained_columns") or ()),
                    constraint["referred_table"],
                    tuple(constraint.get("referred_columns") or ()),
                )
                for constraint in inspector.get_foreign_keys(table, schema="public")
            )),
        }
        for table in tables
    }


def test_alembic_upgrade_head_on_empty_postgresql_schema(monkeypatch):
    database_url = make_url(os.environ["TEST_DATABASE_URL"])
    schema = f"migration_test_{uuid4().hex}"
    admin_engine = create_engine(database_url)
    schema_engine = None
    public_before = _public_schema_signature(admin_engine)

    try:
        with admin_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        schema_url = database_url.set(
            query={**dict(database_url.query), "options": f"-c search_path={schema}"}
        )
        monkeypatch.setenv("DATABASE_URL", schema_url.render_as_string(hide_password=False))
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")

        schema_engine = create_engine(schema_url)
        with schema_engine.connect() as connection:
            assert connection.scalar(text("SELECT current_schema()")) == schema
        inspector = inspect(schema_engine)
        assert {
            "services",
            "counters",
            "users",
            "tickets",
            "ticket_status_history",
            "alembic_version",
        } <= set(inspector.get_table_names())
        assert "ticket_cutoff_minutes_before_closing" in {
            column["name"] for column in inspector.get_columns("services")
        }
        assert len(inspector.get_foreign_keys("tickets")) == 3
        email_index = next(index for index in inspector.get_indexes("users") if index["name"] == "ix_users_email")
        assert email_index["unique"] is True
        assert any(index["name"] == "ix_tickets_service_status" for index in inspector.get_indexes("tickets"))
        assert any(index["name"] == "ix_tickets_counter_id" for index in inspector.get_indexes("tickets"))
        assert any(index["name"] == "ix_tickets_user_id" for index in inspector.get_indexes("tickets"))
        assert any(index["name"] == "ix_counters_service_id" for index in inspector.get_indexes("counters"))
        command.check(config)
        assert _public_schema_signature(admin_engine) == public_before
    finally:
        if schema_engine is not None:
            schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin_engine.dispose()
