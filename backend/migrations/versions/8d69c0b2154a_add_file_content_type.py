"""add file content type

Revision ID: 8d69c0b2154a
Revises: c1b8747076e6
Create Date: 2026-11-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "8d69c0b2154a"
down_revision: Union[str, Sequence[str], None] = "c1b8747076e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column(
            "content_type",
            sa.String(length=100),
            server_default=sa.text("'application/octet-stream'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("files", "content_type")