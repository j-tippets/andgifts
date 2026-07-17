"""
DigitalOcean Spaces storage helper.

Spaces is S3-compatible, so this uses boto3's "s3" client pointed at the
region's Spaces endpoint. Used for agent/admin avatar photos and household
contact photos so uploads survive App Platform deploys (the local disk is
ephemeral).

Required env vars: SPACES_KEY, SPACES_SECRET, SPACES_BUCKET, SPACES_REGION.
Optional: SPACES_CDN_DOMAIN if the Space has a CDN endpoint enabled.
"""
import uuid

import boto3
from flask import current_app

ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}
MAX_PHOTO_SIZE_BYTES = 5 * 1024 * 1024  # 5 MB


class StorageError(Exception):
    """Raised for any Spaces config/upload/delete failure."""


def _client():
    cfg = current_app.config
    if not (cfg.get("SPACES_KEY") and cfg.get("SPACES_SECRET") and cfg.get("SPACES_BUCKET")):
        raise StorageError(
            "DigitalOcean Spaces isn't configured. Set SPACES_KEY, SPACES_SECRET, "
            "SPACES_BUCKET, and SPACES_REGION."
        )
    region = cfg.get("SPACES_REGION", "nyc3")
    return boto3.client(
        "s3",
        region_name=region,
        endpoint_url=f"https://{region}.digitaloceanspaces.com",
        aws_access_key_id=cfg["SPACES_KEY"],
        aws_secret_access_key=cfg["SPACES_SECRET"],
    )


def _public_url(key):
    cfg = current_app.config
    if cfg.get("SPACES_CDN_DOMAIN"):
        return f"https://{cfg['SPACES_CDN_DOMAIN']}/{key}"
    region = cfg.get("SPACES_REGION", "nyc3")
    return f"https://{cfg['SPACES_BUCKET']}.{region}.digitaloceanspaces.com/{key}"


def _validate_photo(file_storage):
    """Raises StorageError if the file fails extension/size checks; otherwise
    returns (ext, content_type)."""
    filename = file_storage.filename or ""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_PHOTO_EXTENSIONS:
        raise StorageError("Photo must be a PNG, JPG, or WEBP file.")

    file_storage.stream.seek(0, 2)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > MAX_PHOTO_SIZE_BYTES:
        raise StorageError("Photo must be smaller than 5MB.")

    content_type = file_storage.content_type or f"image/{'jpeg' if ext == 'jpg' else ext}"
    return ext, content_type


def _upload(file_storage, key, content_type):
    try:
        _client().put_object(
            Bucket=current_app.config["SPACES_BUCKET"],
            Key=key,
            Body=file_storage.stream.read(),
            ContentType=content_type,
            ACL="public-read",
        )
    except Exception as exc:
        raise StorageError(f"Upload to Spaces failed: {exc}") from exc
    return _public_url(key)


def _delete_by_prefix(photo_url, prefix):
    """Best-effort delete of a previously uploaded photo. Never raises."""
    if not photo_url or f"{prefix}/" not in photo_url:
        return
    try:
        key = f"{prefix}/" + photo_url.split(f"{prefix}/", 1)[1]
        _client().delete_object(Bucket=current_app.config["SPACES_BUCKET"], Key=key)
    except Exception:
        # Deletion is best-effort -- don't block the calling action (e.g. profile
        # update or account deletion) over a stray orphaned file in Spaces.
        pass


def upload_avatar(file_storage, user_id):
    """
    Upload a Werkzeug FileStorage as a user's avatar. Returns the public URL.
    Raises StorageError on any validation or upload failure.
    """
    ext, content_type = _validate_photo(file_storage)
    key = f"avatars/{user_id}/{uuid.uuid4().hex}.{ext}"
    return _upload(file_storage, key, content_type)


def delete_avatar(photo_url):
    """Best-effort delete of a previously uploaded avatar. Never raises."""
    _delete_by_prefix(photo_url, "avatars")


def upload_contact_photo(file_storage, contact_id):
    """
    Upload a Werkzeug FileStorage as a contact/household photo. Returns the
    public URL. Raises StorageError on any validation or upload failure.
    """
    ext, content_type = _validate_photo(file_storage)
    key = f"contact-photos/{contact_id}/{uuid.uuid4().hex}.{ext}"
    return _upload(file_storage, key, content_type)


def delete_contact_photo(photo_url):
    """Best-effort delete of a previously uploaded contact photo. Never raises."""
    _delete_by_prefix(photo_url, "contact-photos")
