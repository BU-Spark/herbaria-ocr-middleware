"""Tests for the confidence envelope (confidence.to_envelope)."""

import json
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from confidence import (  # noqa: E402
    SCHEMA_VERSION,
    synthesize_confidence,
    to_envelope,
)

FIXTURE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "test_data",
    "azure_native_sample.json",
)


def _meta_ok(env, model):
    assert env["_meta"]["model"] == model
    assert env["_meta"]["schema_version"] == SCHEMA_VERSION


# --- (1a) Azure Document Intelligence native format -------------------------

def test_azure_native_flattens_values_and_confidence():
    with open(FIXTURE) as f:
        raw = json.load(f)
    env = to_envelope(raw, model="azure")

    # Flat values are flattened out of the field objects.
    assert env["scientificName"] == "Acer rubrum"
    assert env["recordedBy"] == "J. Smith"
    assert env["eventDate"] == "1899-07-04"
    assert env["locality"] == "Bank of the Charles River"

    # Confidence map mirrors field names -> scores.
    assert env["_confidence"]["scientificName"] == 0.92
    assert env["_confidence"]["recordedBy"] == 0.71
    assert env["_confidence"]["eventDate"] == 0.5

    # Field with no confidence is absent from the map but value still present.
    assert "locality" not in env["_confidence"]
    assert "locality" in env

    _meta_ok(env, "azure")


def test_azure_native_missing_documents_is_graceful():
    raw = {"analyzeResult": {"documents": []}}
    env = to_envelope(raw, model="azure")
    assert env["_confidence"] == {}
    _meta_ok(env, "azure")


# --- (1b) Already an envelope ----------------------------------------------

def test_existing_envelope_passthrough():
    raw = {
        "scientificName": "Quercus alba",
        "recordedBy": "A. Gray",
        "_confidence": {"scientificName": 0.8, "recordedBy": 0.4},
        "_meta": {"model": "azure", "schema_version": SCHEMA_VERSION},
    }
    env = to_envelope(raw, model="mock")

    assert env["scientificName"] == "Quercus alba"
    assert env["recordedBy"] == "A. Gray"
    assert env["_confidence"] == {"scientificName": 0.8, "recordedBy": 0.4}
    # Existing meta wins over the passed model.
    assert env["_meta"]["model"] == "azure"
    assert env["_meta"]["schema_version"] == SCHEMA_VERSION


def test_envelope_without_meta_gets_meta():
    raw = {"scientificName": "X", "_confidence": {"scientificName": 0.5}}
    env = to_envelope(raw, model="azure")
    _meta_ok(env, "azure")


# --- (1c) Flat DWC dict, no confidence -------------------------------------

def test_flat_dwc_no_confidence():
    raw = {
        "scientificName": "Tigridia pavonia",
        "country": "France",
        "recordedBy": "Antoine Laurent de Jussieu",
    }
    env = to_envelope(raw, model="mock")

    assert env["scientificName"] == "Tigridia pavonia"
    assert env["country"] == "France"
    assert env["recordedBy"] == "Antoine Laurent de Jussieu"
    assert env["_confidence"] == {}
    _meta_ok(env, "mock")


# --- Flat values preserved byte-for-byte -----------------------------------

def test_flat_values_preserved_exactly():
    raw = {
        "scientificName": "Croton californicus Tenuis (S.Wats) Feng.",
        "minimumElevationInMeters": "792.48",
        "decimalLatitude": "",
        "institutionCode": "MUSÉUM D'HISTOIRE NATURELLE DE PARIS",
    }
    env = to_envelope(raw, model="mock")
    for key, value in raw.items():
        assert env[key] == value
        assert type(env[key]) is type(value)


def test_azure_values_preserved_from_fixture():
    with open(FIXTURE) as f:
        raw = json.load(f)
    env = to_envelope(raw, model="azure")
    fields = raw["analyzeResult"]["documents"][0]["fields"]
    for name, field in fields.items():
        assert env[name] == field["valueString"]


# --- Clamping ---------------------------------------------------------------

def test_confidence_clamped_to_unit_interval():
    raw = {
        "analyzeResult": {
            "documents": [
                {
                    "fields": {
                        "a": {"valueString": "x", "confidence": 1.5},
                        "b": {"valueString": "y", "confidence": -0.2},
                        "c": {"valueString": "z", "confidence": 0.5},
                    }
                }
            ]
        }
    }
    env = to_envelope(raw, model="azure")
    assert env["_confidence"]["a"] == 1.0
    assert env["_confidence"]["b"] == 0.0
    assert env["_confidence"]["c"] == 0.5


def test_envelope_confidence_also_clamped():
    raw = {
        "a": "x",
        "_confidence": {"a": 2.0, "b": -5},
    }
    env = to_envelope(raw, model="mock")
    assert env["_confidence"]["a"] == 1.0
    assert env["_confidence"]["b"] == 0.0


# --- NaN / non-numeric skipped ---------------------------------------------

def test_nan_and_non_numeric_confidence_skipped():
    raw = {
        "analyzeResult": {
            "documents": [
                {
                    "fields": {
                        "nanField": {"valueString": "v1", "confidence": float("nan")},
                        "infField": {"valueString": "v2", "confidence": float("inf")},
                        "strField": {"valueString": "v3", "confidence": "high"},
                        "boolField": {"valueString": "v4", "confidence": True},
                        "noneField": {"valueString": "v5", "confidence": None},
                        "goodField": {"valueString": "v6", "confidence": 0.9},
                    }
                }
            ]
        }
    }
    env = to_envelope(raw, model="azure")

    # Bad scores are skipped...
    for bad in ("nanField", "infField", "strField", "boolField", "noneField"):
        assert bad not in env["_confidence"]
        # ...but the values still populate.
        assert bad in env

    assert env["_confidence"]["goodField"] == 0.9
    # No NaN ever leaks into the map.
    assert not any(
        isinstance(v, float) and math.isnan(v) for v in env["_confidence"].values()
    )


# --- Non-dict input is graceful --------------------------------------------

@pytest.mark.parametrize("bad", [None, [], "string", 42])
def test_non_dict_input_returns_empty_envelope(bad):
    env = to_envelope(bad, model="azure")
    assert env["_confidence"] == {}
    _meta_ok(env, "azure")


# --- Mock confidence synthesis ---------------------------------------------

def test_synthesize_confidence_scores_in_range():
    flat = {
        "scientificName": "Acer rubrum",
        "recordedBy": "unknown",
        "decimalLatitude": "",
        "_confidence": {},
        "_meta": {},
    }
    conf = synthesize_confidence(flat)
    assert "_confidence" not in conf
    assert "_meta" not in conf
    for score in conf.values():
        assert 0.0 <= score <= 1.0
    # Empty / unknown values get the low score.
    assert conf["recordedBy"] == 0.30
    assert conf["decimalLatitude"] == 0.30
    assert conf["scientificName"] > 0.30
