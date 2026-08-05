from __future__ import annotations

import argparse
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__:
    from scripts.build_compact_semantic_tasks import (
        canonical_json_bytes,
        digest_without_field,
        load_json,
        sha256_bytes,
    )
else:
    from build_compact_semantic_tasks import (
        canonical_json_bytes,
        digest_without_field,
        load_json,
        sha256_bytes,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INVENTORY = ROOT / "data" / "bluebook_v3" / "bluebook_v3_clause_inventory.json"
DEFAULT_OUTPUT = ROOT / "data" / "bluebook_v3" / "semantic_asset_scaffold.json"


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.:]+", "_", value).strip("_.:").lower()
    return result if result and result[0].isalpha() else "x_" + result


def _source_urls(payload: Mapping[str, Any]) -> list[str]:
    result = []
    for field in ("url", "link_url", "link_href"):
        value = payload.get(field)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            if value not in result:
                result.append(value)
    return result


def build_asset_scaffold(
    inventory: Mapping[str, Any], *, inventory_file_sha256: str
) -> dict[str, Any]:
    assets = []
    seen = set()
    for record in inventory["records"]:
        for unit in record["source_units"]:
            if unit["unit_kind"] != "image_asset":
                continue
            clause_id = unit["unit_id"]
            if clause_id in seen:
                raise ValueError(f"Duplicate image-asset clause: {clause_id}")
            seen.add(clause_id)
            payload = unit.get("payload")
            if not isinstance(payload, Mapping):
                raise ValueError(f"Image asset has no payload: {clause_id}")
            urls = _source_urls(payload)
            if not urls:
                raise ValueError(f"Image asset has no absolute source URL: {clause_id}")
            source_url = urls[0].lower()
            kind = (
                "source_icon"
                if "/greek/" in source_url or source_url.endswith("/alter.gif")
                else "chemical_structure"
            )
            assets.append(
                {
                    "clause_id": clause_id,
                    "figure_id": f"figure.asset.{_slug(clause_id)}",
                    "kind": kind,
                    "source_urls": urls,
                    "source_payload_sha256": unit.get("payload_sha256"),
                }
            )
    scaffold: dict[str, Any] = {
        "format": "iupac-bluebook-semantic-asset-scaffold",
        "format_version": "1.0.0",
        "clause_inventory_file_sha256": inventory_file_sha256,
        "asset_count": len(assets),
        "assets": assets,
    }
    scaffold["scaffold_sha256"] = digest_without_field(
        scaffold, "scaffold_sha256"
    )
    return scaffold


@lru_cache(maxsize=4)
def load_asset_scaffold(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    scaffold = load_json(path)
    if scaffold.get("format") != "iupac-bluebook-semantic-asset-scaffold":
        raise ValueError("Semantic asset scaffold has an unexpected format")
    if scaffold.get("format_version") != "1.0.0":
        raise ValueError("Semantic asset scaffold has an unsupported version")
    if scaffold.get("scaffold_sha256") != digest_without_field(
        scaffold, "scaffold_sha256"
    ):
        raise ValueError("Semantic asset scaffold hash does not reproduce")
    assets = scaffold.get("assets")
    if not isinstance(assets, list) or scaffold.get("asset_count") != len(assets):
        raise ValueError("Semantic asset scaffold count does not reproduce")
    return scaffold


def task_asset_figures(
    task: Mapping[str, Any], scaffold: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if (
        scaffold["clause_inventory_file_sha256"]
        != task["source_hashes"]["clause_inventory_sha256"]
    ):
        raise ValueError("Semantic asset scaffold differs from task source snapshot")
    by_clause = {item["clause_id"]: item for item in scaffold["assets"]}
    figures = []
    for rule in task["rules"]:
        for unit in rule["source_units"]:
            if unit["unit_kind"] != "image_asset":
                continue
            clause_id = unit["clause_id"]
            asset = by_clause.get(clause_id)
            if asset is None:
                raise ValueError(f"Semantic asset scaffold misses {clause_id}")
            figures.append(
                {
                    "figure_id": asset["figure_id"],
                    "clause_ids": [clause_id],
                    "kind": asset["kind"],
                    "source_urls": asset["source_urls"],
                    "content_sha256": None,
                }
            )
    return figures


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build deterministic semantic figures for source image assets"
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    raw = args.inventory.read_bytes()
    scaffold = build_asset_scaffold(
        load_json(args.inventory), inventory_file_sha256=sha256_bytes(raw)
    )
    rendered = canonical_json_bytes(scaffold)
    if args.check:
        if not args.output.exists() or args.output.read_bytes() != rendered:
            raise SystemExit(f"Semantic asset scaffold is missing or stale: {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(rendered)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "asset_count": scaffold["asset_count"],
                "bytes": len(rendered),
                "scaffold_sha256": scaffold["scaffold_sha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
