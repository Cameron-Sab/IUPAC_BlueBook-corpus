from iupac_engine.numeric import numerical_term, parent_root


def test_blue_book_table_1_4_numerical_terms():
    assert numerical_term(14) == "tetradeca"
    assert numerical_term(20) == "icosa"
    assert numerical_term(21) == "henicosa"
    assert numerical_term(22) == "docosa"
    assert numerical_term(31) == "hentriaconta"
    assert numerical_term(486) == "hexaoctacontatetracta"


def test_numerical_term_becomes_parent_root():
    assert parent_root(20) == "icos"
    assert parent_root(31) == "hentriacont"
