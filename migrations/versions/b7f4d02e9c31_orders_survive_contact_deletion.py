"""orders survive contact deletion

Revision ID: b7f4d02e9c31
Revises: 833216a599db
Create Date: 2026-09-09 00:00:00.000000

Deleting a contact who had ever been sent a gift raised an
IntegrityError and 500'd, because orders.contact_id is a non-nullable FK
to contacts.id with no ON DELETE behaviour. Every other child of a
contact was already handled in routes/contacts.delete_contact; orders
were missed.

The business rule this encodes: **orders are financial records and
outlive the contact they were sent to.** An agent removing a client from
their CRM must not silently erase the spend and tax history of money
they actually paid. So contact_id becomes nullable and is cleared on
delete, while contact_name_snapshot preserves who the order was for --
the same pattern this codebase already uses for
ContactAuditLog.contact_name_snapshot and Order.gift_name_snapshot.

The alternative (cascade the delete, orders die with the contact) was
considered and rejected: it makes destroying org-wide financial records
a single button press for any agent, which is a bigger problem than the
one being fixed.

For a genuine erasure request, the right operation is to scrub the
personal fields on the retained rows, not to delete the financial
record. That tooling doesn't exist yet and isn't built here.

Backfill: existing orders get their contact's current household_name
copied into the snapshot, so history is complete for rows that predate
the column. Orders whose contact is somehow already missing get a
neutral placeholder rather than NULL, so the column can be relied on as
always-present for display.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b7f4d02e9c31'
down_revision = '833216a599db'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contact_name_snapshot', sa.String(length=255), nullable=True))

    # Backfill before relaxing the FK, so every existing row carries the
    # name it would otherwise lose the moment its contact is deleted.
    op.execute(sa.text("""
        UPDATE orders
           SET contact_name_snapshot = (
                   SELECT c.household_name FROM contacts c WHERE c.id = orders.contact_id
               )
         WHERE contact_name_snapshot IS NULL
    """))
    op.execute(sa.text("""
        UPDATE orders
           SET contact_name_snapshot = 'Deleted contact'
         WHERE contact_name_snapshot IS NULL
    """))

    with op.batch_alter_table('orders', schema=None) as batch_op:
        # Nullable from here on: NULL means "the contact this was sent to
        # has since been deleted", and contact_name_snapshot is what the
        # UI shows in that case.
        batch_op.alter_column(
            'contact_id',
            existing_type=sa.String(length=36),
            nullable=True,
        )


def downgrade():
    # Orders whose contact was deleted have no contact_id to restore, and
    # inventing one would be worse than refusing. Narrowing the column
    # back is only possible if no such rows exist.
    orphaned = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM orders WHERE contact_id IS NULL")
    ).scalar()
    if orphaned:
        raise RuntimeError(
            f"{orphaned} order(s) have no contact_id because their contact was deleted. "
            "Reassign or remove them before downgrading -- this migration will not "
            "fabricate contact references."
        )

    with op.batch_alter_table('orders', schema=None) as batch_op:
        batch_op.alter_column(
            'contact_id',
            existing_type=sa.String(length=36),
            nullable=False,
        )
        batch_op.drop_column('contact_name_snapshot')
