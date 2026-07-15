from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from iupac_engine import name_smiles


DEFAULT_CASES = [
    ("C", "methane", "parent hydride alkane"),
    ("CC", "ethane", "parent hydride alkane"),
    ("CCC", "propane", "parent hydride alkane"),
    ("CCCC", "butane", "parent hydride alkane"),
    ("C=C", "ethene", "simple alkene"),
    ("C#C", "ethyne", "simple alkyne"),
    ("CCO", "ethan-1-ol", "simple alcohol"),
    ("CC(C)O", "propan-2-ol", "branched alcohol"),
    ("CCC(=O)C", "butan-2-one", "simple ketone"),
    ("CC=O", "ethanal", "simple aldehyde"),
    ("CC(=O)O", "ethanoic acid", "simple carboxylic acid"),
    ("CC(C)Cl", "2-chloropropane", "haloalkane"),
    ("CCN", "ethan-1-amine", "simple amine"),
    ("CC(C)C", "2-methylpropane", "branched alkane"),
]


@dataclass(frozen=True)
class TestResult:
    smiles: str
    expected: str
    actual: str | None
    status: str
    passed: bool
    area: str
    reason: str | None


def run_cases() -> list[TestResult]:
    results = []
    for smiles, expected, area in DEFAULT_CASES:
        response = name_smiles(smiles)
        actual = response.get("name")
        status = str(response.get("status"))
        results.append(
            TestResult(
                smiles=smiles,
                expected=expected,
                actual=actual,
                status=status,
                passed=status == "success" and actual == expected,
                area=area,
                reason=response.get("reason"),
            )
        )
    return results


def write_markdown(results: list[TestResult], path: Path) -> None:
    passed = sum(1 for result in results if result.passed)
    lines = [
        "# IUPAC Prototype Test Audit Log",
        "",
        "Scope: example smoke/conformance harness for the current prototype naming path.",
        "",
        f"Total cases: {len(results)}",
        f"Passed: {passed}",
        f"Failed: {len(results) - passed}",
        "",
        "| SMILES | Expected | Actual | Status | Pass | Area | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for result in results:
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                result.smiles,
                result.expected,
                result.actual or "null",
                result.status,
                result.passed,
                result.area,
                result.reason or "null",
            )
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run example IUPAC naming tests against the prototype engine")
    parser.add_argument("--json", action="store_true", help="print JSON instead of text summary")
    parser.add_argument("--markdown-out", type=Path, help="write a markdown audit log")
    args = parser.parse_args()

    results = run_cases()
    payload = {
        "total": len(results),
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [asdict(result) for result in results],
    }

    if args.markdown_out:
        write_markdown(results, args.markdown_out)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"total={payload['total']} passed={payload['passed']} failed={payload['failed']}")
        for result in results:
            marker = "PASS" if result.passed else "FAIL"
            print(f"{marker} {result.smiles}: expected={result.expected} actual={result.actual}")
    return 0 if payload["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
