from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOWNLOADS = Path.home() / "Downloads"
DEFAULT_COMPOUNDS = DEFAULT_DOWNLOADS / "compounds.tsv.gz"
DEFAULT_NAMES = DEFAULT_DOWNLOADS / "names.tsv.gz"
DEFAULT_STRUCTURES = DEFAULT_DOWNLOADS / "structures.tsv.gz"
DEFAULT_OPSIN = (
    ROOT
    / ".cache"
    / "benchmarks"
    / "opsin-cli-2.9.0-jar-with-dependencies.jar"
)
DEFAULT_OUTPUT = ROOT / "work" / "benchmarks" / "chebi_iupac"

FEATURE_PATTERNS = {
    "acid": re.compile(r"\bacid\b", re.IGNORECASE),
    "charged_or_ionic": re.compile(
        r"(?:\b(?:anion|cation|zwitterion|salt)\b|(?:ium|ide|ate)(?:\b|\())",
        re.IGNORECASE,
    ),
    "heterocycle": re.compile(
        r"(?:aza|oxa|thia|selena|tellura|phospha|bor|pyrid|pyrrol|furan|thiophen|azole|azine)",
        re.IGNORECASE,
    ),
    "isotopic": re.compile(
        r"(?:\[(?:1[0-9]|2[0-9]|3[0-9])[A-Z]|\b(?:deuter|triti))",
        re.IGNORECASE,
    ),
    "multiplicative": re.compile(
        r"(?:\b(?:bis|tris|tetrakis|pentakis|hexakis)\b|\b(?:di|tri|tetra|penta|hexa)[a-z])",
        re.IGNORECASE,
    ),
    "ring_system": re.compile(
        r"(?:cyclo|bicyclo|tricyclo|spiro|naphth|anthrac|phenanthr|indol|benz)",
        re.IGNORECASE,
    ),
    "stereochemistry": re.compile(
        r"(?:\([^)]*(?:[0-9][RSZE](?=[,)])|alpha|beta|rel)[^)]*\)|"
        r"\b(?:cis|trans|alpha|beta)-)",
        re.IGNORECASE,
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def open_tsv(path: Path) -> Iterable[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", errors="strict", newline="") as handle:
        yield from csv.DictReader(handle, delimiter="\t")


def load_active_three_star_compounds(path: Path) -> tuple[set[str], dict[str, Any]]:
    selected: set[str] = set()
    rows = 0
    statuses: Counter[tuple[str, str]] = Counter()
    for row in open_tsv(path):
        rows += 1
        statuses[(row["status_id"], row["stars"])] += 1
        if row["status_id"] == "1" and row["stars"] == "3":
            selected.add(row["id"])
    return selected, {
        "row_count": rows,
        "status_star_counts": {
            f"status={status};stars={stars}": count
            for (status, stars), count in sorted(statuses.items())
        },
        "active_three_star_compound_count": len(selected),
    }


def load_default_structures(
    path: Path, selected_compounds: set[str]
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    structures: dict[str, dict[str, str]] = {}
    duplicate_defaults: list[str] = []
    rows = 0
    selected_rows = 0
    for row in open_tsv(path):
        rows += 1
        compound_id = row["compound_id"]
        if (
            compound_id not in selected_compounds
            or row["status_id"] != "1"
            or row["default_structure"].lower() != "true"
        ):
            continue
        selected_rows += 1
        if compound_id in structures:
            duplicate_defaults.append(compound_id)
            continue
        structures[compound_id] = {
            "smiles": row["smiles"],
            "standard_inchi": row["standard_inchi"],
            "standard_inchi_key": row["standard_inchi_key"],
        }
    return structures, {
        "row_count": rows,
        "selected_default_structure_row_count": selected_rows,
        "selected_default_structure_compound_count": len(structures),
        "duplicate_default_structure_compound_count": len(set(duplicate_defaults)),
        "duplicate_default_structure_compound_ids": sorted(
            set(duplicate_defaults), key=int
        )[:100],
    }


def load_iupac_names(
    path: Path, selected_compounds: set[str]
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    names: list[dict[str, str]] = []
    rows = 0
    type_status_counts: Counter[tuple[str, str]] = Counter()
    rejected_newlines = 0
    for row in open_tsv(path):
        rows += 1
        type_status_counts[(row["type"], row["status_id"])] += 1
        if (
            row["compound_id"] not in selected_compounds
            or row["type"] != "IUPAC NAME"
            or row["status_id"] != "1"
            or row["language_code"] != "en"
        ):
            continue
        name = row["ascii_name"] or row["name"]
        if "\n" in name or "\r" in name:
            rejected_newlines += 1
            continue
        names.append(
            {
                "name_id": row["id"],
                "compound_id": row["compound_id"],
                "name": name,
                "display_name": row["name"],
            }
        )
    names.sort(key=lambda item: (int(item["compound_id"]), item["name"], int(item["name_id"])))
    return names, {
        "row_count": rows,
        "iupac_active_english_name_row_count": len(names),
        "rejected_embedded_newline_count": rejected_newlines,
        "type_status_counts": {
            f"type={kind};status={status}": count
            for (kind, status), count in sorted(type_status_counts.items())
        },
    }


def validate_structures(
    structures: Mapping[str, Mapping[str, str]],
) -> tuple[dict[str, str], dict[str, Any]]:
    from rdkit import Chem, RDLogger
    from rdkit.Chem import inchi

    RDLogger.DisableLog("rdApp.*")
    parse_failures: list[str] = []
    key_mismatches: list[dict[str, str]] = []
    missing_keys: list[str] = []
    statuses: dict[str, str] = {}
    for compound_id, structure in structures.items():
        expected_key = structure["standard_inchi_key"]
        if not expected_key:
            missing_keys.append(compound_id)
            statuses[compound_id] = "missing_expected_inchikey"
        molecule = Chem.MolFromSmiles(structure["smiles"])
        if molecule is None:
            parse_failures.append(compound_id)
            statuses[compound_id] = "rdkit_smiles_parse_failure"
            continue
        generated_key = inchi.MolToInchiKey(molecule)
        if expected_key and generated_key != expected_key:
            key_mismatches.append(
                {
                    "compound_id": compound_id,
                    "expected_key": expected_key,
                    "rdkit_key": generated_key,
                }
            )
            statuses[compound_id] = "rdkit_inchikey_mismatch"
        elif expected_key:
            statuses[compound_id] = "rdkit_inchikey_verified"
    return statuses, {
        "tested_structure_count": len(structures),
        "rdkit_smiles_parse_success_count": len(structures) - len(parse_failures),
        "rdkit_smiles_parse_failure_count": len(parse_failures),
        "rdkit_inchikey_exact_match_count": (
            len(structures) - len(parse_failures) - len(key_mismatches) - len(missing_keys)
        ),
        "rdkit_inchikey_mismatch_count": len(key_mismatches),
        "missing_expected_inchikey_count": len(missing_keys),
        "parse_failure_compound_ids": parse_failures[:100],
        "inchikey_mismatch_examples": key_mismatches[:100],
    }


def run_opsin(
    names: Sequence[str],
    jar: Path,
    work_dir: Path,
    label: str,
    extra_args: Sequence[str] = (),
) -> tuple[dict[str, str], dict[str, Any]]:
    input_path = work_dir / f"opsin-{label}.input.txt"
    output_path = work_dir / f"opsin-{label}.output.tsv"
    stderr_path = work_dir / f"opsin-{label}.stderr.txt"
    input_bytes = ("\n".join(names) + "\n").encode("utf-8")
    cached = (
        input_path.exists()
        and output_path.exists()
        and input_path.read_bytes() == input_bytes
    )
    input_path.write_bytes(input_bytes)
    command = [
        "java",
        "-jar",
        str(jar),
        "-o",
        "stdinchikey",
        "-n",
        *extra_args,
        str(input_path),
        str(output_path),
    ]
    if cached:
        duration = 0.0
    else:
        started = time.monotonic()
        with stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
            process = subprocess.run(command, stderr=stderr, check=False)
        duration = time.monotonic() - started
        if process.returncode != 0:
            raise RuntimeError(
                f"OPSIN {label} exited with {process.returncode}; see {stderr_path}"
            )
    lines = output_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != len(names):
        raise RuntimeError(
            f"OPSIN {label} output line count differs: {len(lines)} != {len(names)}"
        )
    results: dict[str, str] = {}
    for expected_name, line in zip(names, lines):
        key, separator, returned_name = line.partition("\t")
        if not separator or returned_name != expected_name:
            raise RuntimeError(f"OPSIN {label} output lost name alignment")
        results[expected_name] = key
    return results, {
        "input_name_count": len(names),
        "parsed_name_count": sum(bool(value) for value in results.values()),
        "failed_name_count": sum(not value for value in results.values()),
        "duration_seconds": round(duration, 3),
        "used_cached_output": cached,
        "extra_args": list(extra_args),
        "stderr_path": str(stderr_path),
    }


def name_features(name: str) -> list[str]:
    features = [label for label, pattern in FEATURE_PATTERNS.items() if pattern.search(name)]
    return features or ["other"]


def classify_result(expected_key: str, opsin_key: str) -> str:
    if not opsin_key:
        return "parse_failure"
    if not expected_key:
        return "missing_expected_key"
    if opsin_key == expected_key:
        return "exact_inchikey_match"
    if opsin_key.split("-", 1)[0] == expected_key.split("-", 1)[0]:
        return "connectivity_match_stereo_or_protonation_differs"
    return "connectivity_mismatch"


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def write_cases(path: Path, cases: Sequence[Mapping[str, Any]]) -> None:
    fields = [
        "compound_id",
        "name_id",
        "name",
        "expected_inchikey",
        "strict_opsin_inchikey",
        "permissive_opsin_inchikey",
        "strict_result",
        "permissive_result",
        "features",
        "dataset_structure_status",
    ]
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(cases)


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    for path in (args.compounds, args.names, args.structures, args.opsin_jar):
        if not path.is_file():
            raise FileNotFoundError(path)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    source_files = {
        label: {
            "path": str(path),
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for label, path in (
            ("compounds", args.compounds),
            ("names", args.names),
            ("structures", args.structures),
            ("opsin_jar", args.opsin_jar),
        )
    }
    compounds, compound_metrics = load_active_three_star_compounds(args.compounds)
    structures, structure_metrics = load_default_structures(args.structures, compounds)
    names, name_metrics = load_iupac_names(args.names, compounds)
    all_eligible = [name for name in names if name["compound_id"] in structures]
    all_eligible_compounds = {item["compound_id"] for item in all_eligible}
    eligible = all_eligible
    if args.max_names is not None:
        eligible = eligible[: args.max_names]
    eligible_compounds = {item["compound_id"] for item in eligible}
    selected_structures = {
        compound_id: structures[compound_id] for compound_id in eligible_compounds
    }
    structure_statuses, rdkit_metrics = validate_structures(selected_structures)

    expected_keys_by_name: dict[str, set[str]] = defaultdict(set)
    for item in eligible:
        expected_keys_by_name[item["name"]].add(
            structures[item["compound_id"]]["standard_inchi_key"]
        )
    unique_names = sorted(expected_keys_by_name)
    strict_results, strict_metrics = run_opsin(
        unique_names, args.opsin_jar, args.output_dir, "strict"
    )
    strict_failures = [name for name in unique_names if not strict_results[name]]
    permissive_results: dict[str, str] = {}
    if strict_failures:
        permissive_results, permissive_metrics = run_opsin(
            strict_failures,
            args.opsin_jar,
            args.output_dir,
            "permissive-failures",
            ("-a", "-r", "-s"),
        )
    else:
        permissive_metrics = {
            "input_name_count": 0,
            "parsed_name_count": 0,
            "failed_name_count": 0,
            "duration_seconds": 0.0,
            "extra_args": ["-a", "-r", "-s"],
        }

    cases = []
    strict_counts: Counter[str] = Counter()
    permissive_counts: Counter[str] = Counter()
    category_counts: dict[str, Counter[str]] = defaultdict(Counter)
    structure_status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in eligible:
        expected_key = structures[item["compound_id"]]["standard_inchi_key"]
        strict_key = strict_results[item["name"]]
        permissive_key = strict_key or permissive_results.get(item["name"], "")
        strict_result = classify_result(expected_key, strict_key)
        permissive_result = classify_result(expected_key, permissive_key)
        features = name_features(item["name"])
        strict_counts[strict_result] += 1
        permissive_counts[permissive_result] += 1
        for feature in features:
            category_counts[feature][strict_result] += 1
        structure_status = structure_statuses[item["compound_id"]]
        structure_status_counts[structure_status][strict_result] += 1
        cases.append(
            {
                "compound_id": item["compound_id"],
                "name_id": item["name_id"],
                "name": item["name"],
                "expected_inchikey": expected_key,
                "strict_opsin_inchikey": strict_key,
                "permissive_opsin_inchikey": permissive_key,
                "strict_result": strict_result,
                "permissive_result": permissive_result,
                "features": ",".join(features),
                "dataset_structure_status": structure_status,
            }
        )
    write_cases(args.output_dir / "cases.tsv.gz", cases)

    scorable_pair_count = sum(bool(case["expected_inchikey"]) for case in cases)
    strict_parsed_scorable_count = sum(
        strict_counts[key]
        for key in (
            "exact_inchikey_match",
            "connectivity_match_stereo_or_protonation_differs",
            "connectivity_mismatch",
        )
    )
    strict_connectivity_match_count = (
        strict_counts["exact_inchikey_match"]
        + strict_counts["connectivity_match_stereo_or_protonation_differs"]
    )
    verified_counts = structure_status_counts["rdkit_inchikey_verified"]
    verified_pair_count = sum(verified_counts.values())
    verified_parsed_count = sum(
        verified_counts[key]
        for key in (
            "exact_inchikey_match",
            "connectivity_match_stereo_or_protonation_differs",
            "connectivity_mismatch",
        )
    )
    verified_connectivity_match_count = (
        verified_counts["exact_inchikey_match"]
        + verified_counts["connectivity_match_stereo_or_protonation_differs"]
    )

    ambiguous_names = {
        name: sorted(keys)
        for name, keys in expected_keys_by_name.items()
        if len(keys) > 1
    }
    report: dict[str, Any] = {
        "format": "iupac-chebi-name-structure-benchmark-report",
        "format_version": "1.0.0",
        "scope": (
            "Active status=1, three-star ChEBI compounds; active English IUPAC NAME "
            "rows; active default structures"
        ),
        "source_files": source_files,
        "compound_table": compound_metrics,
        "name_table": name_metrics,
        "structure_table": structure_metrics,
        "selection": {
            "available_name_structure_pair_count": len(all_eligible),
            "available_compound_count": len(all_eligible_compounds),
            "benchmark_name_structure_pair_count": len(eligible),
            "benchmark_compound_count": len(eligible_compounds),
            "benchmark_unique_iupac_name_count": len(unique_names),
            "compound_without_active_default_structure_count": len(compounds - set(structures)),
            "active_three_star_compound_without_eligible_iupac_name_count": len(
                compounds - all_eligible_compounds
            ),
            "exact_name_with_multiple_expected_structure_count": len(ambiguous_names),
            "ambiguous_name_examples": dict(list(sorted(ambiguous_names.items()))[:100]),
            "max_names": args.max_names,
        },
        "dataset_structure_validation": rdkit_metrics,
        "score_summary": {
            "scorable_pair_count_with_expected_inchikey": scorable_pair_count,
            "strict_parsed_scorable_pair_count": strict_parsed_scorable_count,
            "strict_parse_rate_on_scorable_pairs": ratio(
                strict_parsed_scorable_count, scorable_pair_count
            ),
            "strict_exact_match_rate_on_all_scorable_pairs": ratio(
                strict_counts["exact_inchikey_match"], scorable_pair_count
            ),
            "strict_connectivity_match_rate_on_all_scorable_pairs": ratio(
                strict_connectivity_match_count, scorable_pair_count
            ),
            "strict_connectivity_match_rate_when_parsed": ratio(
                strict_connectivity_match_count, strict_parsed_scorable_count
            ),
            "strict_connectivity_mismatch_rate_when_parsed": ratio(
                strict_counts["connectivity_mismatch"], strict_parsed_scorable_count
            ),
            "high_confidence_rdkit_verified_pair_count": verified_pair_count,
            "high_confidence_strict_parse_rate": ratio(
                verified_parsed_count, verified_pair_count
            ),
            "high_confidence_strict_exact_match_rate": ratio(
                verified_counts["exact_inchikey_match"], verified_pair_count
            ),
            "high_confidence_strict_connectivity_match_rate": ratio(
                verified_connectivity_match_count, verified_pair_count
            ),
            "high_confidence_connectivity_match_rate_when_parsed": ratio(
                verified_connectivity_match_count, verified_parsed_count
            ),
            "high_confidence_connectivity_mismatch_rate_when_parsed": ratio(
                verified_counts["connectivity_mismatch"], verified_parsed_count
            ),
        },
        "opsin_strict": {
            **strict_metrics,
            "pair_result_counts": dict(sorted(strict_counts.items())),
        },
        "opsin_permissive_retry_of_strict_failures": {
            **permissive_metrics,
            "pair_result_counts_after_retry": dict(sorted(permissive_counts.items())),
        },
        "strict_results_by_feature": {
            feature: {
                "pair_count": sum(counts.values()),
                **dict(sorted(counts.items())),
            }
            for feature, counts in sorted(category_counts.items())
        },
        "strict_results_by_dataset_structure_status": {
            status: {
                "pair_count": sum(counts.values()),
                **dict(sorted(counts.items())),
            }
            for status, counts in sorted(structure_status_counts.items())
        },
        "artifacts": {
            "case_log": str(args.output_dir / "cases.tsv.gz"),
            "strict_opsin_stderr": strict_metrics.get("stderr_path"),
            "permissive_opsin_stderr": permissive_metrics.get("stderr_path"),
        },
    }
    report_without_hash = json.dumps(
        report, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    report["report_sha256"] = hashlib.sha256(report_without_hash).hexdigest().upper()
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark curated ChEBI IUPAC names against their structures"
    )
    parser.add_argument("--compounds", type=Path, default=DEFAULT_COMPOUNDS)
    parser.add_argument("--names", type=Path, default=DEFAULT_NAMES)
    parser.add_argument("--structures", type=Path, default=DEFAULT_STRUCTURES)
    parser.add_argument("--opsin-jar", type=Path, default=DEFAULT_OPSIN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-names", type=int)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_report(args)
    report_path = args.output_dir / "report.json"
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
