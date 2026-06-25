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
    """Build the envelope from Azure Document Intelligence native output.

    Iterates ALL documents (a multi-page herbarium sheet can yield more than
    one), not just ``documents[0]``, so no field is silently dropped. On a field
    name appearing in multiple documents, the first occurrence wins (we do not
    overwrite an already-seen value), matching "first document is primary".
    """
    documents = raw["analyzeResult"]["documents"]
    if not isinstance(documents, list):
        documents = []

    flat: dict = {}
    confidence: dict = {}
    for doc in documents:
        if not isinstance(doc, dict):
            continue
        fields = doc.get("fields")
        if not isinstance(fields, dict):
            continue
        for name, field in fields.items():
            if name in flat:
                continue  # first-wins: don't let a later document overwrite
            value = _extract_field_value(field)
            # Coerce a present-but-empty field to "" so the Azure path matches
            # the flat path's behavior (consumers test `value == ""`).
            flat[name] = "" if value is None else value
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

    # Rebuild _meta from only the known keys so arbitrary/adversarial keys in an
    # incoming envelope's _meta never pass through into our output.
    raw_meta = raw.get("_meta")
    raw_meta = raw_meta if isinstance(raw_meta, dict) else {}
    out["_meta"] = {
        "model": raw_meta.get("model", model),
        "schema_version": raw_meta.get("schema_version", SCHEMA_VERSION),
    }
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
