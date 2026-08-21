from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, request, flash
from flask_login import login_required, current_user
from app.extensions import db
from app.models import SuggestedAction, ActionLog, ContactAuditLog, FlowRecommendation, Order, User
from app.models.actions import HANDWRITTEN_NOTE_PRICE_CENTS
from app.services.suggestion_engine import (
    generate_suggestions_for_org, generate_campaign_suggestions_for_org, expire_stale_suggestions,
)
from app.services.flow_recommendations import generate_flow_recommendations_for_user
from app.services.email import send_flow_action_email, send_wdf_fulfillment_notice, send_wdf_handwritten_note_notice
from app.services.payments import charge_saved_card
from app.services.wdf_client import send_wdf_webhook

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/dashboard")


def _get_visible_action(action_id, status=None):
    """Fetch a SuggestedAction the current user is allowed to act on --
    the same ownership scope as the dashboard's own query (see
    SuggestedAction.visible_to), so an agent can't approve/skip/delete a
    card for a contact they can't even see, e.g. via a raw POST to a
    stale/copied form. Admins are unrestricted, same as everywhere else."""
    query = SuggestedAction.query.filter_by(id=action_id, org_id=current_user.org_id)
    if status:
        query = query.filter_by(status=status)
    if not current_user.is_admin:
        query = SuggestedAction.visible_to(query, current_user)
    return query.first_or_404()


def _get_recommendation_or_404(recommendation_id):
    """Fetch a FlowRecommendation the current user is allowed to act on:
    your own, or (admin only) any agent's -- needed so an admin viewing
    another agent's queue via the dashboard's agent picker can actually
    dismiss/accept their recommendation cards."""
    query = FlowRecommendation.query.filter_by(id=recommendation_id, org_id=current_user.org_id)
    if not current_user.is_admin:
        query = query.filter_by(user_id=current_user.id)
    return query.first_or_404()


@dashboard_bp.route("/")
@login_required
def index():
    org = current_user.org

    # For MVP we generate on-demand rather than requiring a cron job to have
    # already run. Cheap because it's idempotent (skips dates already covered).
    # flow_triggers is universal (every tier, including free); ai_recommendations
    # is the paid-only upsell and only gates the recommendation engine below.
    if org.feature_enabled("flow_triggers"):
        generate_suggestions_for_org(org)
        generate_campaign_suggestions_for_org(org)
        expire_stale_suggestions(org)
    if org.feature_enabled("ai_recommendations"):
        generate_flow_recommendations_for_user(current_user)

    # Admin-only agent picker: narrows the dashboard to one agent's queue
    # (see SuggestedAction.owned_by). No selection (the default) keeps the
    # existing admin behavior of seeing the whole org's queue unfiltered --
    # this is deliberately NOT extended to non-admins, who are always
    # scoped by SuggestedAction.visible_to below regardless of query params.
    viewing_agent = None
    if current_user.is_admin:
        agent_id = request.args.get("agent_id", "").strip()
        if agent_id:
            viewing_agent = User.query.filter_by(id=agent_id, org_id=org.id).first()

    pending_query = SuggestedAction.query.filter_by(org_id=org.id, status="pending")
    if current_user.is_admin:
        if viewing_agent:
            pending_query = SuggestedAction.owned_by(pending_query, viewing_agent.id)
        # else: no extra filter -- admin sees every agent's pending suggestions.
    else:
        pending_query = SuggestedAction.visible_to(pending_query, current_user)
    pending = pending_query.order_by(SuggestedAction.target_date).all()

    # Flow recommendations are inherently per-agent (one row per user+event
    # type -- see FlowRecommendation's docstring), so there's no sensible
    # "recommendations for the whole org" merged view. Default to your own
    # (matches pre-existing behavior); an admin viewing a specific agent
    # sees that agent's instead.
    recommendations_for = viewing_agent or current_user
    flow_recommendations = (
        FlowRecommendation.query
        .filter_by(user_id=recommendations_for.id, status="pending")
        .order_by(FlowRecommendation.contact_count.desc())
        .all()
        if org.feature_enabled("ai_recommendations")
        else []
    )
    # One merged, swipeable stack rather than two separate UI blocks --
    # sorted by created_at so neither type is systematically pushed to
    # the front or back; see dashboard/index.html and today.js, which
    # both branch on item.item_kind (SuggestedAction vs
    # FlowRecommendation, see app/models/actions.py) to render/handle
    # each card's own action buttons.
    cards = sorted(pending + flow_recommendations, key=lambda item: item.created_at)

    agents = (
        User.query.filter_by(org_id=org.id, status="active").order_by(User.first_name, User.last_name).all()
        if current_user.is_admin else []
    )
    return render_template(
        "dashboard/index.html",
        cards=cards,
        ai_enabled=org.feature_enabled("ai_recommendations"),
        agents=agents,
        viewing_agent=viewing_agent,
    )


