from __future__ import annotations

from .model import Atom, Bond, Molecule


class SmilesError(ValueError):
    pass


ORGANIC_ATOMS = ("Cl", "Br", "C", "N", "O", "F", "I")


def parse_smiles(smiles: str) -> Molecule:
    if not smiles:
        raise SmilesError("Empty SMILES")
    if "." in smiles:
        raise SmilesError("Disconnected structures are outside the current scope")

    molecule = Molecule()
    stack: list[int] = []
    current: int | None = None
    pending_order = 1
    index = 0

    while index < len(smiles):
        char = smiles[index]

        if char in "-=#":
            pending_order = {"-": 1, "=": 2, "#": 3}[char]
            index += 1
            continue

        if char == "(":
            if current is None:
                raise SmilesError("Branch cannot start before an atom")
            stack.append(current)
            index += 1
            continue

        if char == ")":
            if not stack:
                raise SmilesError("Unmatched branch close")
            current = stack.pop()
            index += 1
            continue

        if char.isdigit():
            raise SmilesError("Ring closures are outside the current scope")

        if char == "[":
            raise SmilesError("Bracket atoms, charges, isotopes, and explicit stereochemistry are outside the current scope")

        element = _read_atom(smiles, index)
        if element is None:
            if char.islower():
                raise SmilesError("Aromatic atoms are outside the current scope")
            raise SmilesError(f"Unsupported token at position {index}: {char!r}")

        atom_id = len(molecule.atoms)
        molecule.atoms.append(Atom(atom_id, element))
        if current is not None:
            molecule.bonds.append(Bond(current, atom_id, pending_order))
        current = atom_id
        pending_order = 1
        index += len(element)

    if stack:
        raise SmilesError("Unclosed branch")
    return molecule


def _read_atom(smiles: str, index: int) -> str | None:
    for symbol in ORGANIC_ATOMS:
        if smiles.startswith(symbol, index):
            return symbol
    return None
