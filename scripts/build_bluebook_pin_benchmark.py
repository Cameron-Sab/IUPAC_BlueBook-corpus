from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import time
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NODES = ROOT / "data" / "bluebook_v3" / "bluebook_v3_document_nodes"
DEFAULT_OPSIN = ROOT / ".cache" / "benchmarks" / "opsin-cli-2.9.0-jar-with-dependencies.jar"
DEFAULT_OUTPUT = ROOT / "work" / "benchmarks" / "bluebook_pin"
PIN_MARKER = re.compile(r"\(\s*PIN\b[^)]*\)", re.IGNORECASE)
ENUMERATION = re.compile(r"^\s*\([0-9ivx]+\)\s+", re.IGNORECASE)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def iter_text_fields(value: Any, path: tuple[str, ...] = ()) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = (*path, str(key))
            if key in {"text", "caption"} and isinstance(child, str):
                yield "/".join(child_path), child
            elif key not in {"field_sources", "source"}:
                yield from iter_text_fields(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_text_fields(child, (*path, str(index)))


def extract_pin_names(text: str) -> list[str]:
    names = []
    for raw_line in text.splitlines():
        line = unicodedata.normalize("NFC", raw_line).strip()
        for marker in PIN_MARKER.finditer(line):
            candidate = ENUMERATION.sub("", line[: marker.start()].strip())
            if ":" in candidate:
                candidate = candidate.rsplit(":", 1)[-1].strip()
            if ". " in candidate:
                candidate = candidate.rsplit(". ", 1)[-1].strip()
            candidate = candidate.strip(" \t;:|\u00a0")
            if (
                candidate
                and len(candidate) <= 512
                and not candidate.lower().startswith(("not ", "the ", "this ", "names "))
            ):
                names.append(candidate)
    return names


def extract_occurrences(nodes_dir: Path) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, Any]]]:
    occurrences: dict[str, list[dict[str, str]]] = defaultdict(list)
    source_files = []
    for path in sorted(nodes_dir.glob("bluebook_v3_document_nodes.P-*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        source_files.append(
            {"path": str(path), "bytes": path.stat().st_size, "sha256": sha256_file(path)}
        )
        for fragment in document["fragments"]:
            for node in fragment["nodes"]:
                for field_path, text in iter_text_fields(node):
                    for name in extract_pin_names(text):
                        occurrences[name].append(
                            {
                                "document_id": document["document_id"],
                                "rule_id": fragment["rule_id"],
                                "node_id": node["node_id"],
                                "field_path": field_path,
                                "source_text_sha256": hashlib.sha256(
                                    text.encode("utf-8")
                                ).hexdigest().upper(),
                            }
                        )
    return dict(occurrences), source_files


def run_opsin_smiles(
    names: Sequence[str], jar: Path, output_dir: Path, label: str
) -> tuple[dict[str, str], dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_path = output_dir / f"opsin-{label}.input.txt"
    output_path = output_dir / f"opsin-{label}.output.tsv"
    stderr_path = output_dir / f"opsin-{label}.stderr.txt"
    input_bytes = ("\n".join(names) + "\n").encode("utf-8")
    cached = (
        input_path.exists()
        and output_path.exists()
        and input_path.read_bytes() == input_bytes
    )
    input_path.write_bytes(input_bytes)
    started = time.monotonic()
    if not cached:
        command = [
            "java",
            "-jar",
            str(jar),
            "-o",
            "smi",
            "-n",
            str(input_path),
            str(output_path),
        ]
        with stderr_path.open("w", encoding="utf-8", newline="\n") as stderr:
            process = subprocess.run(command, stderr=stderr, check=False)
        if process.returncode != 0:
            raise RuntimeError(f"OPSIN exited with {process.returncode}; see {stderr_path}")
    lines = output_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != len(names):
        raise RuntimeError(f"OPSIN output line count differs: {len(lines)} != {len(names)}")
    results = {}
    for name, line in zip(names, lines):
        smiles, separator, returned_name = line.partition("\t")
        if not separator or returned_name != name:
            raise RuntimeError("OPSIN output lost input-name alignment")
        results[name] = smiles
    return results, {
        "input_count": len(names),
        "parsed_count": sum(bool(value) for value in results.values()),
        "failed_count": sum(not value for value in results.values()),
        "duration_seconds": round(0.0 if cached else time.monotonic() - started, 3),
        "cached": cached,
        "stderr_path": str(stderr_path),
    }


def benchmark_split(case_id: str) -> str:
    bucket = int(hashlib.sha256(case_id.encode("ascii")).hexdigest()[:8], 16) % 10
    if bucket == 9:
        return "final_holdout"
    if bucket == 8:
        return "holdout"
    return "calibration"


def build_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    if not args.opsin_jar.is_file():
        raise FileNotFoundError(args.opsin_jar)
    occurrences, source_files = extract_occurrences(args.nodes_dir)
    names = sorted(occurrences)
    smiles_by_name, opsin_metrics = run_opsin_smiles(
        names, args.opsin_jar, args.output_dir, "bluebook-pin"
    )

    from rdkit import Chem, RDLogger
    from rdkit.Chem import inchi

    RDLogger.DisableLog("rdApp.*")
    cases = []
    rdkit_failures = []
    for name in names:
        smiles = smiles_by_name[name]
        if not smiles:
            continue
        molecule = Chem.MolFromSmiles(smiles)
        if molecule is None:
            rdkit_failures.append(name)
            continue
        canonical_smiles = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
        key = inchi.MolToInchiKey(molecule)
        case_id = "BBPIN-" + hashlib.sha256(
            (name + "\0" + key).encode("utf-8")
        ).hexdigest()[:20].upper()
        cases.append(
            {
                "case_id": case_id,
                "split": benchmark_split(case_id),
                "preferred_iupac_name": name,
                "smiles": canonical_smiles,
                "standard_inchikey": key,
                "authority": "IUPAC Blue Book V3 explicit (PIN) designation",
                "occurrences": occurrences[name],
            }
        )
    cases.sort(key=lambda case: case["case_id"])
    cases_path = args.output_dir / "cases.jsonl"
    cases_bytes = b"".join(canonical_json_bytes(case) for case in cases)
    cases_path.write_bytes(cases_bytes)
    split_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        split_counts[case["split"]] += 1
    report = {
        "format": "iupac-bluebook-pin-benchmark",
        "format_version": "1.0.0",
        "source_files": source_files,
        "opsin": opsin_metrics,
        "extracted_unique_pin_string_count": len(names),
        "scorable_case_count": len(cases),
        "rdkit_parse_failure_count": len(rdkit_failures),
        "rdkit_parse_failures": rdkit_failures[:100],
        "split_counts": dict(sorted(split_counts.items())),
        "cases_path": str(cases_path),
        "cases_sha256": hashlib.sha256(cases_bytes).hexdigest().upper(),
    }
    report_path = args.output_dir / "report.json"
    report_path.write_bytes(canonical_json_bytes(report))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build an authoritative SMILES-to-PIN benchmark from Blue Book examples"
    )
    parser.add_argument("--nodes-dir", type=Path, default=DEFAULT_NODES)
    parser.add_argument("--opsin-jar", type=Path, default=DEFAULT_OPSIN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    report = build_benchmark(parse_args(argv))
    print(json.dumps(report, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
