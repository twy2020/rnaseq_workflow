from __future__ import annotations

import socket

from rnaseq_workflow.core.models import StepStatus
from rnaseq_workflow.steps.download.ena import EnaFastqDownloader, EnaRunFiles, EnaFastqFile, fetch_ena_fastq_files
from rnaseq_workflow.steps.download.models import DownloadRequest


def test_fetch_ena_fastq_files(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return (
                b"run_accession\tfastq_ftp\tfastq_md5\tfastq_bytes\tlibrary_layout\n"
                b"SRR1\tftp.sra.ebi.ac.uk/a_1.fastq.gz;ftp.sra.ebi.ac.uk/a_2.fastq.gz\tm1;m2\t10;20\tPAIRED\n"
            )

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: Response())

    run_files = fetch_ena_fastq_files("SRR1")

    assert run_files is not None
    assert run_files.layout == "PAIRED"
    assert len(run_files.files) == 2
    assert run_files.files[0].url.startswith("https://")
    assert run_files.files[1].size_bytes == 20


def test_ena_downloader_retries_stalled_partial_download(monkeypatch, tmp_path):
    run_files = EnaRunFiles(
        accession="SRR1",
        layout="SINGLE",
        files=[EnaFastqFile(url="https://example.test/SRR1_1.fastq.gz", md5=None, size_bytes=8, filename="SRR1_1.fastq.gz")],
    )
    attempts = []

    class Response:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self, size):
            chunk = self.chunks.pop(0)
            if isinstance(chunk, BaseException):
                raise chunk
            return chunk

    def fake_urlopen(request, timeout):
        attempts.append((request.headers.get("Range"), timeout))
        if len(attempts) == 1:
            return Response([b"abcd", socket.timeout("stalled")])
        return Response([b"efgh", b""])

    monkeypatch.setattr("rnaseq_workflow.steps.download.ena.fetch_ena_fastq_files", lambda accession: run_files)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("rnaseq_workflow.steps.download.ena.shutil.which", lambda command: None)

    downloader = EnaFastqDownloader(stall_timeout_seconds=30, retries=1, retry_delay_seconds=0)
    result = downloader.download(DownloadRequest("SRR1", tmp_path))

    assert result.status == StepStatus.COMPLETED
    assert (tmp_path / "SRR1" / "SRR1_1.fastq.gz").read_bytes() == b"abcdefgh"
    assert not (tmp_path / "SRR1" / "SRR1_1.fastq.gz.part").exists()
    assert attempts == [(None, 30), ("bytes=4-", 30)]


def test_ena_downloader_passes_proxy_to_curl(monkeypatch, tmp_path):
    run_files = EnaRunFiles(
        accession="SRR1",
        layout="SINGLE",
        files=[EnaFastqFile(url="https://example.test/SRR1.fastq.gz", md5=None, size_bytes=4, filename="SRR1.fastq.gz")],
    )
    commands = []

    class Process:
        returncode = 0
        stderr = None

        def __init__(self, command, stdout=None, stderr=None, text=False):
            commands.append(command)
            output = tmp_path / "SRR1" / "SRR1.fastq.gz.part"
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"abcd")

        def poll(self):
            return 0

    monkeypatch.setattr("rnaseq_workflow.steps.download.ena.fetch_ena_fastq_files", lambda accession: run_files)
    monkeypatch.setattr("rnaseq_workflow.steps.download.ena.shutil.which", lambda command: "curl.exe")
    monkeypatch.setattr("rnaseq_workflow.steps.download.ena.subprocess.Popen", Process)

    result = EnaFastqDownloader(proxy="http://127.0.0.1:7890").download(DownloadRequest("SRR1", tmp_path))

    assert result.status == StepStatus.COMPLETED
    assert "--proxy" in commands[0]
    assert "http://127.0.0.1:7890" in commands[0]
