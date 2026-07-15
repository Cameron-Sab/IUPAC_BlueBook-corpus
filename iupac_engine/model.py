from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BondOrder = Literal[1, 2, 3]


@dataclass(frozen=True)
class Atom:
    id: int
    element: str
    aromatic: bool = False
    formal_charge: int = 0
    isotope: int = 0
    explicit_hydrogens: int = 0
    radical_electrons: int = 0
    chiral_tag: str | None = None
    cip_label: str | None = None
    in_ring: bool = False


@dataclass(frozen=True)
class Bond:
    a: int
    b: int
    order: BondOrder = 1
    aromatic: bool = False
    in_ring: bool = False
    stereo: str | None = None


@dataclass
class Molecule:
    atoms: list[Atom] = field(default_factory=list)
    bonds: list[Bond] = field(default_factory=list)
    rings: tuple[tuple[int, ...], ...] = ()
    source_smiles: str = ""
    canonical_smiles: str = ""

    def neighbors(self, atom_id: int) -> list[tuple[int, BondOrder]]:
        pairs: list[tuple[int, BondOrder]] = []
        for bond in self.bonds:
            if bond.a == atom_id:
                pairs.append((bond.b, bond.order))
            elif bond.b == atom_id:
                pairs.append((bond.a, bond.order))
        return sorted(pairs)

    def atom(self, atom_id: int) -> Atom:
        return self.atoms[atom_id]

    def bond_order(self, a: int, b: int) -> BondOrder:
        for bond in self.bonds:
            if {bond.a, bond.b} == {a, b}:
                return bond.order
        raise KeyError((a, b))

    def connected_components(self) -> list[set[int]]:
        unseen = {atom.id for atom in self.atoms}
        components: list[set[int]] = []
        while unseen:
            start = min(unseen)
            stack = [start]
            component: set[int] = set()
            while stack:
                atom_id = stack.pop()
                if atom_id in component:
                    continue
                component.add(atom_id)
                unseen.discard(atom_id)
                stack.extend(n for n, _ in self.neighbors(atom_id) if n not in component)
            components.append(component)
        return components


@dataclass(frozen=True)
class TraceStep:
    rule_id: str
    message: str
    alternatives: tuple[str, ...] = ()
    winner: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "message": self.message,
            "alternatives": list(self.alternatives),
            "winner": self.winner,
        }
