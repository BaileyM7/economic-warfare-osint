"""Add ais_positions table for live vessel-position buffer.

Populated by the AISStream ingest adapter during each snapshot window and
read by vessel_history() / vessel_port_calls() in the OSINT vessels tool.

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-27 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ais_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("mmsi", sa.String(length=16), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=True),
        sa.Column("course", sa.Float(), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "ingested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_ais_positions_mmsi_timestamp",
        "ais_positions",
        ["mmsi", sa.text("timestamp DESC")],
    )
    op.create_index(
        "ix_ais_positions_timestamp",
        "ais_positions",
        ["timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_ais_positions_timestamp", table_name="ais_positions")
    op.drop_index("ix_ais_positions_mmsi_timestamp", table_name="ais_positions")
    op.drop_table("ais_positions")