@dashboard_bp.route("/flow-recommendations/<recommendation_id>/dismiss", methods=["POST"])
@login_required
def dismiss_flow_recommendation(recommendation_id):
    rec = _get_recommendation_or_404(recommendation_id)
    rec.status = "dismissed"
    rec.resolved_at = datetime.utcnow()
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/flow-recommendations/<recommendation_id>/accept", methods=["POST"])
@login_required
def accept_flow_recommendation(recommendation_id):
    """Marks the recommendation accepted and sends the agent into the
    flow wizard with the trigger pre-filled -- see campaigns.campaign_new,
    which reads these same field names from request.values (query
    string on this GET redirect) the same way it already re-populates
    the form from request.form after a validation error. Nothing about
    the flow is actually created here; the agent still reviews and
    saves it themselves in the wizard, same as any other new flow."""
    rec = _get_recommendation_or_404(recommendation_id)
    rec.status = "accepted"
    rec.resolved_at = datetime.utcnow()
    db.session.commit()
    return redirect(url_for(
        "campaigns.campaign_new",
        event_type=rec.event_type,
        name=f"{rec.event_label} outreach",
        timing_direction="same_day",
    ))


@dashboard_bp.route("/actions/<action_id>/approve", methods=["POST"])
@login_required
def approve_action(action_id):
    action = _get_visible_action(action_id)
    org = current_user.org

    # Server-side enforcement of the same check the dashboard card
    # already shows/disables Approve for -- the card-side check is UX
    # only (and is bypassed entirely by a raw POST or a stale page), so
    # this is what actually stops an email flow from trying to send to
    # no address, or a gift from charging a card for something that
    # has nowhere to ship.
    blocked_reason = action.readiness_blocked_reason
    if blocked_reason:
        flash(f"Can't approve — {blocked_reason} Add it on the contact's page, then try again.", "error")
        return redirect(request.referrer or url_for("dashboard.index"))

    # Gift suggestions can be swapped for a different catalog item right from
    # the dashboard before approving -- only trust an id that's actually
    # available to this org (respects catalog curation).
    if action.action_type == "gift":
        chosen_gift_id = request.form.get("gift_catalog_item_id", "").strip()
        if chosen_gift_id:
            available_ids = {g.id for g in current_user.org.available_catalog_items()}
            if chosen_gift_id in available_ids:
                action.suggested_gift_id = chosen_gift_id

    gift_payment_intent_id = None
    note_payment_intent_id = None
    if action.action_type == "gift" and action.suggested_gift:
        detail = f"{action.suggested_gift.name} (${action.suggested_gift.price_cents / 100:.2f})"
        if action.generated_message:
            detail += f" \u2014 note: {action.generated_message}"
        cost_cents = action.suggested_gift.price_cents

        # Charging happens BEFORE the action is marked approved -- per
        # Jeremiah's call, a failed charge blocks the approval entirely
        # (stays pending, decline reason shown) rather than approving
        # anyway with the payment flagged failed, since WDF would
        # otherwise ship something nobody's actually paid for.
        #
        # owning_agent (see SuggestedAction) is who gets billed: the
        # flow's owner for a campaign-triggered suggestion, or the
        # contact's own owner for the older non-flow suggestion engine.
        # Neither exists for a shared contact with no owning flow --
        # there's no agent to charge, so this blocks the same as a
        # decline would, with its own clear reason.
        billing_agent = action.owning_agent
        if not billing_agent:
            flash(
                "Can't approve — this suggestion has no clear owning agent to bill "
                "(a shared contact with no personal flow behind it). Assign the "
                "contact to an agent, or the flow to an agent, first.",
                "error",
            )
            return redirect(request.referrer or url_for("dashboard.index"))

        success, intent_id, error = charge_saved_card(
            billing_agent, cost_cents,
            description=f"{action.suggested_gift.name} for {action.contact.household_name}",
            metadata={"suggested_action_id": action.id},
        )
        if not success:
            flash(f"Can't approve — payment failed: {error}", "error")
            return redirect(request.referrer or url_for("dashboard.index"))

        gift_payment_intent_id = intent_id

        # Charging the card was never the finish line -- WDF still needs
        # to actually hear about this so they can build/ship it. This
        # was previously missing entirely: approving a gift suggestion
        # charged the card and stopped there, with no Order row and no
        # WDF notice at all (unlike the manual one-off order flow in
        # routes/orders.py, which does both). Mirrors that flow: create
        # a real Order (paid, fulfillment_method is always "shipping"
        # here -- there's no pickup/dropoff choice step in an automated
        # approval the way there is in the manual flow) and send the
        # same WDF notice.
        order = Order(
            org_id=org.id,
            contact_id=action.contact_id,
            ordered_by_user_id=billing_agent.id,
            gift_catalog_item_id=action.suggested_gift_id,
            gift_name_snapshot=action.suggested_gift.name,
            gift_price_cents=action.suggested_gift.price_cents,
            fulfillment_method="shipping",
            shipping_address_snapshot=action.contact.formatted_shipping_address(),
            status="paid",
            stripe_payment_intent_id=intent_id,
            payment_method_id=billing_agent.default_payment_method.id if billing_agent.default_payment_method else None,
            paid_at=datetime.utcnow(),
        )
        db.session.add(order)
        db.session.flush()  # order.id, for the notice and for linking ActionLog below
        send_wdf_fulfillment_notice(order)
        gift_timing = action.gift_timing
        send_wdf_webhook(
            "gift", order.id, action.contact, billing_agent,
            item_description=action.suggested_gift.name,
            price_cents=action.suggested_gift.price_cents,
            note_text=action.generated_message,
            target_date=gift_timing["order_by"] if gift_timing else action.target_date,
        )
    elif action.action_type == "handwritten_note":
        detail = f"Handwritten note (${HANDWRITTEN_NOTE_PRICE_CENTS / 100:.2f})"
        if action.generated_message:
            detail += f" \u2014 note: {action.generated_message}"
        cost_cents = HANDWRITTEN_NOTE_PRICE_CENTS

        # Same billing-agent requirement and charge-before-approve
        # ordering as the gift branch above -- see its comment for why.
        billing_agent = action.owning_agent
        if not billing_agent:
            flash(
                "Can't approve — this suggestion has no clear owning agent to bill "
                "(a shared contact with no personal flow behind it). Assign the "
                "contact to an agent, or the flow to an agent, first.",
                "error",
            )
            return redirect(request.referrer or url_for("dashboard.index"))

        success, intent_id, error = charge_saved_card(
            billing_agent, cost_cents,
            description=f"Handwritten note for {action.contact.household_name}",
            metadata={"suggested_action_id": action.id},
        )
        if not success:
            flash(f"Can't approve — payment failed: {error}", "error")
            return redirect(request.referrer or url_for("dashboard.index"))

        note_payment_intent_id = intent_id
        send_wdf_handwritten_note_notice(action, billing_agent)
        send_wdf_webhook(
            "handwritten_note", action.id, action.contact, billing_agent,
            item_description="Handwritten note",
            price_cents=HANDWRITTEN_NOTE_PRICE_CENTS,
            note_text=action.generated_message,
            target_date=action.target_date,
        )
    else:
        detail = action.generated_message or action.reason_text
        cost_cents = None

    action.status = "approved"
    action.resolved_at = datetime.utcnow()

    # "email", "gift", and "handwritten_note" are the action types wired
    # up to an actual automated send/charge (text is still hidden/manual
    # for now). For email, a failed send does NOT block the approval --
    # the agent's decision to approve stands, we just record that it
    # didn't go out automatically so it surfaces in reports and they
    # know to follow up by hand. Gift and handwritten_note are the
    # opposite (see the charge blocks above, which already returned
    # early on failure before this point) -- a failed charge blocks
    # approval entirely rather than approving with payment flagged
    # failed, so by the time we get here their delivery_status can only
    # ever be "sent".
    delivery_status = "sent" if (gift_payment_intent_id or note_payment_intent_id) else None
    delivery_error = None
    if action.action_type == "email":
        allowed, block_reason = org.can_send_email_now(action.contact_id)
        if allowed:
            delivered, error = send_flow_action_email(action, current_user.full_name, sender_user=current_user)
            delivery_status = "sent" if delivered else "failed"
            delivery_error = error
        else:
            delivery_status = "blocked"
            delivery_error = block_reason
            flash(f"Approved, but not sent automatically: {block_reason}", "error")

    # gift and handwritten_note charge + notify WDF above; email sends
    # (or records why it didn't) above; text has no automated send at
    # all right now since the whole channel is hidden pending SMS
    # provider/compliance decisions -- this just logs it regardless.
    db.session.add(ActionLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        suggested_action_id=action.id,
        action_type=action.action_type,
        detail=detail,
        cost_cents=cost_cents,
        delivery_status=delivery_status,
        delivery_error=delivery_error,
        approved_by_user_id=current_user.id,
        stripe_payment_intent_id=gift_payment_intent_id or note_payment_intent_id,
    ))
    audit_summary = _action_summary_for_log(action, "Approved")
    if delivery_status == "failed":
        audit_summary += f" Email did not send automatically: {delivery_error}"
    elif delivery_status == "blocked":
        audit_summary += f" Email not sent (plan limit): {delivery_error}"

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_approved",
        summary=audit_summary,
        suggested_action_id=action.id,
    ))
    db.session.commit()
    if delivery_status == "failed":
        flash(f"Approved, but the email didn't send automatically: {delivery_error}", "error")
    else:
        flash("Action approved and queued.", "success")
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/skip", methods=["POST"])
@login_required
def skip_action(action_id):
    action = _get_visible_action(action_id)
    action.status = "skipped"
    action.resolved_at = datetime.utcnow()
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/delete", methods=["POST"])
@login_required
def delete_action(action_id):
    """Hides the action and stops THIS occurrence from ever regenerating
    (the (contact, event, target_date) tuple stays taken -- see
    _suggestion_exists / _campaign_suggestion_exists in the suggestion
    engine), but does NOT block a future occurrence of a recurring event:
    a deleted purchase-anniversary gift this year still lets the contact
    qualify for next year's anniversary. Logged to the contact's activity
    feed so it can be undone from there if it was a mistake."""
    action = _get_visible_action(action_id)
    action.status = "deleted"
    action.resolved_at = datetime.utcnow()

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_deleted",
        summary=_action_summary_for_log(action, "Deleted"),
        suggested_action_id=action.id,
    ))
    db.session.commit()
    flash("Deleted. It won't reappear for this occurrence, but the contact can still qualify next time.", "success")
    return redirect(request.referrer or url_for("dashboard.index"))


