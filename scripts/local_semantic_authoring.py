from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.audit_semantic_authoring_quality import quality_findings
    from scripts.bootstrap_semantic_authoring import DEFAULT_TASK_DIR
    from scripts.build_compact_semantic_tasks import canonical_json_bytes, load_json
    from scripts.compile_semantic_authoring import AuthoringError, compile_authoring
    from scripts.local_semantic_compaction import (
        DEFAULT_MODEL,
        DEFAULT_SEED,
        _request_model,
        build_candidate_view,
    )
else:
    from audit_semantic_authoring_quality import quality_findings  # type: ignore[no-redef]
    from bootstrap_semantic_authoring import DEFAULT_TASK_DIR  # type: ignore[no-redef]
    from build_compact_semantic_tasks import (  # type: ignore[no-redef]
        canonical_json_bytes,
        load_json,
    )
    from compile_semantic_authoring import (  # type: ignore[no-redef]
        AuthoringError,
        compile_authoring,
    )
    from local_semantic_compaction import (  # type: ignore[no-redef]
        DEFAULT_MODEL,
        DEFAULT_SEED,
        _request_model,
        build_candidate_view,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BOOTSTRAP_DIR = ROOT / "work" / "bootstrap_semantic_authoring"
DEFAULT_OUTPUT_DIR = ROOT / "work" / "local_semantic_authoring"
DEFAULT_EXAMPLE = ROOT / "data" / "bluebook_v3" / "semantic_authoring" / "P-100-part-001.json"
AUTHORING_VALIDATOR_VERSION = "1.0.0"

SYSTEM_PROMPT = """You convert an IUPAC Blue Book task into compact semantic authoring.
The source clauses are authoritative. The bootstrap draft is evidence, not authority.

Return compact executable semantics, not prose summaries and not payloads containing
source_text, operations, generic fallback actions, or an unevaluated copy of a clause.
Use the compact prefix expression and statement forms demonstrated by the example.
Every source clause must have exactly one ordered clause disposition. Operative clauses
must be owned by a semantic unit, retained table, exception, or authored example.
Use safe nonoperative reasons only for genuinely nonoperative headings, navigation,
historical context, rationale, citations, or example labels. Preserve normative force,
ordered preferences, tie continuation, conditions, exceptions, mappings, and procedures.
Retained tables, figures, corrections, and citation bindings are merged mechanically;
do not reproduce them. Return JSON only and obey the response schema.
"""


def response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "task_id",
            "clauses",
            "symbols",
            "units",
            "exceptions",
            "examples",
        ],
        "properties": {
            "task_id": {"type": "string"},
            "clauses": {
                "type": "array",
                "items": {
                    "oneOf": [
                        {"type": "null"},
                        {"type": "array", "minItems": 3, "maxItems": 5},
                    ]
                },
            },
            "symbols": {"type": "array", "items": {"type": "object"}},
            "units": {"type": "array", "items": {"type": "object"}},
            "exceptions": {"type": "array", "items": {"type": "object"}},
            "examples": {"type": "array", "items": {"type": "object"}},
        },
    }


def _sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest().upper()


def build_prompt(
    candidate: Mapping[str, Any],
    bootstrap: Mapping[str, Any],
    example: Mapping[str, Any],
) -> str:
    retained = {
        "table_ids_and_clauses": [
            {"id": item["id"], "c": item["c"]} for item in bootstrap["tables"]
        ],
        "figure_ids_and_clauses": [
            {"id": item["id"], "c": item["c"]} for item in bootstrap["figures"]
        ],
        "mechanical_clause_indexes": [
            index
            for index, decision in enumerate(bootstrap["clauses"], 1)
            if decision is None
        ],
    }
    return (
        SYSTEM_PROMPT
        + "\nRESPONSE SCHEMA:\n"
        + json.dumps(response_schema(), separators=(",", ":"), ensure_ascii=False)
        + "\nCOMPACT AUTHORING EXAMPLE:\n"
        + json.dumps(example, separators=(",", ":"), ensure_ascii=False)
        + "\nMECHANICALLY RETAINED OBJECTS:\n"
        + json.dumps(retained, separators=(",", ":"), ensure_ascii=False)
        + "\nSOURCE TASK AND ROUGH CANDIDATE:\n"
        + json.dumps(candidate, separators=(",", ":"), ensure_ascii=False)
    )


