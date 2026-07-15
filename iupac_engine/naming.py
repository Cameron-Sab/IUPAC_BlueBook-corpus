from __future__ import annotations

import re
from dataclasses import dataclass

from .model import Molecule, TraceStep
from .numeric import multiplicative_prefix, parent_root
from .perception import FunctionalGroup, GroupSeniority


HALO_PREFIX = {"F": "fluoro", "Cl": "chloro", "Br": "bromo", "I": "iodo"}


@dataclass(frozen=True, order=True)
class RankingVector:
    principal_locants: tuple[int, ...]
    multiple_bond_rank: tuple[int, ...]
    substituent_locants: tuple[int, ...]
    citation_locants: tuple[int, ...]
    substituent_names: tuple[str, ...]
    stereo_rank: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class NumberingCandidate:
    chain: tuple[int, ...]
    ranking_vector: RankingVector
    principal_group: FunctionalGroup | None
    groups: tuple[FunctionalGroup, ...]


class NamingUnsupported(ValueError):
    pass


def name_molecule(molecule: Molecule, groups: list[FunctionalGroup]) -> tuple[str, list[TraceStep]]:
    trace: list[TraceStep] = []
    all_carbon_ids = [atom.id for atom in molecule.atoms if atom.element == "C"]
    if not all_carbon_ids:
        raise NamingUnsupported("No carbon parent chain found")

    principal = groups[0] if groups else None
    _reject_unsupported_functional_families(molecule, groups, principal)
    trace.append(
        TraceStep(
            "P-41",
            f"Selected principal group: {principal.kind if principal else 'hydrocarbon'}",
            tuple(g.kind for g in groups) or ("hydrocarbon",),
            principal.kind if principal else "hydrocarbon",
        )
    )

    if molecule.rings and _ring_is_parent(molecule, principal, groups):
        benzene_ring = _benzene_ring_atoms(molecule)
        if benzene_ring is not None:
            return _name_benzene_parent(molecule, benzene_ring, groups, trace)
        return _name_saturated_monocycle(molecule, groups, trace)

    ring_atoms = set().union(*(set(ring) for ring in molecule.rings)) if molecule.rings else set()
    carbon_ids = [atom_id for atom_id in all_carbon_ids if atom_id not in ring_atoms]
    if not carbon_ids:
        raise NamingUnsupported("No acyclic carbon parent chain found")

    candidates = _numbering_candidates(molecule, carbon_ids, principal)
    if not candidates:
        raise NamingUnsupported("No valid acyclic carbon parent candidate found")

    winner = min(candidates, key=lambda c: c.ranking_vector)
    if molecule.rings:
        _validate_acyclic_parent_ring_scope(molecule, winner.chain, groups)
    trace.append(
        TraceStep(
            "P-44.1.1",
            f"Selected parent chain with {len(winner.chain)} carbon atoms",
            tuple("-".join(str(a) for a in c.chain) for c in candidates),
            "-".join(str(a) for a in winner.chain),
        )
    )
    trace.append(
        TraceStep(
            "P-14.4",
            f"Selected numbering by lexicographic ranking vector {winner.ranking_vector}",
            tuple(str(c.ranking_vector) for c in candidates),
            str(winner.ranking_vector),
        )
    )

    name = _render_name(molecule, winner)
    name, stereo_trace = _apply_stereodescriptors(molecule, winner.chain, name)
    if stereo_trace:
        trace.append(stereo_trace)
    return name, trace


def _validate_acyclic_parent_ring_scope(
    molecule: Molecule,
    parent: tuple[int, ...],
    groups: list[FunctionalGroup],
) -> None:
    if len(molecule.rings) != 1:
        raise NamingUnsupported("Polycyclic substituent nomenclature is outside the current scope")
    ring_atoms = frozenset(molecule.rings[0])
    if any(molecule.atom(atom_id).element != "C" for atom_id in ring_atoms):
        raise NamingUnsupported("Heterocyclic substituent nomenclature is outside the current scope")
    ring_bonds = [
        bond
        for bond in molecule.bonds
        if bond.a in ring_atoms and bond.b in ring_atoms
    ]
    saturated_carbocycle = len(ring_bonds) == len(ring_atoms) and all(
        bond.order == 1 and not bond.aromatic for bond in ring_bonds
    )
    benzene = _is_benzene_ring(molecule, ring_atoms, ring_bonds)
    if not saturated_carbocycle and not benzene:
        raise NamingUnsupported("Unsaturated cyclic substituent nomenclature is outside the current scope")
    if any(group.atom_ids & ring_atoms for group in groups):
        raise NamingUnsupported("Functionalized cyclic substituent nomenclature is outside the current scope")

    attachments = [
        (ring_atom, neighbor, order)
        for ring_atom in ring_atoms
        for neighbor, order in molecule.neighbors(ring_atom)
        if neighbor not in ring_atoms
    ]
    if len(attachments) != 1:
        raise NamingUnsupported("Substituted cycloalkyl prefixes are outside the current scope")
    _, outside_atom, order = attachments[0]
    if order != 1 or outside_atom not in parent:
        raise NamingUnsupported(
            "Nested or heteroatom-linked cycloalkyl prefixes are outside the current scope"
        )


def _ring_is_parent(
    molecule: Molecule,
    principal: FunctionalGroup | None,
    groups: list[FunctionalGroup],
) -> bool:
    if principal is None:
        return True
    ring_atoms = set().union(*(set(ring) for ring in molecule.rings))
    exocyclic_suffix_kinds = {
        "carboxylic_acid",
        "ester",
        "acid_halide",
        "amide",
        "nitrile",
        "aldehyde",
    }
    same_kind = [group for group in groups if group.kind == principal.kind]
    for group in same_kind:
        if group.principal_atom in ring_atoms:
            continue
        if group.kind not in exocyclic_suffix_kinds or not any(
            neighbor in ring_atoms and order == 1
            for neighbor, order in molecule.neighbors(group.principal_atom)
        ):
            return False
    return True


