from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid


class CampaignRecipe(db.Model):
    """
    A campaign template in the Flow Library -- e.g. "5 days after a
    showing, ask for feedback by text" or "6 months after closing, send
    a handwritten thank-you + referral ask". Not active anywhere on its
    own: an agency admin or agent copies one into a real Campaign (see
    campaigns.py) to actually use it. Editing a recipe here never
    touches Campaigns that were already copied from it.

    org_id is NULL for a global flow -- authored by the platform, shown
    in every agency's Flow Library, manageable only by a platform_admin.
    org_id set to an agency's org id makes it a local (agency) flow --
    authored by that agency's own admin, shown only in that agency's
    Flow Library, manageable only by an admin in that same org.
    """
    __tablename__ = "campaign_recipes"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=True, index=True)

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
    org = db.relationship("Org")

    @property
    def is_global(self):
        return self.org_id is None

    def timing_label(self):
        if self.offset_days == 0:
            return "on the day of"
        if self.offset_days > 0:
            return f"{self.offset_days} day{'s' if self.offset_days != 1 else ''} after"
        return f"{abs(self.offset_days)} day{'s' if abs(self.offset_days) != 1 else ''} before"


class Campaign(db.Model):
    """
    A live, running rule for one org. Two ways it comes to exist:
    - Copied from a CampaignRecipe (see from_recipe) -- either as a
      team-wide flow or directly as one agent's personal flow.
    - Forked from a team-wide flow when an agent clicks "Add to my
      profile" (see from_campaign) -- becomes an independent personal
      copy at that moment; later edits or deletion of the team-wide
      source never touch it.
    - Built from scratch (agency admins can create team-wide flows;
      anyone can create their own personal flow from scratch).

    owner_user_id is NULL for a team-wide flow: a shared template that
    agency admins manage (create/edit/delete) for the whole org, but
    which never generates suggestions on its own -- each agent must
    explicitly add it to their profile first, which forks their own
    independent copy. owner_user_id set to an agent's id is a personal
    flow: it DOES generate suggestions, scoped to that agent's own
    contacts. Org admins can view, pause, edit, or delete ANY campaign
    in their org (team-wide or any agent's personal flow); an agent can
    only manage their own personal flows.
    """
    __tablename__ = "campaigns"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)
    owner_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)
    source_recipe_id = db.Column(db.String(36), db.ForeignKey("campaign_recipes.id"), nullable=True)
    # Set when this personal copy was forked from a team-wide (agency) flow --
    # i.e. an agent clicked "Add to my profile" on a Campaign with
    # owner_user_id IS NULL. NULL for team-wide flows themselves, for
    # personal flows built from scratch, and for personal flows added
    # directly from the CampaignRecipe library. ON DELETE SET NULL: deleting
    # the team-wide source later never touches (or deletes) this copy --
    # it just loses the "forked from" breadcrumb.
    forked_from_campaign_id = db.Column(
        db.String(36), db.ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True, index=True
    )
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
    forked_from = db.relationship("Campaign", remote_side=[id], foreign_keys=[forked_from_campaign_id])
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

    @classmethod
    def from_campaign(cls, master, owner_user_id, created_by_user_id):
        """Fork a team-wide flow into a brand new, independent personal
        Campaign row for one agent ('Add to my profile'). Fields are
        copied at this moment -- editing or deleting the team-wide
        source afterward never touches this copy."""
        return cls(
            org_id=master.org_id,
            owner_user_id=owner_user_id,
            source_recipe_id=master.source_recipe_id,
            forked_from_campaign_id=master.id,
            created_by_user_id=created_by_user_id,
            name=master.name,
            description=master.description,
            event_type=master.event_type,
            offset_days=master.offset_days,
            interest_tag=master.interest_tag,
            price_max_cents=master.price_max_cents,
            use_llm_gift_selection=master.use_llm_gift_selection,
            action_type=master.action_type,
            suggested_gift_id=master.suggested_gift_id,
            use_llm_copy=master.use_llm_copy,
            message_template=master.message_template,
            llm_prompt_hint=master.llm_prompt_hint,
            is_active=True,
        )

    def timing_label(self):
        if self.offset_days == 0:
            return "on the day of"
        if self.offset_days > 0:
            return f"{self.offset_days} day{'s' if self.offset_days != 1 else ''} after"
        return f"{abs(self.offset_days)} day{'s' if abs(self.offset_days) != 1 else ''} before"
