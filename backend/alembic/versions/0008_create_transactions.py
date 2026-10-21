"""create transactions ledger (append-only, enforced by trigger)

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15
"""
import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

action_enum = sa.Enum("BUY", "SELL", "DEPOSIT", "WITHDRAW", name="transaction_action")

# The guard lives in the database, so a buggy service, a careless migration, and a human at a
# psql prompt are all refused equally. Omitting UPDATE/DELETE functions in Python only protects
# code paths we remembered to write. A correction to the ledger is a new compensating row.
APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION transactions_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'transactions is an append-only ledger: % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    # No explicit action_enum.create() here: create_table emits CREATE TYPE for the Enum column
    # itself, and doing both raises DuplicateObject. drop_table does NOT drop the type, which is
    # why downgrade() still removes it by hand.
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("asset_id", sa.Integer(), sa.ForeignKey("assets.id", ondelete="RESTRICT"), nullable=True),
        sa.Column("action", action_enum, nullable=False),
        sa.Column("quantity", sa.Numeric(18, 8), nullable=False),
        sa.Column("price_per_share", sa.Numeric(18, 8), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "(action IN ('BUY', 'SELL') AND asset_id IS NOT NULL)"
            " OR (action IN ('DEPOSIT', 'WITHDRAW') AND asset_id IS NULL)",
            name="ck_transactions_asset_matches_action",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_transactions_quantity_positive"),
        sa.CheckConstraint("price_per_share > 0", name="ck_transactions_price_positive"),
    )
    op.create_index("ix_transactions_user_id", "transactions", ["user_id"])
    op.create_index("ix_transactions_asset_id", "transactions", ["asset_id"])
    op.create_index("ix_transactions_user_timestamp_id", "transactions", ["user_id", "timestamp", "id"])

    op.execute(APPEND_ONLY_FN)
    # Row-level for UPDATE/DELETE; TRUNCATE fires per statement, so it needs its own trigger.
    op.execute(
        """
        CREATE TRIGGER transactions_no_update_delete
        BEFORE UPDATE OR DELETE ON transactions
        FOR EACH ROW EXECUTE FUNCTION transactions_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER transactions_no_truncate
        BEFORE TRUNCATE ON transactions
        FOR EACH STATEMENT EXECUTE FUNCTION transactions_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS transactions_no_truncate ON transactions")
    op.execute("DROP TRIGGER IF EXISTS transactions_no_update_delete ON transactions")
    op.execute("DROP FUNCTION IF EXISTS transactions_append_only()")
    op.drop_table("transactions")
    action_enum.drop(op.get_bind(), checkfirst=True)
