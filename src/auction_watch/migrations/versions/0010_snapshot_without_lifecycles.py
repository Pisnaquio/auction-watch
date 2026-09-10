"""Drop the duplicated lifecycle table from stored snapshot payloads.

Snapshots embedded a verbatim copy of the ``opportunities`` table — around
10MB of a 12MB payload, tens of thousands of rows per run. Nothing ever read
it: the table itself is the source of truth. Keeping the copy made every
snapshot read parse it and grew the database by megabytes per run.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0010_snapshot_without_lifecycles"
down_revision = "0009_ignored_auctions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text("SELECT snapshot_id, payload_json FROM auction_snapshots")
    ).fetchall()
    for snapshot_id, raw in rows:
        if not raw:
            continue
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or "opportunities" not in payload:
            continue
        payload.pop("opportunities")
        connection.execute(
            sa.text(
                "UPDATE auction_snapshots SET payload_json = :payload "
                "WHERE snapshot_id = :snapshot_id"
            ),
            {
                "payload": json.dumps(
                    payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ),
                "snapshot_id": snapshot_id,
            },
        )


def downgrade() -> None:
    """The copy is redundant with the opportunities table; it is not rebuilt."""
