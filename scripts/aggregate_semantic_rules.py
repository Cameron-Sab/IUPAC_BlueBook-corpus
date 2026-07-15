from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEMANTIC_DIR = ROOT / "data" / "semantic_rules"
OUT = ROOT / "data" / "bluebook_semantic_rules.json"


def main() -> int:
    files = sorted(SEMANTIC_DIR.glob("*.json"))
    records = []
    chapters = []
    for file in files:
        payload = json.loads(file.read_text(encoding="utf-8-sig"))
        chapters.append(payload.get("chapter", file.stem))
        records.extend(payload.get("records", []))
    result = {
        "source": "https://iupac.qmul.ac.uk/BlueBook/",
        "source_version": "IUPAC Blue Book 2013 online version plus post-V3 web corrections",
        "conversion_status": "draft_semantic",
        "chapter_count": len(chapters),
        "record_count": len(records),
        "chapters": chapters,
        "records": records,
    }
    OUT.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(records)} semantic records from {len(files)} files to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
