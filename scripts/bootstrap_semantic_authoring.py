from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.build_semantic_asset_scaffold import load_asset_scaffold, task_asset_figures
    from scripts.compile_semantic_authoring import compile_authoring
    from scripts.scaffold_semantic_authoring import scaffold_authoring
    from scripts.scaffold_semantic_delta import scaffold_delta
    from scripts.validate_normalized_rule_chunks import canonical_json_bytes as chunk_bytes
else:
    from build_compact_semantic_tasks import canonical_json_bytes, load_json
    from build_semantic_asset_scaffold import load_asset_scaffold, task_asset_figures
    from compile_semantic_authoring import compile_authoring
    from scaffold_semantic_authoring import scaffold_authoring
    from scaffold_semantic_delta import scaffold_delta
    from validate_normalized_rule_chunks import canonical_json_bytes as chunk_bytes


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TASK_DIR = ROOT / "work" / "compact_semantic_tasks"
DEFAULT_TRAINING_DIR = ROOT / "data" / "bluebook_v3" / "semantic_authoring"
DEFAULT_AUTHORING_DIR = ROOT / "work" / "bootstrap_semantic_authoring"
DEFAULT_DELTA_DIR = ROOT / "work" / "bootstrap_semantic_deltas"
DEFAULT_CHUNK_DIR = ROOT / "work" / "bootstrap_semantic_chunks"

VALID_ROLES = {
    "heading",
    "scope",
    "definition",
    "condition",
    "effect",
    "constraint",
    "permission",
    "prohibition",
    "preference_criterion",
    "tie_continuation",
    "procedure_step",
    "mapping_entry",
    "exception",
    "cross_reference",
    "table_data",
    "table_layout",
    "figure_asset",
    "example",
    "note",
    "rationale",
    "history",
    "correction_event",
    "source_metadata",
}

NORMATIVE_VERBS = re.compile(
    r"\b(?:is|are|shall|must|may|will|has|have|consists?|contains?|"
    r"named|formed|selected|chosen|cited|numbered|used|indicated|assigned|"
    r"written|added|replaced|retained|preferred|included|proceeds?|begins?|"
    r"takes?|receives?|denotes?|expressed|constructed|applied)\b",
    re.IGNORECASE,
)
RULE_PREFIX = re.compile(r"^(P-\d+(?:\.\d+)*)\s+(.+)$", re.DOTALL)
TOKEN = re.compile(r"[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)?")
REFERENCE_TOKEN = re.compile(r"P-\d+(?:\.\d+)*")

DEFINITION_ROLES = {
    "heading",
    "scope",
    "definition",
    "cross_reference",
    "note",
    "rationale",
    "history",
    "correction_event",
    "source_metadata",
}
RULE_ROLES = {"condition", "permission", "prohibition"}
CONSTRAINT_ROLES = {"constraint"}
PREFERENCE_ROLES = {"preference_criterion", "tie_continuation"}
TABLE_ROLES = {"mapping_entry", "table_data", "table_layout"}


def _ordered_units(task: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [unit for rule in task["rules"] for unit in rule["source_units"]]


def _normalize_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.replace("\u00a0", " ").split())


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return value or "rule"


def _features(unit: Mapping[str, Any]) -> list[str]:
    text = _normalize_text(unit.get("text")).lower()
    words = [match.group(0).lower() for match in TOKEN.finditer(text)]
    result = [
        f"unit={unit.get('unit_kind')}",
        f"node={unit.get('node_kind')}",
        f"cue={unit.get('semantic_cue')}",
    ]
    clause_id = str(unit.get("clause_id", ""))
    chapter = re.match(r"(P-\d+)", clause_id)
    if chapter:
        result.append(f"chapter={chapter.group(1)}")
    result.extend(
        f"ancestor={ancestor}" for ancestor in unit.get("ancestor_node_kinds", [])
    )
    result.extend(f"w={word}" for word in words)
    result.extend(f"b={left}_{right}" for left, right in zip(words, words[1:]))
    if text.endswith(('.', ';')):
        result.append("shape=sentence")
    if RULE_PREFIX.match(text):
        result.append("shape=rule_prefix")
    if any(marker in text for marker in (" if ", " when ", " unless ", " except ")):
        result.append("shape=conditional")
    return result


