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

    @app.cli.command("import-contacts")
    @click.argument("org_id")
    @click.argument("csv_path", type=click.Path(exists=True, dir_okay=False))
    @click.option("--owner-email", default=None,
                  help="Make every imported contact private to this agent. "
                       "Omit to share them org-wide.")
    @click.option("--commit", is_flag=True, default=False,
                  help="Actually write. Without this the import is a preview "
                       "that rolls back.")
    @click.option("--show-errors", default=20, show_default=True,
                  help="How many problem rows to print.")
    def import_contacts_command(org_id, csv_path, owner_email, commit, show_errors):
        """Import contacts from a CSV export into an org.

        Previews by default -- run it, read the report, then re-run with
        --commit. Preview and commit run identical code (the preview
        rolls back at the end), so what you see is what you get.

            flask import-contacts <org-id> ./followupboss.csv
            flask import-contacts <org-id> ./followupboss.csv --commit

        Built for onboarding beta customers by hand. The parsing,
        mapping and dedupe all live in services/contact_import, so the
        eventual in-app upload page is a thin wrapper over this same
        code rather than a second implementation.
        """
        from app.models import Org, User
        from app.services.contact_import import import_contacts

        org = Org.query.get(org_id)
        if not org:
            raise click.ClickException(f"No org with id {org_id}.")

        owner = None
        if owner_email:
            owner = User.query.filter_by(email=owner_email.lower(), org_id=org.id).first()
            if not owner:
                raise click.ClickException(
                    f"No user {owner_email} in {org.name}. Contacts would have been "
                    "shared org-wide instead, which is a different decision -- "
                    "stopping so you can make it deliberately."
                )

        acting_user = owner or User.query.filter_by(org_id=org.id, role="admin").first()
        if not acting_user:
            raise click.ClickException(f"{org.name} has no admin user to attribute the import to.")

        with open(csv_path, newline="", encoding="utf-8-sig") as handle:
            csv_text = handle.read()

        try:
            report = import_contacts(
                csv_text, org, acting_user, owner_user=owner, dry_run=not commit,
            )
        except ValueError as exc:
            raise click.ClickException(str(exc))

        click.echo(f"\nOrg:      {org.name}")
        click.echo(f"Owner:    {owner.full_name + ' (private)' if owner else 'shared org-wide'}")
        click.echo(f"Mode:     {'COMMIT' if commit else 'preview (nothing written)'}")

        click.echo("\nColumns mapped:")
        for field, header in sorted(report.mapping.items()):
            click.echo(f"  {field:<26} <- {header}")
        for event_key, header in sorted(report.date_mapping.items()):
            click.echo(f"  {event_key:<26} <- {header}  (milestone)")
        if report.unmapped_headers:
            click.echo("\nColumns IGNORED (nothing in &Gifts to put them in):")
            for header in report.unmapped_headers:
                click.echo(f"  {header}")

        summary = report.summary()
        click.echo(
            f"\n{summary['created']} to create, {summary['duplicates']} duplicate(s) skipped, "
            f"{summary['errors']} error(s), {summary['over_limit']} blocked by plan limit."
        )

        problems = [r for r in report.rows if r.status in ("error", "over_limit")]
        if problems:
            click.echo(f"\nProblem rows (first {show_errors}):")
            for result in problems[:show_errors]:
                name = result.household_name or "(no name)"
                click.echo(f"  line {result.line_number}: {name} -- {result.reason}")
            if len(problems) > show_errors:
                click.echo(f"  ... and {len(problems) - show_errors} more")

        if not commit:
            click.echo("\nNothing was written. Re-run with --commit when the above looks right.")
        else:
            click.echo(f"\nDone. {summary['created']} contact(s) added to {org.name}.")

    @app.cli.command("wipe-all-tenant-data")
    @click.option("--yes", "confirm_phrase", default="",
                  help='Must be exactly "DELETE EVERYTHING" to actually run -- otherwise this is a dry run.')
    @click.option("--keep-org-email", "keep_org_email", default=None,
                  help="Email of a user whose entire org should be preserved untouched "
                       "(e.g. your own platform_admin login) -- everything belonging to "
                       "every OTHER org still gets wiped. Omit for the original all-orgs "
                       "behavior.")
    def wipe_all_tenant_data(confirm_phrase, keep_org_email):
        """Deletes every Org, User, and Contact (and everything scoped to
        them) across the entire platform. Run with no --yes flag first --
        that's a dry run that only prints row counts, nothing is deleted.

        Pass --keep-org-email to preserve one org (and everything scoped
        to it -- its users, contacts, orders, flows, etc.) untouched while
        still wiping every other org. Typical use: keep your own
        platform_admin login so you don't have to re-register and
        re-grant platform_admin by hand afterward (see the old workflow
        this replaces).

        Left untouched regardless, since they aren't tenant data:
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
        rows stay in place and legible (rows already belonging to the
        kept org, if any, are left completely alone).

        Runs as a single transaction with FK checks suspended for the
        duration (mirrors mysqldump's own approach to avoid ordering
        issues from self-referential FKs like campaigns.forked_from_
        campaign_id) -- either everything below commits together, or an
        error rolls back the whole thing and nothing is deleted.

        Usage:
            flask wipe-all-tenant-data                      # dry run, prints counts only
            flask wipe-all-tenant-data --yes "DELETE EVERYTHING"   # actually deletes everything
            flask wipe-all-tenant-data --keep-org-email you@example.com   # dry run, one org spared
            flask wipe-all-tenant-data --yes "DELETE EVERYTHING" --keep-org-email you@example.com
        """
        from app.extensions import db
        from sqlalchemy import text

        dry_run = confirm_phrase != "DELETE EVERYTHING"
        params = {}

        if keep_org_email:
            row = db.session.execute(
                text("SELECT org_id, org_id IS NULL AS missing FROM users WHERE email = :email"),
                {"email": keep_org_email},
            ).first()
            if row is None:
                click.echo(f"No user found with email {keep_org_email!r} -- aborting without "
                           f"touching anything, since a typo here would otherwise wipe that "
                           f"org too.")
                return
            params["keep_org_id"] = row[0]
            click.echo(f"Keeping org {row[0]} (owner: {keep_org_email}) untouched.\n")

        def scope(sql_no_keep, sql_with_keep):
            return sql_with_keep if keep_org_email else sql_no_keep

        # (label, DELETE sql) in dependency-safe (child-before-parent) order.
        # Every statement scopes out the global/platform rows called out in
        # the docstring above via its WHERE clause, and additionally excludes
        # the kept org's data (directly via org_id where the table has one,
        # otherwise via a join back to whichever parent row carries org_id)
        # when --keep-org-email is passed.
        delete_statements = [
            ("contact_audit_log", scope(
                "DELETE FROM contact_audit_log",
                "DELETE FROM contact_audit_log WHERE org_id != :keep_org_id")),
            ("action_log", scope(
                "DELETE FROM action_log",
                "DELETE FROM action_log WHERE org_id != :keep_org_id")),
            ("suggested_actions", scope(
                "DELETE FROM suggested_actions",
                "DELETE FROM suggested_actions WHERE org_id != :keep_org_id")),
            ("contact_methods", scope(
                "DELETE FROM contact_methods",
                "DELETE FROM contact_methods WHERE person_id IN "
                "(SELECT cp.id FROM contact_people cp JOIN contacts c ON c.id = cp.contact_id "
                "WHERE c.org_id != :keep_org_id)")),
            ("campaign_rules", scope(
                "DELETE FROM campaign_rules",
                "DELETE FROM campaign_rules WHERE campaign_id IN "
                "(SELECT id FROM campaigns WHERE org_id != :keep_org_id)")),
            ("timeline_events", scope(
                "DELETE FROM timeline_events",
                "DELETE FROM timeline_events WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE org_id != :keep_org_id)")),
            ("orders", scope(
                "DELETE FROM orders",
                "DELETE FROM orders WHERE org_id != :keep_org_id")),
            ("custom_field_values", scope(
                "DELETE FROM custom_field_values",
                "DELETE FROM custom_field_values WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE org_id != :keep_org_id)")),
            ("contact_people", scope(
                "DELETE FROM contact_people",
                "DELETE FROM contact_people WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE org_id != :keep_org_id)")),
            ("contact_interests", scope(
                "DELETE FROM contact_interests",
                "DELETE FROM contact_interests WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE org_id != :keep_org_id)")),
            ("contact_badges", scope(
                "DELETE FROM contact_badges",
                "DELETE FROM contact_badges WHERE contact_id IN "
                "(SELECT id FROM contacts WHERE org_id != :keep_org_id)")),
            ("campaigns", scope(
                "DELETE FROM campaigns",
                "DELETE FROM campaigns WHERE org_id != :keep_org_id")),
            ("campaign_recipe_rules", scope(
                "DELETE FROM campaign_recipe_rules WHERE recipe_id IN "
                "(SELECT id FROM campaign_recipes WHERE org_id IS NOT NULL)",
                "DELETE FROM campaign_recipe_rules WHERE recipe_id IN "
                "(SELECT id FROM campaign_recipes WHERE org_id IS NOT NULL AND org_id != :keep_org_id)")),
            ("milestone_priorities", scope(
                "DELETE FROM milestone_priorities",
                "DELETE FROM milestone_priorities WHERE user_id IN "
                "(SELECT id FROM users WHERE org_id != :keep_org_id)")),
            ("org_catalog_selections", scope(
                "DELETE FROM org_catalog_selections",
                "DELETE FROM org_catalog_selections WHERE org_id != :keep_org_id")),
            ("gift_triggers", scope(
                "DELETE FROM gift_triggers WHERE org_id IS NOT NULL",
                "DELETE FROM gift_triggers WHERE org_id IS NOT NULL AND org_id != :keep_org_id")),
            ("custom_field_definitions", scope(
                "DELETE FROM custom_field_definitions",
                "DELETE FROM custom_field_definitions WHERE org_id != :keep_org_id")),
            ("custom_event_types", scope(
                "DELETE FROM custom_event_types",
                "DELETE FROM custom_event_types WHERE org_id != :keep_org_id")),
            ("contacts", scope(
                "DELETE FROM contacts",
                "DELETE FROM contacts WHERE org_id != :keep_org_id")),
            ("campaign_recipes", scope(
                "DELETE FROM campaign_recipes WHERE org_id IS NOT NULL",
                "DELETE FROM campaign_recipes WHERE org_id IS NOT NULL AND org_id != :keep_org_id")),
            ("badges", scope(
                "DELETE FROM badges WHERE org_id IS NOT NULL OR owner_user_id IS NOT NULL",
                "DELETE FROM badges WHERE (org_id IS NOT NULL OR owner_user_id IS NOT NULL) "
                "AND (org_id IS NULL OR org_id != :keep_org_id) "
                "AND (owner_user_id IS NULL OR owner_user_id NOT IN "
                "(SELECT id FROM users WHERE org_id = :keep_org_id))")),
            ("users", scope(
                "DELETE FROM users",
                "DELETE FROM users WHERE org_id != :keep_org_id")),
            ("gift_catalog_items", scope(
                "DELETE FROM gift_catalog_items WHERE org_id IS NOT NULL",
                "DELETE FROM gift_catalog_items WHERE org_id IS NOT NULL AND org_id != :keep_org_id")),
            ("orgs", scope(
                "DELETE FROM orgs",
                "DELETE FROM orgs WHERE id != :keep_org_id")),
        ]
        null_out_statements = [
            ("support_requests", scope(
                "UPDATE support_requests SET org_id = NULL, user_id = NULL WHERE org_id IS NOT NULL",
                "UPDATE support_requests SET org_id = NULL, user_id = NULL "
                "WHERE org_id IS NOT NULL AND org_id != :keep_org_id")),
            ("org_event_log", scope(
                "UPDATE org_event_log SET org_id = NULL WHERE org_id IS NOT NULL",
                "UPDATE org_event_log SET org_id = NULL WHERE org_id IS NOT NULL AND org_id != :keep_org_id")),
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
                count = conn.execute(text(count_sql), params).scalar()
                click.echo(f"{'would delete' if dry_run else 'deleting':13s} {count:6d}  {label}")
                if not dry_run and count:
                    conn.execute(text(sql), params)

            for label, sql in null_out_statements:
                table = sql.split(" SET ", 1)[0].replace("UPDATE ", "").strip()
                where_clause = sql.split(" WHERE ", 1)[1]
                count_sql = f"SELECT COUNT(*) FROM {table} WHERE {where_clause}"
                count = conn.execute(text(count_sql), params).scalar()
                click.echo(f"{'would clear org_id on' if dry_run else 'clearing org_id on':22s} {count:6d}  {label}")
                if not dry_run and count:
                    conn.execute(text(sql), params)

            if not dry_run:
                conn.execute(text("PRAGMA foreign_keys=ON" if is_sqlite else "SET FOREIGN_KEY_CHECKS=1"))
            trans.commit()
        except Exception:
            trans.rollback()
            raise
        finally:
            conn.close()

        if dry_run:
            click.echo("\nDry run complete -- nothing was deleted.")
        elif keep_org_email:
            click.echo(f"\nDone. Every org except {keep_org_email}'s is gone, along with "
                       f"everything scoped to them.")
        else:
            click.echo("\nDone. Every org, user, and contact (and everything scoped to them) is gone.")
