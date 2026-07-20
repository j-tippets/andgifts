"""
QA report script for &Gifts.

Read-only summary of the "QA Test Agency" org (see scripts/qa_seed.py) --
run this after you've logged in and manually approved/skipped/deleted a
mix of actions, to get a quick pass/fail read on whether anything's
regressed. Doesn't touch the database at all.

Usage:
    python scripts/qa_report.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("DATABASE_URL") and os.environ.get("LOCAL_SQLITE_URI"):
    import config as _config
    _config.Config.SQLALCHEMY_ENGINE_OPTIONS = {}

from app import create_app
from app.models import (
    Org, User, Contact, CustomFieldDefinition, CustomEventType,
    Campaign, SuggestedAction, ActionLog,
)

ORG_NAME = "QA Test Agency"


def section(title):
    print(f"\n-- {title} " + "-" * max(0, 66 - len(title)))


def run():
    app = create_app()
    with app.app_context():
        org = Org.query.filter_by(name=ORG_NAME).first()
        if not org:
            print(f"No '{ORG_NAME}' org found -- run scripts/qa_seed.py first.")
            sys.exit(1)

        section("Org")
        agents = User.query.filter_by(org_id=org.id).order_by(User.role.desc(), User.first_name).all()
        print(f"Users: {len(agents)} ({sum(1 for a in agents if a.role == 'admin')} admin, "
              f"{sum(1 for a in agents if a.role == 'agent')} agent)")
        contacts = Contact.query.filter_by(org_id=org.id).all()
        print(f"Contacts: {len(contacts)} ({sum(1 for c in contacts if c.owner_user_id is None)} shared, "
              f"{sum(1 for c in contacts if c.do_not_contact)} do-not-contact, "
              f"{sum(1 for c in contacts if c.marketing_opt_out)} marketing opt-out)")

        section("Custom fields & milestones")
        fields = CustomFieldDefinition.query.filter_by(org_id=org.id).all()
        print(f"Custom fields: {sum(1 for f in fields if f.scope == 'org')} org-scope, "
              f"{sum(1 for f in fields if f.scope == 'personal')} personal")
        milestones = CustomEventType.query.filter_by(org_id=org.id).all()
        print(f"Milestone types: {sum(1 for m in milestones if m.scope == 'org')} org-scope, "
              f"{sum(1 for m in milestones if m.scope == 'personal')} personal")

        section("Flows")
        flows = Campaign.query.filter_by(org_id=org.id).all()
        print(f"Total flows: {len(flows)} ({sum(1 for f in flows if f.is_active)} active)")
        for flow in sorted(flows, key=lambda f: (f.owner.email if f.owner else 'zzz', f.name)):
            resulted = ActionLog.query.join(
                SuggestedAction, ActionLog.suggested_action_id == SuggestedAction.id
            ).filter(SuggestedAction.source_campaign_id == flow.id).count()
            pending = SuggestedAction.query.filter_by(source_campaign_id=flow.id, status="pending").count()
            owner_label = flow.owner.email if flow.owner else "(no owner)"
            print(f"  [{flow.action_type:16s}] {flow.name:38s} owner={owner_label:22s} "
                  f"pending={pending:<3d} completed={resulted}")

        section("Suggested actions by status")
        for status in ("pending", "approved", "skipped", "deleted", "sent"):
            count = SuggestedAction.query.filter_by(org_id=org.id, status=status).count()
            print(f"  {status:10s}: {count}")

        section("Action log (completed actions)")
        logs = ActionLog.query.filter_by(org_id=org.id).all()
        print(f"Total: {len(logs)}")
        for action_type in ("gift", "email", "text", "handwritten_note"):
            count = sum(1 for l in logs if l.action_type == action_type)
            print(f"  {action_type:16s}: {count}")

        sent = sum(1 for l in logs if l.delivery_status == "sent")
        failed = [l for l in logs if l.delivery_status == "failed"]
        no_send = sum(1 for l in logs if l.delivery_status is None)
        print(f"  delivery: sent={sent} failed={len(failed)} n/a={no_send}")
        for l in failed:
            print(f"    FAILED -> {l.contact.household_name}: {l.delivery_error}")

        section("Per-agent breakdown (approved/completed actions)")
        by_agent = {}
        for l in logs:
            agent = l.owning_agent
            key = agent.email if agent else "(unassigned/shared)"
            by_agent[key] = by_agent.get(key, 0) + 1
        for key, count in sorted(by_agent.items()):
            print(f"  {key:30s}: {count}")

        section("Sanity checks")
        dnc_contacts = [c for c in contacts if c.do_not_contact]
        problems = []
        for c in dnc_contacts:
            count = SuggestedAction.query.filter_by(org_id=org.id, contact_id=c.id).count()
            if count:
                problems.append(f"Do-Not-Contact contact '{c.household_name}' has {count} suggestion(s) -- should be 0.")

        opt_out_contacts = [c for c in contacts if c.marketing_opt_out and not c.do_not_contact]
        for c in opt_out_contacts:
            count = SuggestedAction.query.filter_by(org_id=org.id, contact_id=c.id).count()
            if count == 0:
                problems.append(
                    f"Marketing opt-out contact '{c.household_name}' has 0 suggestions -- "
                    "expected at least one (opt-out shouldn't block relationship milestones)."
                )

        zero_match_flows = [f for f in flows if f.owner_user_id and not ActionLog.query.join(
            SuggestedAction, ActionLog.suggested_action_id == SuggestedAction.id
        ).filter(SuggestedAction.source_campaign_id == f.id).count()
            and not SuggestedAction.query.filter_by(source_campaign_id=f.id).count()]
        for f in zero_match_flows:
            problems.append(f"Flow '{f.name}' (owner={f.owner.email if f.owner else '?'}) matched nothing at all.")

        if problems:
            print("Found issues:")
            for p in problems:
                print(f"  !! {p}")
        else:
            print("No issues found.")


if __name__ == "__main__":
    run()