@dataclass(frozen=True)
class Prediction:
    role: str
    force: str
    confidence: float


class DecisionClassifier:
    def __init__(self) -> None:
        self.label_counts: Counter[tuple[str, str]] = Counter()
        self.token_counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
        self.token_totals: Counter[tuple[str, str]] = Counter()
        self.vocabulary: set[str] = set()
        self.kind_label_counts: Counter[str] = Counter()
        self.kind_token_counts: dict[str, Counter[str]] = defaultdict(Counter)
        self.kind_token_totals: Counter[str] = Counter()

    def add(self, unit: Mapping[str, Any], decision: Any) -> None:
        if not isinstance(decision, list) or len(decision) < 3:
            return
        role, force, disposition = decision[:3]
        if disposition != "compile" or role not in VALID_ROLES:
            return
        label = (str(role), str(force))
        features = _features(unit)
        self.label_counts[label] += 1
        self.token_counts[label].update(features)
        self.token_totals[label] += len(features)
        self.vocabulary.update(features)

    def predict(self, unit: Mapping[str, Any]) -> Prediction:
        if not self.label_counts:
            return Prediction("procedure_step", "normative", 0.0)
        features = _features(unit)
        total_labels = sum(self.label_counts.values())
        label_cardinality = len(self.label_counts)
        vocabulary_size = max(len(self.vocabulary), 1)
        scores: dict[tuple[str, str], float] = {}
        for label, count in self.label_counts.items():
            score = math.log((count + 1) / (total_labels + label_cardinality))
            denominator = self.token_totals[label] + vocabulary_size
            token_counter = self.token_counts[label]
            for feature in features:
                score += math.log((token_counter[feature] + 1) / denominator)
            scores[label] = score
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_label, best_score = ordered[0]
        normalizer = best_score + math.log(
            sum(math.exp(score - best_score) for score in scores.values())
        )
        confidence = math.exp(best_score - normalizer)
        return Prediction(best_label[0], best_label[1], confidence)

    def add_kind(self, unit: Mapping[str, Any], kind: str) -> None:
        if kind not in {"rule", "decision", "definition", "mapping", "procedure", "constraint"}:
            return
        features = _features(unit)
        self.kind_label_counts[kind] += 1
        self.kind_token_counts[kind].update(features)
        self.kind_token_totals[kind] += len(features)
        self.vocabulary.update(features)

    def predict_kind(self, unit: Mapping[str, Any]) -> Prediction:
        if not self.kind_label_counts:
            return Prediction("procedure", "required", 0.0)
        features = _features(unit)
        total_labels = sum(self.kind_label_counts.values())
        label_cardinality = len(self.kind_label_counts)
        vocabulary_size = max(len(self.vocabulary), 1)
        scores: dict[str, float] = {}
        for label, count in self.kind_label_counts.items():
            score = math.log((count + 1) / (total_labels + label_cardinality))
            denominator = self.kind_token_totals[label] + vocabulary_size
            token_counter = self.kind_token_counts[label]
            for feature in features:
                score += math.log((token_counter[feature] + 1) / denominator)
            scores[label] = score
        ordered = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_label, best_score = ordered[0]
        normalizer = best_score + math.log(
            sum(math.exp(score - best_score) for score in scores.values())
        )
        return Prediction(best_label, "required", math.exp(best_score - normalizer))


