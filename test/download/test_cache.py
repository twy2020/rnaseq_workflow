from __future__ import annotations

from rnaseq_workflow.steps.download.cache import (
    cleanup_accession_artifacts,
    cleanup_stale_sra_locks,
    directory_size,
    find_partial_sra_artifacts,
    find_cached_sra,
    has_partial_markers,
    lock_path,
)


def test_find_cached_sra_direct_file(tmp_path):
    sra = tmp_path / "SRR001.sra"
    sra.write_bytes(b"abc")

    assert find_cached_sra("SRR001", tmp_path) == sra


def test_find_cached_sra_nested_file(tmp_path):
    nested = tmp_path / "SRR001"
    nested.mkdir()
    sra = nested / "SRR001.sra"
    sra.write_bytes(b"abc")

    assert find_cached_sra("SRR001", tmp_path) == sra


def test_find_cached_sra_rejects_partial_markers(tmp_path):
    nested = tmp_path / "SRR001"
    nested.mkdir()
    (nested / "SRR001.sra").write_bytes(b"abc")
    (nested / "SRR001.sra.prf").write_bytes(b"partial")

    assert find_cached_sra("SRR001", tmp_path) is None
    assert has_partial_markers("SRR001", nested)


def test_find_partial_sra_artifacts(tmp_path):
    nested = tmp_path / "SRR001"
    nested.mkdir()
    tmp = nested / "SRR001.sra.tmp"
    tmp.write_bytes(b"abc")
    prf = nested / "SRR001.sra.prf"
    prf.write_bytes(b"p")

    found = find_partial_sra_artifacts("SRR001", tmp_path)

    assert tmp in found
    assert prf in found


def test_cleanup_stale_sra_locks_only_removes_accession_locks(tmp_path):
    nested = tmp_path / "SRR001"
    nested.mkdir()
    lock = nested / "SRR001.sra.lock"
    lock.write_text("", encoding="utf-8")
    tmp = nested / "SRR001.sra.tmp"
    tmp.write_text("partial", encoding="utf-8")
    global_lock = tmp_path / ".locks"
    global_lock.mkdir()
    manager_lock = global_lock / "SRR001.lock"
    manager_lock.write_text("", encoding="utf-8")

    removed = cleanup_stale_sra_locks("SRR001", tmp_path)

    assert removed == [lock]
    assert not lock.exists()
    assert tmp.exists()
    assert manager_lock.exists()


def test_directory_size(tmp_path):
    (tmp_path / "a.bin").write_bytes(b"abc")
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "b.bin").write_bytes(b"1234")

    assert directory_size(tmp_path) == 7


def test_lock_path_creates_lock_dir(tmp_path):
    path = lock_path("SRR001", tmp_path)

    assert path.name == "SRR001.lock"
    assert path.parent.is_dir()


def test_cleanup_accession_artifacts(tmp_path):
    (tmp_path / "SRR001.sra").write_bytes(b"abc")
    accession_dir = tmp_path / "SRR001"
    accession_dir.mkdir()
    (accession_dir / "SRR001.sra").write_bytes(b"abc")
    tmp_dir = tmp_path / ".tmp" / "SRR001"
    tmp_dir.mkdir(parents=True)
    (tmp_dir / "partial").write_bytes(b"abc")

    cleanup_accession_artifacts("SRR001", tmp_path)

    assert not (tmp_path / "SRR001.sra").exists()
    assert not accession_dir.exists()
    assert not tmp_dir.exists()
