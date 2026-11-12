"""SamaFile initial schema, safe for fresh and existing PostgreSQL databases."""
from alembic import op

revision = "20261112_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE TABLE IF NOT EXISTS services (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            opening_time TIME NOT NULL,
            closing_time TIME NOT NULL,
            ticket_cutoff_time TIME NULL,
            no_show_timeout_seconds INTEGER NOT NULL DEFAULT 120,
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS counters (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            service_id INTEGER NOT NULL REFERENCES services(id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email VARCHAR(254) NOT NULL UNIQUE,
            full_name VARCHAR(100) NOT NULL,
            phone VARCHAR(30) NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(20) NOT NULL DEFAULT 'USER',
            counter_id INTEGER NULL REFERENCES counters(id),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS tickets (
            id SERIAL PRIMARY KEY,
            ticket_number VARCHAR(10) NOT NULL,
            service_id INTEGER NOT NULL REFERENCES services(id),
            counter_id INTEGER NULL REFERENCES counters(id),
            user_id INTEGER NULL REFERENCES users(id),
            user_name VARCHAR(100) NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'WAITING',
            created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            queue_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            called_at TIMESTAMP WITHOUT TIME ZONE NULL,
            reactivation_count INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Mise à niveau des bases créées par les versions antérieures du backend.
    op.execute("ALTER TABLE services ADD COLUMN IF NOT EXISTS ticket_cutoff_time TIME NULL")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(30) NULL")
    op.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS counter_id INTEGER NULL REFERENCES counters(id)")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS user_id INTEGER NULL REFERENCES users(id)")
    op.execute("ALTER TABLE tickets ADD COLUMN IF NOT EXISTS reactivation_count INTEGER NOT NULL DEFAULT 0")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ticket_status_history (
            id SERIAL PRIMARY KEY,
            ticket_id INTEGER NOT NULL REFERENCES tickets(id),
            previous_status VARCHAR(20) NULL,
            new_status VARCHAR(20) NOT NULL,
            changed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
            actor_id INTEGER NULL REFERENCES users(id)
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_users_email ON users(email)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_tickets_service_status ON tickets(service_id, status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_ticket_status_history_ticket_id ON ticket_status_history(ticket_id)")


def downgrade():
    # La migration peut avoir adopté des tables et des données préexistantes.
    # Un downgrade destructif n'est volontairement pas proposé.
    raise RuntimeError("Downgrade automatique désactivé : sauvegarder et restaurer la base explicitement.")
