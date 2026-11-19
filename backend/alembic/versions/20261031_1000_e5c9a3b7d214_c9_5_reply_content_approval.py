"""c9.5: reply content approval evidence

Revision ID: e5c9a3b7d214
Revises: d4b8f2a6c113
Create Date: 2026-10-31 10:00:00.000000

Purely additive: three nullable columns on `outreach_actions`, all NULL for every existing row and for ordinary
outreach. Set only for a managed reply:

* reply_draft_requested_at   -- when the Mailer accepted the request to generate the reply draft (generation starts
                                BEFORE approval, so this is not a send timestamp).
* approved_content_sha256    -- the Mailer-issued digest of the exact subject+body the user approved. Opaque to
                                LeadBoost; echoed back to the Mailer on release, which re-verifies it.
* approved_draft_version     -- the draft version that digest belongs to (always 1 in this release).

No table created/dropped, no existing column or constraint touched; the (organization_id, idempotency_key)
uniqueness that makes a reply one-per-inbound-message is unchanged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e5c9a3b7d214'
down_revision: Union[str, Sequence[str], None] = 'd4b8f2a6c113'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('outreach_actions') as batch:
        batch.add_column(sa.Column('reply_draft_requested_at', sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column('approved_content_sha256', sa.String(length=64), nullable=True))
        batch.add_column(sa.Column('approved_draft_version', sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('outreach_actions') as batch:
        batch.drop_column('approved_draft_version')
        batch.drop_column('approved_content_sha256')
        batch.drop_column('reply_draft_requested_at')
