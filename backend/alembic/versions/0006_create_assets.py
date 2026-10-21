"""create assets table

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("company_name", sa.String(255), nullable=False),
        sa.Column("sector", sa.String(64), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
    )
    op.create_index("ix_assets_symbol", "assets", ["symbol"], unique=True)


def downgrade() -> None:
    op.drop_table("assets")
