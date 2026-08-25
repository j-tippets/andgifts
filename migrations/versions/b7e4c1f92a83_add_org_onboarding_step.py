"""add orgs.onboarding_step

Revision ID: b7e4c1f92a83
Revises: a1f3c9d84b21
Create Date: 2026-08-25 00:00:00.000000

Supports resuming the signup wizard from the email-verification link
(see routes/onboarding.py, routes/auth.verify_email, and
Org.onboarding_route). Existing orgs (pre-wizard, or already through
it) are backfilled to 'done' so nothing pre-existing gets redirected
into the wizard.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7e4c1f92a83'
down_revision = 'a1f3c9d84b21'
branch_labels = None
depends_on = None

onboarding_step_enum = sa.Enum(
    "company_type", "plan", "billing", "invites", "done", name="onboarding_step",
)


def upgrade():
    bind = op.get_bind()
    onboarding_step_enum.create(bind, checkfirst=True)

    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            'onboarding_step', onboarding_step_enum, nullable=False, server_default='done',
        ))

    orgs = sa.table('orgs', sa.column('onboarding_step', sa.String))
    op.execute(orgs.update().values(onboarding_step='done'))


def downgrade():
    with op.batch_alter_table('orgs', schema=None) as batch_op:
        batch_op.drop_column('onboarding_step')

    onboarding_step_enum.drop(op.get_bind(), checkfirst=True)
