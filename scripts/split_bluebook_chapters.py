from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "bluebook_rules.json"
OUT = ROOT / "work" / "chapter_inputs"


def main() -> int:
    payload = json.loads(SOURCE.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    chapters = sorted({record["chapter"] for record in payload["records"]})
    for chapter in chapters:
        records = [record for record in payload["records"] if record["chapter"] == chapter]
        out_file = OUT / f"{chapter}.json"
        out_file.write_text(
            json.dumps(
                {
                    "source": payload["source"],
                    "source_version": payload["source_version"],
                    "chapter": chapter,
                    "record_count": len(records),
                    "records": records,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    corrections = payload.get("corrections", [])
    (OUT / "Post-V3-Corrections.json").write_text(
        json.dumps(
            {
                "source": payload["source"],
                "source_version": payload["source_version"],
                "chapter": "Post-V3-Corrections",
                "record_count": len(corrections),
                "records": corrections,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {len(chapters)} chapter input files plus corrections to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