def _name_saturated_monocycle(
    molecule: Molecule,
    groups: list[FunctionalGroup],
    trace: list[TraceStep],
) -> tuple[str, list[TraceStep]]:
    if len(molecule.rings) != 1:
        raise NamingUnsupported("Polycyclic nomenclature is outside the current scope")

    ring_atoms = frozenset(molecule.rings[0])
    if len(ring_atoms) < 3:
        raise NamingUnsupported("A valid monocyclic parent requires at least three ring atoms")
    if any(molecule.atom(atom_id).element != "C" for atom_id in ring_atoms):
        raise NamingUnsupported("Heterocycle nomenclature is outside the current scope")
    if any(molecule.atom(atom_id).aromatic for atom_id in ring_atoms):
        raise NamingUnsupported("Aromatic parent nomenclature is outside the current scope")

    principal = groups[0] if groups else None
    if groups:
        _validate_cyclic_groups(molecule, ring_atoms, groups, principal)

    ring_bonds = [
        bond
        for bond in molecule.bonds
        if bond.a in ring_atoms and bond.b in ring_atoms
    ]
    if len(ring_bonds) != len(ring_atoms):
        raise NamingUnsupported("Unsupported monocyclic ring topology")
    if any(bond.order != 1 or bond.aromatic for bond in ring_bonds):
        raise NamingUnsupported("Unsaturated ring nomenclature is outside the current scope")

    orientations = _ring_orientations(molecule, ring_atoms)
    candidates = [
        NumberingCandidate(
            orientation,
            _ranking_vector(
                molecule,
                orientation,
                principal,
                candidate_groups=tuple(groups),
            ),
            principal,
            tuple(groups),
        )
        for orientation in orientations
    ]
    winner = min(candidates, key=lambda candidate: candidate.ranking_vector)
    substituents = _substituents(
        molecule,
        winner.chain,
        winner.principal_group,
        winner.groups,
    )

    trace.append(
        TraceStep(
            "P-52.2.8",
            "Selected the hydrocarbon ring as the preferred parent over acyclic components",
            ("ring parent", "acyclic parent"),
            "ring parent",
        )
    )
    trace.append(
        TraceStep(
            "P-22.1.1",
            f"Selected a saturated monocyclic parent with {len(ring_atoms)} carbon atoms",
            (),
            f"{parent_root(len(ring_atoms))}-membered carbon ring",
        )
    )
    trace.append(
        TraceStep(
            "P-14.4",
            f"Selected ring numbering by lexicographic ranking vector {winner.ranking_vector}",
            tuple(str(candidate.ranking_vector) for candidate in candidates),
            str(winner.ranking_vector),
        )
    )

    omit_locants = winner.principal_group is None and (
        len(substituents) == 1
        or _is_complete_single_halogen_substitution(molecule, winner, substituents)
    )
    prefixes = _render_prefixes(substituents, omit_locants=omit_locants)
    parent = _render_saturated_cycle_parent(
        molecule,
        winner,
        has_other_locants=bool(substituents),
    )
    name, stereo_trace = _apply_stereodescriptors(
        molecule, winner.chain, prefixes + parent
    )
    if stereo_trace:
        trace.append(stereo_trace)
    return name, trace


def _name_benzene_parent(
    molecule: Molecule,
    ring_atoms: frozenset[int],
    groups: list[FunctionalGroup],
    trace: list[TraceStep],
) -> tuple[str, list[TraceStep]]:
    principal = groups[0] if groups else None
    if groups:
        _validate_cyclic_groups(molecule, ring_atoms, groups, principal)

    orientations = _ring_orientations(molecule, ring_atoms)
    candidates = [
        NumberingCandidate(
            orientation,
            _ranking_vector(
                molecule,
                orientation,
                principal,
                candidate_groups=tuple(groups),
            ),
            principal,
            tuple(groups),
        )
        for orientation in orientations
    ]
    winner = min(candidates, key=lambda candidate: candidate.ranking_vector)
    substituents = _substituents(
        molecule,
        winner.chain,
        winner.principal_group,
        winner.groups,
    )

    trace.append(
        TraceStep(
            "P-52.2.8",
            "Selected the aromatic ring as the preferred parent over acyclic components",
            ("ring parent", "acyclic parent"),
            "ring parent",
        )
    )
    trace.append(
        TraceStep(
            "P-22.1.2",
            "Selected the retained benzene parent",
            (),
            "benzene",
        )
    )
    trace.append(
        TraceStep(
            "P-14.4",
            f"Selected benzene numbering by lexicographic ranking vector {winner.ranking_vector}",
            tuple(str(candidate.ranking_vector) for candidate in candidates),
            str(winner.ranking_vector),
        )
    )

    name = _render_benzene_name(molecule, winner, substituents)
    name, stereo_trace = _apply_stereodescriptors(molecule, winner.chain, name)
    if stereo_trace:
        trace.append(stereo_trace)
    return name, trace


def _render_benzene_name(
    molecule: Molecule,
    candidate: NumberingCandidate,
    substituents: list[tuple[int, str]],
) -> str:
    principal = candidate.principal_group
    if principal is None:
        if not substituents:
            return "benzene"
        if all(name == "methyl" for _, name in substituents):
            if len(substituents) == 1:
                return "toluene"
            if len(substituents) == 2:
                locants = ",".join(str(locant) for locant, _ in sorted(substituents))
                return f"{locants}-xylene"
        prefixes = _render_prefixes(
            substituents,
            omit_locants=(
                len(substituents) == 1
                or _benzene_prefix_locants_are_redundant(molecule, candidate, substituents)
            ),
        )
        return prefixes + "benzene"

    ring_locants = {atom_id: index + 1 for index, atom_id in enumerate(candidate.chain)}
    same_kind = [group for group in candidate.groups if group.kind == principal.kind]
    if principal.principal_atom in candidate.chain:
        suffix_locants = tuple(sorted(ring_locants[group.principal_atom] for group in same_kind))
        prefixes = _render_prefixes(
            substituents,
            omit_locants=_benzene_prefix_locants_are_redundant(
                molecule, candidate, substituents
            ),
        )
        if principal.kind == "alcohol":
            if len(suffix_locants) == 1:
                return prefixes + "phenol"
            parent = f"benzene-{','.join(str(locant) for locant in suffix_locants)}-{_multiplicative_suffix(len(suffix_locants), 'ol')}"
            return prefixes + parent
        if principal.kind == "amine":
            if len(suffix_locants) == 1:
                return prefixes + "aniline"
            parent = f"benzene-{','.join(str(locant) for locant in suffix_locants)}-{_multiplicative_suffix(len(suffix_locants), 'amine')}"
            return prefixes + parent
        raise NamingUnsupported("Unsupported benzene ring-local suffix")

    return _render_exocyclic_benzene_parent(molecule, candidate, substituents)


