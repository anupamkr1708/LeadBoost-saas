"""l1: email account <-> mailer mailbox link

Revision ID: c1a7e0d2b9f4
Revises: baee12037c73
Create Date: 2026-09-12 12:00:00.000000

L1 schema change. Purely additive on top of the P1.4 revision (baee12037c73):
three nullable/defaulted columns on `email_accounts`, no table is created or
dropped, and no existing column (including `encrypted_credential`, which
LeadBoost still needs for its own SMTP verification) is touched.

- `mailer_mailbox_ref`      opaque Mailer public_reference (server-set only), UNIQUE.
- `mailer_sync_state`       'pending' | 'synced'; existing rows start 'pending' so
                            nothing is considered integrated-send-ready until it has
                            been reconciled with the Mailer.
- `mailer_sync_error_code`  safe code of the last failed reconcile.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c1a7e0d2b9f4'
down_revision: Union[str, Sequence[str], None] = 'baee12037c73'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # batch_alter_table keeps this portable to SQLite (ALTER ... ADD CONSTRAINT is
    # unsupported there) and is a plain ALTER on PostgreSQL.
    with op.batch_alter_table('email_accounts') as batch:
        batch.add_column(sa.Column('mailer_mailbox_ref', sa.String(), nullable=True))
        batch.add_column(sa.Column('mailer_sync_state', sa.String(), nullable=False, server_default='pending'))
        batch.add_column(sa.Column('mailer_sync_error_code', sa.String(), nullable=True))
        batch.create_unique_constraint('uq_email_accounts_mailer_mailbox_ref', ['mailer_mailbox_ref'])


def downgrade() -> None:
    with op.batch_alter_table('email_accounts') as batch:
        batch.drop_constraint('uq_email_accounts_mailer_mailbox_ref', type_='unique')
        batch.drop_column('mailer_sync_error_code')
        batch.drop_column('mailer_sync_state')
        batch.drop_column('mailer_mailbox_ref')
