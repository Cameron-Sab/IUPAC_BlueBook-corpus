from iupac_engine import name_smiles


def test_simple_alcohol():
    result = name_smiles("CCO", explain=True)
    assert result["status"] == "success"
    assert result["name"] == "ethanol"
    assert result["decision_trace"]
    assert result["rule_set"] == "bluebook-prototype-v0.3"
    assert [step["rule_id"] for step in result["decision_trace"]] == [
        "P-41",
        "P-44.1.1",
        "P-14.4",
    ]


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


def test_unambiguous_two_carbon_prefix_locant_is_elided():
    assert name_smiles("CCBr")["name"] == "bromoethane"


def test_methane_prefix_locants_are_elided():
    assert name_smiles("ClC(Cl)(Cl)Cl")["name"] == "tetrachloromethane"


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
    result = name_smiles("C1=CCCCC1")
    assert result["status"] == "unsupported"
    assert result["name"] is None
    assert "Unsaturated ring" in result["reason"]


def test_disconnected_is_structured_unsupported():
    result = name_smiles("CC.O")
    assert result["status"] == "unsupported"
    assert result["supported_scope"] is False


def test_neutral_bracket_atom_is_parsed_when_chemistry_is_supported():
    assert name_smiles("[NH2]CC(=O)O")["name"] == "2-aminoethanoic acid"


def test_graph_modifiers_fail_closed_by_feature():
    assert "Formal-charge" in name_smiles("[NH3+]CC(=O)[O-]")["reason"]
    assert "Isotopic" in name_smiles("[13CH3]CO")["reason"]


def test_absolute_stereodescriptors_use_final_parent_locants():
    assert name_smiles("N[C@@H](C)C(=O)O")["name"] == "(2S)-2-aminopropanoic acid"
    assert name_smiles("C[C@H](O)C(=O)O")["name"] == "(2S)-2-hydroxypropanoic acid"
    assert name_smiles("C/C=C/C")["name"] == "(2E)-but-2-ene"
    assert name_smiles("C/C=C\\C")["name"] == "(2Z)-but-2-ene"

    explained = name_smiles("C/C=C/C", explain=True)
    assert explained["decision_trace"][-1]["rule_id"] == "P-93.4"


def test_multiple_stereodescriptors_are_locant_ordered():
    result = name_smiles("O=C(O)[C@H](O)[C@@H](O)C(=O)O")
    assert result["name"] == "(2R,3R)-2,3-dihydroxybutanedioic acid"


def test_stereo_breaks_a_constitutionally_tied_numbering():
    result = name_smiles("C[C@H](F)C[C@H](F)C")
    assert result["name"] == "(2R,4S)-2,4-difluoropentane"


def test_unnecessary_stereodescriptor_locants_are_omitted():
    assert name_smiles("[C@H](F)(Cl)Br")["name"] == "(S)-bromochlorofluoromethane"
    assert name_smiles("Cl/C=C/Cl")["name"] == "(E)-1,2-dichloroethene"


def test_unspecified_potential_stereo_is_reported_as_incomplete():
    tetrahedral = name_smiles("NC(C)C(=O)O")
    alkene = name_smiles("CC=CC")
    assert tetrahedral["status"] == "success"
    assert tetrahedral["stereochemistry_complete"] is False
    assert alkene["stereochemistry_complete"] is False


def test_enhanced_stereo_fails_closed_instead_of_becoming_absolute():
    result = name_smiles("C[C@H](F)[C@H](Cl)Br |&1:1,3|")
    assert result["status"] == "unsupported"
    assert "Enhanced" in result["reason"]


def test_aromatic_and_alicyclic_rings_are_distinguished():
    assert "Aromatic ring" in name_smiles("c1ccccc1")["reason"]
    assert name_smiles("C1CCCCC1")["name"] == "cyclohexane"


def test_saturated_monocyclic_hydrocarbon_parents():
    assert name_smiles("C1CC1")["name"] == "cyclopropane"
    assert name_smiles("C1CCC1")["name"] == "cyclobutane"
    assert name_smiles("C1CCCC1")["name"] == "cyclopentane"
    assert name_smiles("C1CCCCC1")["name"] == "cyclohexane"


def test_substituted_monocycle_numbering_and_locant_elision():
    assert name_smiles("CC1CCCCC1")["name"] == "methylcyclohexane"
    assert name_smiles("CC1CC(C)CCC1")["name"] == "1,3-dimethylcyclohexane"
    assert name_smiles("CCC1CCCCC1C")["name"] == "1-ethyl-2-methylcyclohexane"
    assert name_smiles("FC1(F)C(F)(F)C(F)(F)C1(F)F")["name"] == "octafluorocyclobutane"


