"""
Entry point for the nightly scheduled job on DigitalOcean App Platform.
Runs the suggestion engine for every org on a plan that has the AI
dashboard feature enabled. Safe to re-run (idempotent per contact/event/date).
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.models import Org
from app.services.suggestion_engine import generate_suggestions_for_org


def main():
    app = create_app("production")
    with app.app_context():
        orgs = Org.query.all()
        total = 0
        for org in orgs:
            if not org.feature_enabled("ai_dashboard"):
                continue
            created = generate_suggestions_for_org(org)
            total += len(created)
            print(f"[{org.name}] {len(created)} new suggestion(s)")
        print(f"Done. {total} suggestion(s) created across {len(orgs)} org(s).")


if __name__ == "__main__":
    main()
