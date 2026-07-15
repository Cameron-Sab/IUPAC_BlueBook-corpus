from iupac_engine.rule_engine import BlueBookRuleEngine


def test_rule_engine_loads_full_corpus():
    engine = BlueBookRuleEngine()
    stats = engine.stats()
    assert stats["record_count"] == 1829
    assert stats["graph_nodes"] == 1829
    assert stats["graph_edges"] > 0
    assert engine.metadata["conversion_status"] == "normalized_semantic"


def test_rule_lookup_and_dependencies():
    engine = BlueBookRuleEngine()
    rules = engine.get("P-61.2.1")
    assert rules
    deps = engine.dependencies("P-61.2.1")
    assert any(edge["target"] == "P-44.3" for edge in deps["edges"])


def test_fact_evaluation_returns_activated_rules():
    engine = BlueBookRuleEngine()
    result = engine.evaluate(["parent", "locant"], limit=5)
    assert result["activated_count"] > 0
    assert result["activated"]


def test_normalized_rules_have_requirements_not_draft_markers():
    engine = BlueBookRuleEngine()
    assert all("implementation_requirements" in rule.as_dict() for rule in engine.rules)
    assert not any(key.endswith("_semantics") for rule in engine.rules for key in rule.as_dict())
    assert any(rule.implementation_requirements for rule in engine.rules)
