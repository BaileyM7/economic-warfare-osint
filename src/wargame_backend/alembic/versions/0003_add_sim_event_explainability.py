"""Add explainability column to sim_events.

Holds the structured "X did Y because Z in hopes of W" triplet emitted by
country agents. Nullable so legacy rows render via the rationale-only fallback.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-16 00:00:00 UTC
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sim_events",
        sa.Column(
            "explainability",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("sim_events", "explainability")
