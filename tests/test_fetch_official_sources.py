from __future__ import annotations

import hashlib
import io
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from scripts.fetch_official_sources import (
    IntegrityError,
    MetadataError,
    SourceSpec,
    fetch_source,
    fetch_sources,
    load_source_specs,
    main,
    offline_verify,
)


def _metadata(tmp_path: Path) -> tuple[tuple[Path, Path, Path], dict[str, bytes]]:
    payloads = {"BlueBookV3.pdf": b"pdf-source"}
    artifacts = []
    for number in range(1, 12):
        filename = f"P{number}.html"
        raw = f"chapter-{number}".encode()
        payloads[filename] = raw
        artifacts.append(
            {
                "cache_path": filename,
                "source_url": f"fake://official/{filename}",
                "source_sha256": hashlib.sha256(raw).hexdigest().upper(),
                "source_byte_count": len(raw),
            }
        )
    payloads["changes2.html"] = b"corrections-source"

    corpus = tmp_path / "source-corpus.json"
    references = tmp_path / "reference-occurrences.json"
    corrections = tmp_path / "correction-overlays.json"
    corpus.write_text(
        json.dumps(
            {
                "source_document": {
                    "source_pdf_url": "fake://official/BlueBookV3.pdf",
                    "local_pdf_sha256": hashlib.sha256(
                        payloads["BlueBookV3.pdf"]
                    ).hexdigest(),
                    "local_pdf_byte_count": len(payloads["BlueBookV3.pdf"]),
                }
            }
        ),
        encoding="utf-8",
    )
    references.write_text(
        json.dumps({"source_artifacts": artifacts}), encoding="utf-8"
    )
    corrections.write_text(
        json.dumps(
            {
                "source_document": {
                    "source_path": ".cache/bluebook_html/changes2.html",
                    "source_url": "fake://official/changes2.html",
                    "source_sha256": hashlib.sha256(
                        payloads["changes2.html"]
                    ).hexdigest(),
                    "source_byte_count": len(payloads["changes2.html"]),
                }
            }
        ),
        encoding="utf-8",
    )
    return (corpus, references, corrections), payloads


def _downloader(payloads: dict[str, bytes], calls: list[str]):
    def download(url: str) -> io.BytesIO:
        calls.append(url)
        return io.BytesIO(payloads[url.rsplit("/", 1)[-1]])

    return download


def test_specs_are_derived_from_the_three_generated_artifacts(tmp_path: Path):
    paths, payloads = _metadata(tmp_path)
    specs = load_source_specs(*paths)

    assert len(specs) == 13
    assert specs[0] == SourceSpec(
        role="canonical_pdf",
        filename="BlueBookV3.pdf",
        url="fake://official/BlueBookV3.pdf",
        sha256=hashlib.sha256(payloads["BlueBookV3.pdf"]).hexdigest().upper(),
        byte_count=len(payloads["BlueBookV3.pdf"]),
    )
    assert [spec.filename for spec in specs[1:-1]] == [
        f"P{number}.html" for number in range(1, 12)
    ]
    assert specs[-1].filename == "changes2.html"


def test_fetches_all_sources_atomically_without_network(tmp_path: Path):
    paths, payloads = _metadata(tmp_path)
    specs = load_source_specs(*paths)
    output = tmp_path / "cache"
    calls: list[str] = []

    results = fetch_sources(specs, output, downloader=_downloader(payloads, calls))

    assert len(calls) == 13
    assert {result.action for result in results} == {"downloaded"}
    assert {path.name: path.read_bytes() for path in output.iterdir()} == payloads
    assert not list(output.glob(".*.tmp"))


def test_valid_files_are_preserved_unless_refresh(tmp_path: Path):
    paths, payloads = _metadata(tmp_path)
    source = load_source_specs(*paths)[0]
    output = tmp_path / "cache"
    output.mkdir()
    destination = output / source.filename
    destination.write_bytes(payloads[source.filename])

    result = fetch_source(
        source,
        output,
        downloader=lambda _url: pytest.fail("valid source was downloaded"),
    )
    assert result.action == "preserved"

    calls: list[str] = []
    refreshed = fetch_source(
        source,
        output,
        refresh=True,
        downloader=_downloader(payloads, calls),
    )
    assert refreshed.action == "downloaded"
    assert calls == [source.url]


def test_bad_download_does_not_replace_an_existing_valid_file(tmp_path: Path):
    paths, payloads = _metadata(tmp_path)
    source = load_source_specs(*paths)[0]
    output = tmp_path / "cache"
    output.mkdir()
    destination = output / source.filename
    destination.write_bytes(payloads[source.filename])

    with pytest.raises(IntegrityError, match="Rejected download"):
        fetch_source(
            source,
            output,
            refresh=True,
            downloader=lambda _url: io.BytesIO(b"bad-source"),
        )

    assert destination.read_bytes() == payloads[source.filename]
    assert not list(output.glob(".*.tmp"))


@pytest.mark.parametrize("mutation", ["hash", "length"])
def test_mutated_integrity_metadata_rejects_download(
    tmp_path: Path, mutation: str
):
    paths, payloads = _metadata(tmp_path)
    source = load_source_specs(*paths)[0]
    values: dict[str, Any] = {
        "sha256": "0" * 64 if mutation == "hash" else source.sha256,
        "byte_count": source.byte_count + 1 if mutation == "length" else source.byte_count,
    }
    mutated = SourceSpec(
        role=source.role,
        filename=source.filename,
        url=source.url,
        **values,
    )

    with pytest.raises(IntegrityError):
        fetch_source(
            mutated,
            tmp_path / "cache",
            downloader=lambda _url: io.BytesIO(payloads[source.filename]),
        )
    assert not (tmp_path / "cache" / source.filename).exists()


def test_offline_verify_aggregates_missing_and_corrupt_files_without_download(
    tmp_path: Path,
):
    paths, payloads = _metadata(tmp_path)
    specs = load_source_specs(*paths)
    output = tmp_path / "cache"
    output.mkdir()
    for source in specs:
        (output / source.filename).write_bytes(payloads[source.filename])
    assert len(offline_verify(specs, output)) == 13

    (output / specs[0].filename).write_bytes(b"mutated")
    (output / specs[1].filename).unlink()
    with pytest.raises(IntegrityError) as error:
        offline_verify(specs, output)
    assert specs[0].filename in str(error.value)
    assert specs[1].filename in str(error.value)


def test_metadata_mutations_are_rejected(tmp_path: Path):
    paths, _ = _metadata(tmp_path)
    references = json.loads(paths[1].read_text(encoding="utf-8"))

    for mutation in (
        lambda value: value["source_artifacts"].pop(),
        lambda value: value["source_artifacts"][0].update(
            {"cache_path": "../outside.html"}
        ),
        lambda value: value["source_artifacts"][0].update(
            {"source_sha256": "not-a-digest"}
        ),
    ):
        changed = deepcopy(references)
        mutation(changed)
        paths[1].write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(MetadataError):
            load_source_specs(*paths)


def test_offline_verify_cli_uses_only_local_files(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    paths, payloads = _metadata(tmp_path)
    output = tmp_path / "cache"
    output.mkdir()
    for source in load_source_specs(*paths):
        (output / source.filename).write_bytes(payloads[source.filename])

    assert (
        main(
            [
                "--source-corpus",
                str(paths[0]),
                "--reference-occurrences",
                str(paths[1]),
                "--correction-overlays",
                str(paths[2]),
                "--output-dir",
                str(output),
                "--offline-verify",
            ]
        )
        == 0
    )
    assert capsys.readouterr().out.count("verified:") == 13
