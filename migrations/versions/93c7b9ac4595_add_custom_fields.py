"""add custom field definitions and values

Revision ID: 93c7b9ac4595
Revises: 1d65b3a7fa4c
Create Date: 2026-07-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '93c7b9ac4595'
down_revision = '1d65b3a7fa4c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'custom_field_definitions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('org_id', sa.String(length=36), nullable=False),
        sa.Column('scope', sa.Enum('org', 'personal', name='custom_field_scope'), nullable=False),
        sa.Column('owner_user_id', sa.String(length=36), nullable=True),
        sa.Column('label', sa.String(length=100), nullable=False),
        sa.Column(
            'field_type',
            sa.Enum('text', 'textarea', 'number', 'date', 'checkbox', 'select', name='custom_field_type'),
            nullable=False,
        ),
        sa.Column('options', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['org_id'], ['orgs.id']),
        sa.ForeignKeyConstraint(['owner_user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('custom_field_definitions', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_custom_field_definitions_org_id'), ['org_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_custom_field_definitions_owner_user_id'), ['owner_user_id'], unique=False
        )

    op.create_table(
        'custom_field_values',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('contact_id', sa.String(length=36), nullable=False),
        sa.Column('field_definition_id', sa.String(length=36), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['contact_id'], ['contacts.id']),
        sa.ForeignKeyConstraint(['field_definition_id'], ['custom_field_definitions.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('contact_id', 'field_definition_id', name='uq_custom_field_value'),
    )
    with op.batch_alter_table('custom_field_values', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_custom_field_values_contact_id'), ['contact_id'], unique=False
        )
        batch_op.create_index(
            batch_op.f('ix_custom_field_values_field_definition_id'),
            ['field_definition_id'], unique=False
        )


def downgrade():
    with op.batch_alter_table('custom_field_values', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_custom_field_values_field_definition_id'))
        batch_op.drop_index(batch_op.f('ix_custom_field_values_contact_id'))
    op.drop_table('custom_field_values')

    with op.batch_alter_table('custom_field_definitions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_custom_field_definitions_owner_user_id'))
        batch_op.drop_index(batch_op.f('ix_custom_field_definitions_org_id'))
    op.drop_table('custom_field_definitions')
