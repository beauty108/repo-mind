"""Add private repo support and user auth.

- Create users table (email, hashed_password, github_access_token)
- Add is_private boolean to repositories
- Add owner_id FK to repositories → users (nullable, SET NULL on delete)

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create users table FIRST (repositories will FK to it)
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False, unique=True),
        sa.Column("hashed_password", sa.Text, nullable=False),
        sa.Column("github_access_token", sa.Text, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # Add is_private to repositories
    op.add_column(
        "repositories",
        sa.Column("is_private", sa.Boolean, nullable=False, server_default="false"),
    )

    # Add owner_id FK (nullable — existing repos have no owner)
    op.add_column(
        "repositories",
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_repositories_owner_id",
        "repositories",
        "users",
        ["owner_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_repositories_owner_id", "repositories", ["owner_id"])


def downgrade() -> None:
    op.drop_index("ix_repositories_owner_id", table_name="repositories")
    op.drop_constraint("fk_repositories_owner_id", "repositories", type_="foreignkey")
    op.drop_column("repositories", "owner_id")
    op.drop_column("repositories", "is_private")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
