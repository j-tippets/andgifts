from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class PracticeType(db.Model):
    """
    A category of business &Gifts supports (Real Estate, Law Firm,
    Dental, ...), managed entirely in App Admin -- adding a new one
    needs zero code changes or deploys. Each org is assigned one (see
    Org.practice_type_id); its milestones (see PracticeTypeMilestone)
    are the starting set copied into that org's own CustomEventType
    rows when the org is created or reassigned (see
    services.practice_types.seed_org_milestones).

    Deliberately NOT a hardcoded enum -- that's the whole point of this
    model existing instead of another STANDARD_EVENT_TYPES-style
    Python list.
    """
    __tablename__ = "practice_types"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    key = db.Column(db.String(60), unique=True, nullable=False)
    name = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    milestones = db.relationship(
        "PracticeTypeMilestone", back_populates="practice_type",
        cascade="all, delete-orphan", order_by="PracticeTypeMilestone.sort_order",
    )


class PracticeTypeMilestone(db.Model):
    """
    One preset milestone belonging to a PracticeType -- e.g. 'Closing'
    under Real Estate, or 'Case Filed' under Law Firm. Only a template:
    copying it into an org (see services.practice_types) produces a
    real, independent CustomEventType row that org can then rename or
    remove freely. Editing a preset here never touches orgs that
    already copied it -- see the App Admin practice-type edit page for
    that tradeoff spelled out.
    """
    __tablename__ = "practice_type_milestones"
    __table_args__ = (
        db.UniqueConstraint("practice_type_id", "key", name="uq_practice_type_milestone_key"),
    )

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    practice_type_id = db.Column(db.String(36), db.ForeignKey("practice_types.id"), nullable=False, index=True)

    key = db.Column(db.String(60), nullable=False)
    label = db.Column(db.String(100), nullable=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    practice_type = db.relationship("PracticeType", back_populates="milestones")
