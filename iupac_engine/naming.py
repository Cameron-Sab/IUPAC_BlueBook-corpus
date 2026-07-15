from __future__ import annotations

from dataclasses import dataclass

from .model import Molecule, TraceStep
from .perception import FunctionalGroup, GroupSeniority


ROOTS = {
    1: "meth",
    2: "eth",
    3: "prop",
    4: "but",
    5: "pent",
    6: "hex",
    7: "hept",
    8: "oct",
    9: "non",
    10: "dec",
}

HALO_PREFIX = {"F": "fluoro", "Cl": "chloro", "Br": "bromo", "I": "iodo"}
MULT = {2: "di", 3: "tri", 4: "tetra", 5: "penta", 6: "hexa"}


@dataclass(frozen=True)
class NumberingCandidate:
    chain: tuple[int, ...]
    ranking_vector: tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[str, ...]]
    principal_group: FunctionalGroup | None


class NamingUnsupported(ValueError):
    pass


def name_molecule(molecule: Molecule, groups: list[FunctionalGroup]) -> tuple[str, list[TraceStep]]:
    trace: list[TraceStep] = []
    carbon_ids = [atom.id for atom in molecule.atoms if atom.element == "C"]
    if not carbon_ids:
        raise NamingUnsupported("No carbon parent chain found")
    if len(carbon_ids) > 10:
        raise NamingUnsupported("Carbon chains longer than C10 are outside the current prototype scope")

    principal = groups[0] if groups else None
    trace.append(
        TraceStep(
            "P-1",
            f"Selected principal group: {principal.kind if principal else 'hydrocarbon'}",
            tuple(g.kind for g in groups) or ("hydrocarbon",),
            principal.kind if principal else "hydrocarbon",
        )
    )

    candidates = _numbering_candidates(molecule, carbon_ids, principal)
    if not candidates:
        raise NamingUnsupported("No valid acyclic carbon parent candidate found")

    winner = min(candidates, key=lambda c: c.ranking_vector)
    trace.append(
        TraceStep(
            "P-2",
            f"Selected parent chain with {len(winner.chain)} carbon atoms",
            tuple("-".join(str(a) for a in c.chain) for c in candidates),
            "-".join(str(a) for a in winner.chain),
        )
    )
    trace.append(
        TraceStep(
            "P-3",
            f"Selected numbering by lexicographic ranking vector {winner.ranking_vector}",
            tuple(str(c.ranking_vector) for c in candidates),
            str(winner.ranking_vector),
        )
    )

    return _render_name(molecule, winner), trace


def _numbering_candidates(molecule: Molecule, carbon_ids: list[int], principal: FunctionalGroup | None) -> list[NumberingCandidate]:
    paths = _all_carbon_paths(molecule, carbon_ids)
    candidates: list[NumberingCandidate] = []
    for path in paths:
        if principal and principal.principal_atom not in path:
            continue
        for oriented in (path, tuple(reversed(path))):
            candidates.append(NumberingCandidate(oriented, _ranking_vector(molecule, oriented, principal), principal))
    return candidates


def _all_carbon_paths(molecule: Molecule, carbon_ids: list[int]) -> list[tuple[int, ...]]:
    carbon_set = set(carbon_ids)
    paths: set[tuple[int, ...]] = set()

    def dfs(path: list[int]) -> None:
        paths.add(tuple(path))
        current = path[-1]
        for neighbor, _ in molecule.neighbors(current):
            if neighbor in carbon_set and neighbor not in path:
                dfs(path + [neighbor])

    for atom_id in carbon_ids:
        dfs([atom_id])

    max_len = max(len(path) for path in paths)
    return sorted(path for path in paths if len(path) == max_len)


def _ranking_vector(
    molecule: Molecule, chain: tuple[int, ...], principal: FunctionalGroup | None
) -> tuple[tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[str, ...]]:
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}
    principal_locants = (locants[principal.principal_atom],) if principal else ()
    multiple_bond_locants = tuple(
        min(locants[bond.a], locants[bond.b])
        for bond in molecule.bonds
        if bond.a in locants and bond.b in locants and bond.order > 1
    )
    substituents = _substituents(molecule, chain, principal)
    substituent_locants = tuple(sorted(locant for locant, _ in substituents))
    substituent_names = tuple(name for _, name in sorted(substituents, key=lambda item: (item[1], item[0])))
    return (principal_locants, tuple(sorted(multiple_bond_locants)), substituent_locants, substituent_names)