def train_classifier(
    training_dir: Path = DEFAULT_TRAINING_DIR,
    task_dir: Path = DEFAULT_TASK_DIR,
    exclude_task_ids: Iterable[str] = (),
) -> DecisionClassifier:
    classifier = DecisionClassifier()
    excluded = set(exclude_task_ids)
    for authoring_path in sorted(training_dir.glob("P-*-part-*.json")):
        if authoring_path.stem in excluded:
            continue
        task_path = task_dir / authoring_path.name
        if not task_path.exists():
            continue
        authoring = load_json(authoring_path)
        task = load_json(task_path)
        units = _ordered_units(task)
        decisions = authoring.get("clauses", [])
        if len(units) != len(decisions):
            continue
        for unit, decision in zip(units, decisions):
            classifier.add(unit, decision)
        for semantic_unit in authoring.get("units", []):
            kind = semantic_unit.get("k")
            for raw_index in semantic_unit.get("c", []):
                if (
                    isinstance(raw_index, int)
                    and 1 <= raw_index <= len(units)
                    and isinstance(kind, str)
                ):
                    classifier.add_kind(units[raw_index - 1], kind)
    return classifier


def _clear_title(unit: Mapping[str, Any]) -> bool:
    if unit.get("unit_kind") != "heading_text":
        return False
    text = _normalize_text(unit.get("text"))
    match = RULE_PREFIX.match(text)
    if not match:
        return text.isupper() and not NORMATIVE_VERBS.search(text)
    remainder = match.group(2).strip()
    if NORMATIVE_VERBS.search(remainder):
        return False
    if remainder.isupper():
        return True
    if remainder.endswith((".", ";")):
        return False
    return len(remainder.split()) <= 18


def classify_clause(
    unit: Mapping[str, Any], classifier: DecisionClassifier
) -> tuple[list[Any], float]:
    kind = unit.get("unit_kind")
    ancestors = unit.get("ancestor_node_kinds", [])
    text = _normalize_text(unit.get("text"))
    semantic_text = RULE_PREFIX.sub(lambda match: match.group(2), text).strip()
    lowered = semantic_text.lower()
    if _clear_title(unit):
        return ["heading", "informative", "compile"], 1.0
    if kind == "empty_table_cell" and not unit.get("text"):
        if "example_block" in ancestors:
            return ["table_layout", "illustrative", "compile"], 1.0
        return ["table_layout", "informative", "compile"], 1.0
    if kind in {"figure_caption", "caption_text"}:
        return ["example", "illustrative", "compile"], 1.0
    if "example_block" in ancestors:
        return ["example", "illustrative", "compile"], 0.99
    if kind == "correction_event":
        return ["correction_event", "correction", "compile"], 1.0
    if kind == "note_text":
        return ["note", "informative", "compile"], 0.98
    if unit.get("node_kind") == "table":
        force = "illustrative" if "example" in lowered else "normative"
        return ["table_data", force, "compile"], 0.98
    cue = unit.get("semantic_cue")
    if cue in {"criteria", "explicit_order"}:
        return ["preference_criterion", "normative", "compile"], 0.99
    if lowered.startswith(("explanation:", "reason:")):
        return ["rationale", "informative", "compile"], 0.98
    if re.match(r"^(?:if|when|where|provided that|in cases? where)\b", lowered):
        return ["condition", "normative", "compile"], 0.98
    if re.search(r"\b(?:except(?:\s+(?:when|for|in))?|unless)\b", lowered):
        return ["exception", "normative", "compile"], 0.96
    if re.search(
        r"\b(?:must not|shall not|may not|is not permitted|are not permitted|"
        r"cannot|never|is prohibited|are prohibited|no longer (?:used|recommended))\b",
        lowered,
    ):
        return ["prohibition", "normative", "compile"], 0.97
    if re.search(r"\b(?:may|is permitted|are permitted|can be used|optional)\b", lowered):
        return ["permission", "normative", "compile"], 0.95
    if re.search(
        r"\b(?:preferred|preference|senior|seniority|priority|precedence|"
        r"lowest locant|lower locant|greater number|larger number|alphabetical order|"
        r"increasing order|decreasing order|order of citation|where there is a choice)\b",
        lowered,
    ):
        return ["preference_criterion", "normative", "compile"], 0.96
    if re.search(
        r"\b(?:is called|are called|is defined|are defined|means|denotes|"
        r"is termed|are termed|consists of|refers to)\b",
        lowered,
    ):
        return ["definition", "normative", "compile"], 0.96
    if re.search(
        r"\b(?:this (?:section|rule|subsection|chapter)|applies to|applicable to|"
        r"deals with|is used for|are used for|scope)\b",
        lowered,
    ):
        return ["scope", "normative", "compile"], 0.94
    if re.match(r"^(?:see|refer to|for .+, see)\b", lowered):
        return ["cross_reference", "informative", "compile"], 0.95
    if cue == "alternatives":
        return ["procedure_step", "normative", "compile"], 0.94
    prediction = classifier.predict(unit)
    role = prediction.role if prediction.role in VALID_ROLES else "procedure_step"
    # The bootstrapper never discards uncertain prose. A wrong role is reviewable;
    # a skipped rule is lost evidence.
    return [role, prediction.force, "compile"], prediction.confidence


