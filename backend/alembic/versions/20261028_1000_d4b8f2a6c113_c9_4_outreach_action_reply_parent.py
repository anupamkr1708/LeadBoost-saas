"""c9.4: outreach action reply parent

Revision ID: d4b8f2a6c113
Revises: c1a7e0d2b9f4
Create Date: 2026-10-28 10:00:00.000000

Purely additive: one nullable column on `outreach_actions`. NULL for every existing row
and for ordinary outreach; set only for a reply, to the Mailer `message_reference` (the Mailer's opaque
32-hex reply token for the inbound message being answered; never a database id) of that message. The column is
a plain nullable String with no format constraint, so the token format needs no schema change. No table
created/dropped, no existing column touched.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd4b8f2a6c113'
down_revision: Union[str, Sequence[str], None] = 'c1a7e0d2b9f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('outreach_actions') as batch:
        batch.add_column(sa.Column('reply_to_message_reference', sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('outreach_actions') as batch:
        batch.drop_column('reply_to_message_reference')