@dashboard_bp.route("/actions/<action_id>/undelete", methods=["POST"])
@login_required
def undelete_action(action_id):
    """Restores a deleted suggestion back to pending -- called from the
    contact's recent-activity list, not from the Today tab."""
    action = _get_visible_action(action_id, status="deleted")
    action.status = "pending"
    action.resolved_at = None

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_undeleted",
        summary=_action_summary_for_log(action, "Restored"),
        suggested_action_id=action.id,
    ))
    db.session.commit()
    flash("Restored. It's back on the Today tab.", "success")
    return redirect(request.referrer or url_for("contacts.view_contact", contact_id=action.contact_id))


@dashboard_bp.route("/actions/<action_id>/unapprove", methods=["POST"])
@login_required
def unapprove_action(action_id):
    """Undoes an approval: puts the suggestion back to pending (so it's
    editable again via edit_action and reappears as an active-event card
    on the Today tab / contact page), and removes the ActionLog row that
    approve_action created -- that row is the permanent spend/tax record,
    and it shouldn't survive an undone approval, both because it was
    never accurate to begin with and because re-approving later would
    otherwise create a second, duplicate ActionLog entry for the same
    suggestion. Only called from the contact's Recent Activity list,
    next to the action_approved entry it's undoing -- same pattern as
    undelete_action next to action_deleted."""
    action = _get_visible_action(action_id, status="approved")

    ActionLog.query.filter_by(suggested_action_id=action.id).delete(synchronize_session=False)

    action.status = "pending"
    action.resolved_at = None

    db.session.add(ContactAuditLog(
        org_id=action.org_id,
        contact_id=action.contact_id,
        contact_name_snapshot=action.contact.household_name,
        actor_user_id=current_user.id,
        actor_name_snapshot=current_user.full_name,
        action="action_unapproved",
        summary=_action_summary_for_log(action, "Un-approved"),
        suggested_action_id=action.id,
    ))
    db.session.commit()
    flash("Approval undone. It's back to pending -- edit it and re-approve when it's ready.", "success")
    return redirect(request.referrer or url_for("contacts.view_contact", contact_id=action.contact_id))


