from __future__ import annotations

from rdkit import Chem, rdBase

from .model import Atom, Bond, Molecule


class SmilesError(ValueError):
    pass


def parse_smiles(smiles: str) -> Molecule:
    if not smiles:
        raise SmilesError("Empty SMILES")

    with rdBase.BlockLogs():
        parsed = Chem.MolFromSmiles(smiles)
    if parsed is None:
        raise SmilesError("Invalid SMILES")

    canonical_smiles = Chem.MolToSmiles(parsed, canonical=True, isomericSmiles=True)
    with rdBase.BlockLogs():
        canonical = Chem.MolFromSmiles(canonical_smiles)
    if canonical is None:
        raise SmilesError("Canonical SMILES could not be parsed")
    Chem.AssignStereochemistry(canonical, cleanIt=True, force=True)

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
            )
        )

    return molecule
