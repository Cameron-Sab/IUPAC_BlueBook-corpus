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
    groups: tuple[FunctionalGroup, ...]


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
    _reject_unsupported_functional_families(molecule, groups, principal)
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
    if principal:
        same_kind_groups = [group for group in groups_for_molecule(molecule) if group.kind == principal.kind]
        principal_paths = [path for path in paths if principal.principal_atom in path]
        if not principal_paths:
            return []
        max_suffix_count = max(
            sum(1 for group in same_kind_groups if group.principal_atom in path)
            for path in principal_paths
        )
    else:
        max_suffix_count = 0
    candidates: list[NumberingCandidate] = []
    for path in paths:
        if principal and principal.principal_atom not in path:
            continue
        if principal and sum(1 for group in same_kind_groups if group.principal_atom in path) < max_suffix_count:
            continue
        for oriented in (path, tuple(reversed(path))):
            candidates.append(NumberingCandidate(oriented, _ranking_vector(molecule, oriented, principal), principal, tuple(groups_for_chain(molecule, oriented))))
    return candidates


def groups_for_molecule(molecule: Molecule) -> list[FunctionalGroup]:
    from .perception import perceive_functional_groups

    return perceive_functional_groups(molecule)


def groups_for_chain(molecule: Molecule, chain: tuple[int, ...]) -> list[FunctionalGroup]:
    chain_set = set(chain)
    return [group for group in groups_for_molecule(molecule) if group.principal_atom in chain_set]


def _reject_unsupported_functional_families(
    molecule: Molecule, groups: list[FunctionalGroup], principal: FunctionalGroup | None
) -> None:
    if principal and principal.kind == "amide":
        raise NamingUnsupported("Amide suffix nomenclature is outside the current scope")

    hydroxyimino_atoms = set().union(*(group.atom_ids for group in groups if group.kind == "hydroxyimino")) if groups else set()
    for bond in molecule.bonds:
        has_nitrogen = molecule.atom(bond.a).element == "N" or molecule.atom(bond.b).element == "N"
        if bond.order == 2 and has_nitrogen and not ({bond.a, bond.b} <= hydroxyimino_atoms):
            raise NamingUnsupported("Imine, amidine, and guanidine nomenclature is outside the current scope")


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
    if principal:
        principal_locants = tuple(
            sorted(
                locants[group.principal_atom]
                for group in groups_for_chain(molecule, chain)
                if group.kind == principal.kind
            )
        )
    else:
        principal_locants = ()
    multiple_bond_locants = tuple(
        min(locants[bond.a], locants[bond.b])
        for bond in molecule.bonds
        if bond.a in locants and bond.b in locants and bond.order > 1
    )
    substituents = _substituents(molecule, chain, principal)
    substituent_locants = tuple(sorted(locant for locant, _ in substituents))
    substituent_names = tuple(name for _, name in sorted(substituents, key=lambda item: (item[1], item[0])))
    return (principal_locants, tuple(sorted(multiple_bond_locants)), substituent_locants, substituent_names)


def _substituents(
    molecule: Molecule,
    chain: tuple[int, ...],
    principal: FunctionalGroup | None,
    groups: tuple[FunctionalGroup, ...] = (),
) -> list[tuple[int, str]]:
    chain_set = set(chain)
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}
    principal_atoms = principal.atom_ids if principal else frozenset()
    substituents: list[tuple[int, str]] = []
    functional_group_atoms = set().union(*(group.atom_ids for group in groups)) if groups else set()

    for group in groups:
        if _is_suffix_group(group, principal):
            continue
        if group.kind == "ketone":
            substituents.append((locants[group.principal_atom], "oxo"))
        elif group.kind == "aldehyde":
            substituents.append((locants[group.principal_atom], "oxo"))
        elif group.kind == "alcohol":
            substituents.append((locants[group.principal_atom], "hydroxy"))
        elif group.kind == "amine":
            substituents.append((locants[group.principal_atom], "amino"))
        elif group.kind == "amide":
            substituents.append((locants[group.principal_atom], "carbamoyl"))
        elif group.kind == "hydroxyimino":
            substituents.append((locants[group.principal_atom], "hydroxyimino"))

    for atom_id in chain:
        for neighbor, order in molecule.neighbors(atom_id):
            if neighbor in chain_set or neighbor in principal_atoms or neighbor in functional_group_atoms or order != 1:
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
                if len(molecule.neighbors(neighbor)) == 2:
                    substituents.append((locants[atom_id], _alkoxy_name(molecule, neighbor, blocked=chain_set | {atom_id})))
                else:
                    substituents.append((locants[atom_id], "hydroxy"))
            elif atom.element == "N":
                acylamino = _acylamino_name(molecule, neighbor, blocked=chain_set | {atom_id})
                if acylamino:
                    substituents.append((locants[atom_id], acylamino))
                elif len(molecule.neighbors(neighbor)) == 1:
                    substituents.append((locants[atom_id], "amino"))
                else:
                    raise NamingUnsupported("Complex amine substituents are outside scope")
            else:
                raise NamingUnsupported(f"Unsupported substituent atom: {atom.element}")
    return substituents


