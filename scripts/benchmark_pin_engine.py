from __future__ import annotations

import argparse
import hashlib
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_bluebook_pin_benchmark import (
        DEFAULT_OPSIN,
        DEFAULT_OUTPUT as DEFAULT_DATASET_DIR,
        canonical_json_bytes,
        run_opsin_smiles,
    )
else:
    from build_bluebook_pin_benchmark import (  # type: ignore[no-redef]
        DEFAULT_OPSIN,
        DEFAULT_OUTPUT as DEFAULT_DATASET_DIR,
        canonical_json_bytes,
        run_opsin_smiles,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "work" / "benchmarks" / "pin_engine"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def normalized_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).split())


def load_cases(path: Path, split: str) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if split != "all":
        cases = [case for case in cases if case["split"] == split]
    return cases


def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from iupac_engine import name_smiles

    cases = load_cases(args.cases, args.split)
    if args.max_cases is not None:
        cases = cases[: args.max_cases]
    generated_names = []
    engine_results = []
    for case in cases:
        result = name_smiles(case["smiles"], explain=True)
        engine_results.append(result)
        if isinstance(result.get("name"), str):
            generated_names.append(result["name"])
    unique_generated = sorted(set(generated_names))
    parsed_generated, opsin_metrics = run_opsin_smiles(
        unique_generated, args.opsin_jar, args.output_dir, "engine-generated"
    )

    from rdkit import Chem, RDLogger
    from rdkit.Chem import inchi

    RDLogger.DisableLog("rdApp.*")
    generated_keys = {}
    for name, smiles in parsed_generated.items():
        molecule = Chem.MolFromSmiles(smiles) if smiles else None
        generated_keys[name] = inchi.MolToInchiKey(molecule) if molecule is not None else ""

    outcomes: Counter[str] = Counter()
    failures_by_rule: dict[str, Counter[str]] = defaultdict(Counter)
    rows = []
    for case, result in zip(cases, engine_results):
        expected_name = case["preferred_iupac_name"]
        generated_name = result.get("name")
        trace_rule_ids = [
            step.get("rule_id")
            for step in result.get("decision_trace", [])
            if isinstance(step, Mapping) and isinstance(step.get("rule_id"), str)
        ]
        generated_key = generated_keys.get(generated_name, "") if generated_name else ""
        if result.get("status") != "success":
            outcome = "unsupported"
        elif normalized_name(str(generated_name)) == normalized_name(expected_name):
            outcome = "exact_pin_match"
        elif generated_key and generated_key == case["standard_inchikey"]:
            outcome = "structure_match_but_not_pin"
        elif generated_key and generated_key.split("-", 1)[0] == case["standard_inchikey"].split("-", 1)[0]:
            outcome = "connectivity_match_stereo_or_protonation_differs"
        elif generated_key:
            outcome = "generated_name_has_wrong_structure"
        else:
            outcome = "generated_name_unparseable"
        outcomes[outcome] += 1
        if outcome != "exact_pin_match":
            for rule_id in trace_rule_ids or ["unlocalized"]:
                failures_by_rule[rule_id][outcome] += 1
        rows.append(
            {
                "case_id": case["case_id"],
                "split": case["split"],
                "smiles": case["smiles"],
                "expected_pin": expected_name,
                "generated_name": generated_name,
                "outcome": outcome,
                "engine_status": result.get("status"),
                "engine_reason": result.get("reason"),
                "expected_inchikey": case["standard_inchikey"],
                "generated_inchikey": generated_key,
                "trace_rule_ids": trace_rule_ids,
                "decision_trace": result.get("decision_trace", []),
                "source_rule_ids": sorted(
                    {item["rule_id"] for item in case["occurrences"]}
                ),
            }
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results_path = args.output_dir / f"results-{args.split}.jsonl"
    results_bytes = b"".join(canonical_json_bytes(row) for row in rows)
    results_path.write_bytes(results_bytes)
    exact = outcomes["exact_pin_match"]
    report = {
        "format": "iupac-pin-engine-benchmark",
        "format_version": "1.0.0",
        "split": args.split,
        "case_count": len(cases),
        "exact_pin_match_count": exact,
        "exact_pin_match_rate": round(exact / max(len(cases), 1), 6),
        "outcomes": dict(sorted(outcomes.items())),
        "opsin_generated_name_round_trip": opsin_metrics,
        "failures_by_trace_rule": {
            rule_id: dict(sorted(counts.items()))
            for rule_id, counts in sorted(failures_by_rule.items())
        },
        "cases_sha256": hashlib.sha256(args.cases.read_bytes()).hexdigest().upper(),
        "results_path": str(results_path),
        "results_sha256": hashlib.sha256(results_bytes).hexdigest().upper(),
    }
    (args.output_dir / f"report-{args.split}.json").write_bytes(
        canonical_json_bytes(report)
    )
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the deterministic engine on Blue Book PIN cases")
    parser.add_argument("--cases", type=Path, default=DEFAULT_DATASET_DIR / "cases.jsonl")
    parser.add_argument("--opsin-jar", type=Path, default=DEFAULT_OPSIN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--split",
        choices=("calibration", "holdout", "final_holdout", "all"),
        default="calibration",
    )
    parser.add_argument("--max-cases", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    report = benchmark(parse_args(argv))
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