@dashboard_bp.route("/actions/<action_id>/edit", methods=["POST"])
@login_required
def edit_action(action_id):
    """Lets the agent fix the LLM's copy (email/text/handwritten_note) or
    swap the gift before ever approving/deleting it. Saves in place and
    stays pending -- this is deliberately separate from approve, which is
    still required to actually queue/send it."""
    action = _get_visible_action(action_id)
    if action.status != "pending":
        flash("Only pending suggestions can be edited.", "error")
        return redirect(url_for("dashboard.index"))

    if action.action_type == "gift":
        chosen_gift_id = request.form.get("gift_catalog_item_id", "").strip()
        if chosen_gift_id:
            available_ids = {g.id for g in current_user.org.available_catalog_items()}
            if chosen_gift_id in available_ids:
                action.suggested_gift_id = chosen_gift_id

    if "generated_message" in request.form:
        new_message = request.form.get("generated_message", "").strip()
        action.generated_message = new_message or None

    db.session.commit()
    flash("Changes saved.", "success")
    return redirect(url_for("dashboard.index"))


def _action_summary_for_log(action, verb):
    kind = action.action_type.replace("_", " ")
    if action.action_type == "gift" and action.suggested_gift:
        return f"{verb} suggested gift \u2014 {action.suggested_gift.name} \u2014 for {action.contact.household_name}."
    return f"{verb} suggested {kind} for {action.contact.household_name}."