def _is_suffix_group(group: FunctionalGroup, principal: FunctionalGroup | None) -> bool:
    if principal is None:
        return False
    if group.kind == principal.kind:
        return group.kind in {"carboxylic_acid", "ester", "aldehyde", "ketone", "alcohol", "amine"}
    return group == principal


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


def _alkoxy_name(molecule: Molecule, oxygen: int, blocked: set[int]) -> str:
    carbon_neighbors = [
        neighbor
        for neighbor, order in molecule.neighbors(oxygen)
        if order == 1 and molecule.atom(neighbor).element == "C" and neighbor not in blocked
    ]
    if len(carbon_neighbors) != 1:
        raise NamingUnsupported("Complex ether substituents are outside scope")
    carbon_ids = _carbon_fragment(molecule, carbon_neighbors[0], blocked | {oxygen})
    if len(carbon_ids) not in ROOTS:
        raise NamingUnsupported("Alkoxy substituents larger than C10 are outside scope")
    halo_prefix = _halo_fragment_prefix(molecule, carbon_ids)
    return halo_prefix + ROOTS[len(carbon_ids)] + "oxy"


def _acylamino_name(molecule: Molecule, nitrogen: int, blocked: set[int]) -> str | None:
    for neighbor, order in molecule.neighbors(nitrogen):
        if neighbor in blocked or order != 1 or molecule.atom(neighbor).element != "C":
            continue
        has_oxo = any(molecule.atom(n).element == "O" and bond_order == 2 for n, bond_order in molecule.neighbors(neighbor))
        if not has_oxo:
            continue
        carbon_count = _acyl_carbon_count(molecule, neighbor, blocked | {nitrogen})
        if carbon_count == 1:
            return "formamido"
        if carbon_count == 2:
            return "acetamido"
        if carbon_count in ROOTS:
            return ROOTS[carbon_count] + "anamido"
    return None


def _acyl_carbon_count(molecule: Molecule, start: int, blocked: set[int]) -> int:
    seen: set[int] = set()
    stack = [start]
    while stack:
        atom_id = stack.pop()
        if atom_id in seen or atom_id in blocked:
            continue
        atom = molecule.atom(atom_id)
        if atom.element != "C":
            continue
        seen.add(atom_id)
        for neighbor, order in molecule.neighbors(atom_id):
            neighbor_atom = molecule.atom(neighbor)
            if neighbor in blocked:
                continue
            if neighbor_atom.element == "O" and order in {1, 2}:
                continue
            if neighbor_atom.element == "C" and order == 1:
                stack.append(neighbor)
            elif neighbor_atom.element in HALO_PREFIX and order == 1:
                continue
            else:
                raise NamingUnsupported("Complex acylamino substituents are outside scope")
    return len(seen)


def _carbon_fragment(molecule: Molecule, start: int, blocked: set[int]) -> set[int]:
    seen: set[int] = set()
    stack = [start]
    while stack:
        atom_id = stack.pop()
        if atom_id in seen or atom_id in blocked:
            continue
        atom = molecule.atom(atom_id)
        if atom.element != "C":
            continue
        seen.add(atom_id)
        for neighbor, order in molecule.neighbors(atom_id):
            if order != 1:
                raise NamingUnsupported("Unsaturated alkoxy substituents are outside scope")
            if neighbor not in blocked and molecule.atom(neighbor).element == "C":
                stack.append(neighbor)
            elif neighbor not in blocked and molecule.atom(neighbor).element not in HALO_PREFIX:
                raise NamingUnsupported("Heteroatom-containing alkoxy substituents are outside scope")
    return seen


def _halo_fragment_prefix(molecule: Molecule, carbon_ids: set[int]) -> str:
    halo_counts: dict[str, int] = {}
    for carbon_id in carbon_ids:
        for neighbor, order in molecule.neighbors(carbon_id):
            atom = molecule.atom(neighbor)
            if order == 1 and atom.element in HALO_PREFIX:
                halo_counts[HALO_PREFIX[atom.element]] = halo_counts.get(HALO_PREFIX[atom.element], 0) + 1
    parts = []
    for name in sorted(halo_counts):
        count = halo_counts[name]
        parts.append((MULT.get(count, "") if count > 1 else "") + name)
    return "".join(parts)


