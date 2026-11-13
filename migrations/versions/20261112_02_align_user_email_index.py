"""Align the email uniqueness index with SQLAlchemy metadata."""
from alembic import op

revision = "20261112_02"
down_revision = "20261112_01"
branch_labels = None
depends_on = None


def upgrade():
    # The initial migration created both a UNIQUE constraint and a redundant
    # non-unique index. Older adopted databases may only have the index, so
    # preserve uniqueness while normalizing the schema to the model's index.
    op.execute("""
        DO $$
        DECLARE constraint_name TEXT;
        BEGIN
            FOR constraint_name IN
                SELECT c.conname
                FROM pg_constraint AS c
                JOIN pg_attribute AS a
                  ON a.attrelid = c.conrelid
                 AND a.attname = 'email'
                WHERE c.conrelid = 'users'::regclass
                  AND c.contype = 'u'
                  AND c.conkey = ARRAY[a.attnum]::smallint[]
            LOOP
                EXECUTE format('ALTER TABLE users DROP CONSTRAINT %I', constraint_name);
            END LOOP;
        END $$
    """)
    op.execute("""
        DO $$
        DECLARE index_is_unique BOOLEAN;
        BEGIN
            SELECT i.indisunique
              INTO index_is_unique
              FROM pg_index AS i
              JOIN pg_class AS idx ON idx.oid = i.indexrelid
             WHERE idx.oid = to_regclass('ix_users_email')
               AND i.indrelid = 'users'::regclass;
            IF index_is_unique = FALSE THEN
                EXECUTE 'DROP INDEX ix_users_email';
            END IF;
        END $$
    """)
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users(email)")


def downgrade():
    # Restore the initial migration's UNIQUE constraint plus ordinary index.
    op.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                  FROM pg_constraint AS c
                  JOIN pg_attribute AS a
                    ON a.attrelid = c.conrelid
                   AND a.attname = 'email'
                 WHERE c.conrelid = 'users'::regclass
                   AND c.contype = 'u'
                   AND c.conkey = ARRAY[a.attnum]::smallint[]
            ) THEN
                ALTER TABLE users ADD CONSTRAINT users_email_key UNIQUE (email);
            END IF;
        END $$
    """)
    op.execute("DROP INDEX IF EXISTS ix_users_email")
    op.execute("CREATE INDEX ix_users_email ON users(email)")
