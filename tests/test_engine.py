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


def test_aldehyde_suffix_does_not_render_oxo_prefix():
    assert name_smiles("CC(C)CCC=O")["name"] == "4-methylpentanal"
    assert name_smiles("C#CC=O")["name"] == "prop-2-ynal"


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
    assert name_smiles("C=CC")["name"] == "prop-1-ene"
    assert name_smiles("CC#CC")["name"] == "but-2-yne"


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


def test_oxo_prefix_with_carboxylic_acid():
    assert name_smiles("O=CCCCCC(=O)O")["name"] == "6-oxohexanoic acid"


def test_multiple_oxo_prefixes_with_carboxylic_acid():
    assert name_smiles("CC(=O)CC(=O)C(=O)O")["name"] == "2,4-dioxopentanoic acid"


def test_parent_chain_prefers_maximum_suffix_groups():
    assert name_smiles("CC(N)(CO)CO")["name"] == "2-amino-2-methylpropane-1,3-diol"


def test_unsaturated_parent_keeps_terminal_e_before_multiplicative_suffix():
    assert name_smiles("OCC#CCO")["name"] == "but-2-yne-1,4-diol"


def test_three_alcohol_suffixes_render_triol():
    assert name_smiles("OCC(O)CO")["name"] == "propane-1,2,3-triol"


def test_dicarboxylic_acid_numbering_prefers_unsaturation_after_suffix_locants():
    assert name_smiles("O=C(O)CCC(O)C=C(O)C(=O)O")["name"] == "2,4-dihydroxyhept-2-enedioic acid"


def test_parent_selection_prefers_multiple_bonds():
    assert name_smiles("C=C(C)C(=O)O")["name"] == "2-methylprop-2-enoic acid"


def test_exocyclic_methylidene_prefix():
    assert name_smiles("C=C(CCC(=O)O)C(=O)O")["name"] == "2-methylidenepentanedioic acid"
    assert name_smiles("C=C(C(=O)O)C(C)C(=O)O")["name"] == "2-methyl-3-methylidenebutanedioic acid"


def test_simple_ester():
    assert name_smiles("CC(=O)OCC")["name"] == "ethyl ethanoate"


def test_oxo_substituted_ester():
    assert name_smiles("CCOC(=O)CC(C)=O")["name"] == "ethyl 3-oxobutanoate"


def test_halogenated_alkoxy_prefix():
    assert name_smiles("FC(F)OC(F)C(F)(F)F")["name"] == "2-(difluoromethoxy)-1,1,1,2-tetrafluoroethane"


def test_halogenated_alkoxy_prefix_with_chloro_parent():
    assert name_smiles("FC(F)OC(F)(F)C(F)Cl")["name"] == "2-chloro-1-(difluoromethoxy)-1,1,2-trifluoroethane"


def test_fluoromethoxy_prefix():
    assert name_smiles("FCOC(C(F)(F)F)C(F)(F)F")["name"] == "1,1,1,3,3,3-hexafluoro-2-(fluoromethoxy)propane"


def test_hydroxyimino_prefix_on_ketone():
    assert name_smiles("CC(=O)C(C)=NO")["name"] == "3-(hydroxyimino)butan-2-one"


def test_lone_acylamino_prefix_prefers_acetylamino_style():
    assert name_smiles("CC(=O)NCCCCC(=O)O")["name"] == "5-(acetylamino)pentanoic acid"


def test_acetamido_prefix_with_other_prefixes():
    assert name_smiles("CC(=O)NCCCC(N)CC(=O)O")["name"] == "6-acetamido-3-aminohexanoic acid"
    assert name_smiles("CC(=O)NCCCC(=O)CC(=O)O")["name"] == "6-acetamido-3-oxohexanoic acid"


def test_unsupported_amide_suffix_fails_closed():
    result = name_smiles("CC(=O)NN")
    assert result["status"] == "unsupported"
    assert "Amide suffix" in result["reason"]


def test_unsupported_guanidine_fails_closed():
    result = name_smiles("N=C(N)NC(=N)N")
    assert result["status"] == "unsupported"
    assert "guanidine" in result["reason"]


def test_complex_amine_substituent_fails_closed():
    result = name_smiles("O=C(O)CN(CCN(CC(=O)O)CC(=O)O)CC(=O)O")
    assert result["status"] == "unsupported"
    assert "Complex amine" in result["reason"]