def _render_name(molecule: Molecule, candidate: NumberingCandidate) -> str:
    chain = candidate.chain
    if len(chain) not in ROOTS:
        raise NamingUnsupported("Parent roots above decane are outside scope")

    parent = _render_parent(molecule, chain, candidate.principal_group, candidate.groups)
    if candidate.principal_group and candidate.principal_group.kind == "ester":
        return parent
    prefixes = _render_prefixes(_substituents(molecule, chain, candidate.principal_group, candidate.groups))
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
        rendered_name = "acetylamino" if name == "acetamido" and len(grouped) == 1 and len(locants) == 1 else name
        rendered = multiplier + rendered_name
        if _needs_substitutive_parentheses(rendered_name):
            rendered = f"({rendered})"
        parts.append(f"{','.join(str(l) for l in locants)}-{rendered}")
    return "-".join(parts)


def _needs_substitutive_parentheses(name: str) -> bool:
    return name in {"acetylamino", "hydroxyimino"} or (name.endswith("methoxy") and name != "methoxy")


def _render_parent(
    molecule: Molecule,
    chain: tuple[int, ...],
    principal: FunctionalGroup | None,
    groups: tuple[FunctionalGroup, ...] = (),
) -> str:
    root = ROOTS[len(chain)]
    unsat = _unsaturation(molecule, chain)
    group = principal.kind if principal else "hydrocarbon"
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}

    suffix_groups = [g for g in groups if g.kind == group]
    suffix_locants = tuple(sorted(locants[g.principal_atom] for g in suffix_groups if g.principal_atom in locants))

    if group == "carboxylic_acid":
        if len(suffix_locants) > 1:
            return f"{root}{unsat}edioic acid"
        return f"{root}{unsat}oic acid"
    if group == "ester":
        ester_group = suffix_groups[0] if suffix_groups else principal
        alkyl = _ester_alkyl_name(molecule, ester_group, chain)
        prefixes = _render_prefixes(_substituents(molecule, chain, principal, groups))
        return f"{alkyl} {prefixes}{root}{unsat}oate"
    if group == "aldehyde":
        return f"{root}{unsat}al"
    if group == "ketone":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}-{','.join(str(n) for n in suffix_locants)}-dione"
        return f"{root}{unsat}-{locants[principal.principal_atom]}-one"
    if group == "alcohol":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}-{','.join(str(n) for n in suffix_locants)}-diol"
        return f"{root}{unsat}-{locants[principal.principal_atom]}-ol"
    if group == "amine":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}-{','.join(str(n) for n in suffix_locants)}-diamine"
        return f"{root}{unsat}-{locants[principal.principal_atom]}-amine"
    if unsat == "an":
        return f"{root}ane"
    return _elide_terminal_locant_for_two_carbon_unsaturation(f"{root}{unsat}e")


def _parent_stem(root: str, unsat: str, *, keep_terminal_e: bool) -> str:
    if unsat == "an":
        return f"{root}ane" if keep_terminal_e else f"{root}an"
    return f"{root}{unsat}e" if keep_terminal_e else f"{root}{unsat}"


def _ester_alkyl_name(molecule: Molecule, ester_group: FunctionalGroup, chain: tuple[int, ...]) -> str:
    chain_set = set(chain)
    oxygens = [atom_id for atom_id in ester_group.atom_ids if molecule.atom(atom_id).element == "O" and len(molecule.neighbors(atom_id)) == 2]
    if not oxygens:
        raise NamingUnsupported("Ester alkoxy oxygen could not be identified")
    oxygen = oxygens[0]
    alkyl_roots = [
        neighbor
        for neighbor, order in molecule.neighbors(oxygen)
        if order == 1 and molecule.atom(neighbor).element == "C" and neighbor not in chain_set
    ]
    if len(alkyl_roots) != 1:
        raise NamingUnsupported("Complex ester alkyl groups are outside scope")
    size = _alkyl_size(molecule, alkyl_roots[0], blocked={oxygen})
    if size not in ROOTS:
        raise NamingUnsupported("Ester alkyl groups larger than C10 are outside scope")
    return ROOTS[size] + "yl"


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
    return name.replace("eth-1-ene", "ethene").replace("eth-1-yne", "ethyne")
