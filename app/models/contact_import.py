"""A CSV upload staged between preview and confirmation.

The import is deliberately two steps -- upload, review what will happen,
then commit -- because an agent bringing 400 contacts across from
another CRM cannot undo a bad import, and there is no bulk-delete in the
app to rescue them with.

That means the uploaded file has to survive between two requests. The
options were all worse than a table:

  * The session cookie is capped around 4KB; a real export is hundreds
    of KB.
  * DigitalOcean Spaces, which the app already uses, serves objects at
    public URLs (see services/storage._public_url). A spreadsheet of
    client names, home addresses, phone numbers and birthdays is
    precisely the thing that must not sit behind a guessable public URL.
  * The container filesystem is ephemeral and a deploy mid-review would
    lose the file.

So the CSV text lives here: private, scoped to an org, deleted the
moment it is committed or cancelled, and swept after 24 hours if the
agent simply walks away (see
suggestion_engine.purge_stale_contact_imports).
"""
from datetime import datetime

from app.extensions import db
from app.models.org import gen_uuid


class ContactImportJob(db.Model):
    __tablename__ = "contact_import_jobs"

    id = db.Column(db.String(36), primary_key=True, default=gen_uuid)
    org_id = db.Column(db.String(36), db.ForeignKey("orgs.id"), nullable=False, index=True)
    created_by_user_id = db.Column(db.String(36), db.ForeignKey("users.id"), nullable=True, index=True)

    filename = db.Column(db.String(255), nullable=True)
    # MEDIUMTEXT on MySQL (16MB). The route caps uploads far below that;
    # the headroom is so a large-but-legitimate export is never truncated
    # into a silently partial import.
    csv_text = db.Column(db.Text(length=16_777_215), nullable=False)

    # Whether the imported contacts should be private to the uploading
    # agent or shared org-wide. Captured at upload time so the preview
    # reflects the same choice the commit will use.
    assign_to_uploader = db.Column(db.Boolean, default=False, nullable=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    org = db.relationship("Org")
    created_by = db.relationship("User", foreign_keys=[created_by_user_id])
