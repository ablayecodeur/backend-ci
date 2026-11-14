"""Index frequently queried foreign keys."""
from alembic import op

revision = "20261112_03"
down_revision = "20261112_02"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE INDEX IF NOT EXISTS ix_counters_service_id ON counters(service_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_counter_id ON tickets(counter_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_user_id ON tickets(user_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS ix_tickets_user_id")
    op.execute("DROP INDEX IF EXISTS ix_tickets_counter_id")
    op.execute("DROP INDEX IF EXISTS ix_counters_service_id")