def parse_logic(text: str, role: str) -> dict[str, Any]:
    normalized = _normalize_text(text)
    result: dict[str, Any] = {
        "operator": {
            "definition": "define",
            "condition": "condition",
            "effect": "apply",
            "constraint": "require",
            "permission": "permit",
            "prohibition": "prohibit",
            "preference_criterion": "prefer",
            "tie_continuation": "continue_on_tie",
            "procedure_step": "apply",
            "mapping_entry": "map",
            "exception": "except",
            "scope": "scope",
            "table_data": "map",
            "cross_reference": "depend_on",
            "note": "note",
            "history": "history",
            "correction_event": "correct",
            "example": "example",
        }.get(role, "assert"),
        "source_text": normalized,
    }
    conditional = re.match(
        r"^(?:if|when|where|provided that|in cases? where)\s+(.+?)(?:,|\bthen\b)\s+(.+)$",
        normalized,
        re.IGNORECASE,
    )
    if conditional:
        result["if"] = conditional.group(1).strip(" ,;")
        result["then"] = conditional.group(2).strip()
    else:
        result["then"] = normalized
    exception = re.search(r"\b(except(?:\s+(?:when|for|in))?|unless)\b\s*(.+)$", normalized, re.IGNORECASE)
    if exception:
        result["except"] = exception.group(2).strip(" ,;.")
    predicate = re.match(
        r"^(.+?)\s+(is|are|shall|must|may|named|formed|selected|chosen|cited|numbered|used|indicated|assigned|written|added|replaced|retained|preferred|included)\s+(.+)$",
        normalized,
        re.IGNORECASE,
    )
    if predicate:
        result["subject"] = predicate.group(1).strip()
        result["verb"] = predicate.group(2).lower()
        result["object"] = predicate.group(3).strip()
    directions = []
    for marker in (
        "maximum",
        "minimum",
        "higher",
        "lower",
        "greatest",
        "least",
        "alphabetical order",
        "decreasing order",
        "increasing order",
        "lowest locant",
    ):
        if marker in normalized.lower():
            directions.append(marker.replace(" ", "_"))
    if directions:
        result["ordering"] = directions
    refs = REFERENCE_TOKEN.findall(normalized)
    if refs:
        result["rule_dependencies"] = list(dict.fromkeys(refs))
    return result


def _semantic_family(role: str, source: Mapping[str, Any]) -> str:
    if role == "example":
        return "example"
    if source.get("node_kind") == "table" or role in TABLE_ROLES:
        return "mapping"
    if role in DEFINITION_ROLES:
        return "definition"
    if role in RULE_ROLES:
        return "rule"
    if role in CONSTRAINT_ROLES:
        return "constraint"
    if role in PREFERENCE_ROLES:
        return "preference"
    if role == "exception":
        return "exception"
    return "procedure"