def _render_exocyclic_benzene_parent(
    molecule: Molecule,
    candidate: NumberingCandidate,
    substituents: list[tuple[int, str]],
) -> str:
    principal = candidate.principal_group
    if principal is None:
        raise NamingUnsupported("Missing benzene principal group")
    ring_atoms = frozenset(candidate.chain)
    ring_locants = {atom_id: index + 1 for index, atom_id in enumerate(candidate.chain)}
    suffix_groups = [group for group in candidate.groups if group.kind == principal.kind]
    suffix_locants = tuple(
        sorted(
            ring_locants[_cyclic_group_parent_atom(molecule, group, ring_atoms)]
            for group in suffix_groups
        )
    )
    prefixes = _render_prefixes(
        substituents,
        omit_locants=_benzene_prefix_locants_are_redundant(
            molecule, candidate, substituents
        ),
    )
    group = principal.kind

    if len(suffix_groups) == 1:
        retained = {
            "carboxylic_acid": "benzoic acid",
            "amide": "benzamide",
            "nitrile": "benzonitrile",
            "aldehyde": "benzaldehyde",
        }
        if group in retained:
            return prefixes + retained[group]
        if group == "ester":
            alkyl = _ester_alkyl_name(molecule, principal, candidate.chain)
            return f"{alkyl} {prefixes}benzoate"
        if group == "acid_halide":
            halogens = [
                molecule.atom(atom_id).element
                for atom_id in principal.atom_ids
                if molecule.atom(atom_id).element in HALO_PREFIX
            ]
            if len(halogens) != 1:
                raise NamingUnsupported("Acid halide atom could not be identified")
            return f"{prefixes}benzoyl {_halide_class_name(halogens[0])}"

    locant_text = ",".join(str(locant) for locant in suffix_locants)
    multiplier = multiplicative_prefix(len(suffix_groups))
    if group == "carboxylic_acid":
        return f"{prefixes}benzene-{locant_text}-{multiplier}carboxylic acid"
    if group == "amide":
        return f"{prefixes}benzene-{locant_text}-{multiplier}carboxamide"
    if group == "nitrile":
        return f"{prefixes}benzene-{locant_text}-{multiplier}carbonitrile"
    if group == "aldehyde":
        return f"{prefixes}benzene-{locant_text}-{multiplier}carbaldehyde"
    if group == "ester":
        alkyl_names = sorted(
            (_ester_alkyl_name(molecule, group_item, candidate.chain) for group_item in suffix_groups),
            key=_alphabetical_key,
        )
        alkyl = _combine_organyl_names(alkyl_names)
        return f"{alkyl} {prefixes}benzene-{locant_text}-{multiplier}carboxylate"
    raise NamingUnsupported("Unsupported multiple benzene suffix")


def _benzene_prefix_locants_are_redundant(
    molecule: Molecule,
    candidate: NumberingCandidate,
    substituents: list[tuple[int, str]],
) -> bool:
    if not substituents or len({name for _, name in substituents}) != 1:
        return False
    ring_locants = {atom_id: index + 1 for index, atom_id in enumerate(candidate.chain)}
    occupied: set[int] = set()
    principal = candidate.principal_group
    if principal is not None:
        same_kind = [
            group for group in candidate.groups if group.kind == principal.kind
        ]
        if len(same_kind) != 1:
            return False
        for group in same_kind:
            occupied.add(
                ring_locants[
                    _cyclic_group_parent_atom(
                        molecule, group, frozenset(candidate.chain)
                    )
                ]
            )
    prefix_locants = [locant for locant, _ in substituents]
    return (
        len(prefix_locants) == len(set(prefix_locants))
        and set(prefix_locants) == set(range(1, len(candidate.chain) + 1)) - occupied
    )


def _validate_cyclic_groups(
    molecule: Molecule,
    ring_atoms: frozenset[int],
    groups: list[FunctionalGroup],
    principal: FunctionalGroup,
) -> None:
    ring_local_kinds = {"ketone", "alcohol", "amine"}
    exocyclic_suffix_kinds = {
        "carboxylic_acid",
        "ester",
        "acid_halide",
        "amide",
        "nitrile",
        "aldehyde",
    }
    exocyclic = [group for group in groups if group.principal_atom not in ring_atoms]

    if not exocyclic:
        if principal.kind not in ring_local_kinds or any(
            group.kind not in ring_local_kinds for group in groups
        ):
            raise NamingUnsupported("Characteristic-group nomenclature on rings is outside the current scope")
        return

    if principal.kind not in exocyclic_suffix_kinds or principal not in exocyclic:
        raise NamingUnsupported("Exocyclic lower-priority group prefixes on rings are outside the current scope")
    if any(
        group.kind != principal.kind
        for group in exocyclic
    ):
        raise NamingUnsupported("Mixed exocyclic characteristic groups on rings are outside the current scope")
    if any(
        group.kind not in ring_local_kinds and group.kind != principal.kind
        for group in groups
    ):
        raise NamingUnsupported("Characteristic-group nomenclature on rings is outside the current scope")
    for group in exocyclic:
        _cyclic_group_parent_atom(molecule, group, ring_atoms)


