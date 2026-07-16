"""backfill legacy campaign fields into rule rows

Revision ID: f0540e8126d6
Revises: 6955205c1a0e
Create Date: 2026-07-16 00:00:00.000000

Data-only migration: for every existing Campaign/CampaignRecipe with a
non-null interest_tag, price_max_cents, or use_llm_gift_selection=True,
create the equivalent campaign_rules / campaign_recipe_rules row.

The legacy columns are intentionally NOT dropped here -- this is the
"migrate" step of expand -> migrate -> contract. Both representations
coexist after this runs; app/services/campaign_rules.py's
get_price_cap_cents/uses_llm_gift_selection helpers prefer the rule row
but fall back to the column, and the interest_tag column check in
suggestion_engine.py is left in place alongside the new interest_tag
rule type. Dropping the columns is a separate future migration, once
the wizard UI (which will be the only writer going forward) has
shipped and nothing reads the columns directly anymore.

downgrade() removes rows of these three rule_types wholesale rather
than trying to reverse-match which ones came from this migration --
same tradeoff as any data-migration downgrade; if rule rows of these
types were created by real usage after this ran, downgrading loses
those too. Given this is meant to be a one-way step in a larger
transition, that's an accepted risk, not an oversight.
"""
import json
import uuid

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f0540e8126d6'
down_revision = '6955205c1a0e'
branch_labels = None
depends_on = None


def _backfill(conn, source_table, source_id_col, rules_table, owner_col):
    rows = conn.execute(sa.text(
        f"SELECT {source_id_col} AS id, interest_tag, price_max_cents, use_llm_gift_selection "
        f"FROM {source_table}"
    )).mappings().all()

    to_insert = []
    for row in rows:
        if row["interest_tag"]:
            to_insert.append((row["id"], "interest_tag", {"tag": row["interest_tag"]}))
        if row["price_max_cents"]:
            to_insert.append((row["id"], "price_cap", {"max_cents": row["price_max_cents"]}))
        if row["use_llm_gift_selection"]:
            to_insert.append((row["id"], "llm_gift_selection", {}))

    for owner_id, rule_type, config in to_insert:
        conn.execute(
            sa.text(
                f"INSERT INTO {rules_table} (id, {owner_col}, rule_type, config, position, created_at) "
                f"VALUES (:id, :owner_id, :rule_type, :config, 0, NOW())"
            ),
            {
                "id": str(uuid.uuid4()),
                "owner_id": owner_id,
                "rule_type": rule_type,
                "config": json.dumps(config),
            },
        )


def upgrade():
    conn = op.get_bind()
    _backfill(conn, "campaigns", "id", "campaign_rules", "campaign_id")
    _backfill(conn, "campaign_recipes", "id", "campaign_recipe_rules", "recipe_id")


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM campaign_rules WHERE rule_type IN "
        "('interest_tag', 'price_cap', 'llm_gift_selection')"
    ))
    conn.execute(sa.text(
        "DELETE FROM campaign_recipe_rules WHERE rule_type IN "
        "('interest_tag', 'price_cap', 'llm_gift_selection')"
    ))
