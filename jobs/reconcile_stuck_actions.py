"""
Entry point for a DigitalOcean App Platform scheduled job. Runs every
15 minutes (DO's minimum cron interval) and releases any
SuggestedAction stuck in "processing" for longer than a normal
claim/charge/approve cycle should ever take -- see
reconcile_stuck_processing_actions's docstring in
app/services/suggestion_engine.py for why this is safe to do with a
plain revert-to-pending and no separate check against Stripe.

Expected to find zero rows on almost every run -- this only ever finds
something after a process crash (OOM kill, a deploy restart landing
mid-request) during the brief window between claiming an action for
approval and either charging succeeding or failing. A non-empty result
here is worth a look (why did a worker crash mid-charge?) even though
the reconciliation itself needs no follow-up -- the action is simply
approvable again.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app
from app.services.suggestion_engine import reconcile_stuck_processing_actions


def main():
    app = create_app("production")
    with app.app_context():
        released = reconcile_stuck_processing_actions()
        if released:
            print(f"Released {len(released)} suggestion(s) stuck in 'processing':")
            for row in released:
                print(
                    f"  - {row['id']} (org {row['org_id']}, contact {row['contact_id']}, "
                    f"type {row['action_type']}, stuck since {row['processing_started_at']})"
                )
        else:
            print("No stuck 'processing' suggestions found.")


if __name__ == "__main__":
    main()
