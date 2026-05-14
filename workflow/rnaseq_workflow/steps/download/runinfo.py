from __future__ import annotations

import csv
import json
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import StringIO
from pathlib import Path


RUNINFO_ENDPOINT = "https://trace.ncbi.nlm.nih.gov/Traces/sra-db-be/runinfo"


@dataclass(frozen=True, slots=True)
class SraRunMetadata:
    run: str
    bioproject: str = ""
    biosample: str = ""
    experiment: str = ""
    sample: str = ""
    taxid: str = ""
    scientific_name: str = ""
    library_strategy: str = ""
    library_selection: str = ""
    library_source: str = ""
    library_layout: str = ""
    platform: str = ""
    model: str = ""
    center_name: str = ""
    size_mb: str = ""
    spots: str = ""
    bases: str = ""
    metadata_source: str = "ncbi_runinfo"
    metadata_fetched_at: str = ""
    raw: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SraMetadataGroup:
    key: tuple[str, str, str, str, str]
    runs: list[SraRunMetadata]

    @property
    def taxid(self) -> str:
        return self.key[0]

    @property
    def scientific_name(self) -> str:
        return self.key[1]

    @property
    def bioproject(self) -> str:
        return self.key[2]

    @property
    def library_layout(self) -> str:
        return self.key[3]

    @property
    def library_source(self) -> str:
        return self.key[4]


def fetch_sra_run_size_bytes(accession: str, timeout_seconds: float = 20.0) -> int | None:
    rows = fetch_sra_runinfo_rows([accession], timeout_seconds=timeout_seconds)
    if not rows:
        return None
    raw = (rows[0].get("size_MB") or "").strip()
    if not raw:
        return None
    try:
        return int(float(raw) * 1024 * 1024)
    except ValueError:
        return None


def fetch_sra_runinfo_rows(accessions: list[str], timeout_seconds: float = 20.0) -> list[dict[str, str]]:
    if not accessions:
        return []
    acc = ",".join(accession.strip().upper() for accession in accessions if accession.strip())
    url = RUNINFO_ENDPOINT + "?" + urllib.parse.urlencode({"acc": acc})
    with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
        text = response.read().decode("utf-8", errors="replace")
    return list(csv.DictReader(StringIO(text)))


def fetch_sra_metadata(accessions: list[str], timeout_seconds: float = 20.0) -> list[SraRunMetadata]:
    fetched_at = datetime.now().isoformat(timespec="seconds")
    return [_metadata_from_runinfo_row(row, fetched_at=fetched_at) for row in fetch_sra_runinfo_rows(accessions, timeout_seconds)]


def write_sra_metadata_sidecars(metadata: list[SraRunMetadata], output_dir: str | Path) -> list[Path]:
    written: list[Path] = []
    base = Path(output_dir)
    for record in metadata:
        root = base / record.run
        root.mkdir(parents=True, exist_ok=True)
        path = root / "metadata.json"
        path.write_text(json.dumps(asdict(record), ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def load_sra_metadata_sidecar(accession: str, output_dir: str | Path) -> SraRunMetadata | None:
    path = Path(output_dir) / accession / "metadata.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return SraRunMetadata(**data)


def group_sra_metadata(metadata: list[SraRunMetadata]) -> list[SraMetadataGroup]:
    grouped: dict[tuple[str, str, str, str, str], list[SraRunMetadata]] = {}
    for record in metadata:
        key = (
            record.taxid,
            record.scientific_name,
            record.bioproject,
            record.library_layout,
            record.library_source,
        )
        grouped.setdefault(key, []).append(record)
    return [SraMetadataGroup(key=key, runs=sorted(runs, key=lambda item: item.run)) for key, runs in sorted(grouped.items())]


def metadata_has_mixed_groups(metadata: list[SraRunMetadata]) -> bool:
    return len(group_sra_metadata(metadata)) > 1


def _metadata_from_runinfo_row(row: dict[str, str], fetched_at: str) -> SraRunMetadata:
    return SraRunMetadata(
        run=row.get("Run", ""),
        bioproject=row.get("BioProject", ""),
        biosample=row.get("BioSample", ""),
        experiment=row.get("Experiment", ""),
        sample=row.get("Sample", ""),
        taxid=row.get("TaxID", ""),
        scientific_name=row.get("ScientificName", ""),
        library_strategy=row.get("LibraryStrategy", ""),
        library_selection=row.get("LibrarySelection", ""),
        library_source=row.get("LibrarySource", ""),
        library_layout=row.get("LibraryLayout", ""),
        platform=row.get("Platform", ""),
        model=row.get("Model", ""),
        center_name=row.get("CenterName", ""),
        size_mb=row.get("size_MB", ""),
        spots=row.get("spots", ""),
        bases=row.get("bases", ""),
        metadata_fetched_at=fetched_at,
        raw=dict(row),
    )
