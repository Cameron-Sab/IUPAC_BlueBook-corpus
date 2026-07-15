from iupac_engine.parser import parse_smiles


def test_parser_canonicalizes_equivalent_smiles():
    assert parse_smiles("OCC").canonical_smiles == parse_smiles("CCO").canonical_smiles


def test_parser_preserves_ring_aromatic_charge_isotope_and_stereo_metadata():
    aromatic = parse_smiles("c1ccccc1")
    assert aromatic.rings
    assert all(atom.aromatic and atom.in_ring for atom in aromatic.atoms)

    charged = parse_smiles("[NH3+]CC(=O)[O-]")
    assert sorted(atom.formal_charge for atom in charged.atoms) == [-1, 0, 0, 0, 1]

    isotope = parse_smiles("[13CH3]CO")
    assert any(atom.isotope == 13 for atom in isotope.atoms)

    stereo = parse_smiles("N[C@@H](C)C(=O)O")
    assert any(atom.chiral_tag and atom.cip_label for atom in stereo.atoms)
