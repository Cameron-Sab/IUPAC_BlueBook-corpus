from __future__ import annotations

from typing import Any

from .naming import NamingUnsupported, name_molecule
from .parser import SmilesError, parse_smiles
from .perception import perceive_functional_groups
from .rules import BLUEBOOK_SOURCE, BLUEBOOK_VERSION, rulebook_summary


def name_smiles(smiles: str, *, explain: bool = False) -> dict[str, Any]:
    try:
        molecule = parse_smiles(smiles)
        _validate_graph_scope(molecule)
        groups = perceive_functional_groups(molecule)
        name, trace = name_molecule(molecule, groups)
    except (SmilesError, NamingUnsupported) as exc:
        return {
            "status": "unsupported",
            "name": None,
            "name_type": "systematic",
            "rule_set": "bluebook-prototype-v0.4",
            "supported_scope": False,
            "reason": str(exc),
            "warnings": [],
            "round_trip_verified": False,
            "decision_trace": [],
            "alternative_valid_names": [],
            "bluebook_source": BLUEBOOK_SOURCE,
            "bluebook_version": BLUEBOOK_VERSION,
            "rule_coverage": rulebook_summary(),
        }

    stereochemistry_complete = not any(
        unit.specified == "Unspecified" for unit in molecule.potential_stereo
    )
    warnings = ["Round-trip name-to-structure verification is not implemented in this prototype"]
    if not stereochemistry_complete:
        warnings.append("The input leaves one or more potential stereogenic units unspecified")

    return {
        "status": "success",
        "name": name,
        "name_type": "systematic",
        "rule_set": "bluebook-prototype-v0.4",
        "supported_scope": True,
        "round_trip_verified": False,
        "warnings": warnings,
        "stereochemistry_complete": stereochemistry_complete,
        "decision_trace": [step.as_dict() for step in trace] if explain else [],
        "alternative_valid_names": [],
        "bluebook_source": BLUEBOOK_SOURCE,
        "bluebook_version": BLUEBOOK_VERSION,
        "rule_coverage": rulebook_summary(),
    }


def _validate_graph_scope(molecule) -> None:
    if len(molecule.connected_components()) != 1:
        raise NamingUnsupported("Disconnected structures are outside the current scope")
    if any(atom.formal_charge for atom in molecule.atoms):
        raise NamingUnsupported("Formal-charge nomenclature is outside the current scope")
    if any(atom.isotope for atom in molecule.atoms):
        raise NamingUnsupported("Isotopic modification nomenclature is outside the current scope")
    if any(atom.radical_electrons for atom in molecule.atoms):
        raise NamingUnsupported("Radical nomenclature is outside the current scope")
    for atom in molecule.atoms:
        if atom.element not in {"C", "N", "O", "F", "Cl", "Br", "I"}:
            raise NamingUnsupported(f"Unsupported element: {atom.element}")
