"""A case's signing policy, and the checks a template's fields must pass against it.

There are two kinds of finding:
- An *error* means the save is invalid and is refused. It is a field that cannot exist under the
  policy or on the source: an unknown type, a countersigner the policy does not allow, or a page
  outside the source.
- An *issue* leaves the save valid but blocks review, approval and create. It is an empty field
  map, or a policy requirement that no field meets yet. Work in progress can be saved.

Required fields come from the case's policy alone. There is no universal signature rule.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from studio.services.fields import MERGE_NAMES, MERGE_TYPES, SENDER, TYPES

ROLES = ("Client", "CounterSigner")  # signer indexes 0 and 1
REQUIREMENT_TYPES = tuple(kind for kind in TYPES if kind not in MERGE_TYPES)
FIELD_KEYS = frozenset(
    {"id", "document_index", "page", "x", "y", "width", "height", "type", "signer", "required"}
)
FIELD_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
MAX_FIELDS = 2000
EDGE = 1e-6  # points: a box may touch the page edge


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Policy:
    countersigner: bool
    required: tuple[tuple[int, str], ...]  # (signer, type): at least one required field of each

    @property
    def roles(self) -> list[str]:
        return list(ROLES[: 2 if self.countersigner else 1])

    def to_dict(self) -> dict:
        required = [{"signer": signer, "type": kind} for signer, kind in self.required]
        return {"countersigner": self.countersigner, "required": required}


def parse_policy(raw: object) -> Policy:
    if not isinstance(raw, dict) or set(raw) != {"countersigner", "required"}:
        raise PolicyError("a policy has exactly the keys countersigner and required")
    countersigner, items = raw["countersigner"], raw["required"]
    if not isinstance(countersigner, bool):
        raise PolicyError("countersigner must be true or false")
    if not isinstance(items, list):
        raise PolicyError("required must be a list")
    required: list[tuple[int, str]] = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {"signer", "type"}:
            raise PolicyError("a requirement has exactly the keys signer and type")
        signer, kind = item["signer"], item["type"]
        if type(signer) is not int or signer not in (0, 1):
            raise PolicyError("a requirement names signer 0 (Client) or 1 (CounterSigner)")
        if signer == 1 and not countersigner:
            raise PolicyError("a CounterSigner requirement needs countersigner: true")
        if kind not in REQUIREMENT_TYPES:
            raise PolicyError(f"a requirement's type is one of {list(REQUIREMENT_TYPES)}")
        if (signer, kind) in required:
            raise PolicyError(f"duplicate requirement for {ROLES[signer]} {kind}")
        required.append((signer, kind))
    return Policy(countersigner, tuple(required))


def field_errors(fields: object, policy: Policy, pages: dict[tuple[int, int], dict]) -> list[str]:
    """Errors that make a save invalid. `pages` is the sealed source's inventory."""
    if not isinstance(fields, list):
        return ["fields must be a list"]
    if len(fields) > MAX_FIELDS:
        return [f"a template has at most {MAX_FIELDS} fields"]
    errors, seen = [], set()
    for index, field in enumerate(fields):
        problem = _field_error(field, policy, pages)
        if problem is None and field["id"] in seen:
            problem = f"duplicate id {field['id']!r}"
        if problem is None:
            seen.add(field["id"])
        else:
            errors.append(f"field {index}: {problem}")
    return errors


def issues(fields: list[dict], policy: Policy) -> list[str]:
    """What blocks review, approval and create on a valid revision."""
    if not fields:
        return ["the field map is empty"]
    present = {(field["signer"], field["type"]) for field in fields if field["required"]}
    return [
        f"the policy requires a required {kind} field for {ROLES[signer]}"
        for signer, kind in policy.required
        if (signer, kind) not in present
    ]


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _field_error(field: object, policy: Policy, pages: dict[tuple[int, int], dict]) -> str | None:
    if not isinstance(field, dict):
        return "a field is an object"
    kind = field.get("type")
    keys = FIELD_KEYS | {"name"} if kind in MERGE_TYPES else FIELD_KEYS
    if set(field) != keys:
        return f"a {kind!r} field has exactly the keys {sorted(keys)}"
    if not isinstance(field["id"], str) or not FIELD_ID.fullmatch(field["id"]):
        return "id is 1 to 64 letters, digits, '-' or '_'"
    if kind not in TYPES:
        return f"unknown type {kind!r}"
    signer = field["signer"]
    if kind in MERGE_TYPES:
        if signer != SENDER or field["name"] not in MERGE_NAMES:
            return f"a merge field has signer {SENDER!r} and a name from {list(MERGE_NAMES)}"
    elif signer == SENDER:
        return "only merge fields belong to the sender"
    elif type(signer) is not int or not 0 <= signer < len(policy.roles):
        return f"signer {signer!r} is not allowed by the policy (roles {policy.roles})"
    if not isinstance(field["required"], bool):
        return "required must be true or false"
    if type(field["document_index"]) is not int or type(field["page"]) is not int:
        return "document_index and page are integers"
    page = pages.get((field["document_index"], field["page"]))
    if page is None:
        return "the page is not in the source"
    if page["rotation"]:
        return "the page is rotated; rotated pages need an audited normalizer first"
    if page["width"] is None:
        return "the page could not be measured"
    box = [field[key] for key in ("x", "y", "width", "height")]
    if not all(_number(value) for value in box):
        return "x, y, width and height are finite numbers"
    x, y, width, height = box
    if width <= 0 or height <= 0 or x < 0 or y < 0:
        return "the box has a positive size and starts on the page"
    if x + width > page["width"] + EDGE or y + height > page["height"] + EDGE:
        return "the box lies outside the page"
    return None
