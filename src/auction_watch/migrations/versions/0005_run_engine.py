"""Add run provenance, durable leases and historical profile matches."""

import sqlalchemy as sa
from alembic import op

revision = "0005_run_engine"
down_revision = "0004_contextual_profile_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE runs SET status = CASE status "
            "WHEN 'pending' THEN 'queued' WHEN 'succeeded' THEN 'completed' "
            "WHEN 'degraded' THEN 'partial' ELSE status END"
        )
    )
    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.add_column(
            sa.Column("trigger", sa.String(16), nullable=False, server_default="manual")
        )
        batch.add_column(
            sa.Column("selected_sources", sa.JSON(), nullable=False, server_default="[]")
        )
        batch.drop_constraint("ck_runs_status", type_="check")
        batch.create_check_constraint(
            "ck_runs_status", "status IN ('queued', 'running', 'completed', 'partial', 'failed')"
        )
        batch.create_check_constraint(
            "ck_runs_trigger", "trigger IN ('manual', 'scheduled', 'system')"
        )

    op.create_table(
        "run_profiles",
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("profile_id", sa.String(256), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("run_id", "profile_id"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.run_id"]),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"]),
    )
    op.create_table(
        "run_leases",
        sa.Column("lease_name", sa.String(64), nullable=False),
        sa.Column("run_id", sa.String(256), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lease_name"),
        sa.UniqueConstraint("run_id", name="uq_run_leases_run_id"),
    )
    with op.batch_alter_table("profile_matches", recreate="always") as batch:
        batch.add_column(
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.add_column(sa.Column("first_match_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_match_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("confirmed_match_run_id", sa.String(256)))
        batch.add_column(sa.Column("confirmed_absence_run_id", sa.String(256)))
        batch.create_foreign_key(
            "fk_profile_matches_confirmed_match_run", "runs", ["confirmed_match_run_id"], ["run_id"]
        )
        batch.create_foreign_key(
            "fk_profile_matches_confirmed_absence_run",
            "runs",
            ["confirmed_absence_run_id"],
            ["run_id"],
        )
    op.execute(
        sa.text(
            "UPDATE profile_matches SET first_match_at = first_seen_at, "
            "last_match_at = last_seen_at WHERE first_match_at IS NULL OR last_match_at IS NULL"
        )
    )


def downgrade() -> None:
    with op.batch_alter_table("profile_matches", recreate="always") as batch:
        batch.drop_constraint("fk_profile_matches_confirmed_absence_run", type_="foreignkey")
        batch.drop_constraint("fk_profile_matches_confirmed_match_run", type_="foreignkey")
        batch.drop_column("confirmed_absence_run_id")
        batch.drop_column("confirmed_match_run_id")
        batch.drop_column("last_match_at")
        batch.drop_column("first_match_at")
        batch.drop_column("active")
    op.drop_table("run_leases")
    op.drop_table("run_profiles")
    with op.batch_alter_table("runs", recreate="always") as batch:
        batch.drop_constraint("ck_runs_trigger", type_="check")
        batch.drop_column("selected_sources")
        batch.drop_column("trigger")
