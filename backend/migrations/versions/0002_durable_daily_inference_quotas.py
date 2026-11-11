"""Durable UTC daily request and reserved output token budgets."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "daily_quotas",
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("subject_hash", sa.String(64), primary_key=True),
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("requests", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("scope IN ('ip','session')", name="ck_quota_scope"),
        sa.CheckConstraint("requests >= 0 AND tokens >= 0", name="ck_quota_usage"),
    )
    op.create_index("ix_daily_quotas_day", "daily_quotas", ["day"])


def downgrade():
    op.drop_table("daily_quotas")
