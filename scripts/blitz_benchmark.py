from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from iupac_engine import name_smiles


BUILTIN_CASES = [
    {"id": "bb-smoke-001", "smiles": "C", "expected_name": "methane", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-002", "smiles": "CC", "expected_name": "ethane", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-003", "smiles": "CCC", "expected_name": "propane", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-004", "smiles": "CCCC", "expected_name": "butane", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-005", "smiles": "C=C", "expected_name": "ethene", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-006", "smiles": "C#C", "expected_name": "ethyne", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-007", "smiles": "CCO", "expected_name": "ethan-1-ol", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-008", "smiles": "CC(C)O", "expected_name": "propan-2-ol", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-009", "smiles": "CCC(=O)C", "expected_name": "butan-2-one", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-010", "smiles": "CC=O", "expected_name": "ethanal", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-011", "smiles": "CC(=O)O", "expected_name": "ethanoic acid", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-012", "smiles": "CC(C)Cl", "expected_name": "2-chloropropane", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-013", "smiles": "CCN", "expected_name": "ethan-1-amine", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-smoke-014", "smiles": "CC(C)C", "expected_name": "2-methylpropane", "source": "built_in_smoke", "confidence": "smoke"},
    {"id": "bb-negative-001", "smiles": "C1CCCCC1", "expected_name": None, "source": "built_in_negative", "confidence": "unsupported_expected"},
    {"id": "bb-negative-002", "smiles": "c1ccccc1", "expected_name": None, "source": "built_in_negative", "confidence": "unsupported_expected"},
    {"id": "bb-negative-003", "smiles": "CC.O", "expected_name": None, "source": "built_in_negative", "confidence": "unsupported_expected"},
]


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    smiles: str
    expected_name: str | None
    source: str
    confidence: str


@dataclass(frozen=True)
class BenchmarkResult:
    id: str
    smiles: str
    expected_name: str | None
    actual_name: str | None
    status: str
    passed: bool
    failure_stage: str | None
    source: str
    confidence: str
    reason: str | None


def load_cases(path: Path | None, limit: int | None) -> list[BenchmarkCase]:
    raw_cases = BUILTIN_CASES if path is None else load_tabular_cases(path)
    cases = [
        BenchmarkCase(
            id=str(case.get("id") or f"case-{index:06d}"),
            smiles=str(case.get("smiles") or case.get("SMILES") or "").strip(),
            expected_name=clean_expected(case.get("expected_name") or case.get("name") or case.get("iupac_name") or case.get("IUPAC Name")),
            source=str((case.get("source") if isinstance(case, dict) else None) or (path.name if path else "built_in")),
            confidence=str((case.get("confidence") if isinstance(case, dict) else None) or "external"),
        )
        for index, case in enumerate(raw_cases, start=1)
    ]
    cases = [case for case in cases if case.smiles]
    return cases[:limit] if limit else cases


def load_tabular_cases(path: Path) -> list[dict[str, Any]]:
    delimiter = "\t" if path.suffix.lower() in {".tsv", ".tab"} else ","
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def clean_expected(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def run_case(case: BenchmarkCase) -> BenchmarkResult:
    response = name_smiles(case.smiles)
    actual = response.get("name")
    status = str(response.get("status"))
    reason = response.get("reason")
    if case.expected_name is None:
        passed = status == "unsupported"
    else:
        passed = status == "success" and normalize_name(str(actual)) == normalize_name(case.expected_name)
    return BenchmarkResult(
        id=case.id,
        smiles=case.smiles,
        expected_name=case.expected_name,
        actual_name=actual,
        status=status,
        passed=passed,
        failure_stage=None if passed else classify_failure(status, reason, actual, case.expected_name),
        source=case.source,
        confidence=case.confidence,
        reason=reason,
    )


def normalize_name(name: str | None) -> str:
    if not name:
        return ""
    return " ".join(name.lower().replace("‐", "-").replace("‑", "-").split())


def classify_failure(status: str, reason: Any, actual: Any, expected: Any) -> str:
    if status == "unsupported":
        text = str(reason or "").lower()
        if "ring" in text or "aromatic" in text:
            return "unsupported_scope_ring_or_aromatic"
        if "bracket" in text or "stereochemistry" in text or "isotope" in text or "charge" in text:
            return "unsupported_scope_bracket_charge_isotope_stereo"
        if "disconnected" in text:
            return "unsupported_scope_disconnected"
        return "unsupported_scope_other"
    if status != "success":
        return "engine_error"
    if actual != expected:
        return "name_mismatch"
    return "unknown"


def summarize(results: list[BenchmarkResult]) -> dict[str, Any]:
    summary: dict[str, Any] = {"total": len(results), "passed": 0, "failed": 0, "by_source": {}, "by_failure_stage": {}}
    for result in results:
        summary["passed" if result.passed else "failed"] += 1
        source_bucket = summary["by_source"].setdefault(result.source, {"total": 0, "passed": 0, "failed": 0})
        source_bucket["total"] += 1
        source_bucket["passed" if result.passed else "failed"] += 1
        if not result.passed:
            stage = result.failure_stage or "unknown"
            summary["by_failure_stage"][stage] = summary["by_failure_stage"].get(stage, 0) + 1
    return summary


def write_outputs(results: list[BenchmarkResult], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    payload = {"summary": summary, "results": [asdict(result) for result in results]}
    (out_dir / "blitz_results.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / "blitz_report.md").write_text(render_markdown(summary, results), encoding="utf-8")


def render_markdown(summary: dict[str, Any], results: list[BenchmarkResult]) -> str:
    lines = [
        "# Blitz Benchmark Report",
        "",
        f"Total: {summary['total']}",
        f"Passed: {summary['passed']}",
        f"Failed: {summary['failed']}",
        "",
        "## Failure Stages",
        "",
    ]
    if summary["by_failure_stage"]:
        for stage, count in sorted(summary["by_failure_stage"].items()):
            lines.append(f"- `{stage}`: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Results", "", "| ID | SMILES | Expected | Actual | Status | Pass | Failure Stage | Source |", "|---|---|---|---|---|---|---|---|"])
    for result in results:
        lines.append(
            f"| {result.id} | `{result.smiles}` | {result.expected_name or 'unsupported'} | {result.actual_name or 'null'} | {result.status} | {result.passed} | {result.failure_stage or ''} | {result.source} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Blitz benchmark current prototype against name/SMILES datasets")
    parser.add_argument("--input", type=Path, help="CSV/TSV with smiles and expected_name/name/iupac_name columns")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--out-dir", type=Path, default=PROJECT_ROOT / "benchmark_results")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    cases = load_cases(args.input, args.limit)
    results = [run_case(case) for case in cases]
    write_outputs(results, args.out_dir)
    summary = summarize(results)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"total={summary['total']} passed={summary['passed']} failed={summary['failed']}")
        for stage, count in sorted(summary["by_failure_stage"].items()):
            print(f"{stage}: {count}")
        print(f"wrote {args.out_dir}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
