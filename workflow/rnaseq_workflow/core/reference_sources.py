from __future__ import annotations

import gzip
import re
import shutil
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from rnaseq_workflow.core.models import RunContext
from rnaseq_workflow.core.references import (
    ReferenceAsset,
    build_hisat2_index_for_reference,
    register_reference,
)


ENSEMBL_BASES = {
    "vertebrates": "https://ftp.ensembl.org/pub",
    "plants": "https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants",
    "fungi": "https://ftp.ensemblgenomes.ebi.ac.uk/pub/fungi",
    "metazoa": "https://ftp.ensemblgenomes.ebi.ac.uk/pub/metazoa",
    "protists": "https://ftp.ensemblgenomes.ebi.ac.uk/pub/protists",
}


@dataclass(frozen=True, slots=True)
class ReferenceDownloadPlan:
    reference_id: str
    fasta_url: str
    annotation_url: str
    fasta_download: Path
    annotation_download: Path
    fasta_ready: Path
    annotation_ready: Path


@dataclass(frozen=True, slots=True)
class PreparedReference:
    asset: ReferenceAsset
    plan: ReferenceDownloadPlan
    index_command: list[str] | None = None
    index_return_code: int | None = None
    dry_run: bool = False


def build_ensembl_reference_urls(
    species: str,
    division: str = "vertebrates",
    release: str = "current",
    fasta_kind: str = "primary_assembly",
) -> tuple[str, str]:
    species_id = normalize_species_name(species)
    base = _ensembl_release_base(division, release)
    fasta_dir = f"{base}/fasta/{species_id}/dna/"
    gtf_dir = f"{base}/gtf/{species_id}/"
    fasta_url = _select_ensembl_fasta_url(fasta_dir, fasta_kind=fasta_kind)
    annotation_url = _select_ensembl_gtf_url(gtf_dir)
    return fasta_url, annotation_url


def prepare_reference_from_urls(
    reference_id: str,
    fasta_url: str,
    annotation_url: str,
    reference_dir: str | Path,
    download_dir: str | Path,
    context: RunContext | None = None,
    threads: int = 4,
    build_index: bool = True,
    force: bool = False,
    keep_compressed: bool = True,
    provider: str = "custom",
    annotation_provider: str | None = None,
    species: str | None = None,
    assembly: str | None = None,
    release: str | None = None,
    taxon_id: str | None = None,
    created_by: str = "download",
    allow_mixed_source: bool = False,
) -> PreparedReference:
    plan = build_reference_download_plan(reference_id, fasta_url, annotation_url, download_dir)
    _download_file(plan.fasta_url, plan.fasta_download, force=force)
    _download_file(plan.annotation_url, plan.annotation_download, force=force)
    _decompress_if_needed(plan.fasta_download, plan.fasta_ready, force=force, keep_source=keep_compressed)
    _decompress_if_needed(plan.annotation_download, plan.annotation_ready, force=force, keep_source=keep_compressed)
    asset = register_reference(
        reference_id,
        fasta=plan.fasta_ready,
        annotation=plan.annotation_ready,
        reference_dir=reference_dir,
        copy_files=True,
        overwrite=force,
        provider=provider,
        annotation_provider=annotation_provider or provider,
        species=species,
        assembly=assembly,
        release=release,
        taxon_id=taxon_id,
        source_urls=[plan.fasta_url, plan.annotation_url],
        annotation_format=_annotation_format_from_url(plan.annotation_url),
        created_by=created_by,
        build_status="registered",
        allow_mixed_source=allow_mixed_source,
        notes=f"Downloaded from {plan.fasta_url} and {plan.annotation_url}",
    )
    index_command = None
    index_return_code = None
    dry_run = False
    if build_index:
        if context is None:
            raise ValueError("context is required when build_index=True")
        asset, result = build_hisat2_index_for_reference(
            reference_id=asset.reference_id,
            reference_dir=reference_dir,
            context=context,
            threads=threads,
            force=force,
        )
        index_command = result.command
        index_return_code = result.return_code
        dry_run = result.dry_run
        if not result.ok:
            raise RuntimeError(result.stderr or f"hisat2-build failed with code {result.return_code}")
    return PreparedReference(asset=asset, plan=plan, index_command=index_command, index_return_code=index_return_code, dry_run=dry_run)


