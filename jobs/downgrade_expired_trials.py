"""
Entry point for the nightly scheduled job on DigitalOcean App Platform.
Enforces the hard cutoff on Solo's free trial (config.TRIAL_DAYS, set
at onboarding.plan -- see Org.trial_ends_at): any "starter" org whose
trial has expired and that never actually started paying (no live
Stripe subscription) gets dropped to "free".

Deliberately a HARD downgrade with no grace period (per Jeremiah's
call) -- this runs once a day, so in practice an org can be up to
~24h past trial_ends_at before this catches it, but there's no
additional buffer built in on top of that.

Downgrading only ever changes org.tier. It does NOT touch, delete, or
hide any existing data (contacts, flows, etc.) -- every tier limit in
this app (Org.can_add_contact, can_send_email_now, can_add_seat) is
already enforced live against whatever count exists right now, not
just at creation time, so an org that had 40 contacts on Solo and
drops to Free's lower cap keeps all 40 visible; only adding a 41st is
blocked. That's the intended "freeze forward" behavior and requires no
extra code here.

trial_ends_at is left in place after the downgrade (not cleared) --
both so Org.trial_recently_ended can show a short-lived "come back"
banner, and so re-picking Solo later doesn't grant a second trial (see
onboarding.plan).

Does NOT touch Team -- a Team trial lives entirely in Stripe (see
onboarding.billing_start's trial_period_days), and Stripe itself
handles the trial-to-paid transition (or cancellation on card
failure) via the subscription lifecycle, not this job.

Safe to re-run: only matches orgs still sitting on "starter" with no
subscription, so an org already downgraded (or one that converted in
the interim) is never touched again.
"""
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.extensions import db
from app.models import Org


def main():
    app = create_app("production")
    with app.app_context():
        expired = (
            Org.query
            .filter(Org.tier == "starter")
            .filter(Org.trial_ends_at.isnot(None))
            .filter(Org.trial_ends_at <= datetime.utcnow())
            .filter(Org.stripe_subscription_id.is_(None))
            .all()
        )
        for org in expired:
            org.tier = "free"
            print(f"[{org.name}] Solo trial expired ({org.trial_ends_at}) with no subscription -- downgraded to Free.")
        db.session.commit()
        print(f"Done. {len(expired)} org(s) downgraded.")


if __name__ == "__main__":
    main()