def _render_saturated_cycle_parent(
    molecule: Molecule,
    candidate: NumberingCandidate,
    *,
    has_other_locants: bool,
) -> str:
    root = parent_root(len(candidate.chain))
    base = f"cyclo{root}"
    principal = candidate.principal_group
    if principal is None:
        return base + "ane"

    if principal.principal_atom not in candidate.chain:
        return _render_exocyclic_cycle_parent(
            molecule,
            candidate,
            base,
            has_other_locants=has_other_locants,
        )

    group = principal.kind
    if group not in {"ketone", "alcohol", "amine"}:
        raise NamingUnsupported("Unsupported cyclic suffix")
    locants = {atom_id: index + 1 for index, atom_id in enumerate(candidate.chain)}
    suffix_locants = tuple(
        sorted(
            locants[item.principal_atom]
            for item in candidate.groups
            if item.kind == group
        )
    )
    suffix = {"ketone": "one", "alcohol": "ol", "amine": "amine"}[group]

    if len(suffix_locants) == 1:
        if has_other_locants:
            return f"{base}an-{suffix_locants[0]}-{suffix}"
        return f"{base}an{suffix}"

    multiplied = _multiplicative_suffix(len(suffix_locants), suffix)
    return f"{base}ane-{','.join(str(locant) for locant in suffix_locants)}-{multiplied}"


def _render_exocyclic_cycle_parent(
    molecule: Molecule,
    candidate: NumberingCandidate,
    base: str,
    *,
    has_other_locants: bool,
) -> str:
    principal = candidate.principal_group
    if principal is None:
        raise NamingUnsupported("Missing cyclic principal group")
    locants = {atom_id: index + 1 for index, atom_id in enumerate(candidate.chain)}
    suffix_groups = [group for group in candidate.groups if group.kind == principal.kind]
    suffix_locants = tuple(
        sorted(
            locants[_cyclic_group_parent_atom(molecule, group, frozenset(candidate.chain))]
            for group in suffix_groups
        )
    )
    if not suffix_locants:
        raise NamingUnsupported("Exocyclic suffix attachment could not be numbered")

    cite_locants = has_other_locants or len(suffix_locants) > 1
    locant_text = f"-{','.join(str(locant) for locant in suffix_locants)}-" if cite_locants else ""
    multiplier = multiplicative_prefix(len(suffix_locants)) if len(suffix_locants) > 1 else ""
    parent = base + "ane"
    group = principal.kind

    if group == "carboxylic_acid":
        return f"{parent}{locant_text}{multiplier}carboxylic acid"
    if group == "amide":
        return f"{parent}{locant_text}{multiplier}carboxamide"
    if group == "nitrile":
        return f"{parent}{locant_text}{multiplier}carbonitrile"
    if group == "aldehyde":
        return f"{parent}{locant_text}{multiplier}carbaldehyde"
    if group == "ester":
        alkyl_names = sorted(
            (_ester_alkyl_name(molecule, group_item, candidate.chain) for group_item in suffix_groups),
            key=_alphabetical_key,
        )
        alkyl = _combine_organyl_names(alkyl_names)
        return f"{alkyl} {parent}{locant_text}{multiplier}carboxylate"
    if group == "acid_halide":
        if len(suffix_groups) != 1:
            raise NamingUnsupported("Multiple cyclic carbonyl halides are outside the current scope")
        halogens = [
            molecule.atom(atom_id).element
            for atom_id in principal.atom_ids
            if molecule.atom(atom_id).element in HALO_PREFIX
        ]
        if len(halogens) != 1:
            raise NamingUnsupported("Acid halide atom could not be identified")
        return f"{parent}{locant_text}carbonyl {_halide_class_name(halogens[0])}"
    raise NamingUnsupported("Unsupported exocyclic cyclic suffix")


def _cyclic_group_parent_atom(
    molecule: Molecule,
    group: FunctionalGroup,
    ring_atoms: frozenset[int],
) -> int:
    if group.principal_atom in ring_atoms:
        return group.principal_atom
    attachments = [
        neighbor
        for neighbor, order in molecule.neighbors(group.principal_atom)
        if neighbor in ring_atoms and order == 1
    ]
    if len(attachments) != 1:
        raise NamingUnsupported(
            "Characteristic group is not attached directly to one ring atom"
        )
    return attachments[0]


def _ring_orientations(
    molecule: Molecule, ring_atoms: frozenset[int]
) -> list[tuple[int, ...]]:
    adjacency = {
        atom_id: tuple(
            neighbor
            for neighbor, _ in molecule.neighbors(atom_id)
            if neighbor in ring_atoms
        )
        for atom_id in ring_atoms
    }
    if any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise NamingUnsupported("Unsupported monocyclic ring topology")

    orientations: set[tuple[int, ...]] = set()
    for start in sorted(ring_atoms):
        for second in adjacency[start]:
            path = [start, second]
            while len(path) < len(ring_atoms):
                next_atoms = [
                    atom_id
                    for atom_id in adjacency[path[-1]]
                    if atom_id != path[-2] and atom_id not in path
                ]
                if len(next_atoms) != 1:
                    raise NamingUnsupported("Unsupported monocyclic ring topology")
                path.append(next_atoms[0])
            if start not in adjacency[path[-1]]:
                raise NamingUnsupported("Unsupported monocyclic ring topology")
            orientations.add(tuple(path))
    return sorted(orientations)


