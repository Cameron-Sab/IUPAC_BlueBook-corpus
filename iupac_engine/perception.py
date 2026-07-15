from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .model import Molecule


class GroupSeniority(IntEnum):
    HYDROCARBON = 0
    AMINE = 10
    ALCOHOL = 20
    KETONE = 30
    ALDEHYDE = 40
    ESTER = 45
    CARBOXYLIC_ACID = 50


@dataclass(frozen=True)
class FunctionalGroup:
    kind: str
    seniority: GroupSeniority
    principal_atom: int
    atom_ids: frozenset[int]


def perceive_functional_groups(molecule: Molecule) -> list[FunctionalGroup]:
    groups: list[FunctionalGroup] = []

    for atom in molecule.atoms:
        if atom.element != "C":
            continue

        oxygen_double = [n for n, order in molecule.neighbors(atom.id) if molecule.atom(n).element == "O" and order == 2]
        oxygen_single = [n for n, order in molecule.neighbors(atom.id) if molecule.atom(n).element == "O" and order == 1]
        hydroxy_oxygen = [n for n in oxygen_single if len(molecule.neighbors(n)) == 1]
        ester_oxygen = [n for n in oxygen_single if len(molecule.neighbors(n)) == 2]
        carbon_single = [n for n, order in molecule.neighbors(atom.id) if molecule.atom(n).element == "C" and order == 1]
        nitrogen_single = [n for n, order in molecule.neighbors(atom.id) if molecule.atom(n).element == "N" and order == 1]

        if oxygen_double and hydroxy_oxygen:
            groups.append(
                FunctionalGroup(
                    "carboxylic_acid",
                    GroupSeniority.CARBOXYLIC_ACID,
                    atom.id,
                    frozenset({atom.id, oxygen_double[0], hydroxy_oxygen[0]}),
                )
            )
        elif oxygen_double and ester_oxygen:
            groups.append(FunctionalGroup("ester", GroupSeniority.ESTER, atom.id, frozenset({atom.id, oxygen_double[0], ester_oxygen[0]})))
        elif oxygen_double and nitrogen_single:
            groups.append(FunctionalGroup("amide", GroupSeniority.HYDROCARBON, atom.id, frozenset({atom.id, oxygen_double[0], nitrogen_single[0]})))
        elif oxygen_double and len(carbon_single) == 1:
            groups.append(FunctionalGroup("aldehyde", GroupSeniority.ALDEHYDE, atom.id, frozenset({atom.id, oxygen_double[0]})))
        elif oxygen_double and len(carbon_single) == 2:
            groups.append(FunctionalGroup("ketone", GroupSeniority.KETONE, atom.id, frozenset({atom.id, oxygen_double[0]})))

    for atom in molecule.atoms:
        if atom.element == "O":
            carbon_neighbors = [n for n, order in molecule.neighbors(atom.id) if molecule.atom(n).element == "C" and order == 1]
            if len(carbon_neighbors) == 1 and len(molecule.neighbors(atom.id)) == 1:
                carbon = carbon_neighbors[0]
                if not any(atom.id in group.atom_ids for group in groups):
                    groups.append(FunctionalGroup("alcohol", GroupSeniority.ALCOHOL, carbon, frozenset({atom.id, carbon})))
        if atom.element == "N":
            carbon_neighbors = [n for n, order in molecule.neighbors(atom.id) if molecule.atom(n).element == "C" and order == 1]
            if len(carbon_neighbors) == 1 and len(molecule.neighbors(atom.id)) == 1:
                groups.append(FunctionalGroup("amine", GroupSeniority.AMINE, carbon_neighbors[0], frozenset({atom.id, carbon_neighbors[0]})))

    return sorted(groups, key=lambda g: (-int(g.seniority), g.principal_atom, g.kind))