def classify_native_kind(
    source: Mapping[str, Any], role: str, classifier: DecisionClassifier
) -> tuple[str, float]:
    family = _semantic_family(role, source)
    if family in {"example", "mapping", "rule", "constraint"}:
        return family, 1.0
    prediction = classifier.predict_kind(source)
    predicted_family = {
        "decision": "preference",
        "rule": "rule",
        "definition": "definition",
        "mapping": "mapping",
        "procedure": "procedure",
        "constraint": "constraint",
    }.get(prediction.role, family)
    # Explicit preference and exception roles carry control-flow meaning that
    # a lexical kind classifier can otherwise flatten into definitions.
    if family in {"preference", "exception"}:
        return family, max(prediction.confidence, 0.9)
    return predicted_family, prediction.confidence


def _unit_force(roles: Sequence[str]) -> str:
    role_set = set(roles)
    if "prohibition" in role_set:
        return "prohibited"
    if "permission" in role_set:
        return "permitted"
    if role_set.intersection(PREFERENCE_ROLES):
        return "preference"
    if role_set and role_set.issubset(DEFINITION_ROLES):
        return "definition"
    return "required"


def _rule_topic(rule: Mapping[str, Any]) -> str:
    rule_id = str(rule["rule_id"])
    for source in rule["source_units"]:
        if source.get("unit_kind") != "heading_text":
            continue
        text = _normalize_text(source.get("text"))
        match = RULE_PREFIX.match(text)
        topic = match.group(2) if match else text
        if topic:
            return _slug(topic)[:72]
    return _slug(rule_id)


def _group_semantic_indexes(
    indexes: Sequence[int],
    units: Sequence[Mapping[str, Any]],
    clauses: Sequence[Any],
    classifier: DecisionClassifier,
) -> list[tuple[str, list[int]]]:
    groups: list[tuple[str, list[int]]] = []
    for index in indexes:
        decision = clauses[index - 1]
        role = str(decision[0])
        family, _ = classify_native_kind(units[index - 1], role, classifier)
        if family == "example":
            continue
        if groups and groups[-1][0] == family:
            groups[-1][1].append(index)
        else:
            groups.append((family, [index]))

    # A condition immediately controls the following operative block. Keeping
    # the two together recovers explicit if/then structure without rereading a
    # whole task in a generative pass.
    merged: list[tuple[str, list[int]]] = []
    position = 0
    while position < len(groups):
        family, members = groups[position]
        if family == "rule" and position + 1 < len(groups):
            following_family, following = groups[position + 1]
            roles = {str(clauses[index - 1][0]) for index in members}
            if "condition" in roles and following_family in {
                "procedure",
                "preference",
                "constraint",
                "exception",
            }:
                merged.append(("rule", members + following))
                position += 2
                continue
        merged.append((family, members))
        position += 1
    return merged


def _clause_models(
    indexes: Sequence[int],
    units: Sequence[Mapping[str, Any]],
    clauses: Sequence[Any],
) -> list[dict[str, Any]]:
    return [
        {
            "clause_index": index,
            "clause_id": units[index - 1]["clause_id"],
            "role": clauses[index - 1][0],
            "source_kind": units[index - 1].get("unit_kind"),
            "logic": parse_logic(
                _normalize_text(units[index - 1].get("text")),
                str(clauses[index - 1][0]),
            ),
            "payload": units[index - 1].get("payload"),
        }
        for index in indexes
    ]