def _apply_stereodescriptors(
    molecule: Molecule,
    parent: tuple[int, ...],
    name: str,
) -> tuple[str, TraceStep | None]:
    if molecule.stereo_groups:
        raise NamingUnsupported("Enhanced relative or mixture stereochemistry is outside the current scope")
    if any(unit.specified not in {"Specified", "Unspecified"} for unit in molecule.potential_stereo):
        raise NamingUnsupported("Explicit unknown stereochemistry is outside the current scope")

    locants = {atom_id: index + 1 for index, atom_id in enumerate(parent)}
    descriptors: list[tuple[int, str, bool]] = []

    for atom in molecule.atoms:
        if not atom.chiral_tag and not atom.cip_label:
            continue
        if atom.chiral_tag not in {"CHI_TETRAHEDRAL_CW", "CHI_TETRAHEDRAL_CCW"}:
            raise NamingUnsupported("Non-tetrahedral stereodescriptor nomenclature is outside the current scope")
        if atom.cip_label not in {"R", "S", "r", "s"}:
            raise NamingUnsupported("Unresolved tetrahedral stereochemistry is outside the current scope")
        if atom.id not in locants:
            raise NamingUnsupported("Stereochemistry within a substituent is outside the current scope")
        locant = locants[atom.id]
        descriptors.append((locant, atom.cip_label, len(parent) > 1))

    for bond in molecule.bonds:
        if not bond.stereo:
            continue
        if bond.cip_label not in {"E", "Z"}:
            raise NamingUnsupported("Non-E/Z bond stereochemistry is outside the current scope")
        if bond.order != 2 or bond.a not in locants or bond.b not in locants:
            raise NamingUnsupported("Stereochemistry within a substituent is outside the current scope")
        locant = min(locants[bond.a], locants[bond.b])
        descriptors.append((locant, bond.cip_label, len(parent) > 2))

    if not descriptors:
        return name, None

    descriptors.sort(key=lambda item: (item[0], item[1]))
    rendered_descriptors = tuple(
        f"{locant if include_locant else ''}{descriptor}"
        for locant, descriptor, include_locant in descriptors
    )
    rendered = ",".join(rendered_descriptors)
    trace = TraceStep(
        "P-93.4",
        "Assigned absolute stereodescriptors after final parent numbering",
        rendered_descriptors,
        rendered,
    )
    return f"({rendered})-{name}", trace


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
        paths = [
            path
            for path in principal_paths
            if sum(1 for group in same_kind_groups if group.principal_atom in path) == max_suffix_count
        ]
    else:
        max_suffix_count = 0
    max_len = max(len(path) for path in paths)
    paths = [path for path in paths if len(path) == max_len]
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
        for group in (group for group in groups if group.kind == "amide"):
            nitrogen_atoms = [atom_id for atom_id in group.atom_ids if molecule.atom(atom_id).element == "N"]
            if not nitrogen_atoms or len(molecule.neighbors(nitrogen_atoms[0])) != 1:
                raise NamingUnsupported("N-substituted amide suffix nomenclature is outside the current scope")

    if principal and principal.kind != "nitrile" and any(group.kind == "nitrile" for group in groups):
        raise NamingUnsupported("Cyano prefix parent selection is outside the current scope")

    if principal and principal.kind != "acid_halide" and any(group.kind == "acid_halide" for group in groups):
        raise NamingUnsupported("Acyl-halide prefix nomenclature is outside the current scope")

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

    return sorted(paths)


def _ranking_vector(
    molecule: Molecule,
    chain: tuple[int, ...],
    principal: FunctionalGroup | None,
    *,
    candidate_groups: tuple[FunctionalGroup, ...] | None = None,
) -> RankingVector:
    locants = {atom_id: idx + 1 for idx, atom_id in enumerate(chain)}
    chain_groups = candidate_groups or tuple(groups_for_chain(molecule, chain))
    if principal:
        principal_locants = tuple(
            sorted(
                locants[
                    _cyclic_group_parent_atom(molecule, group, frozenset(chain))
                    if group.principal_atom not in locants
                    else group.principal_atom
                ]
                for group in chain_groups
                if group.kind == principal.kind
            )
        )
    else:
        principal_locants = ()
    multiple_bond_locants = tuple(sorted(
        min(locants[bond.a], locants[bond.b])
        for bond in molecule.bonds
        if bond.a in locants and bond.b in locants and bond.order > 1
    ))
    multiple_bond_rank = (-len(multiple_bond_locants), *multiple_bond_locants)
    substituents = _substituents(molecule, chain, principal, chain_groups)
    substituent_locants = tuple(sorted(locant for locant, _ in substituents))
    citation_order = sorted(substituents, key=lambda item: (_alphabetical_key(item[1]), item[0]))
    citation_locants = tuple(locant for locant, _ in citation_order)
    substituent_names = tuple(name for _, name in citation_order)
    return RankingVector(
        principal_locants,
        multiple_bond_rank,
        substituent_locants,
        citation_locants,
        substituent_names,
        _stereo_ranking_vector(molecule, chain),
    )


def _stereo_ranking_vector(
    molecule: Molecule, parent: tuple[int, ...]
) -> tuple[tuple[int, ...], ...]:
    locants = {atom_id: index + 1 for index, atom_id in enumerate(parent)}
    by_locant: dict[int, list[int]] = {}

    for atom in molecule.atoms:
        if atom.id in locants and atom.cip_label in {"R", "S", "r", "s"}:
            rank = 0 if atom.cip_label in {"R", "r"} else 1
            by_locant.setdefault(locants[atom.id], []).append(rank)

    for bond in molecule.bonds:
        if (
            bond.a in locants
            and bond.b in locants
            and bond.cip_label in {"E", "Z"}
        ):
            rank = 0 if bond.cip_label == "Z" else 1
            by_locant.setdefault(min(locants[bond.a], locants[bond.b]), []).append(rank)

    if not by_locant:
        return ()
    return tuple(
        tuple(sorted(by_locant.get(locant, [2])))
        for locant in range(1, len(parent) + 1)
    )


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
            substituents.append((locants[group.principal_atom], "amino"))
            substituents.append((locants[group.principal_atom], "oxo"))
        elif group.kind == "ester":
            alkyl = _ester_alkyl_name(molecule, group, chain)
            substituents.append((locants[group.principal_atom], _organyl_to_oxy(alkyl)))
            substituents.append((locants[group.principal_atom], "oxo"))
        elif group.kind == "hydroxyimino":
            substituents.append((locants[group.principal_atom], "hydroxyimino"))

    for atom_id in chain:
        for neighbor, order in molecule.neighbors(atom_id):
            if neighbor in chain_set or neighbor in principal_atoms or neighbor in functional_group_atoms:
                continue
            atom = molecule.atom(neighbor)
            if order == 2 and atom.element == "C":
                if _alkyl_size(molecule, neighbor, blocked=chain_set | {atom_id}) == 1:
                    substituents.append((locants[atom_id], "methylidene"))
                else:
                    raise NamingUnsupported("Complex double-bond substituents are outside scope")
            elif order != 1:
                continue
            elif atom.element in HALO_PREFIX:
                if len(molecule.neighbors(neighbor)) != 1:
                    raise NamingUnsupported("Functionalized halogen substituents are outside the current scope")
                substituents.append((locants[atom_id], HALO_PREFIX[atom.element]))
            elif atom.element == "C":
                substituents.append(
                    (
                        locants[atom_id],
                        _simple_alkyl_name(molecule, neighbor, blocked=chain_set | {atom_id}),
                    )
                )
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
        return group.kind in {
            "carboxylic_acid",
            "ester",
            "acid_halide",
            "amide",
            "nitrile",
            "aldehyde",
            "ketone",
            "alcohol",
            "amine",
        }
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
            if neighbor in blocked:
                continue
            if order != 1:
                raise NamingUnsupported("Unsaturated substituent recursion is outside scope")
            stack.append(neighbor)
    return len(seen)


