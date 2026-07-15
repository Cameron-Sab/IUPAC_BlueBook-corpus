from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


BLUEBOOK_SOURCE = "https://iupac.qmul.ac.uk/BlueBook/PDF/BlueBookV3.pdf"
BLUEBOOK_VERSION = "IUPAC Blue Book Version 3 PDF plus post-V3 web corrections"


class RuleStatus(str, Enum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"


@dataclass(frozen=True)
class RuleRecord:
    rule_id: str
    title: str
    priority: tuple[int, ...]
    predicate: str
    action: str
    status: RuleStatus
    source_url: str = BLUEBOOK_SOURCE

    def as_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "title": self.title,
            "priority": list(self.priority),
            "predicate": self.predicate,
            "action": self.action,
            "status": self.status.value,
            "source_url": self.source_url,
        }


BLUEBOOK_RULEBOOK: tuple[RuleRecord, ...] = (
    RuleRecord(
        "P-10",
        "Structure containing at least one carbon atom is organic for nomenclature purposes",
        (10,),
        "molecule.contains_carbon",
        "route_to_organic_nomenclature",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-11",
        "Elements in organic nomenclature scope",
        (11,),
        "all_atoms_in_groups_13_to_17_or_allowed_organometallic_scope",
        "accept_bluebook_element_domain_or_route_elsewhere",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-12.1",
        "Preferred IUPAC names are selected among multiple recommended names",
        (12, 1),
        "candidate_set.has_multiple_iupac_names",
        "rank_candidates_for_pin",
        RuleStatus.PLANNED,
    ),
    RuleRecord(
        "P-13.1",
        "Substitutive operation",
        (13, 1),
        "compound_can_be_derived_from_parent_by_replacing_hydrogen",
        "construct_substitutive_name",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-13.2.1.1",
        "Skeletal replacement operation",
        (13, 2, 1, 1),
        "heteroatoms_replace_skeletal_atoms_in_parent",
        "construct_replacement_name",
        RuleStatus.PLANNED,
    ),
    RuleRecord(
        "P-13.3.3.2",
        "Functional class operation",
        (13, 3, 3, 2),
        "class_name_preferred_or_allowed",
        "construct_functional_class_name",
        RuleStatus.PLANNED,
    ),
    RuleRecord(
        "P-14.4",
        "General rules, locants, lowest locant sets, first point of difference",
        (14,),
        "candidate_numberings_exist",
        "select_lowest_locant_numbering",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-21.2.1",
        "Unbranched saturated acyclic hydrocarbons",
        (21, 2, 1),
        "acyclic_unbranched_saturated_carbon_parent",
        "select_alkane_parent_hydride",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-22.1.1",
        "Monocyclic saturated hydrocarbons",
        (22, 1, 1),
        "monocyclic_saturated_carbon_parent",
        "select_cycloalkane_parent_hydride",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-22.1.2",
        "Retained benzene and monocyclic unsaturated hydrocarbon parents",
        (22, 1, 2),
        "isolated_six_membered_aromatic_carbon_parent",
        "select_retained_benzene_parent",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-29.6",
        "Retained prefixes derived from parent hydrides",
        (29, 6),
        "retained_substituent_prefix_is_preferred",
        "render_phenyl_or_retained_organyl_prefix",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-31.1.3.1",
        "Subtractive unsaturation operation for alkenes",
        (31, 1, 3, 1),
        "parent_has_double_bond",
        "render_ene_suffix",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-31.1.3.2",
        "Subtractive unsaturation operation for alkynes",
        (31, 1, 3, 2),
        "parent_has_triple_bond",
        "render_yne_suffix",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-33",
        "Characteristic groups as suffixes and prefixes",
        (33,),
        "functional_groups_detected",
        "rank_characteristic_groups_and_render_suffix_or_prefix",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-44",
        "Selection of parent structures",
        (44,),
        "parent_candidates_exist",
        "rank_parent_candidates",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-45",
        "Selection of preferred prefixes",
        (45,),
        "substituent_candidates_exist",
        "rank_and_render_prefixes",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-52.2.8",
        "Selection between ring and chain parent structures for preferred names",
        (52, 2, 8),
        "ring_and_chain_parent_candidates_tied_by_class_and_suffix_count",
        "prefer_ring_parent_for_pin",
        RuleStatus.PARTIAL,
    ),
    RuleRecord(
        "P-5",
        "Selecting preferred IUPAC names",
        (50,),
        "candidate_set.complete",
        "select_pin",
        RuleStatus.PLANNED,
    ),
    RuleRecord(
        "P-93.4",
        "Citation and placement of stereodescriptors",
        (93, 4),
        "absolute_parent_stereodescriptors_assigned",
        "render_stereodescriptors_with_final_parent_locants",
        RuleStatus.PARTIAL,
    ),
)


def rulebook_summary() -> list[dict[str, object]]:
    return [rule.as_dict() for rule in BLUEBOOK_RULEBOOK]
