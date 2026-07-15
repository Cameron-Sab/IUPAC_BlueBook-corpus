from __future__ import annotations

from rdkit import Chem, rdBase
from rdkit.Chem import rdCIPLabeler

from .model import Atom, Bond, Molecule, StereoGroup, StereoUnit


class SmilesError(ValueError):
    pass


def parse_smiles(smiles: str) -> Molecule:
    with rdBase.BlockLogs():
        return _parse_smiles(smiles)


def _parse_smiles(smiles: str) -> Molecule:
    if not smiles:
        raise SmilesError("Empty SMILES")

    params = Chem.SmilesParserParams()
    params.allowCXSMILES = True
    params.parseName = False
    with rdBase.BlockLogs():
        parsed = Chem.MolFromSmiles(smiles, params)
    if parsed is None:
        raise SmilesError("Invalid SMILES")

    Chem.AssignStereochemistry(parsed, cleanIt=True, force=True)
    ranks = Chem.CanonicalRankAtoms(
        parsed,
        breakTies=True,
        includeChirality=True,
        includeIsotopes=True,
        includeAtomMaps=True,
        includeChiralPresence=True,
    )
    canonical_order = sorted(range(parsed.GetNumAtoms()), key=lambda atom_id: ranks[atom_id])
    canonical = Chem.RenumberAtoms(parsed, canonical_order)
    Chem.AssignStereochemistry(canonical, cleanIt=True, force=True)
    try:
        rdCIPLabeler.AssignCIPLabels(canonical, maxRecursiveIterations=1_250_000)
    except RuntimeError as exc:
        raise SmilesError("CIP assignment did not converge") from exc
    canonical_smiles = Chem.MolToCXSmiles(canonical)

    molecule = Molecule(source_smiles=smiles, canonical_smiles=canonical_smiles)
    ring_info = canonical.GetRingInfo()
    molecule.rings = tuple(tuple(ring) for ring in ring_info.AtomRings())

    for atom in canonical.GetAtoms():
        chiral_tag = str(atom.GetChiralTag())
        molecule.atoms.append(
            Atom(
                id=atom.GetIdx(),
                element=atom.GetSymbol() if atom.GetAtomicNum() else "*",
                aromatic=atom.GetIsAromatic(),
                formal_charge=atom.GetFormalCharge(),
                isotope=atom.GetIsotope(),
                explicit_hydrogens=atom.GetNumExplicitHs(),
                radical_electrons=atom.GetNumRadicalElectrons(),
                chiral_tag=None if chiral_tag == "CHI_UNSPECIFIED" else chiral_tag,
                cip_label=atom.GetProp("_CIPCode") if atom.HasProp("_CIPCode") else None,
                in_ring=atom.IsInRing(),
            )
        )

    for bond in canonical.GetBonds():
        numeric_order = bond.GetBondTypeAsDouble()
        if bond.GetIsAromatic():
            order = 1
        elif numeric_order in {1.0, 2.0, 3.0}:
            order = int(numeric_order)
        else:
            raise SmilesError(f"Unsupported bond type: {bond.GetBondType()}")
        stereo = str(bond.GetStereo())
        molecule.bonds.append(
            Bond(
                a=bond.GetBeginAtomIdx(),
                b=bond.GetEndAtomIdx(),
                order=order,
                aromatic=bond.GetIsAromatic(),
                in_ring=bond.IsInRing(),
                stereo=None if stereo == "STEREONONE" else stereo,
                cip_label=bond.GetProp("_CIPCode") if bond.HasProp("_CIPCode") else None,
            )
        )

    molecule.potential_stereo = tuple(
        StereoUnit(
            kind=str(info.type),
            centered_on=info.centeredOn,
            specified=str(info.specified),
            descriptor=str(info.descriptor),
        )
        for info in Chem.FindPotentialStereo(canonical, cleanIt=False, flagPossible=True)
    )
    molecule.stereo_groups = tuple(
        StereoGroup(
            kind=str(group.GetGroupType()),
            atom_ids=tuple(atom.GetIdx() for atom in group.GetAtoms()),
        )
        for group in canonical.GetStereoGroups()
    )

    return molecule
