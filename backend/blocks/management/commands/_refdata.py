"""
Shared validation helpers for the reference-data management commands
(create_quarry, create_officer).

Named with a leading underscore so Django's management-command discovery
ignores it - it is a helper module, not a command.

Everything here rejects rather than repairs. These commands exist because the
granite_blocks database starts empty and real AR ingestion needs real Quarry
and Officer records; the one thing they must never do is invent a plausible
value, because a fabricated quarry or officer is indistinguishable from a real
one once it is in the database.
"""

import datetime
import re

from django.core.management.base import CommandError

# Models store naive UTC (fields default to datetime.datetime.utcnow), so dates
# parsed here are naive UTC too. Emitting aware datetimes would make these
# records sort and compare differently from every existing one.
DATE_FORMAT = "%Y-%m-%d"

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def required_text(value, field, max_length):
    """A required string field: present, non-blank after stripping, within length."""
    if value is None:
        raise CommandError(f"--{field.replace('_', '-')} is required.")
    text = value.strip()
    if not text:
        raise CommandError(f"--{field.replace('_', '-')} cannot be empty or whitespace.")
    if len(text) > max_length:
        raise CommandError(
            f"--{field.replace('_', '-')} is {len(text)} characters; the model allows {max_length}."
        )
    return text


def optional_text(value, field, max_length):
    """An optional string field. Absent stays absent; blank is rejected rather
    than stored, so a record never carries an empty string pretending to be data."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise CommandError(
            f"--{field.replace('_', '-')} was supplied but is empty. "
            f"Omit the option entirely rather than passing a blank value."
        )
    if len(text) > max_length:
        raise CommandError(
            f"--{field.replace('_', '-')} is {len(text)} characters; the model allows {max_length}."
        )
    return text


def optional_date(value, field):
    """Optional YYYY-MM-DD date -> naive UTC datetime at midnight."""
    if value is None:
        return None
    text = value.strip()
    if not text:
        raise CommandError(f"--{field.replace('_', '-')} was supplied but is empty.")
    try:
        parsed = datetime.datetime.strptime(text, DATE_FORMAT)
    except ValueError:
        raise CommandError(
            f"--{field.replace('_', '-')} must be in YYYY-MM-DD format; got {text!r}."
        )
    return parsed


def optional_positive_float(value, field):
    """Optional float that must be finite and strictly positive."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CommandError(f"--{field.replace('_', '-')} must be a number; got {value!r}.")
    if number != number or number in (float("inf"), float("-inf")):
        raise CommandError(f"--{field.replace('_', '-')} must be a finite number.")
    if number <= 0:
        raise CommandError(
            f"--{field.replace('_', '-')} must be greater than zero; got {number}."
        )
    return number


def optional_email(value, field):
    if value is None:
        return None
    text = optional_text(value, field, 100)
    if not _EMAIL_RE.match(text):
        raise CommandError(f"--{field.replace('_', '-')} is not a valid email address: {text!r}.")
    return text


def parse_bool(value, field):
    """Explicit boolean. No truthiness guessing - an unrecognised value is an error."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    raise CommandError(
        f"--{field.replace('_', '-')} must be one of true/false (also yes/no, 1/0); got {value!r}."
    )


def render(label, values):
    """Human-readable summary of what would be / was written."""
    lines = [label]
    width = max(len(k) for k in values) if values else 0
    for key, val in values.items():
        shown = "(not set)" if val is None else val
        lines.append(f"    {key.ljust(width)} : {shown}")
    return "\n".join(lines)
