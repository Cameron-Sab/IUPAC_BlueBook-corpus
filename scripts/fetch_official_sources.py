from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.request
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "bluebook_v3"
DEFAULT_OUTPUT_DIR = ROOT / ".cache" / "bluebook_official_sources"

# This maps each file role to its generated source of truth. URLs, sizes, digests,
# and HTML filenames are deliberately read from those artifacts at runtime.
ROLE_ARTIFACTS = {
    "canonical_pdf": BASE / "bluebook_v3_source_corpus.json",
    "chapter_html": BASE / "bluebook_v3_reference_occurrences.json",
    "corrections_html": BASE / "bluebook_v3_correction_overlays.json",
}

CHUNK_SIZE = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9A-Fa-f]{64}$")


class SourceFetchError(RuntimeError):
    """Base error for source metadata, download, and verification failures."""


class MetadataError(SourceFetchError):
    """Raised when a generated artifact cannot define the expected sources."""


class IntegrityError(SourceFetchError):
    """Raised when a local or downloaded source fails integrity verification."""


@dataclass(frozen=True)
class SourceSpec:
    role: str
    filename: str
    url: str
    sha256: str
    byte_count: int


@dataclass(frozen=True)
class FetchResult:
    source: SourceSpec
    path: Path
    action: str


Downloader = Callable[[str], BinaryIO]


def _load_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise MetadataError(f"Cannot read source metadata {path}: {error}") from error
    if not isinstance(value, dict):
        raise MetadataError(f"Source metadata must be a JSON object: {path}")
    return value


