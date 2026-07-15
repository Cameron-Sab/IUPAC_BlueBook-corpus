from iupac_engine import name_smiles


def test_simple_alcohol():
    result = name_smiles("CCO", explain=True)
    assert result["status"] == "success"
    assert result["name"] == "ethan-1-ol"
    assert result["decision_trace"]


def test_branched_alcohol_numbering():
    assert name_smiles("CC(C)O")["name"] == "propan-2-ol"


def test_ketone():
    assert name_smiles("CCC(=O)C")["name"] == "butan-2-one"


def test_carboxylic_acid():
    assert name_smiles("CC(=O)O")["name"] == "ethanoic acid"


def test_halo_prefix():
    assert name_smiles("CC(C)Cl")["name"] == "2-chloropropane"


def test_simple_alkanes():
    assert name_smiles("C")["name"] == "methane"
    assert name_smiles("CC")["name"] == "ethane"
    assert name_smiles("CCC")["name"] == "propane"
    assert name_smiles("CCCC")["name"] == "butane"


def test_simple_unsaturation():
    assert name_smiles("C=C")["name"] == "ethene"
    assert name_smiles("C#C")["name"] == "ethyne"


def test_branched_alkane():
    assert name_smiles("CC(C)C")["name"] == "2-methylpropane"


def test_ring_is_structured_unsupported():
    result = name_smiles("C1CCCCC1")
    assert result["status"] == "unsupported"
    assert result["name"] is None
    assert "Ring closures" in result["reason"]


def test_disconnected_is_structured_unsupported():
    result = name_smiles("CC.O")
    assert result["status"] == "unsupported"
    assert result["supported_scope"] is False