def _native_unit(
    *,
    unit_id: str,
    family: str,
    indexes: Sequence[int],
    models: Sequence[Mapping[str, Any]],
    rule: Mapping[str, Any],
    dependencies: Sequence[Mapping[str, Any]],
    corrections: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    roles = [str(model["role"]) for model in models]
    force = _unit_force(roles)
    semantic_value = {
        "source_rule_id": rule["rule_id"],
        "record_id": rule["record_id"],
        "operations": list(models),
        "dependencies": list(dependencies),
        "correction_overlays": list(corrections),
    }
    base: dict[str, Any] = {
        "id": unit_id,
        "f": force,
        "c": list(indexes),
        "out": [["result", "NomenclatureResult"]],
    }
    if family == "definition":
        base.update(
            {
                "k": "definition",
                "term": unit_id.replace("_", " "),
                "entity": "NomenclatureDefinition",
                "value": ["lit", semantic_value],
            }
        )
        return base
    if family == "constraint":
        base.update(
            {
                "k": "constraint",
                "assert": ["lit", {"requirements": list(models)}],
                "violation": [
                    ["reject", "nomenclature_candidate", "bluebook_constraint_violation"]
                ],
            }
        )
        return base
    if family == "rule":
        conditions = [model for model in models if model["role"] == "condition"]
        actions = [model for model in models if model["role"] != "condition"]
        if not conditions:
            conditions = [
                {
                    "applicability": "always",
                    "force": force,
                }
            ]
        base.update(
            {
                "k": "rule",
                "if": ["lit", {"conditions": conditions}],
                "then": [
                    ["set", "result", ["lit", {**semantic_value, "actions": actions}]],
                    ["emit", ["var", "result"]],
                ],
                "else": [["set", "applicable", ["lit", False]]],
            }
        )
        return base

    # Preferences and source exceptions remain ordered, explicitly typed
    # procedure data when no safe candidate comparator or exception target can
    # be inferred. This preserves meaning without inventing executable symbols.
    operation = {
        "preference": "apply_ordered_preference_criteria",
        "exception": "apply_source_exception",
    }.get(family, "apply_nomenclature_procedure")
    base.update(
        {
            "k": "procedure",
            "steps": [
                [
                    "set",
                    "result",
                    ["lit", {**semantic_value, "operation": operation}],
                ],
                ["emit", ["var", "result"]],
            ],
        }
    )
    return base


def _rule_clause_indexes(task: Mapping[str, Any]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    index = 0
    for rule in task["rules"]:
        indexes = []
        for _ in rule["source_units"]:
            index += 1
            indexes.append(index)
        result[str(rule["rule_id"])] = indexes
    return result


def _citation_clause_index(
    rule: Mapping[str, Any], indexes: Sequence[int], occurrence: Mapping[str, Any]
) -> int:
    needle = _normalize_text(occurrence.get("reference_text"))
    for index, unit in zip(indexes, rule["source_units"]):
        if needle and needle in _normalize_text(unit.get("text")):
            return index
    return indexes[0]


def bootstrap_authoring(
    task: Mapping[str, Any], classifier: DecisionClassifier
) -> tuple[dict[str, Any], dict[str, Any]]:
    authoring = scaffold_authoring(dict(task))
    units = _ordered_units(task)
    rule_indexes = _rule_clause_indexes(task)
    predictions: dict[int, tuple[list[Any], float]] = {}
    for index, (unit, slot) in enumerate(zip(units, authoring["clauses"]), 1):
        if slot != []:
            continue
        predictions[index] = classify_clause(unit, classifier)
        authoring["clauses"][index - 1] = predictions[index][0]

    semantic_units = []
    examples = []
    tables = []
    index_to_unit: dict[int, str] = {}
    for rule in task["rules"]:
        rule_id = str(rule["rule_id"])
        indexes = rule_indexes[rule_id]
        compiled_indexes = [
            index
            for index in indexes
            if isinstance(authoring["clauses"][index - 1], list)
            and authoring["clauses"][index - 1][2] == "compile"
        ]
        if not compiled_indexes:
            continue
        dependencies = [
            {
                "occurrence_id": reference["occurrence_id"],
                "target_rule_id": reference["effective_target_rule_id"],
                "target_kind": reference["effective_target_kind"],
                "reference_text": reference["reference_text"],
            }
            for reference in rule["references"]
        ]
        corrections = [
            correction
            for correction in task.get("corrections", [])
            if correction["overlay_id"] in rule.get("correction_overlay_ids", [])
        ]
        table_indexes = [
            index
            for index in compiled_indexes
            if units[index - 1].get("node_kind") == "table"
            and units[index - 1].get("unit_kind") != "image_asset"
        ]
        table_slug = None
        if table_indexes:
            table_slug = f"{_rule_topic(rule)}_{_slug(rule_id)}_mapping"
            title = next(
                (
                    _normalize_text(units[index - 1].get("text"))
                    for index in table_indexes
                    if units[index - 1].get("unit_kind") == "table_caption"
                ),
                f"Source table for {rule_id}",
            )
            tables.append(
                {
                    "id": table_slug,
                    "c": table_indexes,
                    "label": rule_id,
                    "title": title,
                    "cols": [
                        ["clause_index", "Clause index", "integer"],
                        ["text", "Source cell text", "string"],
                        ["payload", "Source cell payload", "json"],
                    ],
                    "rows": [
                        {
                            "id": f"clause_{index}",
                            "c": [index],
                            "v": [
                                index,
                                _normalize_text(units[index - 1].get("text")),
                                units[index - 1].get("payload"),
                            ],
                        }
                        for index in table_indexes
                    ],
                    "contract": {
                        "key": ["clause_index"],
                        "result": ["text", "payload"],
                        "cardinality": "one_to_one",
                        "ordering": "source_order",
                    },
                }
            )

        topic = _rule_topic(rule)
        family_counts: Counter[str] = Counter()
        for family, group_indexes in _group_semantic_indexes(
            compiled_indexes, units, authoring["clauses"], classifier
        ):
            family_counts[family] += 1
            unit_id = (
                f"{topic}_{_slug(rule_id)}_{family}_{family_counts[family]}"
            )
            models = _clause_models(group_indexes, units, authoring["clauses"])
            if family == "mapping" and table_slug is not None:
                native = {
                    "id": unit_id,
                    "k": "mapping",
                    "f": "required",
                    "c": list(group_indexes),
                    "out": [["mapped_value", "NomenclatureMappingValue"]],
                    "table": table_slug,
                }
            else:
                native = _native_unit(
                    unit_id=unit_id,
                    family="procedure" if family == "mapping" else family,
                    indexes=group_indexes,
                    models=models,
                    rule=rule,
                    dependencies=dependencies,
                    corrections=corrections,
                )
            semantic_units.append(native)
            index_to_unit.update({index: unit_id for index in group_indexes})

    for index, (unit, decision) in enumerate(zip(units, authoring["clauses"]), 1):
        if not isinstance(decision, list) or decision[:3] != ["example", "illustrative", "compile"]:
            continue
        text = _normalize_text(unit.get("text"))
        shows = []
        demonstrated = index_to_unit.get(index)
        if demonstrated is None:
            preceding = [candidate for candidate in index_to_unit if candidate < index]
            if preceding:
                demonstrated = index_to_unit[max(preceding)]
        if demonstrated is not None:
            shows.append(["semantic_unit", demonstrated])
        examples.append(
            {
                "id": f"source_example_{index}",
                "c": [index],
                "input": {"source_text": text, "payload": unit.get("payload")},
                "ok": [text.splitlines()[0]] if text else [],
                "bad": [],
                "shows": shows,
                "why": "Source-bound Blue Book example retained by deterministic bootstrap conversion.",
            }
        )

    authoring["units"] = semantic_units
    authoring["examples"] = examples
    authoring["tables"] = tables

    mechanical_occurrences = {
        occurrence_id
        for binding in scaffold_delta(dict(task))["citation_bindings"]
        for occurrence_id in binding["occurrence_ids"]
    }
    refs = []
    for rule in task["rules"]:
        indexes = rule_indexes[str(rule["rule_id"])]
        for reference in rule["references"]:
            if reference["occurrence_id"] in mechanical_occurrences:
                continue
            refs.append(
                {
                    "id": f"bootstrap.{_slug(str(reference['occurrence_id']))}",
                    "c": [_citation_clause_index(rule, indexes, reference)],
                    "rel": "cites",
                    "o": reference["occurrence_id"],
                    "resolution": (
                        "external"
                        if reference["effective_target_kind"] == "external_or_historical"
                        else "exact"
                    ),
                }
            )
    authoring["refs"] = refs

    low_confidence = [
        {
            "clause_index": index,
            "clause_id": units[index - 1]["clause_id"],
            "decision": decision,
            "confidence": round(confidence, 6),
            "text": _normalize_text(units[index - 1].get("text")),
        }
        for index, (decision, confidence) in predictions.items()
        if confidence < 0.75
    ]
    report = {
        "task_id": task["task_id"],
        "clause_count": len(units),
        "explicit_decision_count": len(predictions),
        "compiled_clause_count": sum(
            isinstance(decision, list) and decision[2] == "compile"
            for decision in authoring["clauses"]
        ),
        "nonoperative_clause_count": sum(
            isinstance(decision, list) and decision[2] == "skip"
            for decision in authoring["clauses"]
        ),
        "semantic_unit_count": len(semantic_units),
        "table_count": len(tables),
        "example_count": len(examples),
        "reference_binding_count": len(refs),
        "low_confidence_count": len(low_confidence),
        "low_confidence": low_confidence,
    }
    return authoring, report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Bootstrap strict semantic authoring from validated corpus patterns"
    )
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--training-dir", type=Path, default=DEFAULT_TRAINING_DIR)
    parser.add_argument("--task-dir", type=Path, default=DEFAULT_TASK_DIR)
    parser.add_argument("--authoring-dir", type=Path, default=DEFAULT_AUTHORING_DIR)
    parser.add_argument("--delta-dir", type=Path, default=DEFAULT_DELTA_DIR)
    parser.add_argument("--chunk-dir", type=Path, default=DEFAULT_CHUNK_DIR)
    parser.add_argument("--compile", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--exclude-training-task", action="append", default=[])
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    classifier = train_classifier(
        args.training_dir,
        args.task_dir,
        exclude_task_ids=args.exclude_training_task,
    )
    reports = []
    try:
        for task_path in args.tasks:
            task = load_json(task_path)
            authoring, report = bootstrap_authoring(task, classifier)
            authoring_path = args.authoring_dir / f"{task['task_id']}.json"
            authoring_path.parent.mkdir(parents=True, exist_ok=True)
            authoring_path.write_bytes(canonical_json_bytes(authoring))
            if args.compile:
                delta, chunk, validation = compile_authoring(authoring, task)
                if not validation["passed"]:
                    raise ValueError(json.dumps(validation, ensure_ascii=False))
                delta_path = args.delta_dir / f"{task['task_id']}.json"
                compiled_path = args.chunk_dir / f"{task['task_id']}.json"
                delta_path.parent.mkdir(parents=True, exist_ok=True)
                compiled_path.parent.mkdir(parents=True, exist_ok=True)
                delta_path.write_bytes(canonical_json_bytes(delta))
                compiled_path.write_bytes(chunk_bytes(chunk))
                report["compiled"] = True
                report["delta_sha256"] = delta["delta_sha256"]
                report["chunk_sha256"] = chunk["chunk_sha256"]
            reports.append(report)
        output = {
            "format": "iupac-bluebook-bootstrap-semantic-authoring-report",
            "format_version": "1.0.0",
            "training_decision_count": sum(classifier.label_counts.values()),
            "task_count": len(reports),
            "tasks": reports,
        }
        rendered = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(rendered, encoding="utf-8")
        sys.stdout.buffer.write(rendered.encode("utf-8"))
        return 0
    except (KeyError, OSError, TypeError, ValueError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
