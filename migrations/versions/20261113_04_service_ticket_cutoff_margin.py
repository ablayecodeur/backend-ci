"""Configure a per-service ticket cutoff margin, defaulting to 30 minutes."""
from alembic import op

revision = "20261113_04"
down_revision = "20261112_03"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE services
        ADD COLUMN IF NOT EXISTS ticket_cutoff_minutes_before_closing
        INTEGER NOT NULL DEFAULT 30
    """)


def downgrade():
    op.execute("""
        ALTER TABLE services
        DROP COLUMN IF EXISTS ticket_cutoff_minutes_before_closing
    """)
