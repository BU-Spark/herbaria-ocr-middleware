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

# CONFIDENCE_CONTRACT.md: "_meta.model is "azure" or "mock"". An upstream response
# may claim its own model, but only from this set -- otherwise a compromised or
# simply misconfigured upstream could make a route report a provenance we never used.
_KNOWN_MODELS = {"azure", "mock"}

# Keys that belong to an Azure Document Intelligence *operation* wrapper rather than
# to a transcription. Their presence means we are looking at a response shape this
# middleware does not know how to read -- never at Darwin Core field names.
_AZURE_OPERATION_KEYS = {
    "analyzeResult",
    "status",
    "createdDateTime",
    "lastUpdatedDateTime",
    "error",
}


class UnrecognisedPayload(ValueError):
    """A payload matching none of the shapes to_envelope knows how to read.

    Raised rather than guessed at, because the alternative is worse: treating an
    unknown shape as flat Darwin Core produces a 200 with plausible-looking
    structure and no usable fields, so OCR silently "succeeds" and fills nothing.
    """


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

    # Values, not just keys, are validated. Previously both were copied verbatim, so
    # an upstream response could set model to anything and schema_version to any type
    # -- a route reporting a provenance it never used, and a schema_version that the
    # contract promises is the integer 1.
    raw_model = raw_meta.get("model")
    claimed_model = raw_model if raw_model in _KNOWN_MODELS else model

    out["_meta"] = {
        "model": claimed_model,
        # Always ours: this envelope is schema 1 because THIS code built it. Relaying
        # an upstream value could only break the guarantee the contract makes.
        "schema_version": SCHEMA_VERSION,
    }
    return out


def _from_flat(raw: dict, model: str) -> dict:
    """Wrap a flat DWC dict (no confidence) into the envelope."""
    out = {k: v for k, v in raw.items() if k not in _RESERVED_KEYS}
    out["_confidence"] = {}
    out["_meta"] = {"model": model, "schema_version": SCHEMA_VERSION}
    return out


def _looks_like_flat_dwc(raw: dict) -> bool:
    """Is this plausibly a flat ``{dwc_field: scalar}`` transcription?

    Two things disqualify it, and both are shapes we have actually seen or expect:

    * an Azure operation wrapper key -- ``prebuilt-read``/``prebuilt-layout``
      returns ``analyzeResult`` with ``pages`` and no ``documents``, and an
      in-flight or failed analyze returns ``status``/``error``. None of those are
      DWC field names.
    * a non-scalar value -- a DWC field holds text or a number, never a dict or a
      list, so a nested value means we are reading some other schema.

    An empty dict counts as flat: "OCR found nothing" is a legitimate result, and
    the caller can tell it apart from an error by the absence of fields.
    """
    fields = {k: v for k, v in raw.items() if k not in _RESERVED_KEYS}
    if not fields:
        return True
    if _AZURE_OPERATION_KEYS & fields.keys():
        return False
    return all(not isinstance(v, (dict, list)) for v in fields.values())


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
    if not _looks_like_flat_dwc(raw):
        raise UnrecognisedPayload(
            "payload is neither Azure-native, an envelope, nor a flat Darwin Core dict"
        )
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
