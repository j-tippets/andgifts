"""
Copies a PracticeType's preset milestones into an org's own
CustomEventType rows. This is the ONLY place preset milestones get
copied -- called at org creation (self-registration always starts on
the 'real_estate' practice type today, see routes/auth.register) and
whenever an org's practice type is set or changed from App Admin (see
routes/app_admin.org_edit).

Deliberately additive and idempotent: only inserts a milestone the org
doesn't already have a CustomEventType with that key for, so re-running
this (e.g. after switching an org's practice type, or after an admin
adds a new milestone to a preset later) never touches or duplicates a
milestone the org already personalized -- renamed, or otherwise. There
is no ongoing sync between a preset and orgs that already copied it;
editing a preset in App Admin only affects orgs seeded after that edit.
"""
from app.extensions import db
from app.models.timeline import CustomEventType


def seed_org_milestones(org):
    if not org.practice_type_id:
        return []

    existing_keys = {
        c.key for c in CustomEventType.query.filter_by(org_id=org.id, scope="org").all()
    }

    seeded = []
    for milestone in org.practice_type.milestones:
        if milestone.key in existing_keys:
            continue
        event_type = CustomEventType(
            org_id=org.id,
            scope="org",
            owner_user_id=None,
            key=milestone.key,
            label=milestone.label,
        )
        db.session.add(event_type)
        seeded.append(event_type)
    return seeded
