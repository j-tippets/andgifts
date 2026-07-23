from datetime import datetime
from app.extensions import db
from app.models.org import gen_uuid

# Shared by both CampaignRecipe and Campaign timing fields.
TIMING_DIRECTIONS = ("before", "same_day", "after")
TIMING_UNITS = ("day", "week", "month", "year")


class CampaignRecipeRule(db.Model):
    """
    One condition attached to a CampaignRecipe (Flow Library template).
    Mirrors CampaignRule below -- kept as a separate table (rather than
    one polymorphic table with two nullable owner FKs) for the same
    reason CampaignRecipe and Campaign are two separate tables instead
    of one shared base: simple non-nullable FKs, no CHECK-constraint
    reliance, and copying rows at from_recipe()/from_campaign() time is
    a plain loop instead of a re-parenting operation.

    field is a key into the registry in app/services/campaign_rules.py
    (CONDITION_FIELDS) -- either a built-in ("interest_tag",
    "gift_cooldown_days") or "custom:<CustomFieldDefinition.id>" for an
    org's own custom field. operator/value live in config as
    {"operator": ..., "value": ...}. Not a DB enum -- new field types
    are added in code (see that module's docstring), but new condition
    ROWS (which fields are attached to which campaign, with what
    operator/value) are pure data and need no deploy.
    """
    __tablename__ = "campaign_recipe_rules"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    recipe_id = db.Column(
        db.String(36), db.ForeignKey("campaign_recipes.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    field = db.Column("rule_type", db.String(80), nullable=False)
    config = db.Column(db.JSON, nullable=False, default=dict)
    # Display/evaluation order when a campaign has several conditions --
    # purely cosmetic today (order doesn't change whether all-must-pass
    # logic matches), but kept so the builder UI can show conditions in
    # the order the agent added them rather than an arbitrary DB order.
    position = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    recipe = db.relationship("CampaignRecipe", back_populates="rules")


class CampaignRule(db.Model):
    """One condition attached to a live Campaign. See CampaignRecipeRule
    above for the full design notes -- this is the same shape, just
    owned by a Campaign instead of a CampaignRecipe."""
    __tablename__ = "campaign_rules"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    campaign_id = db.Column(
        db.String(36), db.ForeignKey("campaigns.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    field = db.Column("rule_type", db.String(80), nullable=False)
    config = db.Column(db.JSON, nullable=False, default=dict)
    position = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    campaign = db.relationship("Campaign", back_populates="rules")


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

    # --- Trigger + timing ---
    event_type = db.Column(db.String(50), nullable=False)
    # direction + amount + unit replace the old signed offset_days --
    # lets the builder ask "Before / On the day / After" as its own
    # question, and lets timing be expressed in calendar-sensitive units
    # (month/year) instead of forcing everything through raw day counts.
    # See Campaign.trigger_offset() for how these combine at generation
    # time (calendar arithmetic for month/year, plain day math otherwise).
    timing_direction = db.Column(
        db.Enum(*TIMING_DIRECTIONS, name="campaign_timing_direction"),
        nullable=False, default="after",
    )
    timing_amount = db.Column(db.Integer, nullable=False, default=1)  # ignored when direction is same_day
    timing_unit = db.Column(
        db.Enum(*TIMING_UNITS, name="campaign_timing_unit"),
        nullable=False, default="day",
    )
    # Whether this flow should keep firing every time its event recurs
    # (e.g. every year for an annual anniversary), or only ever once per
    # contact. False replaces what used to be a separate "once per
    # contact" condition row -- see Campaign.repeat_enabled for the full
    # reasoning, since that's the copy that's actually live anywhere.
    repeat_enabled = db.Column(db.Boolean, nullable=False, default=True)

    # --- Action ---
    action_type = db.Column(
        db.Enum("gift", "email", "text", "handwritten_note", name="campaign_action_type"),
        nullable=False,
    )
    # Fixed gift choice for 'gift' actions when NOT using LLM selection.
    suggested_gift_id = db.Column(db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=True)
    price_max_cents = db.Column(db.Integer, nullable=True)  # gift budget cap, e.g. "$150 or less"
    use_llm_gift_selection = db.Column(db.Boolean, default=False, nullable=False)

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
    rules = db.relationship(
        "CampaignRecipeRule", back_populates="recipe", cascade="all, delete-orphan",
        order_by="CampaignRecipeRule.position",
    )

    @property
    def is_global(self):
        return self.org_id is None

    def timing_label(self):
        return _timing_label(self.timing_direction, self.timing_amount, self.timing_unit)


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
    timing_direction = db.Column(
        db.Enum(*TIMING_DIRECTIONS, name="live_campaign_timing_direction"),
        nullable=False, default="after",
    )
    timing_amount = db.Column(db.Integer, nullable=False, default=1)
    timing_unit = db.Column(
        db.Enum(*TIMING_UNITS, name="live_campaign_timing_unit"),
        nullable=False, default="day",
    )
    # False means "fire at most once per contact, ever" -- even if the
    # underlying event (a birthday, a closing anniversary) recurs every
    # year. Enforced in suggestion_engine.generate_campaign_suggestions_for_org
    # by checking whether this campaign has ever produced a suggestion
    # for the contact before, regardless of which occurrence triggered
    # it. True (the default) is today's existing behavior: it fires
    # again every time the event's next occurrence comes due.
    repeat_enabled = db.Column(db.Boolean, nullable=False, default=True)

    action_type = db.Column(
        db.Enum("gift", "email", "text", "handwritten_note", name="live_campaign_action_type"),
        nullable=False,
    )
    suggested_gift_id = db.Column(db.String(36), db.ForeignKey("gift_catalog_items.id"), nullable=True)
    price_max_cents = db.Column(db.Integer, nullable=True)
    use_llm_gift_selection = db.Column(db.Boolean, default=False, nullable=False)
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
    rules = db.relationship(
        "CampaignRule", back_populates="campaign", cascade="all, delete-orphan",
        order_by="CampaignRule.position",
    )

    @classmethod
    def from_recipe(cls, recipe, org_id, owner_user_id, created_by_user_id):
        """Copy a recipe's fields into a brand new, independent Campaign row."""
        campaign = cls(
            org_id=org_id,
            owner_user_id=owner_user_id,
            source_recipe_id=recipe.id,
            created_by_user_id=created_by_user_id,
            name=recipe.name,
            description=recipe.description,
            event_type=recipe.event_type,
            timing_direction=recipe.timing_direction,
            timing_amount=recipe.timing_amount,
            timing_unit=recipe.timing_unit,
            repeat_enabled=recipe.repeat_enabled,
            action_type=recipe.action_type,
            suggested_gift_id=recipe.suggested_gift_id,
            price_max_cents=recipe.price_max_cents,
            use_llm_gift_selection=recipe.use_llm_gift_selection,
            use_llm_copy=recipe.use_llm_copy,
            message_template=recipe.message_template,
            llm_prompt_hint=recipe.llm_prompt_hint,
            is_active=True,
        )
        campaign.rules = [
            CampaignRule(field=r.field, config=r.config, position=r.position)
            for r in recipe.rules
        ]
        return campaign

    @classmethod
    def from_campaign(cls, master, owner_user_id, created_by_user_id):
        """Fork a team-wide flow into a brand new, independent personal
        Campaign row for one agent ('Add to my profile'). Fields are
        copied at this moment -- editing or deleting the team-wide
        source afterward never touches this copy."""
        campaign = cls(
            org_id=master.org_id,
            owner_user_id=owner_user_id,
            source_recipe_id=master.source_recipe_id,
            forked_from_campaign_id=master.id,
            created_by_user_id=created_by_user_id,
            name=master.name,
            description=master.description,
            event_type=master.event_type,
            timing_direction=master.timing_direction,
            timing_amount=master.timing_amount,
            timing_unit=master.timing_unit,
            repeat_enabled=master.repeat_enabled,
            action_type=master.action_type,
            suggested_gift_id=master.suggested_gift_id,
            price_max_cents=master.price_max_cents,
            use_llm_gift_selection=master.use_llm_gift_selection,
            use_llm_copy=master.use_llm_copy,
            message_template=master.message_template,
            llm_prompt_hint=master.llm_prompt_hint,
            is_active=True,
        )
        campaign.rules = [
            CampaignRule(field=r.field, config=r.config, position=r.position)
            for r in master.rules
        ]
        return campaign

    def timing_label(self):
        return _timing_label(self.timing_direction, self.timing_amount, self.timing_unit)


def _timing_label(direction, amount, unit):
    """Shared by Campaign and CampaignRecipe -- the deterministic,
    plain-English phrase for a timing configuration ('1 year after',
    '5 days before', 'on the day of'). This is exactly the kind of
    summary text an LLM might be tempted to generate on the fly; it
    stays a plain function over structured fields instead, so the same
    inputs always produce the same sentence."""
    if direction == "same_day":
        return "on the day of"
    plural = "s" if amount != 1 else ""
    phrase = f"{amount} {unit}{plural}"
    return f"{phrase} after" if direction == "after" else f"{phrase} before"