def _substituents(molecule: Molecule, chain: tuple[int, ...], principal: FunctionalGroup | None) -> list[tuple[int, str]]:
    chain_set = set(chain)
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}
    principal_atoms = principal.atom_ids if principal else frozenset()
    substituents: list[tuple[int, str]] = []

    for atom_id in chain:
        for neighbor, order in molecule.neighbors(atom_id):
            if neighbor in chain_set or neighbor in principal_atoms or order != 1:
                continue
            atom = molecule.atom(neighbor)
            if atom.element in HALO_PREFIX:
                substituents.append((locants[atom_id], HALO_PREFIX[atom.element]))
            elif atom.element == "C":
                size = _alkyl_size(molecule, neighbor, blocked=chain_set | {atom_id})
                if size not in ROOTS:
                    raise NamingUnsupported("Alkyl substituents larger than C10 are outside scope")
                substituents.append((locants[atom_id], ROOTS[size] + "yl"))
            elif atom.element == "O":
                substituents.append((locants[atom_id], "hydroxy"))
            elif atom.element == "N":
                substituents.append((locants[atom_id], "amino"))
            else:
                raise NamingUnsupported(f"Unsupported substituent atom: {atom.element}")
    return substituents


def _alkyl_size(molecule: Molecule, start: int, blocked: set[int]) -> int:
    seen: set[int] = set()
    stack = [start]
    while stack:
        atom_id = stack.pop()
        if atom_id in seen or atom_id in blocked:
            continue
        atom = molecule.atom(atom_id)
        if atom.element != "C":
            raise NamingUnsupported("Heteroatom-containing substituent recursion is outside scope")
        seen.add(atom_id)
        for neighbor, order in molecule.neighbors(atom_id):
            if order != 1:
                raise NamingUnsupported("Unsaturated substituent recursion is outside scope")
            if neighbor not in blocked:
                stack.append(neighbor)
    return len(seen)


def _render_name(molecule: Molecule, candidate: NumberingCandidate) -> str:
    chain = candidate.chain
    if len(chain) not in ROOTS:
        raise NamingUnsupported("Parent roots above decane are outside scope")

    prefixes = _render_prefixes(_substituents(molecule, chain, candidate.principal_group))
    parent = _render_parent(molecule, chain, candidate.principal_group)
    return prefixes + parent


def _render_prefixes(substituents: list[tuple[int, str]]) -> str:
    if not substituents:
        return ""
    grouped: dict[str, list[int]] = {}
    for locant, name in substituents:
        grouped.setdefault(name, []).append(locant)

    parts = []
    for name in sorted(grouped):
        locants = sorted(grouped[name])
        multiplier = MULT.get(len(locants), "") if len(locants) > 1 else ""
        parts.append(f"{','.join(str(l) for l in locants)}-{multiplier}{name}")
    return "-".join(parts)


def _render_parent(molecule: Molecule, chain: tuple[int, ...], principal: FunctionalGroup | None) -> str:
    root = ROOTS[len(chain)]
    unsat = _unsaturation(molecule, chain)
    group = principal.kind if principal else "hydrocarbon"
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}

    if group == "carboxylic_acid":
        return f"{root}{unsat}oic acid"
    if group == "aldehyde":
        return f"{root}{unsat}al"
    if group == "ketone":
        return f"{root}{unsat}-{locants[principal.principal_atom]}-one"
    if group == "alcohol":
        return f"{root}{unsat}-{locants[principal.principal_atom]}-ol"
    if group == "amine":
        return f"{root}{unsat}-{locants[principal.principal_atom]}-amine"
    if unsat == "an":
        return f"{root}ane"
    return _elide_terminal_locant_for_two_carbon_unsaturation(f"{root}{unsat}")


def _unsaturation(molecule: Molecule, chain: tuple[int, ...]) -> str:
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}
    double_locs = []
    triple_locs = []
    for a, b in zip(chain, chain[1:]):
        order = molecule.bond_order(a, b)
        if order == 2:
            double_locs.append(min(locants[a], locants[b]))
        elif order == 3:
            triple_locs.append(min(locants[a], locants[b]))

    if double_locs and triple_locs:
        raise NamingUnsupported("Combined en-yne rendering is outside the current scope")
    if len(double_locs) == 1:
        return f"-{double_locs[0]}-en"
    if len(triple_locs) == 1:
        return f"-{triple_locs[0]}-yn"
    if len(double_locs) > 1 or len(triple_locs) > 1:
        raise NamingUnsupported("Multiple unsaturations are outside the current scope")
    return "an"


def _elide_terminal_locant_for_two_carbon_unsaturation(name: str) -> str:
    return name.replace("eth-1-en", "ethene").replace("eth-1-yn", "ethyne")