def _simple_alkyl_name(molecule: Molecule, start: int, blocked: set[int]) -> str:
    if molecule.atom(start).in_ring:
        return _simple_cycloalkyl_name(molecule, start, blocked)

    carbon_ids = _carbon_fragment(molecule, start, blocked)
    paths = [path for path in _fragment_paths(molecule, carbon_ids) if start in path]
    if not paths:
        raise NamingUnsupported("No carbon chain found for alkyl substituent")
    max_len = max(len(path) for path in paths)
    oriented_paths = {
        oriented
        for path in paths
        if len(path) == max_len
        for oriented in (path, tuple(reversed(path)))
    }

    candidates: list[tuple[tuple[object, ...], tuple[int, ...], list[tuple[int, str]]]] = []
    for path in oriented_paths:
        locants = {atom_id: index + 1 for index, atom_id in enumerate(path)}
        branches = _alkyl_branches(molecule, path, blocked)
        citation_order = sorted(branches, key=lambda item: (_alphabetical_key(item[1]), item[0]))
        rank: tuple[object, ...] = (
            locants[start],
            tuple(sorted(locant for locant, _ in branches)),
            tuple(locant for locant, _ in citation_order),
            tuple(name for _, name in citation_order),
        )
        candidates.append((rank, path, branches))

    _, parent_chain, branches = min(candidates, key=lambda item: item[0])
    attachment_locant = parent_chain.index(start) + 1
    root = parent_root(len(parent_chain))
    if attachment_locant == 1:
        parent_name = root + "yl"
    else:
        parent_name = f"{root}an-{attachment_locant}-yl"
    name = _render_prefixes(branches, omit_locants=len(parent_chain) == 1) + parent_name
    return "tert-butyl" if name == "2-methylpropan-2-yl" else name


def _simple_cycloalkyl_name(
    molecule: Molecule,
    start: int,
    blocked: set[int],
) -> str:
    containing_rings = [ring for ring in molecule.rings if start in ring]
    if len(containing_rings) != 1 or len(molecule.rings) != 1:
        raise NamingUnsupported("Polycyclic substituent nomenclature is outside the current scope")
    ring_atoms = frozenset(containing_rings[0])
    if any(molecule.atom(atom_id).element != "C" for atom_id in ring_atoms):
        raise NamingUnsupported("Heterocyclic substituent nomenclature is outside the current scope")
    ring_bonds = [
        bond
        for bond in molecule.bonds
        if bond.a in ring_atoms and bond.b in ring_atoms
    ]
    if _is_benzene_ring(molecule, ring_atoms, ring_bonds):
        parent_name = "phenyl"
    elif len(ring_bonds) == len(ring_atoms) and all(
        bond.order == 1 and not bond.aromatic for bond in ring_bonds
    ):
        parent_name = f"cyclo{parent_root(len(ring_atoms))}yl"
    else:
        raise NamingUnsupported("Unsaturated cyclic substituent nomenclature is outside the current scope")

    for ring_atom in ring_atoms:
        for neighbor, order in molecule.neighbors(ring_atom):
            if neighbor in ring_atoms or neighbor in blocked:
                continue
            raise NamingUnsupported("Substituted cycloalkyl prefixes are outside the current scope")
    return parent_name


def _is_benzene_ring(
    molecule: Molecule,
    ring_atoms: frozenset[int],
    ring_bonds: list,
) -> bool:
    return (
        len(ring_atoms) == 6
        and len(ring_bonds) == 6
        and all(molecule.atom(atom_id).element == "C" for atom_id in ring_atoms)
        and all(molecule.atom(atom_id).aromatic for atom_id in ring_atoms)
        and all(bond.aromatic and bond.order == 1 for bond in ring_bonds)
        and all(
            order == 1
            for atom_id in ring_atoms
            for neighbor, order in molecule.neighbors(atom_id)
            if neighbor not in ring_atoms
        )
    )


def _benzene_ring_atoms(molecule: Molecule) -> frozenset[int] | None:
    if len(molecule.rings) != 1:
        return None
    ring_atoms = frozenset(molecule.rings[0])
    ring_bonds = [
        bond
        for bond in molecule.bonds
        if bond.a in ring_atoms and bond.b in ring_atoms
    ]
    return ring_atoms if _is_benzene_ring(molecule, ring_atoms, ring_bonds) else None


