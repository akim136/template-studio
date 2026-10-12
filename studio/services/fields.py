"""The model's field format, its validation, and conversion to the checker's envelope.

The model answers in image pixels and may anchor a field to a candidate. A field anchored to a
candidate takes the candidate's exact geometry from the PDF; otherwise its pixel box is converted
to source points. Invalid fields are rejected with a reason and kept in the record, never dropped
silently.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from studio.services.candidates import Candidate

COORDINATE_SPACE = "source_pt_top_left_72"
TYPES = ("signature", "initials", "date_signed", "text", "text-merge", "checkbox", "checkbox-merge")
MERGE_TYPES = ("text-merge", "checkbox-merge")
MERGE_NAMES = ("name", "emailAddress", "homePhone", "email", "amount", "units")
SENDER = "sender"
# Provisional heights for a field that sits on a line, in points. Development values only.
LINE_FIELD_HEIGHT = {"signature": 24.0, "initials": 20.0}
DEFAULT_LINE_FIELD_HEIGHT = 14.0


def field_schema(roles: list[str]) -> dict:
    nullable = lambda schema: {"anyOf": [schema, {"type": "null"}]}  # noqa: E731
    number = {"type": "number"}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["candidate_id", "type", "role", "merge_name", "required", "box", "evidence"],
        "properties": {
            "candidate_id": nullable({"type": "string"}),
            "type": {"type": "string", "enum": list(TYPES)},
            "role": {"type": "string", "enum": [*roles, SENDER]},
            "merge_name": nullable({"type": "string", "enum": list(MERGE_NAMES)}),
            "required": {"type": "boolean"},
            "box": {
                "type": "object",
                "additionalProperties": False,
                "required": ["left", "top", "width", "height"],
                "properties": {"left": number, "top": number, "width": number, "height": number},
            },
            "evidence": {"type": "string"},
        },
    }


def stage_schema(roles: list[str], self_check: bool) -> dict:
    properties = {
        "fields": {"type": "array", "items": field_schema(roles)},
        "notes": {"type": "string"},
    }
    if self_check:
        properties["changes"] = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": list(properties),
        "properties": properties,
    }


@dataclass(frozen=True)
class Field:
    page: int
    type: str
    signer: int | str  # role index, or "sender" for merge fields
    required: bool
    x: float
    y: float
    width: float
    height: float
    name: str | None
    candidate_id: str | None
    geometry_source: str  # "candidate" or "model_box"
    evidence: str

    def to_checker(self, document_index: int = 0) -> dict:
        out = {
            "document_index": document_index,
            "page": self.page,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "type": self.type,
            "signer": self.signer,
            "required": self.required,
        }
        if self.type in MERGE_TYPES:
            out["name"] = self.name
        return out


@dataclass(frozen=True)
class Rejected:
    page: int
    raw: dict
    reason: str


def _geometry(raw, kind, candidate, scale, page_size):
    """(x, y, width, height, source) in points, or a reason string."""
    if candidate is not None and candidate.kind == "box":
        return candidate.x, candidate.y, candidate.width, candidate.height, "candidate"
    if candidate is not None and kind not in ("checkbox", "checkbox-merge"):
        # A field on a line or underscore run sits on it: the line's span, a height by type.
        height = LINE_FIELD_HEIGHT.get(kind, DEFAULT_LINE_FIELD_HEIGHT)
        bottom = candidate.y + candidate.height
        return candidate.x, round(bottom - height, 2), candidate.width, height, "candidate"
    # No candidate, or a checkbox anchored to a line: the model's own box.
    box = raw["box"]
    x, y = box["left"] / scale, box["top"] / scale
    width, height = box["width"] / scale, box["height"] / scale
    page_width, page_height = page_size
    x, y = max(x, 0.0), max(y, 0.0)
    width, height = min(width, page_width - x), min(height, page_height - y)
    if width <= 0 or height <= 0:
        return "the box has no area inside the page"
    return round(x, 2), round(y, 2), round(width, 2), round(height, 2), "model_box"


def resolve(
    page: int,
    raws: list[dict],
    roles: list[str],
    candidates: list[Candidate],
    scale: float,
    page_size: tuple[float, float],
) -> tuple[list[Field], list[Rejected]]:
    """Validate the model's fields for one page and place them in source points."""
    by_id = {candidate.id: candidate for candidate in candidates}
    fields, rejected = [], []
    for raw in raws:
        kind, role, name = raw["type"], raw["role"], raw["merge_name"]
        if kind in MERGE_TYPES and (role != SENDER or name not in MERGE_NAMES):
            rejected.append(Rejected(page, raw, "a merge field needs role sender and a merge name"))
            continue
        if kind not in MERGE_TYPES and (role == SENDER or name is not None):
            rejected.append(Rejected(page, raw, "only merge fields belong to the sender"))
            continue
        candidate = None
        if raw["candidate_id"] is not None:
            candidate = by_id.get(raw["candidate_id"])
            if candidate is None:
                rejected.append(Rejected(page, raw, f"unknown candidate {raw['candidate_id']}"))
                continue
        geometry = _geometry(raw, kind, candidate, scale, page_size)
        if isinstance(geometry, str):
            rejected.append(Rejected(page, raw, geometry))
            continue
        x, y, width, height, source = geometry
        fields.append(
            Field(
                page=page,
                type=kind,
                signer=SENDER if role == SENDER else roles.index(role),
                required=bool(raw["required"]),
                x=x,
                y=y,
                width=width,
                height=height,
                name=name,
                candidate_id=raw["candidate_id"],
                geometry_source=source,
                evidence=raw["evidence"][:200],
            )
        )
    return fields, rejected


def envelope(
    *,
    source_sha256: str,
    pages: list[dict],
    fields: list[Field],
    signer_mode: str,
    business_rules_sha256: str,
    output_stage: str,
) -> dict:
    """The checker's prediction envelope for one document (document_index 0)."""
    return {
        "coordinate_space": COORDINATE_SPACE,
        "document_sha256": {"0": source_sha256},
        "pages": pages,
        "signer_mode": signer_mode,
        "business_rules_sha256": business_rules_sha256,
        "output_stage": output_stage,
        "fields": [field.to_checker() for field in fields],
    }


def field_dict(field: Field) -> dict:
    return asdict(field)
