# OCR Confidence Envelope Contract

**Schema version: 1**

The OCR middleware always returns a **backward-compatible envelope**. Existing
flat Darwin Core (DWC) values are preserved exactly as before; a parallel
`_confidence` map and a small `_meta` block are added alongside them.

## Envelope shape

```json
{
  "scientificName": "Acer rubrum",
  "recordedBy": "J. Smith",
  "_confidence": { "scientificName": 0.92, "recordedBy": 0.71 },
  "_meta": { "model": "azure", "schema_version": 1 }
}
```

- The top level keeps the **flat DWC fields** (e.g. `scientificName`,
  `recordedBy`, `eventDate`, `locality`) unchanged. Existing consumers that
  ignore the `_`-prefixed keys keep working with no changes.
- `_confidence` is a **parallel map**: DWC field name -> confidence score.
- `_meta.model` is `"azure"` or `"mock"`. `_meta.schema_version` is `1`.

## Confidence scale

- Scores are **floats in the closed interval `[0.0, 1.0]`** (0 = no confidence,
  1 = maximum confidence).
- Scores are **clamped** into `[0.0, 1.0]`. Any non-numeric, `null`, boolean,
  `NaN`, or infinite confidence is **dropped** — the middleware never emits a
  bad number.

## "Field absent from `_confidence` => unavailable"

If a DWC field appears at the top level but **is not a key in `_confidence`**,
the confidence for that field is **unavailable** (the model did not provide a
score, or the score was invalid and dropped). Front-end code should treat an
absent confidence as "unknown" — show the value, but do not render a score bar.

An empty `_confidence` (`{}`) is valid and means **no scores at all** (e.g. a
legacy flat OCR response with no confidence data). The values still populate.

## Input shapes the middleware accepts (defensive)

`confidence.to_envelope(raw, model)` handles, in priority order:

1. **Azure Document Intelligence native** —
   `raw["analyzeResult"]["documents"][0]["fields"]`, where each field is like
   `{ "valueString" | "content": "...", "confidence": 0.0-1.0 }`. Field name ->
   value is flattened into the flat keys; field name -> confidence into
   `_confidence`.
2. **Already an envelope** — `raw` already contains `_confidence`. Passed
   through; `_meta` ensured; confidences re-clamped/sanitised.
3. **Flat DWC dict, no confidence** — values emitted unchanged,
   `_confidence = {}`.

## Note for Dang (model scripts)

Your model/OCR scripts should emit **either**:

- **Azure-DI-native** output (`analyzeResult.documents[].fields[].confidence`),
  which the middleware flattens automatically, **or**
- this **envelope directly** (flat DWC values + a `_confidence` map of
  field -> float in `[0, 1]`).

Either way the middleware normalises into the envelope above. Prefer including
per-field confidences so the front-end can surface them.

## Assumption (to confirm)

We built against Azure's **documented**
`analyzeResult.documents[].fields[].confidence` shape. We do **not yet** have a
real Azure response (the endpoint URL arrives later). Once a live response is
available, confirm the field structure (typed `valueString`/`content` keys and
the `confidence` placement) and adjust `confidence.py` if Azure's actual payload
differs. The flat-dict and envelope paths are unaffected by that confirmation.