def _fragment_paths(molecule: Molecule, atom_ids: set[int]) -> set[tuple[int, ...]]:
    paths: set[tuple[int, ...]] = set()

    def walk(path: list[int]) -> None:
        paths.add(tuple(path))
        for neighbor, order in molecule.neighbors(path[-1]):
            if neighbor in atom_ids and neighbor not in path:
                if order != 1:
                    raise NamingUnsupported("Unsaturated alkyl substituents are outside scope")
                walk(path + [neighbor])

    for atom_id in atom_ids:
        walk([atom_id])
    return paths


def _alkyl_branches(
    molecule: Molecule, parent_chain: tuple[int, ...], blocked: set[int]
) -> list[tuple[int, str]]:
    chain_set = set(parent_chain)
    branches: list[tuple[int, str]] = []
    for locant, atom_id in enumerate(parent_chain, start=1):
        for neighbor, order in molecule.neighbors(atom_id):
            if neighbor in chain_set or neighbor in blocked:
                continue
            atom = molecule.atom(neighbor)
            if atom.element in HALO_PREFIX and order == 1:
                branches.append((locant, HALO_PREFIX[atom.element]))
                continue
            if atom.element != "C" or order != 1:
                raise NamingUnsupported("Unsaturated alkyl substituents are outside scope")
            branches.append(
                (
                    locant,
                    _simple_alkyl_name(molecule, neighbor, blocked | chain_set),
                )
            )
    return branches


def _alkoxy_name(molecule: Molecule, oxygen: int, blocked: set[int]) -> str:
    carbon_neighbors = [
        neighbor
        for neighbor, order in molecule.neighbors(oxygen)
        if order == 1 and molecule.atom(neighbor).element == "C" and neighbor not in blocked
    ]
    if len(carbon_neighbors) != 1:
        raise NamingUnsupported("Complex ether substituents are outside scope")
    carbon_ids = _carbon_fragment(molecule, carbon_neighbors[0], blocked | {oxygen})
    halo_prefix = _halo_fragment_prefix(molecule, carbon_ids)
    if halo_prefix:
        return halo_prefix + parent_root(len(carbon_ids)) + "oxy"
    alkyl = _simple_alkyl_name(molecule, carbon_neighbors[0], blocked | {oxygen})
    return _organyl_to_oxy(alkyl)


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
        if carbon_count:
            return parent_root(carbon_count) + "anamido"
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
        parts.append(multiplicative_prefix(count) + name)
    return "".join(parts)


def _render_name(molecule: Molecule, candidate: NumberingCandidate) -> str:
    chain = candidate.chain

    parent = _render_parent(molecule, chain, candidate.principal_group, candidate.groups)
    if candidate.principal_group and candidate.principal_group.kind == "ester":
        return parent
    substituents = _substituents(molecule, chain, candidate.principal_group, candidate.groups)
    if _uses_retained_acetic_parent(candidate, substituents):
        return _render_prefixes(substituents, omit_locants=True) + "acetic acid"
    omit_locants = (
        len(chain) == 1
        or _single_prefix_has_no_distinct_locant(candidate, substituents)
        or _is_complete_single_halogen_substitution(molecule, candidate, substituents)
    )
    prefixes = _render_prefixes(substituents, omit_locants=omit_locants)
    return prefixes + parent


def _uses_retained_acetic_parent(
    candidate: NumberingCandidate,
    substituents: list[tuple[int, str]],
) -> bool:
    return (
        candidate.principal_group is not None
        and candidate.principal_group.kind == "carboxylic_acid"
        and len(candidate.chain) == 2
        and bool(substituents)
        and all(locant == 2 for locant, _ in substituents)
        and all(
            name == "phenyl" or (name.startswith("cyclo") and name.endswith("yl"))
            for _, name in substituents
        )
    )


def _single_prefix_has_no_distinct_locant(
    candidate: NumberingCandidate, substituents: list[tuple[int, str]]
) -> bool:
    return candidate.principal_group is None and len(candidate.chain) == 2 and len(substituents) == 1


def _is_complete_single_halogen_substitution(
    molecule: Molecule,
    candidate: NumberingCandidate,
    substituents: list[tuple[int, str]],
) -> bool:
    if not substituents or len({name for _, name in substituents}) != 1:
        return False
    if any(name not in HALO_PREFIX.values() for _, name in substituents):
        return False
    if candidate.principal_group and candidate.principal_group.kind not in {"carboxylic_acid", "alcohol"}:
        return False
    for atom in molecule.atoms:
        if atom.element != "C":
            continue
        valence = sum(order for _, order in molecule.neighbors(atom.id))
        if valence < 4:
            return False
    return True


def _render_prefixes(substituents: list[tuple[int, str]], *, omit_locants: bool = False) -> str:
    if not substituents:
        return ""
    grouped: dict[str, list[int]] = {}
    for locant, name in substituents:
        grouped.setdefault(name, []).append(locant)

    parts = []
    for name in sorted(grouped, key=_alphabetical_key):
        locants = sorted(grouped[name])
        rendered_name = "acetylamino" if name == "acetamido" and len(grouped) == 1 and len(locants) == 1 else name
        needs_enclosure = _needs_substitutive_parentheses(rendered_name)
        if len(locants) > 1 and needs_enclosure:
            rendered = f"{_derived_multiplicative_prefix(len(locants))}({rendered_name})"
        else:
            rendered = multiplicative_prefix(len(locants)) + rendered_name
            if rendered_name.startswith("(") and rendered_name.endswith("oxy"):
                rendered = f"[{rendered}]"
            elif needs_enclosure:
                rendered = f"({rendered})"
        if omit_locants:
            parts.append(rendered)
        else:
            parts.append(f"{','.join(str(l) for l in locants)}-{rendered}")
    return "".join(parts) if omit_locants else "-".join(parts)


def _needs_substitutive_parentheses(name: str) -> bool:
    return (
        name in {"acetylamino", "hydroxyimino"}
        or (
            name.endswith(("methyl", "ethyl", "propyl", "butyl"))
            and any(prefix in name for prefix in HALO_PREFIX.values())
        )
        or (
            name.endswith("oxy")
            and name not in {"hydroxy", "methoxy", "ethoxy", "propoxy", "butoxy"}
        )
        or any(char.isdigit() for char in name)
    )


