"""Every number must be traceable in one hop, and the framing text must not drift."""

from __future__ import annotations

import pytest

from astra.domain.model_config import MODEL_CONFIG
from astra.domain.notices import NOTICES
from astra.domain.registry import FORMULAS, constants_for, get_formula


def test_formula_ids_are_consistent_with_their_keys() -> None:
    for formula_id, spec in FORMULAS.items():
        assert spec.formula_id == formula_id


def test_every_formula_declares_expression_inputs_and_engine() -> None:
    for spec in FORMULAS.values():
        assert spec.expression.strip(), f"{spec.formula_id} has no expression"
        assert spec.inputs, f"{spec.formula_id} declares no inputs"
        assert spec.engine.startswith("engines."), f"{spec.formula_id} names no engine"
        assert spec.version, f"{spec.formula_id} is unversioned"


def test_every_declared_config_key_resolves_to_a_real_constant() -> None:
    """A formula may not reference a weight that does not exist."""
    known = {c.key for c in MODEL_CONFIG.constants()}
    for spec in FORMULAS.values():
        for key in spec.config_keys:
            if key.endswith("*"):
                prefix = key[:-1]
                assert any(k.startswith(prefix) for k in known), (
                    f"{spec.formula_id} references unknown config prefix '{key}'"
                )
            else:
                assert key in known, f"{spec.formula_id} references unknown config '{key}'"


def test_constants_for_resolves_wildcards() -> None:
    resolved = constants_for("vulnerability.index")
    assert resolved, "vulnerability formula should resolve its weight family"
    assert all(c.key.startswith("vulnerability.w_") for c in resolved)


def test_unknown_formula_is_rejected_loudly() -> None:
    with pytest.raises(KeyError, match="not registered"):
        get_formula("hazard.vibes")


def test_notices_state_the_things_astra_must_never_imply() -> None:
    assert "never to produce them" in NOTICES.how_this_works
    assert "SDMA" in NOTICES.decision_authority
    assert NOTICES.classification_label == "ASTRA analytical classification"
    assert "synthetic" in NOTICES.scenario_disclaimer
    assert "ownership" in NOTICES.site_tenure_limitation
    assert "not a probability" in NOTICES.priority_not_probability


def test_priority_formula_carries_the_rank_not_probability_caveat() -> None:
    spec = get_formula("priority.score")
    assert spec.notes and "not a probability" in spec.notes


def test_zone_classification_formula_disclaims_statutory_authority() -> None:
    spec = get_formula("hazard.zone_class")
    assert spec.notes and "not a statutory" in spec.notes
