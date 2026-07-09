from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class CampaignRecipe(db.Model):
    """
    A platform-authored campaign template ("recipe book" entry) -- e.g.
    "5 days after a showing, ask for feedback by text" or "6 months after
    closing, send a handwritten thank-you + referral ask". Not active
    anywhere on its own: an agency admin or agent copies one into a real
    Campaign (see campaigns.py) to actually use it. Editing a recipe here
    never touches Campaigns that were already copied from it.

    Only a platform_admin can create/edit recipes. Every agency and agent
    can browse the book and copy from it.
    """
    __tablename__ = "campaign_recipes"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)

    name = db.Column(db.String(255), nullable=False)  # "Post-showing feedback ask"
    description = db.Column(db.Text, nullable=True)  # shown in the recipe book, explains the intent

    # --- Trigger: event_type + a signed day offset. ---
    # 0 = the day of the event itself (e.g. closing day).
    # Positive = N days AFTER the event (e.g. +5 for "5 days after showing",
    #   +180 for the 6-month post-closing note).
    # Negative = N days BEFORE the event (e.g. -7 for a pre-closing touch).
    event_type = db.Column(db.String(50), nullable=False)
    offset_days = db.Column(db.Integer, nullable=False, default=0)

    # --- Conditions (all optional refinements) ---
    interest_tag = db.Column(db.String(100), nullable=True)
    price_max_cents = db.Column(db.Integer, nullable=True)  # e.g. "$150 or less"
    use_llm_gift_selection = db.Column(db.Boolean, default=False, nullable=False)

    # --- Action ---
    action_type = db.Column(
        db.Enum("gift", "email", "text", "handwritten_note", name="campaign_action_type"),
        nullable=False,
    )
    # Fixed gift choice for 'gift' actions when NOT using LLM selection.
    suggested_gift_id = db.Column(db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=True)

    # For email/text/handwritten_note actions: either a static template
    # (supports {contact_name}, {event_label}, {event_date} placeholders,
    # same convention as GiftTrigger.default_reason_template) or, if
    # use_llm_copy is True, a short hint the LLM writes the real copy from
    # ("thank them again and ask for a referral").
    use_llm_copy = db.Column(db.Boolean, default=False, nullable=False)
    message_template = db.Column(db.Text, nullable=True)
    llm_prompt_hint = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)  # retire without deleting history

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    suggested_gift = db.relationship("GiftCatalogItem")

    def timing_label(self):
        if self.offset_days == 0:
            return "on the day of"
        if self.offset_days > 0:
            return f"{self.offset_days} day{'s' if self.offset_days != 1 else ''} after"
        return f"{abs(self.offset_days)} day{'s' if abs(self.offset_days) != 1 else ''} before"


class Campaign(db.Model):
    """
    A live, running rule for one org. Created by copying a CampaignRecipe
    in (fields are copied at that moment -- editing the source recipe
    later never touches campaigns already created from it) or, in a
    future stage, built from scratch.

    owner_user_id is NULL for an agency-wide campaign (applies to every
    contact in the org, managed by org admins) or set to a specific
    agent's id for a personal one-off (applies only to that agent's own
    contacts). Org admins can view and pause ANY campaign in their org,
    agency-wide or personal; an agent can only manage their own personal
    campaigns.
    """
    __tablename__ = "campaigns"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)
    owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    source_recipe_id = db.Column(db.String(36), db.ForeignKey("campaign_recipes.id"), nullable=True)
    created_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True)

    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)

    event_type = db.Column(db.String(50), nullable=False)
    offset_days = db.Column(db.Integer, nullable=False, default=0)

    interest_tag = db.Column(db.String(100), nullable=True)
    price_max_cents = db.Column(db.Integer, nullable=True)
    use_llm_gift_selection = db.Column(db.Boolean, default=False, nullable=False)

    action_type = db.Column(
        db.Enum("gift", "email", "text", "handwritten_note", name="live_campaign_action_type"),
        nullable=False,
    )
    suggested_gift_id = db.Column(db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=True)
    use_llm_copy = db.Column(db.Boolean, default=False, nullable=False)
    message_template = db.Column(db.Text, nullable=True)
    llm_prompt_hint = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    org = db.relationship("Org")
    owner = db.relationship("User", foreign_keys=[owner_user_id])
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
    source_recipe = db.relationship("CampaignRecipe")
    suggested_gift = db.relationship("GiftCatalogItem")

    @classmethod
    def from_recipe(cls, recipe, org_id, owner_user_id, created_by_user_id):
        """Copy a recipe's fields into a brand new, independent Campaign row."""
        return cls(
            org_id=org_id,
            owner_user_id=owner_user_id,
            source_recipe_id=recipe.id,
            created_by_user_id=created_by_user_id,
            name=recipe.name,
            description=recipe.description,
            event_type=recipe.event_type,
            offset_days=recipe.offset_days,
            interest_tag=recipe.interest_tag,
            price_max_cents=recipe.price_max_cents,
            use_llm_gift_selection=recipe.use_llm_gift_selection,
            action_type=recipe.action_type,
            suggested_gift_id=recipe.suggested_gift_id,
            use_llm_copy=recipe.use_llm_copy,
            message_template=recipe.message_template,
            llm_prompt_hint=recipe.llm_prompt_hint,
            is_active=True,
        )

    def timing_label(self):
        if self.offset_days == 0:
            return "on the day of"
        if self.offset_days > 0:
            return f"{self.offset_days} day{'s' if self.offset_days != 1 else ''} after"
        return f"{abs(self.offset_days)} day{'s' if abs(self.offset_days) != 1 else ''} before"
