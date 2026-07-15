from __future__ import annotations


_UNITS = {
    1: "mono",
    2: "di",
    3: "tri",
    4: "tetra",
    5: "penta",
    6: "hexa",
    7: "hepta",
    8: "octa",
    9: "nona",
}

_COMPOSITE_UNITS = {
    1: "hen",
    2: "do",
    3: "tri",
    4: "tetra",
    5: "penta",
    6: "hexa",
    7: "hepta",
    8: "octa",
    9: "nona",
}

_TENS = {
    2: "icosa",
    3: "triaconta",
    4: "tetraconta",
    5: "pentaconta",
    6: "hexaconta",
    7: "heptaconta",
    8: "octaconta",
    9: "nonaconta",
}

_HUNDREDS = {
    1: "hecta",
    2: "dicta",
    3: "tricta",
    4: "tetracta",
    5: "pentacta",
    6: "hexacta",
    7: "heptacta",
    8: "octacta",
    9: "nonacta",
}


def numerical_term(number: int) -> str:
    """Return the basic numerical term defined by Blue Book Table 1.4."""
    if number < 1 or number > 999:
        raise ValueError("Numerical terms are currently implemented from 1 through 999")
    if number < 10:
        return _UNITS[number]
    if number == 10:
        return "deca"
    if number == 11:
        return "undeca"

    hundreds, remainder = divmod(number, 100)
    tens, units = divmod(remainder, 10)
    parts: list[str] = []

    if units:
        parts.append(_COMPOSITE_UNITS[units])

    if tens == 1:
        parts.append("deca")
    elif tens:
        tens_term = _TENS[tens]
        if parts and parts[-1][-1] in "aeiou" and tens_term.startswith("i"):
            tens_term = tens_term[1:]
        parts.append(tens_term)

    if hundreds:
        parts.append(_HUNDREDS[hundreds])

    return "".join(parts)


def parent_root(carbon_count: int) -> str:
    retained = {1: "meth", 2: "eth", 3: "prop", 4: "but"}
    if carbon_count in retained:
        return retained[carbon_count]
    term = numerical_term(carbon_count)
    return term[:-1] if term.endswith("a") else term


def multiplicative_prefix(count: int) -> str:
    if count < 2:
        return ""
    return numerical_term(count)
