"""The node catalog is the planner's grammar; keep it internally consistent."""

from __future__ import annotations

from app.nodes.catalog import NODE_CATALOG, catalog_digest, get_node_spec, list_specs


def test_keys_match_spec_type():
    for key, spec in NODE_CATALOG.items():
        assert key == spec.type


def test_exactly_one_trigger_and_it_is_categorised():
    triggers = [s for s in list_specs() if s.category == "trigger"]
    assert [s.type for s in triggers] == ["manual_trigger"]


def test_every_spec_has_at_least_one_output_handle():
    for spec in list_specs():
        assert spec.output_handles


def test_if_node_exposes_true_false_branches():
    spec = get_node_spec("if")
    assert spec is not None
    assert spec.output_handles == ["true", "false"]


def test_unknown_type_returns_none():
    assert get_node_spec("does_not_exist") is None


def test_digest_is_json_friendly_and_complete():
    digest = catalog_digest()
    assert {entry["type"] for entry in digest} == set(NODE_CATALOG)
    for entry in digest:
        assert set(entry) >= {"type", "category", "description", "params", "outputs", "output_handles"}
