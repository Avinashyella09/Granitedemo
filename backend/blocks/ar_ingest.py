"""
Validation helpers for the AR measurement ingestion endpoint
(POST /api/blocks/ar-measure/).

Kept out of views.py deliberately: that module already carries six copy-pasted
block-serialisation blocks, and the ingestion path is the one place in the
system where a bad value becomes a permanent, billable record.

Every function here either returns a clean value or raises ARIngestError.
Nothing repairs, defaults, or guesses.
"""

import datetime
import math
import re

# block_id is interpolated straight into a filename (views.py builds
# "{block_id}_{ts}_ar_raw.jpg"), so anything that could escape MEDIA_ROOT/raw
# has to be refused rather than sanitised - silently rewriting an identifier
# would mean the stored block_id no longer matches what the officer submitted.
_BLOCK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")

# Volume agreement tolerance. Floats arrive as decimal strings from the phone,
# and the client computes L*B*H in JS, so exact equality is unrealistic.
# 1% relative covers rounding at any block size; the absolute floor keeps very
# small test blocks from tripping it.
VOLUME_RELATIVE_TOLERANCE = 0.01
VOLUME_ABSOLUTE_TOLERANCE = 0.0005

# A phone photo is a few MB. This is a sanity ceiling, not a quality gate.
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MIN_IMAGE_BYTES = 100


class ARIngestError(Exception):
    """Validation failure. `message` is returned to the client under 'error',
    which is the key field-officer-app/src/services/api.js:106 reads."""

    def __init__(self, message, code=None):
        super().__init__(message)
        self.message = message
        self.code = code


def clean_block_id(raw):
    if raw is None:
        raise ARIngestError("block_id is required.", "block_id_missing")
    value = str(raw).strip()
    if not value:
        raise ARIngestError("block_id is required.", "block_id_missing")
    if not _BLOCK_ID_RE.match(value):
        raise ARIngestError(
            "block_id may contain only letters, digits, dot, dash and underscore, "
            "must start with a letter or digit, and must be at most 100 characters.",
            "block_id_unsafe",
        )
    # Defence in depth: the regex already excludes separators, but ".." would be
    # catastrophic in a filename and is cheap to assert explicitly.
    if ".." in value:
        raise ARIngestError("block_id must not contain '..'.", "block_id_unsafe")
    return value


def clean_dimension(raw, field):
    if raw is None or str(raw).strip() == "":
        raise ARIngestError(f"{field} is required.", "dimension_missing")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ARIngestError(f"{field} must be a number; got {raw!r}.", "dimension_not_numeric")
    if math.isnan(value) or math.isinf(value):
        raise ARIngestError(f"{field} must be a finite number.", "dimension_not_finite")
    if value <= 0:
        raise ARIngestError(
            f"{field} must be greater than zero; got {value}.", "dimension_not_positive"
        )
    return value


def clean_optional_client_volume(raw):
    """The client's own volume. Never stored - only compared. Absent is fine."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ARIngestError(f"volume_m3 must be a number; got {raw!r}.", "volume_not_numeric")
    if math.isnan(value) or math.isinf(value):
        raise ARIngestError("volume_m3 must be a finite number.", "volume_not_finite")
    return value


def compare_volume(server_volume, client_volume):
    """Server volume is authoritative. This only describes the disagreement.

    Returns a dict that is safe to persist in an audit entry and to return to
    the client. `agrees` is None when the client sent no volume at all.
    """
    if client_volume is None:
        return {
            "server_calculated_m3": round(server_volume, 6),
            "client_reported_m3": None,
            "difference_m3": None,
            "agrees": None,
            "note": "Client sent no volume; server value used.",
        }
    difference = abs(server_volume - client_volume)
    tolerance = max(VOLUME_ABSOLUTE_TOLERANCE, abs(server_volume) * VOLUME_RELATIVE_TOLERANCE)
    agrees = difference <= tolerance
    return {
        "server_calculated_m3": round(server_volume, 6),
        "client_reported_m3": round(client_volume, 6),
        "difference_m3": round(difference, 6),
        "agrees": agrees,
        "note": (
            "Client volume agrees with L*B*H within tolerance."
            if agrees
            else "Client volume disagrees with L*B*H; the server-calculated value was stored."
        ),
    }


def clean_coordinate(raw, field, limit):
    """Latitude/longitude. Absent -> None. 0.0 is a legitimate value and is
    preserved: the previous implementation used `if lat:` which silently
    discarded a genuine equator/prime-meridian reading."""
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    try:
        value = float(text)
    except (TypeError, ValueError):
        raise ARIngestError(f"{field} must be a number; got {raw!r}.", "gps_not_numeric")
    if math.isnan(value) or math.isinf(value):
        raise ARIngestError(f"{field} must be a finite number.", "gps_not_finite")
    if value < -limit or value > limit:
        raise ARIngestError(
            f"{field} must be between -{limit} and {limit}; got {value}.", "gps_out_of_range"
        )
    return value


def validate_image(uploaded_file):
    """Confirm the upload is genuinely an image, not merely a non-empty file.

    Returns the lowercase format string (e.g. 'jpeg'). Pillow's verify() reads
    the header and raises on anything that is not a real image, which is what
    stops an arbitrary payload being written to MEDIA_ROOT and then served back
    from the same origin.
    """
    if uploaded_file is None:
        raise ARIngestError("image file is required.", "image_missing")

    size = getattr(uploaded_file, "size", None)
    if size is not None:
        if size < MIN_IMAGE_BYTES:
            raise ARIngestError("image file is empty or too small to be a photo.", "image_too_small")
        if size > MAX_IMAGE_BYTES:
            raise ARIngestError(
                f"image file is {size} bytes; the limit is {MAX_IMAGE_BYTES} bytes.",
                "image_too_large",
            )

    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        raise ARIngestError("Server cannot validate images (Pillow unavailable).", "image_unverifiable")

    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.verify()
        image_format = (image.format or "").lower()
    except ARIngestError:
        raise
    except Exception:
        raise ARIngestError(
            "Uploaded file is not a readable image.", "image_invalid"
        )
    finally:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

    if not image_format:
        raise ARIngestError("Uploaded image has no recognisable format.", "image_invalid")
    return image_format


def utcnow():
    """Naive UTC, matching every existing default in blocks/models.py."""
    return datetime.datetime.utcnow()