def test_ring_parent_is_preferred_to_an_equally_long_chain():
    assert name_smiles("CCCCCCC1CCCCC1")["name"] == "hexylcyclohexane"


def test_ring_parent_is_preferred_even_when_acyclic_branches_are_longer():
    assert name_smiles("CCCCCCC1CCCC1")["name"] == "hexylcyclopentane"
    assert (
        name_smiles("CCCCCCCCC1CCCC1CCCCCCC")["name"]
        == "1-heptyl-2-octylcyclopentane"
    )


def test_unsupported_ring_families_fail_closed():
    assert "Heterocycle" in name_smiles("O1CCCCC1")["reason"]
    assert "Polycyclic" in name_smiles("C1CCC2CCCCC2C1")["reason"]


def test_ring_local_monofunctional_suffixes():
    assert name_smiles("OC1CCCC1")["name"] == "cyclopentanol"
    assert name_smiles("O=C1CCCCC1")["name"] == "cyclohexanone"
    assert name_smiles("NC1CCCCC1")["name"] == "cyclohexanamine"


def test_ring_suffix_locant_is_retained_when_another_locant_is_needed():
    assert name_smiles("OC1C(C)CCCC1")["name"] == "2-methylcyclohexan-1-ol"
    assert name_smiles("O=C1CCC(C)CC1")["name"] == "4-methylcyclohexan-1-one"
    assert name_smiles("NC1C(C)CCCC1")["name"] == "2-methylcyclohexan-1-amine"


def test_multiple_and_lower_priority_ring_suffix_groups():
    assert name_smiles("OC1CC(O)CCC1")["name"] == "cyclohexane-1,3-diol"
    assert name_smiles("O=C1CC(=O)CCC1")["name"] == "cyclohexane-1,3-dione"
    assert name_smiles("NC1CC(N)CCC1")["name"] == "cyclohexane-1,3-diamine"
    assert name_smiles("O=C1CC(O)CCC1")["name"] == "3-hydroxycyclohexan-1-one"


def test_exocyclic_ring_suffixes():
    assert name_smiles("O=C(O)C1CCCCC1")["name"] == "cyclohexanecarboxylic acid"
    assert name_smiles("NC(=O)C1CC1")["name"] == "cyclopropanecarboxamide"
    assert name_smiles("N#CC1CCCCC1")["name"] == "cyclohexanecarbonitrile"
    assert name_smiles("O=CC1CCCCC1")["name"] == "cyclohexanecarbaldehyde"
    assert name_smiles("COC(=O)C1CCCCC1")["name"] == "methyl cyclohexanecarboxylate"
    assert name_smiles("O=C(Cl)C1CCCCC1")["name"] == "cyclohexanecarbonyl chloride"


def test_exocyclic_suffix_locant_completeness_and_multiplication():
    assert (
        name_smiles("O=C(O)C1CCC(=O)CC1")["name"]
        == "4-oxocyclohexane-1-carboxylic acid"
    )
    assert (
        name_smiles("N#CC1CCC(=O)C1")["name"]
        == "3-oxocyclopentane-1-carbonitrile"
    )
    assert (
        name_smiles("O=C(O)C1CC(C(=O)O)CCC1")["name"]
        == "cyclohexane-1,3-dicarboxylic acid"
    )


def test_acid_bearing_side_chain_does_not_become_a_ring_suffix():
    assert name_smiles("O=C(O)CC1CCCCC1")["name"] == "cyclohexylacetic acid"
    assert name_smiles("O=C(O)CCC1CCCC1")["name"] == "3-cyclopentylpropanoic acid"


def test_substituted_cycloalkyl_prefix_fails_closed():
    result = name_smiles("O=C(O)CC1CCCCC1C")
    assert result["status"] == "unsupported"
    assert "Substituted cycloalkyl" in result["reason"]


def test_complex_cyclic_substituents_do_not_flatten_into_acyclic_fragments():
    heterocycle = name_smiles("NCC[C@@H](O)[C@@H](C(=O)O)N1CCC1=O")
    nested_ring = name_smiles("O=C(O)CCCOCC1CCCCC1")
    assert heterocycle["status"] == "unsupported"
    assert "Heterocyclic" in heterocycle["reason"]
    assert nested_ring["status"] == "unsupported"
    assert "Nested" in nested_ring["reason"]


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


