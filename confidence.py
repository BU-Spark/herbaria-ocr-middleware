"""Confidence envelope construction for the OCR middleware.

The middleware must ALWAYS return a backward-compatible envelope: the existing
flat Darwin Core values are preserved byte-for-byte, alongside a parallel
``_confidence`` map and a small ``_meta`` block. See CONFIDENCE_CONTRACT.md.
"""

import math
from typing import Any

SCHEMA_VERSION = 1

# Reserved keys that are part of the envelope structure, not DWC field values.
_RESERVED_KEYS = {"_confidence", "_meta"}


def _clamp_confidence(value: Any):
    """Return a float clamped to [0.0, 1.0], or None if not a usable number.

    Bools are rejected (``isinstance(True, int)`` is True, but a boolean is not
    a confidence score). NaN and +/-inf are rejected so we never emit a bad
    number into the envelope.
    """
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    f = float(value)
    if math.isnan(f) or math.isinf(f):
        return None
    if f < 0.0:
        return 0.0
    if f > 1.0:
        return 1.0
    return f


def _extract_field_value(field: Any) -> Any:
    """Pull the scalar value out of an Azure Document Intelligence field object.

    Azure fields carry the value under a type-specific key (``valueString``,
    ``valueNumber``, ``valueDate``, ...) and almost always also under
    ``content``. We prefer the typed value, falling back to ``content``.
    """
    if not isinstance(field, dict):
        return field
    for key in (
        "valueString",
        "valueNumber",
        "valueInteger",
        "valueDate",
        "valueTime",
        "valuePhoneNumber",
        "valueCountryRegion",
        "value",
        "content",
    ):
        if key in field and field[key] is not None:
            return field[key]
    return field.get("content")


def _from_azure_native(raw: dict, model: str) -> dict:
    """Build the envelope from Azure Document Intelligence native output."""
    documents = raw["analyzeResult"]["documents"]
    fields = {}
    if isinstance(documents, list) and documents:
        first = documents[0]
        if isinstance(first, dict):
            fields = first.get("fields") or {}
    if not isinstance(fields, dict):
        fields = {}

    flat: dict = {}
    confidence: dict = {}
    for name, field in fields.items():
        flat[name] = _extract_field_value(field)
        if isinstance(field, dict):
            score = _clamp_confidence(field.get("confidence"))
            if score is not None:
                confidence[name] = score

    flat["_confidence"] = confidence
    flat["_meta"] = {"model": model, "schema_version": SCHEMA_VERSION}
    return flat


def _from_envelope(raw: dict, model: str) -> dict:
    """Pass through an existing envelope, sanitising confidences and meta."""
    out: dict = {}
    for key, value in raw.items():
        if key in _RESERVED_KEYS:
            continue
        out[key] = value

    clean_conf: dict = {}
    raw_conf = raw.get("_confidence")
    if isinstance(raw_conf, dict):
        for name, score in raw_conf.items():
            clamped = _clamp_confidence(score)
            if clamped is not None:
                clean_conf[name] = clamped
    out["_confidence"] = clean_conf

    meta = raw.get("_meta")
    if not isinstance(meta, dict):
        meta = {}
    else:
        meta = dict(meta)
    meta.setdefault("model", model)
    meta.setdefault("schema_version", SCHEMA_VERSION)
    out["_meta"] = meta
    return out


def _from_flat(raw: dict, model: str) -> dict:
    """Wrap a flat DWC dict (no confidence) into the envelope."""
    out = {k: v for k, v in raw.items() if k not in _RESERVED_KEYS}
    out["_confidence"] = {}
    out["_meta"] = {"model": model, "schema_version": SCHEMA_VERSION}
    return out


def _is_azure_native(raw: dict) -> bool:
    analyze = raw.get("analyzeResult")
    if not isinstance(analyze, dict):
        return False
    docs = analyze.get("documents")
    return isinstance(docs, list)


def to_envelope(raw: Any, model: str) -> dict:
    """Convert a raw OCR response into the backward-compatible envelope.

    Defensively handles, in priority order:
      a. Azure Document Intelligence native format
         (``raw["analyzeResult"]["documents"][0]["fields"]``).
      b. An object that is already an envelope (has ``_confidence``).
      c. A flat DWC dict with no confidence (emits values, ``_confidence={}``).

    All confidences are clamped to [0.0, 1.0]; NaN/inf/non-numeric scores are
    skipped. Flat values are preserved unchanged. ``_meta`` is always added.
    """
    if not isinstance(raw, dict):
        # Unknown / unexpected shape: never crash, return an empty envelope.
        return {
            "_confidence": {},
            "_meta": {"model": model, "schema_version": SCHEMA_VERSION},
        }

    if _is_azure_native(raw):
        return _from_azure_native(raw, model)
    if "_confidence" in raw:
        return _from_envelope(raw, model)
    return _from_flat(raw, model)


def synthesize_confidence(flat: dict) -> dict:
    """Produce a plausible per-field confidence map for mock data.

    Deterministic (no randomness) so the front-end and tests see stable values.
    Empty / "N/A" / unknown values get a low score; everything else gets a
    mid-to-high score that varies by field name length so the UI sees a spread.
    """
    conf: dict = {}
    for name, value in flat.items():
        if name in _RESERVED_KEYS:
            continue
        text = "" if value is None else str(value).strip()
        if text == "" or text.lower() in {"n/a", "unknown"}:
            conf[name] = 0.30
            continue
        # Deterministic spread in roughly [0.72, 0.99].
        score = 0.72 + (len(name) % 10) * 0.03
        conf[name] = round(min(score, 0.99), 2)
    return conf
