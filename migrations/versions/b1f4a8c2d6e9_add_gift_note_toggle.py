"""add gift note toggle and static note text to flows

Revision ID: b1f4a8c2d6e9
Revises: f4c7d2a9e1b6
Create Date: 2026-07-28 00:00:00.000000

Every gift-action flow has always auto-generated a note via the LLM
with no way to turn it off or override it. add_note makes that
explicit and toggleable; note_text lets an agent pin a fixed note
(same {contact_name}/{event_label}/{event_date} placeholders as
message_template) instead of leaving it to the LLM every time.
Defaulting add_note to true preserves today's behavior for every flow
that already exists.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b1f4a8c2d6e9'
down_revision = 'f4c7d2a9e1b6'
branch_labels = None
depends_on = None

TABLES = ("campaign_recipes", "campaigns")


def upgrade():
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column(
                "add_note", sa.Boolean(), nullable=False, server_default=sa.true(),
            ))
            batch_op.add_column(sa.Column("note_text", sa.Text(), nullable=True))


def downgrade():
    for table in TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_column("note_text")
            batch_op.drop_column("add_note")
