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
