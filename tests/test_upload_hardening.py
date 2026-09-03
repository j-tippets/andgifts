"""
Regression tests for the secondary review's upload-hardening item:
_validate_photo used to trust the client-supplied filename extension
(and, as a content_type fallback, the client-supplied Content-Type
header) rather than the file's actual content. A file named
"photo.png" containing arbitrary non-image bytes -- HTML/JS, another
file format entirely -- would pass validation purely on the strength
of its name.

Covers:
- Real PNG/JPEG/WEBP content is accepted (by content, not filename).
- A file with an image extension but non-image content is rejected
  (the core fix -- extension spoofing).
- SVG content is rejected regardless of what extension the filename
  claims (no explicit blocklist entry needed -- it simply doesn't
  match any allowed signature).
- Empty files and oversized files are both rejected.
"""
import io

import pytest
from werkzeug.datastructures import FileStorage

from app.services.storage import _validate_photo, MAX_PHOTO_SIZE_BYTES, StorageError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
JPEG_MAGIC = b"\xff\xd8\xff" + b"\x00" * 20
WEBP_MAGIC = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 20


def make_upload(content, filename="upload.png", content_type="image/png"):
    return FileStorage(stream=io.BytesIO(content), filename=filename, content_type=content_type)


def test_real_png_is_accepted_regardless_of_filename(app):
    with app.app_context():
        ext, content_type = _validate_photo(make_upload(PNG_MAGIC, filename="anything.txt"))
        assert ext == "png"
        assert content_type == "image/png"


def test_real_jpeg_is_accepted(app):
    with app.app_context():
        ext, content_type = _validate_photo(make_upload(JPEG_MAGIC, filename="photo.jpg"))
        assert ext == "jpg"
        assert content_type == "image/jpeg"


def test_real_webp_is_accepted(app):
    with app.app_context():
        ext, content_type = _validate_photo(make_upload(WEBP_MAGIC, filename="photo.webp"))
        assert ext == "webp"
        assert content_type == "image/webp"


def test_non_image_content_with_image_extension_is_rejected(app):
    """The core fix: a file claiming to be a PNG by its filename, but
    whose actual content is not an image at all, must be rejected --
    previously this passed purely on the strength of the filename."""
    with app.app_context():
        fake = make_upload(b"<script>alert(1)</script>", filename="photo.png", content_type="image/png")
        with pytest.raises(StorageError, match="PNG, JPG, or WEBP"):
            _validate_photo(fake)


def test_svg_content_is_rejected_regardless_of_claimed_extension(app):
    """No explicit SVG blocklist entry needed -- SVG (or any other
    script-capable format) simply doesn't match any allowed magic-byte
    signature, so it's rejected by construction even if the filename
    and Content-Type both claim to be a PNG."""
    with app.app_context():
        svg_content = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
        fake = make_upload(svg_content, filename="photo.png", content_type="image/png")
        with pytest.raises(StorageError, match="PNG, JPG, or WEBP"):
            _validate_photo(fake)


def test_empty_file_is_rejected(app):
    with app.app_context():
        with pytest.raises(StorageError, match="empty"):
            _validate_photo(make_upload(b"", filename="empty.png"))


def test_oversized_file_is_rejected(app):
    with app.app_context():
        oversized = PNG_MAGIC + b"\x00" * (MAX_PHOTO_SIZE_BYTES + 1)
        with pytest.raises(StorageError, match="smaller than 5MB"):
            _validate_photo(make_upload(oversized))
