"""
Entry point for the nightly scheduled job on DigitalOcean App Platform.
Runs the full suggestion/recommendation pipeline for every org on a
plan that has the AI dashboard feature enabled -- the same three
things dashboard.index() runs on-demand when an agent loads Today, so
an agent who doesn't open the app for a day still comes back to
suggestions and flow recommendations waiting for them rather than
everything only catching up retroactively on their next visit:
  1. generate_suggestions_for_org -- legacy GiftTrigger path
  2. generate_campaign_suggestions_for_org -- the Flow engine (this
     was missing here until now -- see the chat this got fixed in)
  3. generate_flow_recommendations_for_user -- per-agent, so this one
     loops over each org's active users rather than running once per
     org like the other two
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
            if not org.feature_enabled("ai_dashboard"):
                continue

            created = generate_suggestions_for_org(org)
            created += generate_campaign_suggestions_for_org(org)
            total_suggestions += len(created)
            expired = expire_stale_suggestions(org)

            recommendations_created = 0
            active_users = User.query.filter_by(org_id=org.id, status="active").all()
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
