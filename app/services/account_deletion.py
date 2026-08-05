"""
Permanently deletes an Org and every row scoped to it, across every
table that carries an org_id -- used when the *last* user of an org
deletes their own account (see profile.delete_account). A member
leaving an org that still has other users is a much smaller operation
(delete one User row) and is handled inline in profile.py, mirroring
team.delete_member.

Why this is its own careful module rather than `db.session.delete(org)`:
only two FKs in the whole schema (campaign_recipe_rules -> recipes,
campaign_rules -> campaigns) have a real DB-level ON DELETE CASCADE.
Everything else -- contacts -> people/methods/timeline/custom values,
the contact_badges join table, suggested_actions, action_log,
contact_audit_log, orders, campaigns/recipes, gift_catalog_items,
gift_triggers, badges, custom_field_definitions, custom_event_types,
support_requests -- is a plain FK with no cascade, and would either
block deletion with an IntegrityError or (worse, if deleted via a
bulk ORM query that skips relationship cascades) leave orphaned rows.

Several of these tables use org_id as nullable, where NULL means "a
shared global default usable by every org" (gift_catalog_items,
gift_triggers, campaign_recipes, badges) -- those rows must never be
touched here, only rows actually scoped to this org.

Deletion runs as an explicit, dependency-ordered sequence of raw
bulk deletes in a single transaction (leaf tables first, org row
last), then a single commit. Nothing here is best-effort -- if any
step fails, the whole transaction rolls back rather than leaving the
org half-deleted.
"""
from sqlalchemy import text, bindparam

from app.extensions import db
from app.services.storage import delete_avatar


def _in(sql, **params):
    """Runs `sql`, expanding any list-valued param into a proper SQL
    IN (...) clause -- text() needs an explicit expanding bindparam
    for this, it won't infer it from a plain list/tuple value."""
    stmt = text(sql)
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            stmt = stmt.bindparams(bindparam(key, expanding=True))
    return db.session.execute(stmt, params)


def delete_org_completely(org):
    """Permanently deletes `org`, every user in it, and every row
    scoped to it. Caller is responsible for authorization (only call
    this for a user deleting their own account when they're the org's
    last remaining user) and for logging the user out before/after --
    this function only touches the database."""
    org_id = org.id
    user_ids = [u.id for u in org.users]
    contact_ids = [c.id for c in org.contacts]

    # Best-effort avatar cleanup -- storage failures shouldn't block
    # account deletion, same rationale as delete_avatar's own docstring.
    for user in org.users:
        if user.photo_url:
            delete_avatar(user.photo_url)

    # An empty IN (...) is invalid SQL, and an empty expanding bindparam
    # is handled fine by SQLAlchemy (renders as a clause that matches
    # nothing), so no special-casing needed beyond passing [] through.

    # --- 1. Contact-tree leaves (explicit and ordered rather than
    # relying on ORM cascade, which raw SQL deletes bypass anyway) ---
    person_ids = [
        row[0] for row in _in(
            "SELECT id FROM contact_people WHERE contact_id IN :cids", cids=contact_ids
        )
    ]

    _in("DELETE FROM contact_methods WHERE person_id IN :pids", pids=person_ids)
    _in("DELETE FROM contact_people WHERE contact_id IN :cids", cids=contact_ids)
    _in("DELETE FROM timeline_events WHERE contact_id IN :cids", cids=contact_ids)
    _in("DELETE FROM custom_field_values WHERE contact_id IN :cids", cids=contact_ids)

    # --- 2. Badges: org-scoped + personal badges owned by this org's
    # users (owner_user_id, independent of org_id) -- clear their join
    # rows first regardless of which contact/org they're linked to. ---
    badge_ids = [
        row[0] for row in _in(
            "SELECT id FROM badges WHERE org_id = :org_id OR owner_user_id IN :uids",
            org_id=org_id, uids=user_ids,
        )
    ]
    _in(
        "DELETE FROM contact_badges WHERE contact_id IN :cids OR badge_id IN :bids",
        cids=contact_ids, bids=badge_ids,
    )
    _in("DELETE FROM badges WHERE id IN :bids", bids=badge_ids)

    # --- 3. Org-scoped activity/history tables (must go before the
    # rows they reference: contacts, campaigns, gift_catalog_items) ---
    _in("DELETE FROM org_catalog_selections WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM contact_audit_log WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM action_log WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM suggested_actions WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM orders WHERE org_id = :org_id", org_id=org_id)

    # --- 4. Campaigns before campaign_recipes: campaigns.source_recipe_id
    # references campaign_recipes with no cascade, so the campaign row
    # has to go first. Rule tables cascade automatically at the DB level. ---
    _in("DELETE FROM campaigns WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM campaign_recipes WHERE org_id = :org_id", org_id=org_id)

    # --- 5. Gift catalog: triggers before items (triggers reference
    # items with no cascade). Org-scoped rows only -- NULL org_id rows
    # are shared global defaults and must not be touched. ---
    _in("DELETE FROM gift_triggers WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM gift_catalog_items WHERE org_id = :org_id", org_id=org_id)

    # --- 6. Remaining org-scoped standalone tables ---
    _in("DELETE FROM custom_field_definitions WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM custom_event_types WHERE org_id = :org_id", org_id=org_id)
    _in(
        "DELETE FROM support_requests WHERE org_id = :org_id OR user_id IN :uids",
        org_id=org_id, uids=user_ids,
    )

    # --- 7. Contacts (now childless) ---
    _in("DELETE FROM contacts WHERE org_id = :org_id", org_id=org_id)

    # --- 8. Users: null the self-referential invite pointer first so a
    # multi-row delete doesn't hit a row still pointing at another row
    # in the same batch that hasn't been removed yet. ---
    _in("UPDATE users SET invited_by_user_id = NULL WHERE org_id = :org_id", org_id=org_id)
    _in("DELETE FROM users WHERE org_id = :org_id", org_id=org_id)

    # --- 9. The org itself ---
    _in("DELETE FROM orgs WHERE id = :org_id", org_id=org_id)

    db.session.commit()
