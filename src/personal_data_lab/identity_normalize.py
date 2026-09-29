"""Identifier shape-sniffing for Phase 2 (PHASE2_IMPLEMENTATION_PLAN.md).

Real actor_identity data has no uniform per-source shape: iMessages alone mixes raw phone
numbers (regular messages) and already-resolved display names (iMazing-resolved reactions)
depending on record type within the same source. classify() inspects one value at a time and
returns what it looks like; normalize_email/normalize_phone give the rules tier something it can
safely compare for exact equality across sources.

No reusable phone-normalization helper exists elsewhere in this codebase (checked
imessages/parser.py and the other importers before writing this) -- iMessages exports already
arrive close to E.164 (e.g. "+13173648707"), so a locale-aware parsing library is not worth the
dependency for Phase 2a's scope. This corrects PHASE2_IMPLEMENTATION_PLAN.md's own phrasing
("reusing the existing digit-normalization the importer already does"), which assumed such a
helper already existed; it does not, so this module is self-contained instead.
"""

from __future__ import annotations

import re
from typing import Literal

IdentifierShape = Literal["email", "phone", "name", "role_label", "opaque_id"]

# Values that refer to a role or a generic placeholder, never a specific correspondent. This is
# a living list, not a closed set: the entries below are exactly what this project's own real
# actor_identity data contained at Phase 2 plan-writing time (PHASE2_IMPLEMENTATION_PLAN.md).
# Extend it when review surfaces another one, the same way Phase 1's observable_kinds needed a
# second look after the first pass.
ROLE_LABELS = frozenset(
    {
        "you",
        "user",
        "assistant",
        "system",
        "human",
        "facebook user",
        "meta ai",
        "account_owner",
    }
)

_ANGLE_EMAIL_RE = re.compile(r"<([^<>]+@[^<>]+)>")
_BARE_EMAIL_RE = re.compile(r"^[^\s<>]+@[^\s<>]+\.[^\s<>]+$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_PHONE_CANDIDATE_RE = re.compile(r"^[+()\d\s.\-]{7,20}$")


def normalize_email(value: str) -> str:
    """Lowercase and strip an email address, extracting it from a "Name <email>" wrapper first
    if present (google's From-header strings), otherwise treating the whole value as the email.
    """
    match = _ANGLE_EMAIL_RE.search(value)
    email = match.group(1) if match else value
    return email.strip().lower()


def normalize_phone(value: str) -> str:
    """Digits only, with a leading '+' kept if the original had one. Not locale-aware: see the
    module docstring for why that's an acceptable scope-down here.
    """
    has_plus = value.strip().startswith("+")
    digits = re.sub(r"\D", "", value)
    return ("+" if has_plus else "") + digits


def normalize_name(value: str) -> str:
    """Casefolded, whitespace-collapsed name, for exact (never fuzzy) name comparison."""
    return " ".join(value.split()).casefold()


def phone_key(value: str) -> str | None:
    """A comparison key for phone numbers written different ways ("(317) 555-1234" on a contact
    card vs "+13175551234" in iMessage): a leading North American country code is dropped, so both
    become the same 10 digits. Other numbers keep all their digits. None if too short to be a
    phone number.
    """
    digits = re.sub(r"\D", "", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits if len(digits) >= 7 else None


def classify(value: str) -> IdentifierShape:
    """Classify one actor_identity string by its own shape, not by which source produced it --
    the same source can mix shapes (see module docstring).
    """
    stripped = value.strip()
    lowered = stripped.lower()

    if lowered in ROLE_LABELS:
        return "role_label"

    if _ANGLE_EMAIL_RE.search(stripped) or _BARE_EMAIL_RE.match(stripped):
        return "email"

    if _UUID_RE.match(stripped):
        return "opaque_id"

    if _PHONE_CANDIDATE_RE.match(stripped) and sum(c.isdigit() for c in stripped) >= 7:
        return "phone"

    return "name"