def build_reference_download_plan(
    reference_id: str,
    fasta_url: str,
    annotation_url: str,
    download_dir: str | Path,
) -> ReferenceDownloadPlan:
    root = Path(download_dir) / reference_id
    fasta_download = root / _filename_from_url(fasta_url)
    annotation_download = root / _filename_from_url(annotation_url)
    return ReferenceDownloadPlan(
        reference_id=reference_id,
        fasta_url=fasta_url,
        annotation_url=annotation_url,
        fasta_download=fasta_download,
        annotation_download=annotation_download,
        fasta_ready=_ready_path(fasta_download),
        annotation_ready=_ready_path(annotation_download),
    )


def normalize_species_name(species: str) -> str:
    return species.strip().lower().replace(" ", "_")


def _ensembl_release_base(division: str, release: str) -> str:
    division_key = division.lower()
    if division_key not in ENSEMBL_BASES:
        raise ValueError(f"unsupported Ensembl division: {division}")
    base = ENSEMBL_BASES[division_key]
    release_key = release.strip().lower()
    if division_key == "vertebrates":
        return f"{base}/current" if release_key == "current" else f"{base}/release-{release}"
    return f"{base}/current" if release_key == "current" else f"{base}/release-{release}"


def _select_ensembl_fasta_url(directory_url: str, fasta_kind: str) -> str:
    hrefs = _list_hrefs(directory_url)
    fa_gz = [href for href in hrefs if href.endswith(".fa.gz")]
    preferences = []
    if fasta_kind == "primary_assembly":
        preferences.extend([".dna.primary_assembly.fa.gz", ".dna.toplevel.fa.gz"])
    elif fasta_kind == "toplevel":
        preferences.extend([".dna.toplevel.fa.gz", ".dna.primary_assembly.fa.gz"])
    else:
        preferences.append(f".dna.{fasta_kind}.fa.gz")
    for suffix in preferences:
        matches = [href for href in fa_gz if href.endswith(suffix)]
        if matches:
            return urllib.parse.urljoin(directory_url, sorted(matches)[0])
    if fa_gz:
        return urllib.parse.urljoin(directory_url, sorted(fa_gz)[0])
    raise FileNotFoundError(f"no FASTA .fa.gz found at {directory_url}")


def _select_ensembl_gtf_url(directory_url: str) -> str:
    hrefs = _list_hrefs(directory_url)
    gtf_gz = [
        href
        for href in hrefs
        if href.endswith(".gtf.gz") and "abinitio" not in href.lower() and "chr.gtf.gz" not in href.lower()
    ]
    if gtf_gz:
        return urllib.parse.urljoin(directory_url, sorted(gtf_gz)[0])
    raise FileNotFoundError(f"no GTF .gtf.gz found at {directory_url}")


def _list_hrefs(url: str) -> list[str]:
    with urllib.request.urlopen(url, timeout=60) as response:
        html = response.read().decode("utf-8", errors="ignore")
    return re.findall(r'href=["\']([^"\']+)["\']', html)


def _download_file(url: str, output: Path, force: bool = False) -> Path:
    if output.exists() and output.stat().st_size > 0 and not force:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response, output.open("wb") as handle:
        shutil.copyfileobj(response, handle)
    return output


def _decompress_if_needed(source: Path, output: Path, force: bool = False, keep_source: bool = True) -> Path:
    if source.suffix != ".gz":
        return source
    if output.exists() and output.stat().st_size > 0 and not force:
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(source, "rb") as src, output.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    if not keep_source:
        source.unlink(missing_ok=True)
    return output


def _filename_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = Path(path).name
    if not name:
        raise ValueError(f"cannot infer filename from URL: {url}")
    return name


def _ready_path(path: Path) -> Path:
    if path.suffix == ".gz":
        return path.with_suffix("")
    return path


def _annotation_format_from_url(url: str) -> str | None:
    name = Path(urllib.parse.urlparse(url).path).name.lower()
    if name.endswith(".gtf.gz") or name.endswith(".gtf"):
        return "gtf"
    if name.endswith(".gff3.gz") or name.endswith(".gff3"):
        return "gff3"
    if name.endswith(".gff.gz") or name.endswith(".gff"):
        return "gff"
    return None