def assemble_authoring(
    plan: Mapping[str, Any], bootstrap: Mapping[str, Any]
) -> dict[str, Any]:
    source = {
        "format": "iupac-bluebook-semantic-authoring",
        "format_version": "1.0.0",
        "task_id": plan.get("task_id"),
        "clauses": plan.get("clauses"),
        "symbols": plan.get("symbols"),
        "units": plan.get("units"),
        "exceptions": plan.get("exceptions"),
        "tables": bootstrap["tables"],
        "figures": bootstrap["figures"],
        "examples": plan.get("examples"),
        "corrections": bootstrap["corrections"],
        "refs": bootstrap["refs"],
        "additional_refs": bootstrap["additional_refs"],
    }
    if "mechanical_assets" in bootstrap:
        source["mechanical_assets"] = bootstrap["mechanical_assets"]
    return source


def validate_authoring(
    source: Mapping[str, Any], task: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    try:
        _delta, chunk, compile_report = compile_authoring(source, task)
    except (AuthoringError, KeyError, TypeError, ValueError) as error:
        return (
            {"passed": False, "errors": [f"{type(error).__name__}: {error}"]},
            None,
            None,
        )
    findings = quality_findings(source, task)
    errors = [
        f"quality finding at clause {item['clause_index']}: {item['reason_code']}"
        for item in findings
    ]
    metrics = compile_report["metrics"]
    return (
        {
            "passed": compile_report["passed"] is True and not errors,
            "errors": errors,
            "compiled_clause_count": metrics["compiled_clause_count"],
            "nonoperative_clause_count": metrics["nonoperative_clause_count"],
            "semantic_unit_count": metrics["semantic_unit_count"],
            "exception_count": metrics["exception_count"],
            "example_count": metrics["example_count"],
        },
        chunk,
        compile_report,
    )


def process_task(
    task_path: Path,
    *,
    bootstrap_dir: Path,
    output_dir: Path,
    example_path: Path,
    model: str,
    backend: str,
    endpoint: str,
    context_tokens: int,
    output_tokens: int,
    timeout: int,
    repair_attempts: int,
    seed: int,
    force: bool,
) -> dict[str, Any]:
    task = load_json(task_path)
    task_id = str(task["task_id"])
    bootstrap = load_json(bootstrap_dir / f"{task_id}.json")
    candidate = build_candidate_view(task, bootstrap)
    example = load_json(example_path)
    prompt = build_prompt(candidate, bootstrap, example)
    prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest().upper()
    source_path = output_dir / "authoring" / f"{task_id}.json"
    report_path = output_dir / "reports" / f"{task_id}.json"
    chunk_path = output_dir / "chunks" / f"{task_id}.json"
    for path in (source_path, report_path, chunk_path):
        path.parent.mkdir(parents=True, exist_ok=True)

    if not force and source_path.exists() and report_path.exists():
        report = load_json(report_path)
        source = load_json(source_path)
        validation, _chunk, _compile_report = validate_authoring(source, task)
        if (
            report.get("prompt_sha256") == prompt_sha256
            and report.get("model") == model
            and report.get("backend") == backend
            and report.get("endpoint") == endpoint
            and report.get("seed") == seed
            and report.get("validator_version") == AUTHORING_VALIDATOR_VERSION
            and validation["passed"]
        ):
            return {**report, "validation": validation, "cached": True}

    request_prompt = prompt
    attempts = []
    best_source: dict[str, Any] = {}
    best_validation: dict[str, Any] = {"passed": False, "errors": ["not generated"]}
    best_chunk: dict[str, Any] | None = None
    for attempt in range(repair_attempts + 1):
        estimated_prompt_tokens = math.ceil(len(request_prompt.encode("utf-8")) / 3)
        required_context = estimated_prompt_tokens + output_tokens
        if required_context > context_tokens:
            validation = {
                "passed": False,
                "errors": [
                    f"prompt and output require about {required_context} tokens, "
                    f"above configured context {context_tokens}"
                ],
            }
            attempts.append({"attempt": attempt + 1, "validation": validation})
            break
        plan, metrics = _request_model(
            backend=backend,
            endpoint=endpoint,
            model=model,
            prompt=request_prompt,
            context_tokens=context_tokens,
            output_tokens=output_tokens,
            timeout=timeout,
            seed=seed,
            schema=response_schema(),
        )
        source = assemble_authoring(plan, bootstrap)
        validation, chunk, compile_report = validate_authoring(source, task)
        attempts.append(
            {
                "attempt": attempt + 1,
                "metrics": metrics,
                "validation": validation,
                "compile_report": compile_report,
            }
        )
        if validation["passed"]:
            best_source = source
            best_validation = validation
            best_chunk = chunk
            break
        if not best_source or len(validation["errors"]) < len(best_validation["errors"]):
            best_source = source
            best_validation = validation
            best_chunk = chunk
        if attempt == repair_attempts:
            break
        previous = json.dumps(plan, separators=(",", ":"), ensure_ascii=False)
        repair = (
            prompt
            + "\nPREVIOUS AUTHORING FAILED THE STRICT COMPILER:\n"
            + json.dumps(validation["errors"], separators=(",", ":"), ensure_ascii=False)
            + "\nPREVIOUS AUTHORING PLAN:\n"
            + previous
            + "\nReturn the complete corrected authoring plan as JSON only."
        )
        if math.ceil(len(repair.encode("utf-8")) / 3) + output_tokens > context_tokens:
            repair = (
                prompt
                + "\nPREVIOUS AUTHORING FAILED THE STRICT COMPILER:\n"
                + json.dumps(
                    validation["errors"], separators=(",", ":"), ensure_ascii=False
                )
                + "\nRegenerate the complete corrected authoring plan as JSON only."
            )
        request_prompt = repair

    if best_source:
        source_path.write_bytes(canonical_json_bytes(best_source))
    if best_chunk is not None and best_validation["passed"]:
        chunk_path.write_bytes(canonical_json_bytes(best_chunk))
    report = {
        "format": "iupac-bluebook-local-semantic-authoring-report",
        "format_version": "1.0.0",
        "validator_version": AUTHORING_VALIDATOR_VERSION,
        "task_id": task_id,
        "task_sha256": task["task_sha256"],
        "model": model,
        "backend": backend,
        "endpoint": endpoint,
        "seed": seed,
        "prompt_sha256": prompt_sha256,
        "prompt_bytes": len(prompt.encode("utf-8")),
        "response_schema_sha256": _sha256(response_schema()),
        "authoring_sha256": _sha256(best_source) if best_source else None,
        "attempts": attempts,
        "validation": best_validation,
    }
    report_path.write_bytes(canonical_json_bytes(report))
    return report


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate compact authoring and require strict semantic compilation"
    )
    parser.add_argument("tasks", nargs="+", type=Path)
    parser.add_argument("--bootstrap-dir", type=Path, default=DEFAULT_BOOTSTRAP_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--example", type=Path, default=DEFAULT_EXAMPLE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--backend", choices=("ollama", "openai"), default="openai")
    parser.add_argument("--endpoint", default="http://127.0.0.1:8080")
    parser.add_argument("--context-tokens", type=int, default=49152)
    parser.add_argument("--output-tokens", type=int, default=24576)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    reports = []
    try:
        for task_path in args.tasks:
            reports.append(
                process_task(
                    task_path,
                    bootstrap_dir=args.bootstrap_dir,
                    output_dir=args.output_dir,
                    example_path=args.example,
                    model=args.model,
                    backend=args.backend,
                    endpoint=args.endpoint,
                    context_tokens=args.context_tokens,
                    output_tokens=args.output_tokens,
                    timeout=args.timeout,
                    repair_attempts=args.repair_attempts,
                    seed=args.seed,
                    force=args.force,
                )
            )
    except (KeyError, OSError, TypeError, ValueError, RuntimeError) as error:
        print(json.dumps({"passed": False, "error": str(error)}, indent=2))
        return 1
    output = {
        "format": "iupac-bluebook-local-semantic-authoring-run",
        "format_version": "1.0.0",
        "passed": all(report["validation"]["passed"] for report in reports),
        "reports": reports,
    }
    rendered = json.dumps(output, indent=2, ensure_ascii=False) + "\n"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
