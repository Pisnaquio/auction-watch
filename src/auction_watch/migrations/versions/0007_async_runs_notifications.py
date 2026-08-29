"""Add durable run queue and notification delivery payloads."""

import sqlalchemy as sa
from alembic import op

revision = "0007_async_runs_notifications"
down_revision = "0006_profile_categories"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_queue",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("idempotency_key", sa.String(256), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("profile_id", sa.String(256), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enqueued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error", sa.Text()),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
        sa.UniqueConstraint("idempotency_key", name="uq_run_queue_idempotency_key"),
        sa.UniqueConstraint("run_id", name="uq_run_queue_run_id"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'partial', 'failed')",
            name="ck_run_queue_status",
        ),
        sa.CheckConstraint("attempt >= 0", name="ck_run_queue_attempt"),
    )
    op.create_index("ix_run_queue_claim", "run_queue", ["status", "available_at", "enqueued_at"])
    with op.batch_alter_table("notification_outbox") as batch:
        batch.add_column(
            sa.Column("notification_type", sa.String(16), nullable=False, server_default="matches")
        )
        batch.add_column(
            sa.Column("payload_json", sa.JSON(), nullable=False, server_default="{}")
        )


def downgrade() -> None:
    with op.batch_alter_table("notification_outbox") as batch:
        batch.drop_column("payload_json")
        batch.drop_column("notification_type")
    op.drop_index("ix_run_queue_claim", table_name="run_queue")
    op.drop_table("run_queue")