def _alphabetical_key(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def _derived_multiplicative_prefix(count: int) -> str:
    if count == 2:
        return "bis"
    if count == 3:
        return "tris"
    return multiplicative_prefix(count) + "kis"


def _combine_organyl_names(names: list[str]) -> str:
    if not names:
        raise NamingUnsupported("No organyl component was available")
    if len(set(names)) != 1:
        return " ".join(names)
    name = names[0]
    if len(names) > 1 and _needs_substitutive_parentheses(name):
        return f"{_derived_multiplicative_prefix(len(names))}({name})"
    return multiplicative_prefix(len(names)) + name


def _render_parent(
    molecule: Molecule,
    chain: tuple[int, ...],
    principal: FunctionalGroup | None,
    groups: tuple[FunctionalGroup, ...] = (),
) -> str:
    root = parent_root(len(chain))
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
        ester_groups = suffix_groups or [principal]
        alkyl_names = sorted(
            (_ester_alkyl_name(molecule, ester_group, chain) for ester_group in ester_groups),
            key=_alphabetical_key,
        )
        alkyl = _combine_organyl_names(alkyl_names)
        prefixes = _render_prefixes(_substituents(molecule, chain, principal, groups), omit_locants=len(chain) == 1)
        if len(ester_groups) == 1:
            anion_name = f"{root}{unsat}oate"
        else:
            anion_name = f"{_parent_stem(root, unsat, keep_terminal_e=True)}{_multiplicative_suffix(len(ester_groups), 'oate')}"
        return f"{alkyl} {prefixes}{anion_name}"
    if group == "acid_halide":
        halogens = [
            molecule.atom(atom_id).element
            for atom_id in principal.atom_ids
            if molecule.atom(atom_id).element in HALO_PREFIX
        ]
        if len(halogens) != 1:
            raise NamingUnsupported("Acid halide atom could not be identified")
        return f"{_parent_stem(root, unsat, keep_terminal_e=False)}oyl {_halide_class_name(halogens[0])}"
    if group == "amide":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}{_multiplicative_suffix(len(suffix_locants), 'amide')}"
        return f"{_parent_stem(root, unsat, keep_terminal_e=False)}amide"
    if group == "nitrile":
        ending = "nitrile" if len(suffix_locants) == 1 else _multiplicative_suffix(len(suffix_locants), "nitrile")
        return f"{_parent_stem(root, unsat, keep_terminal_e=True)}{ending}"
    if group == "aldehyde":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}{_multiplicative_suffix(len(suffix_locants), 'al')}"
        return f"{root}{unsat}al"
    if group == "ketone":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}-{','.join(str(n) for n in suffix_locants)}-{_multiplicative_suffix(len(suffix_locants), 'one')}"
        return f"{root}{unsat}-{locants[principal.principal_atom]}-one"
    if group == "alcohol":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}-{','.join(str(n) for n in suffix_locants)}-{_multiplicative_suffix(len(suffix_locants), 'ol')}"
        if len(chain) <= 2 and suffix_locants == (1,):
            return f"{root}{unsat}ol"
        return f"{root}{unsat}-{locants[principal.principal_atom]}-ol"
    if group == "amine":
        if len(suffix_locants) > 1:
            return f"{_parent_stem(root, unsat, keep_terminal_e=True)}-{','.join(str(n) for n in suffix_locants)}-{_multiplicative_suffix(len(suffix_locants), 'amine')}"
        if len(chain) <= 2 and suffix_locants == (1,):
            return f"{root}{unsat}amine"
        return f"{root}{unsat}-{locants[principal.principal_atom]}-amine"
    if unsat == "an":
        return f"{root}ane"
    return _elide_terminal_locant_for_two_carbon_unsaturation(f"{root}{unsat}e")


def _parent_stem(root: str, unsat: str, *, keep_terminal_e: bool) -> str:
    if unsat == "an":
        return f"{root}ane" if keep_terminal_e else f"{root}an"
    return f"{root}{unsat}e" if keep_terminal_e else f"{root}{unsat}"


def _multiplicative_suffix(count: int, suffix: str) -> str:
    multiplier = multiplicative_prefix(count)
    if suffix[0] in "aeiou" and multiplier.endswith("a"):
        multiplier = multiplier[:-1]
    return multiplier + suffix


def _halide_class_name(element: str) -> str:
    return {"F": "fluoride", "Cl": "chloride", "Br": "bromide", "I": "iodide"}[element]


def _organyl_to_oxy(name: str) -> str:
    if not name.endswith("yl"):
        raise NamingUnsupported(f"Cannot form an oxy prefix from {name!r}")
    if name in {"methyl", "ethyl", "propyl", "butyl"}:
        return name[:-2] + "oxy"
    if any(char.isdigit() for char in name):
        return f"({name})oxy"
    return name + "oxy"


def _ester_alkyl_name(molecule: Molecule, ester_group: FunctionalGroup, chain: tuple[int, ...]) -> str:
    chain_set = set(chain)
    oxygens = [atom_id for atom_id in ester_group.atom_ids if molecule.atom(atom_id).element == "O" and len(molecule.neighbors(atom_id)) == 2]
    if not oxygens:
        raise NamingUnsupported("Ester alkoxy oxygen could not be identified")
    oxygen = oxygens[0]
    alkyl_roots = [
        neighbor
        for neighbor, order in molecule.neighbors(oxygen)
        if (
            order == 1
            and molecule.atom(neighbor).element == "C"
            and neighbor not in chain_set
            and neighbor != ester_group.principal_atom
        )
    ]
    if len(alkyl_roots) != 1:
        raise NamingUnsupported("Complex ester alkyl groups are outside scope")
    name = _simple_alkyl_name(molecule, alkyl_roots[0], blocked={oxygen})
    if name == "2-methylpropan-2-yl":
        return "tert-butyl"
    return name


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
