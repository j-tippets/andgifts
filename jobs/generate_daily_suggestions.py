"""
Entry point for the nightly scheduled job on DigitalOcean App Platform.
Runs the same suggestion/recommendation pipeline dashboard.index() runs
on-demand when an agent loads Today, so an agent who doesn't open the
app for a day still comes back to suggestions and flow recommendations
waiting for them rather than everything only catching up retroactively
on their next visit:
  1. generate_suggestions_for_org -- legacy GiftTrigger path
  2. generate_campaign_suggestions_for_org -- the Flow engine
  Both of the above run for EVERY org regardless of tier (flow_triggers
  is universal, including free -- see TIER_LIMITS in config.py).
  3. generate_flow_recommendations_for_user -- per-agent, so this one
     loops over each org's active users rather than running once per
     org like the other two. Gated on ai_recommendations, the paid-only
     tier feature (Solo/Pro/Team, not Free).
Safe to re-run (idempotent per contact/event/date, and per
(user, event_type) for recommendations).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import Org, User
from app.services.suggestion_engine import (
    generate_suggestions_for_org, generate_campaign_suggestions_for_org, expire_stale_suggestions,
)
from app.services.flow_recommendations import generate_flow_recommendations_for_user


def main():
    app = create_app("production")
    with app.app_context():
        orgs = Org.query.all()
        total_suggestions = 0
        total_recommendations = 0
        for org in orgs:
            if not org.feature_enabled("flow_triggers"):
                continue

            created = generate_suggestions_for_org(org)
            created += generate_campaign_suggestions_for_org(org)
            total_suggestions += len(created)
            expired = expire_stale_suggestions(org)

            recommendations_created = 0
            active_users = User.query.filter_by(org_id=org.id, status="active").all()
            if org.feature_enabled("ai_recommendations"):
                for user in active_users:
                    recommendations_created += len(generate_flow_recommendations_for_user(user))
            total_recommendations += recommendations_created

            print(
                f"[{org.name}] {len(created)} new suggestion(s), {len(expired)} expired, "
                f"{recommendations_created} new flow recommendation(s) across {len(active_users)} agent(s)"
            )
        print(
            f"Done. {total_suggestions} suggestion(s) and {total_recommendations} "
            f"flow recommendation(s) created across {len(orgs)} org(s)."
        )


if __name__ == "__main__":
    main()