def _required_string(record: Mapping[str, Any], key: str, context: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise MetadataError(f"{context}.{key} must be a non-empty string")
    return value


def _required_digest(record: Mapping[str, Any], key: str, context: str) -> str:
    value = _required_string(record, key, context)
    if SHA256_RE.fullmatch(value) is None:
        raise MetadataError(f"{context}.{key} must be a 64-digit SHA256")
    return value.upper()


def _required_size(record: Mapping[str, Any], key: str, context: str) -> int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MetadataError(f"{context}.{key} must be a non-negative integer")
    return value


def _safe_filename(value: str, context: str) -> str:
    if value in {"", ".", ".."} or Path(value).name != value:
        raise MetadataError(f"{context} must be a plain filename, got {value!r}")
    if "/" in value or "\\" in value:
        raise MetadataError(f"{context} must not contain path separators")
    return value


def _source_document(artifact: Mapping[str, Any], context: str) -> Mapping[str, Any]:
    value = artifact.get("source_document")
    if not isinstance(value, dict):
        raise MetadataError(f"{context}.source_document must be an object")
    return value


def load_source_specs(
    source_corpus: Path = ROLE_ARTIFACTS["canonical_pdf"],
    reference_occurrences: Path = ROLE_ARTIFACTS["chapter_html"],
    correction_overlays: Path = ROLE_ARTIFACTS["corrections_html"],
) -> tuple[SourceSpec, ...]:
    """Load all expected official files from committed generated artifacts."""
    corpus_document = _source_document(_load_json(source_corpus), str(source_corpus))
    pdf = SourceSpec(
        role="canonical_pdf",
        filename="BlueBookV3.pdf",
        url=_required_string(corpus_document, "source_pdf_url", "source_document"),
        sha256=_required_digest(
            corpus_document, "local_pdf_sha256", "source_document"
        ),
        byte_count=_required_size(
            corpus_document, "local_pdf_byte_count", "source_document"
        ),
    )

    references = _load_json(reference_occurrences)
    artifacts = references.get("source_artifacts")
    if not isinstance(artifacts, list):
        raise MetadataError(
            f"{reference_occurrences}.source_artifacts must be an array"
        )
    if len(artifacts) != 11:
        raise MetadataError(
            f"Expected 11 chapter HTML source artifacts, found {len(artifacts)}"
        )

    chapters: list[SourceSpec] = []
    for index, artifact in enumerate(artifacts):
        context = f"source_artifacts[{index}]"
        if not isinstance(artifact, dict):
            raise MetadataError(f"{context} must be an object")
        chapters.append(
            SourceSpec(
                role="chapter_html",
                filename=_safe_filename(
                    _required_string(artifact, "cache_path", context),
                    f"{context}.cache_path",
                ),
                url=_required_string(artifact, "source_url", context),
                sha256=_required_digest(artifact, "source_sha256", context),
                byte_count=_required_size(artifact, "source_byte_count", context),
            )
        )

    corrections_document = _source_document(
        _load_json(correction_overlays), str(correction_overlays)
    )
    corrections = SourceSpec(
        role="corrections_html",
        filename=_safe_filename(
            Path(
                _required_string(
                    corrections_document, "source_path", "source_document"
                )
            ).name,
            "source_document.source_path",
        ),
        url=_required_string(corrections_document, "source_url", "source_document"),
        sha256=_required_digest(
            corrections_document, "source_sha256", "source_document"
        ),
        byte_count=_required_size(
            corrections_document, "source_byte_count", "source_document"
        ),
    )

    specs = (pdf, *chapters, corrections)
    filenames = [spec.filename.casefold() for spec in specs]
    if len(filenames) != len(set(filenames)):
        raise MetadataError("Generated source metadata contains duplicate filenames")
    return specs


def _digest_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    byte_count = 0
    with path.open("rb") as stream:
        while chunk := stream.read(CHUNK_SIZE):
            byte_count += len(chunk)
            digest.update(chunk)
    return byte_count, digest.hexdigest().upper()


def verify_file(path: Path, source: SourceSpec) -> None:
    """Raise IntegrityError unless path exactly matches source metadata."""
    if not path.is_file():
        raise IntegrityError(f"Missing {source.role} source: {path}")
    try:
        byte_count, digest = _digest_file(path)
    except OSError as error:
        raise IntegrityError(f"Cannot read {path}: {error}") from error
    problems = []
    if byte_count != source.byte_count:
        problems.append(f"length {byte_count}, expected {source.byte_count}")
    if digest != source.sha256:
        problems.append(f"SHA256 {digest}, expected {source.sha256}")
    if problems:
        raise IntegrityError(f"Integrity check failed for {path}: " + "; ".join(problems))


def _default_downloader(url: str) -> BinaryIO:
    request = urllib.request.Request(
        url, headers={"User-Agent": "IUPAC-BlueBook-official-source-fetcher/1"}
    )
    return urllib.request.urlopen(request, timeout=120)


def _download_atomic(
    source: SourceSpec,
    destination: Path,
    downloader: Downloader,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            response = downloader(source.url)
            with closing(response):
                byte_count = 0
                digest = hashlib.sha256()
                while chunk := response.read(CHUNK_SIZE):
                    if not isinstance(chunk, bytes):
                        raise SourceFetchError(
                            f"Downloader returned non-bytes content for {source.url}"
                        )
                    byte_count += len(chunk)
                    if byte_count > source.byte_count:
                        raise IntegrityError(
                            f"Downloaded {source.filename} exceeds expected length "
                            f"{source.byte_count}"
                        )
                    digest.update(chunk)
                    output.write(chunk)
            output.flush()
            os.fsync(output.fileno())

        actual_digest = digest.hexdigest().upper()
        problems = []
        if byte_count != source.byte_count:
            problems.append(f"length {byte_count}, expected {source.byte_count}")
        if actual_digest != source.sha256:
            problems.append(
                f"SHA256 {actual_digest}, expected {source.sha256}"
            )
        if problems:
            raise IntegrityError(
                f"Rejected download for {source.filename}: " + "; ".join(problems)
            )
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def fetch_source(
    source: SourceSpec,
    output_dir: Path,
    *,
    refresh: bool = False,
    downloader: Downloader = _default_downloader,
) -> FetchResult:
    destination = output_dir / source.filename
    if not refresh:
        try:
            verify_file(destination, source)
        except IntegrityError:
            pass
        else:
            return FetchResult(source, destination, "preserved")

    try:
        _download_atomic(source, destination, downloader)
    except (IntegrityError, SourceFetchError):
        raise
    except Exception as error:
        raise SourceFetchError(f"Failed to download {source.url}: {error}") from error
    return FetchResult(source, destination, "downloaded")


def fetch_sources(
    sources: Sequence[SourceSpec],
    output_dir: Path,
    *,
    refresh: bool = False,
    downloader: Downloader = _default_downloader,
) -> tuple[FetchResult, ...]:
    """Fetch and verify sources, replacing each destination atomically."""
    return tuple(
        fetch_source(source, output_dir, refresh=refresh, downloader=downloader)
        for source in sources
    )


def offline_verify(
    sources: Sequence[SourceSpec], output_dir: Path
) -> tuple[FetchResult, ...]:
    """Verify every expected source without constructing or calling a downloader."""
    failures: list[str] = []
    results: list[FetchResult] = []
    for source in sources:
        path = output_dir / source.filename
        try:
            verify_file(path, source)
        except IntegrityError as error:
            failures.append(str(error))
        else:
            results.append(FetchResult(source, path, "verified"))
    if failures:
        raise IntegrityError("Offline verification failed:\n- " + "\n- ".join(failures))
    return tuple(results)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch the official Blue Book V3 PDF, chapter HTML, and corrections "
            "using integrity metadata from generated artifacts."
        )
    )
    parser.add_argument(
        "--output-dir",
        "--cache-dir",
        dest="output_dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
    )
    parser.add_argument(
        "--source-corpus",
        type=Path,
        default=ROLE_ARTIFACTS["canonical_pdf"],
    )
    parser.add_argument(
        "--reference-occurrences",
        type=Path,
        default=ROLE_ARTIFACTS["chapter_html"],
    )
    parser.add_argument(
        "--correction-overlays",
        type=Path,
        default=ROLE_ARTIFACTS["corrections_html"],
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--refresh",
        action="store_true",
        help="Download every source again, while retaining old files until verified.",
    )
    mode.add_argument(
        "--offline-verify",
        action="store_true",
        help="Verify all cached files without making network requests.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        sources = load_source_specs(
            args.source_corpus,
            args.reference_occurrences,
            args.correction_overlays,
        )
        if args.offline_verify:
            results = offline_verify(sources, args.output_dir)
        else:
            results = fetch_sources(sources, args.output_dir, refresh=args.refresh)
    except SourceFetchError as error:
        print(error, file=sys.stderr)
        return 1

    for result in results:
        print(f"{result.action}: {result.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
