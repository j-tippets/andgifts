"""
Flask CLI commands for one-off diagnostics. Not imported by anything at
request time -- only registered on the app so `flask <command>` works.
"""
import click


def register_cli(app):
    @app.cli.command("send-test-email")
    @click.argument("to_email")
    def send_test_email(to_email):
        """Send a real test email through SendGrid and print exactly what
        happened -- unlike the app's normal best-effort send (which only
        writes a log line), this prints straight to the terminal so it's
        useful when run against production, e.g.:

            doctl apps console <app-id> --component web
            flask send-test-email you@example.com

        Checks config presence first (the most common cause of silent
        failures), then attempts a real send and reports SendGrid's raw
        status code + body on failure.
        """
        from app.services.email import send_email

        api_key = app.config.get("SENDGRID_API_KEY")
        from_email = app.config.get("SENDGRID_FROM_EMAIL")
        click.echo(f"SENDGRID_API_KEY set: {'yes (' + api_key[:6] + '...)' if api_key else 'NO -- this is why nothing sends'}")
        click.echo(f"SENDGRID_FROM_EMAIL: {from_email}")
        click.echo(f"Sending domain (org emails): {app.config.get('SENDGRID_SENDING_DOMAIN')}")

        if not api_key:
            click.echo("\nStopping here -- SENDGRID_API_KEY isn't set in this environment's "
                        "config/env vars, so every send is silently skipped. Set it in "
                        "DigitalOcean under the app's Settings -> App-Level Environment "
                        "Variables (it's declared as a SECRET in .do/app.yaml, but that "
                        "only reserves the slot -- the value has to be entered separately).")
            return

        click.echo(f"\nAttempting a real send to {to_email}...")
        ok = send_email(
            to_email,
            "&Gifts test email",
            "<p>This is a test email from the send-test-email CLI command.</p>",
        )
        if ok:
            click.echo("SendGrid accepted the send. If it still doesn't arrive, check "
                       "SendGrid's Activity Feed (app.sendgrid.com/email_activity) for "
                       "this address -- that will show bounces/blocks/spam-folder drops "
                       "that happen after SendGrid accepts the API call.")
        else:
            click.echo("Send failed -- check the app log output just above/below this "
                       "for the exact status code and body SendGrid returned (look for "
                       "'SendGrid send failed'). Common causes: the From address isn't "
                       "verified (Single Sender Verification) or its domain isn't "
                       "authenticated in SendGrid yet.")

    @app.cli.command("wipe-all-tenant-data")
    @click.option("--yes", "confirm_phrase", default="",
                  help='Must be exactly "DELETE EVERYTHING" to actually run -- otherwise this is a dry run.')
    def wipe_all_tenant_data(confirm_phrase):
        """Deletes every Org, User, and Contact (and everything scoped to
        them) across the entire platform. Run with no --yes flag first --
        that's a dry run that only prints row counts, nothing is deleted.

        Left untouched, since they aren't tenant data:
          - practice_types / practice_type_milestones (preset milestone
            templates)
          - interests (global tag list)
          - gift_catalog_items where org_id IS NULL (the global catalog)
          - campaign_recipes where org_id IS NULL (global Flow Library)
          - badges where org_id IS NULL AND owner_user_id IS NULL (global
            badges like "VIP")

        support_requests and org_event_log rows are NOT deleted -- both
        were deliberately designed (see their model docstrings) to
        survive their org/user being removed, via denormalized snapshot
        columns. Their org_id/user_id FKs are set to NULL instead so the
        rows stay in place and legible.

        Runs as a single transaction with FK checks suspended for the
        duration (mirrors mysqldump's own approach to avoid ordering
        issues from self-referential FKs like campaigns.forked_from_
        campaign_id) -- either everything below commits together, or an
        error rolls back the whole thing and nothing is deleted.

        Usage:
            flask wipe-all-tenant-data                      # dry run, prints counts only
            flask wipe-all-tenant-data --yes "DELETE EVERYTHING"   # actually deletes
        """
        from app.extensions import db
        from sqlalchemy import text

        dry_run = confirm_phrase != "DELETE EVERYTHING"

        # (label, DELETE sql) in dependency-safe (child-before-parent) order.
        # Every statement scopes out the global/platform rows called out in
        # the docstring above via its WHERE clause.
        delete_statements = [
            ("contact_audit_log", "DELETE FROM contact_audit_log"),
            ("action_log", "DELETE FROM action_log"),
            ("suggested_actions", "DELETE FROM suggested_actions"),
            ("contact_methods", "DELETE FROM contact_methods"),
            ("campaign_rules", "DELETE FROM campaign_rules"),
            ("timeline_events", "DELETE FROM timeline_events"),
            ("orders", "DELETE FROM orders"),
            ("custom_field_values", "DELETE FROM custom_field_values"),
            ("contact_people", "DELETE FROM contact_people"),
            ("contact_interests", "DELETE FROM contact_interests"),
            ("contact_badges", "DELETE FROM contact_badges"),
            ("campaigns", "DELETE FROM campaigns"),
            ("campaign_recipe_rules",
             "DELETE FROM campaign_recipe_rules WHERE recipe_id IN "
             "(SELECT id FROM campaign_recipes WHERE org_id IS NOT NULL)"),
            ("milestone_priorities", "DELETE FROM milestone_priorities"),
            ("org_catalog_selections", "DELETE FROM org_catalog_selections"),
            ("gift_triggers", "DELETE FROM gift_triggers WHERE org_id IS NOT NULL"),
            ("custom_field_definitions", "DELETE FROM custom_field_definitions"),
            ("custom_event_types", "DELETE FROM custom_event_types"),
            ("contacts", "DELETE FROM contacts"),
            ("campaign_recipes", "DELETE FROM campaign_recipes WHERE org_id IS NOT NULL"),
            ("badges", "DELETE FROM badges WHERE org_id IS NOT NULL OR owner_user_id IS NOT NULL"),
            ("users", "DELETE FROM users"),
            ("gift_catalog_items", "DELETE FROM gift_catalog_items WHERE org_id IS NOT NULL"),
            ("orgs", "DELETE FROM orgs"),
        ]
        null_out_statements = [
            ("support_requests", "UPDATE support_requests SET org_id = NULL, user_id = NULL"),
            ("org_event_log", "UPDATE org_event_log SET org_id = NULL"),
        ]

        click.echo("DRY RUN -- nothing will be deleted (pass --yes \"DELETE EVERYTHING\" to actually run)\n"
                   if dry_run else "LIVE RUN -- this will permanently delete data\n")

        is_sqlite = db.engine.dialect.name == "sqlite"
        conn = db.engine.connect()
        trans = conn.begin()
        try:
            if not dry_run:
                conn.execute(text("PRAGMA foreign_keys=OFF" if is_sqlite else "SET FOREIGN_KEY_CHECKS=0"))

            for label, sql in delete_statements:
                count_sql = "SELECT COUNT(*) FROM (" + sql.replace("DELETE FROM", "SELECT * FROM", 1) + ") t"
                count = conn.execute(text(count_sql)).scalar()
                click.echo(f"{'would delete' if dry_run else 'deleting':13s} {count:6d}  {label}")
                if not dry_run and count:
                    conn.execute(text(sql))

            for label, sql in null_out_statements:
                table = label
                count = conn.execute(text(f"SELECT COUNT(*) FROM {table} WHERE org_id IS NOT NULL")).scalar()
                click.echo(f"{'would clear org_id on' if dry_run else 'clearing org_id on':22s} {count:6d}  {label}")
                if not dry_run and count:
                    conn.execute(text(sql))

            if not dry_run:
                conn.execute(text("PRAGMA foreign_keys=ON" if is_sqlite else "SET FOREIGN_KEY_CHECKS=1"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()

        click.echo("\nDry run complete -- nothing was deleted." if dry_run
                   else "\nDone. Every org, user, and contact (and everything scoped to them) is gone.")