def test_four_alcohol_suffixes_contract_to_tetrol():
    assert name_smiles("OCC(O)C(O)CO")["name"] == "butane-1,2,3,4-tetrol"


def test_dicarboxylic_acid_numbering_prefers_unsaturation_after_suffix_locants():
    assert name_smiles("O=C(O)CCC(O)C=C(O)C(=O)O")["name"] == "2,4-dihydroxyhept-2-enedioic acid"


def test_parent_selection_prefers_multiple_bonds():
    assert name_smiles("C=C(C)C(=O)O")["name"] == "2-methylprop-2-enoic acid"


def test_functional_prefixes_participate_in_numbering():
    assert name_smiles("COCC(=O)CO")["name"] == "1-hydroxy-3-methoxypropan-2-one"


def test_prefix_citation_order_breaks_numbering_tie():
    assert name_smiles("ClCCBr")["name"] == "1-bromo-2-chloroethane"


def test_exocyclic_methylidene_prefix():
    assert name_smiles("C=C(CCC(=O)O)C(=O)O")["name"] == "2-methylidenepentanedioic acid"
    assert name_smiles("C=C(C(=O)O)C(C)C(=O)O")["name"] == "2-methyl-3-methylidenebutanedioic acid"


def test_simple_ester():
    assert name_smiles("CC(=O)OCC")["name"] == "ethyl ethanoate"


def test_dicarboxylic_diester():
    assert name_smiles("CCOC(=O)CCCC(=O)OCC")["name"] == "diethyl pentanedioate"


def test_attachment_aware_ester_alkyl_groups():
    assert name_smiles("CC(O)=CC(=O)OC(C)C")["name"] == "propan-2-yl 3-hydroxybut-2-enoate"
    assert name_smiles("CCCC(=O)OCC(C)C")["name"] == "2-methylpropyl butanoate"
    assert name_smiles("CCCC(=O)OCCC(C)C")["name"] == "3-methylbutyl butanoate"
    assert name_smiles("CCCC(=O)OC(C)CC")["name"] == "butan-2-yl butanoate"


def test_retained_tert_butyl_ester_prefix():
    assert name_smiles("CCCCCCCCCCCCCCCC(=O)OC(C)(C)C")["name"] == "tert-butyl hexadecanoate"


def test_attachment_aware_complex_alkyl_prefix():
    assert name_smiles("CC(C)C(O)(CC(=O)O)C(=O)O")["name"] == "2-hydroxy-2-(propan-2-yl)butanedioic acid"


def test_long_alkoxy_prefix_retains_yl():
    assert name_smiles("CCCCCCCCCCCCOCCO")["name"] == "2-(dodecyloxy)ethanol"


def test_complex_alkoxy_prefix_uses_nested_enclosure():
    assert name_smiles("CC(=O)COC(C)C")["name"] == "1-[(propan-2-yl)oxy]propan-2-one"


def test_repeated_complex_ester_organyl_uses_bis():
    smiles = "CCCCC(CC)COC(=O)CCCCC(=O)OCC(CC)CCCC"
    assert name_smiles(smiles)["name"] == "bis(2-ethylhexyl) hexanedioate"


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
    assert "N-substituted amide" in result["reason"]


def test_primary_amide_suffixes():
    assert name_smiles("CCC(N)=O")["name"] == "propanamide"
    assert name_smiles("NC(=O)CC(N)=O")["name"] == "propanediamide"


def test_lower_priority_amide_is_amino_and_oxo():
    assert name_smiles("NC(=O)CCCC(=O)O")["name"] == "5-amino-5-oxopentanoic acid"


def test_nitrile_suffixes():
    assert name_smiles("CCCC#N")["name"] == "butanenitrile"
    assert name_smiles("N#CCC#N")["name"] == "propanedinitrile"


def test_acid_halide_suffix():
    assert name_smiles("CCC(=O)Cl")["name"] == "propanoyl chloride"


def test_complete_halogen_substitution_elides_locants():
    assert name_smiles("FC(F)(F)C(F)(F)F")["name"] == "hexafluoroethane"


def test_icosane_parent_root():
    assert name_smiles("C" * 20)["name"] == "icosane"


def test_unsupported_guanidine_fails_closed():
    result = name_smiles("N=C(N)NC(=N)N")
    assert result["status"] == "unsupported"
    assert "guanidine" in result["reason"]


def test_complex_amine_substituent_fails_closed():
    result = name_smiles("O=C(O)CN(CCN(CC(=O)O)CC(=O)O)CC(=O)O")
    assert result["status"] == "unsupported"
    assert "Complex amine" in result["reason"]
