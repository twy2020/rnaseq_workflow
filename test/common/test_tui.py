from __future__ import annotations

import json
from pathlib import Path

from rnaseq_workflow.cli.tui import _is_interactive_terminal


def test_tui_imports():
    from rnaseq_workflow.cli.tui import run_tui

    assert callable(run_tui)


def test_home_menu_values_are_grouped(monkeypatch):
    from rich.console import Console

    from rnaseq_workflow.cli import tui

    captured = {}

    def fake_menu(title, text, values):
        captured["values"] = [value for value, label in values]
        return "exit"

    monkeypatch.setattr(tui, "_menu", fake_menu)
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui.run_tui(Console(), fallback_when_not_tty=False)

    assert captured["values"] == ["doctor", "assets", "config", "workflow", "reference", "tools", "system", "output", "exit"]


def test_tools_menu_contains_single_step_entries(monkeypatch):
    from rich.console import Console

    from rnaseq_workflow.cli import tui

    captured = {}

    def fake_menu(title, text, values):
        captured["values"] = [value for value, label in values]
        return "back"

    monkeypatch.setattr(tui, "_menu", fake_menu)

    tui._tools_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))

    assert "fastqc" in captured["values"]
    assert "featurecounts" in captured["values"]
    assert "report" in captured["values"]


def test_tui_path_input_helper_can_be_patched(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(tui, "_input", lambda title, text, default="": "config.yaml")

    assert tui._path_input("config") == Path("config.yaml")


def test_is_interactive_terminal_returns_bool():
    assert isinstance(_is_interactive_terminal(), bool)


def test_capture_output_stores_rendered_text(monkeypatch):
    from rich.console import Console

    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(tui, "_message", lambda title, text: None)
    state = tui.TuiState(config=Path("config.yaml"), console=Console())

    output = tui._capture_output(state, lambda console: console.print("hello"), "title")

    assert "hello" in output
    assert state.output_log == output


def test_truncate_output_keeps_tail():
    from rnaseq_workflow.cli.tui import _truncate_output

    assert _truncate_output("abcdef", limit=3) == "def"


def test_dialog_text_contains_keyboard_hint():
    from rnaseq_workflow.cli import tui

    text = str(tui._dialog_text("hello", include_multiselect=True))

    assert "Space" in text


def test_escape_html_for_dialog_text():
    from rnaseq_workflow.cli import tui

    assert tui._escape_html("<x&y>") == "&lt;x&amp;y&gt;"


def test_line_dialog_mode_can_be_forced(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setenv("RNASEQ_TUI_MODE", "line")

    assert tui._use_line_dialogs()


def test_line_menu_selects_number(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr("builtins.input", lambda prompt="": "2")

    assert tui._line_menu("菜单", "说明", [("a", "A"), ("b", "B")]) == "b"


def test_menu_uses_keyboard_menu_when_not_line_mode(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setenv("RNASEQ_TUI_MODE", "dialog")
    monkeypatch.setattr(tui, "_keyboard_menu", lambda title, text, values: values[0][0])

    assert tui._menu("菜单", "说明", [("a", "A")]) == "a"


def test_line_input_uses_default_on_enter(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    assert tui._line_input("输入", "说明", "default") == "default"


def test_line_multiselect_defaults_on_enter(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr("builtins.input", lambda prompt="": "")

    assert tui._line_multiselect("选择", [("S1", "one"), ("S2", "two")], ["S1", "S2"]) == ["S1", "S2"]


def test_simple_download_menu_only_prompts_target(monkeypatch):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.steps.download.models import BatchDownloadSummary

    def fake_build_requests(target, output_dir, fetch_expected_sizes=True):
        assert target == "SRR000001"
        assert str(output_dir) == "downloads"
        assert fetch_expected_sizes is False
        return []

    def fake_tui_progress(manager, requests, dry_run, title):
        assert dry_run is False
        assert manager.max_workers == tui.DEFAULT_TUI_CONCURRENCY
        assert manager.downloader.sra_downloader.execution_mode == "docker"
        assert manager.downloader.sra_downloader.max_size == "5G"
        assert title == "下载: SRR000001"
        return BatchDownloadSummary(results=[])

    monkeypatch.setattr(
        tui,
        "_download_wizard",
        lambda title, state, advanced: {
            "target": "SRR000001",
            "output_dir": Path("downloads"),
            "max_size": "5G",
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "max_workers": tui.DEFAULT_TUI_CONCURRENCY,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_message", lambda title, text: None)
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")
    monkeypatch.setattr(tui, "_preflight_sra_metadata_for_download", lambda requests, output_dir, state: True)
    monkeypatch.setattr(tui, "build_smart_download_requests", fake_build_requests)
    monkeypatch.setattr(tui, "_run_download_with_tui_progress", fake_tui_progress)

    tui._download_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))


def test_create_task_sets_current_task(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui

    answers = iter(["demo task", "description"])
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": next(answers) if title != "user UUID" else "user-1")
    monkeypatch.setattr(tui, "_menu", lambda title, text, values: "temp")
    monkeypatch.setattr(tui, "_message", lambda title, text: None)
    state = tui.TuiState(config=tmp_path / "config.yaml", console=Console(), asset_root=tmp_path / "workspace")

    task = tui._create_task(state)

    assert task is not None
    assert state.user_id == "user-1"
    assert state.task_id == task.task_id
    assert task.root.parent == tmp_path / "workspace" / "users" / "user-1" / "tasks"
    assert task.read_metadata().task_name == "demo task"
    assert state.workspace.database.list_tasks("user-1")[0].task_id == task.task_id


def test_workflow_manifest_page_writes_metadata(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    state = tui.TuiState(
        config=tmp_path / "config.yaml",
        console=Console(),
        asset_root=tmp_path / "workspace",
        user_id="user-1",
        task_id=task.task_id,
    )
    monkeypatch.setattr(tui, "_menu", lambda title, text, values: "sra")
    monkeypatch.setattr(tui, "_multiline_input", lambda title, text, default="": "SRR000001\nSRR000002")
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui._workflow_manifest_page(state)

    assert (task.metadata_dir / "manifest.json").exists()
    assert "SRR000001" in (task.metadata_dir / "manifest.json").read_text(encoding="utf-8")


def test_workflow_manifest_page_reuses_existing_raw(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    (task.metadata_dir / "manifest.json").write_text(
        '{"raw": "SRR123\\nSRR456", "accessions": ["SRR123", "SRR456"], "urls": [], "errors": []}',
        encoding="utf-8",
    )
    state = tui.TuiState(
        config=tmp_path / "config.yaml",
        console=Console(),
        asset_root=tmp_path / "workspace",
        user_id="user-1",
        task_id=task.task_id,
    )
    seen_defaults = []
    monkeypatch.setattr(tui, "_menu", lambda title, text, values: "sra")
    monkeypatch.setattr(tui, "_multiline_input", lambda title, text, default="": seen_defaults.append(default) or default)
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui._workflow_manifest_page(state)

    assert seen_defaults == ["SRR123\nSRR456"]


def test_workflow_manifest_page_records_local_files(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    fastq = local_dir / "S1_R1.fastq.gz"
    fastq.write_text("x", encoding="utf-8")
    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    state = tui.TuiState(
        config=tmp_path / "config.yaml",
        console=Console(),
        asset_root=tmp_path / "workspace",
        user_id="user-1",
        task_id=task.task_id,
    )
    monkeypatch.setattr(tui, "_menu", lambda title, text, values: "local")
    monkeypatch.setattr(tui, "_multiline_input", lambda title, text, default="", completer=None: str(local_dir))
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui._workflow_manifest_page(state)

    data = __import__("json").loads((task.metadata_dir / "manifest.json").read_text(encoding="utf-8"))
    assert data["local_files"][0]["path"] == str(fastq)


def test_prepare_workflow_inputs_uses_local_manifest_without_copy(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.task_params import TaskParams

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    fastq = local_dir / "S1.fastq.gz"
    fastq.write_text("x", encoding="utf-8")
    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    (task.metadata_dir / "manifest.json").write_text(
        __import__("json").dumps({"local_files": [{"path": str(fastq), "sample_id": "S1", "input_type": "fastq"}], "errors": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    prepared = tui._prepare_workflow_inputs_from_manifest(task, TaskParams())

    assert [sample.source_path for sample in prepared] == [fastq]
    assert not (task.downloads_dir / fastq.name).exists()


def test_prepare_workflow_inputs_writes_download_report_for_slots_result(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.models import StepStatus
    from rnaseq_workflow.core.task_params import TaskParams
    from rnaseq_workflow.steps.download.models import BatchDownloadSummary, DownloadResult

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    (task.metadata_dir / "manifest.json").write_text(
        __import__("json").dumps({"accessions": ["SRR1"], "urls": [], "errors": []}),
        encoding="utf-8",
    )
    monkeypatch.setattr(tui, "_downloader_for_params", lambda params: object())
    seen_workers = []

    def fake_run(manager, requests, dry_run, title):
        seen_workers.append(manager.max_workers)
        return BatchDownloadSummary(results=[DownloadResult(accession="SRR1", status=StepStatus.COMPLETED, downloaded_bytes=10)])

    monkeypatch.setattr(tui, "_run_download_with_tui_progress", fake_run)

    prepared = tui._prepare_workflow_inputs_from_manifest(task, TaskParams(max_workers=8, download_workers=1))

    report = task.reports_dir / "workflow_download_results.json"
    assert prepared == task.downloads_dir / "ena_fastq"
    assert seen_workers == [1]
    assert report.exists()
    assert '"accession": "SRR1"' in report.read_text(encoding="utf-8")


def test_prepare_workflow_inputs_separates_download_sources(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.models import StepStatus
    from rnaseq_workflow.core.task_params import TaskParams
    from rnaseq_workflow.steps.download.models import BatchDownloadSummary, DownloadResult

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    (task.metadata_dir / "manifest.json").write_text(
        __import__("json").dumps({"accessions": ["SRR1"], "urls": [], "errors": []}),
        encoding="utf-8",
    )
    seen_dirs = []
    monkeypatch.setattr(tui, "_downloader_for_params", lambda params: object())

    def fake_run(manager, requests, dry_run, title):
        seen_dirs.append(requests[0].output_dir)
        return BatchDownloadSummary(results=[DownloadResult(accession="SRR1", status=StepStatus.COMPLETED)])

    monkeypatch.setattr(tui, "_run_download_with_tui_progress", fake_run)

    assert tui._prepare_workflow_inputs_from_manifest(task, TaskParams(download_source="ena")) == task.downloads_dir / "ena_fastq"
    assert tui._prepare_workflow_inputs_from_manifest(task, TaskParams(download_source="sra")) == task.downloads_dir / "ncbi_sra"
    assert seen_dirs == [task.downloads_dir / "ena_fastq", task.downloads_dir / "ncbi_sra"]


def test_edit_current_task_updates_metadata_and_database(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task(task_name="old")
    state = tui.TuiState(config=tmp_path / "config.yaml", console=Console(), asset_root=tmp_path / "workspace", user_id="user-1", task_id=task.task_id)
    answers = iter(["new name", "new desc"])
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": next(answers))
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui._edit_current_task(state)

    assert task.read_metadata().task_name == "new name"
    assert state.workspace.database.list_tasks("user-1")[0].task_name == "new name"


def test_delete_current_task_removes_metadata_and_db(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    workspace = AssetWorkspace(tmp_path / "workspace")
    task = workspace.ensure_user("user-1").create_task(task_name="doomed")
    workspace.database.upsert_task(task_id=task.task_id, user_id="user-1", task_dir=task.root, task_name="doomed")
    state = tui.TuiState(config=tmp_path / "config.yaml", console=Console(), asset_root=tmp_path / "workspace", user_id="user-1", task_id=task.task_id)
    monkeypatch.setattr(tui, "_yes_no", lambda title, default: True)
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": "doomed")
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui._delete_current_task(state)

    assert not task.root.exists()
    assert state.task_id is None
    assert workspace.database.list_tasks("user-1") == []


def test_task_artifact_targets_and_cleanup(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    (task.downloads_dir / "ena_fastq" / "S1").mkdir(parents=True)
    (task.downloads_dir / "ena_fastq" / "S1" / "S1.fastq.gz.part").write_text("abcd", encoding="utf-8")
    (task.samples_dir / "S1" / "qc_raw").mkdir(parents=True)
    (task.samples_dir / "S1" / "qc_raw" / "fastqc.zip").write_text("xy", encoding="utf-8")
    (task.reports_dir / "report.json").write_text("{}", encoding="utf-8")

    targets = tui._task_artifact_targets(task)

    assert next(target for target in targets if target.key == "downloads").size_bytes == 4
    assert next(target for target in targets if target.key == "samples").files == 1

    removed = tui._remove_task_artifacts(task, [target for target in targets if target.key in {"downloads", "samples"}])

    assert removed == 6
    assert task.downloads_dir.exists()
    assert task.samples_dir.exists()
    assert not any(task.downloads_dir.rglob("*.*"))
    assert (task.reports_dir / "report.json").exists()


def test_workflow_resource_check_writes_metadata(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.resource_check import ResourceCheck

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    state = tui.TuiState(
        config=tmp_path / "config.yaml",
        console=Console(),
        asset_root=tmp_path / "workspace",
        user_id="user-1",
        task_id=task.task_id,
    )
    monkeypatch.setattr(tui, "run_resource_checks", lambda root, docker_image, estimate=None: [ResourceCheck("disk", "info", True, "ok")])
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")

    tui._workflow_resource_check_page(state)

    assert (task.metadata_dir / "resource_check.json").exists()
    assert "resource_estimate" in (task.metadata_dir / "params.json").read_text(encoding="utf-8")


def test_workflow_resource_check_counts_local_manifest(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.resource_check import ResourceCheck

    local_dir = tmp_path / "local"
    local_dir.mkdir()
    fastq = local_dir / "S1_R1.fastq.gz"
    fastq.write_text("x" * 10, encoding="utf-8")
    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    (task.metadata_dir / "manifest.json").write_text(
        __import__("json").dumps(
            {
                "local_files": [{"path": str(fastq), "sample_id": "S1", "input_type": "fastq", "size_bytes": 10}],
                "errors": [],
            }
        ),
        encoding="utf-8",
    )
    state = tui.TuiState(
        config=tmp_path / "config.yaml",
        console=Console(),
        asset_root=tmp_path / "workspace",
        user_id="user-1",
        task_id=task.task_id,
    )
    seen = []
    monkeypatch.setattr(tui, "run_resource_checks", lambda root, docker_image, estimate=None: seen.append(estimate) or [ResourceCheck("disk", "info", True, "ok")])
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")

    tui._workflow_resource_check_page(state)

    assert seen[0].sample_count == 1
    assert seen[0].input_file_count == 1
    assert seen[0].input_size_bytes == 10


def test_download_menu_uses_task_downloads_dir(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.steps.download.models import BatchDownloadSummary

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    state = tui.TuiState(
        config=tmp_path / "config.yaml",
        console=Console(),
        asset_root=tmp_path / "workspace",
        user_id="user-1",
        task_id=task.task_id,
    )

    monkeypatch.setattr(
        tui,
        "_download_wizard",
        lambda title, state, advanced: {
            "target": "SRR000001",
            "output_dir": task.downloads_dir,
            "max_size": "5G",
            "execution_mode": "docker",
            "docker_image": "rnaseq-workflow:tools",
            "max_workers": tui.DEFAULT_TUI_CONCURRENCY,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_message", lambda title, text: None)
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")
    monkeypatch.setattr(tui, "_preflight_sra_metadata_for_download", lambda requests, output_dir, state: True)

    def fake_build_requests(target, output_dir, fetch_expected_sizes=True):
        assert output_dir == task.downloads_dir
        return []

    monkeypatch.setattr(tui, "build_smart_download_requests", fake_build_requests)
    monkeypatch.setattr(tui, "_run_download_with_tui_progress", lambda manager, requests, dry_run, title: BatchDownloadSummary())

    tui._download_menu(state)


def test_download_preflight_blocks_mixed_groups_when_user_declines(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.steps.download.models import DownloadRequest
    from rnaseq_workflow.steps.download.runinfo import SraRunMetadata

    metadata = [
        SraRunMetadata(
            run="SRR1",
            bioproject="PRJ1",
            taxid="1",
            scientific_name="Species A",
            library_layout="SINGLE",
            library_source="TRANSCRIPTOMIC",
        ),
        SraRunMetadata(
            run="SRR2",
            bioproject="PRJ2",
            taxid="2",
            scientific_name="Species B",
            library_layout="PAIRED",
            library_source="TRANSCRIPTOMIC",
        ),
    ]

    monkeypatch.setattr(tui, "fetch_sra_metadata", lambda accessions: metadata)
    monkeypatch.setattr(tui, "write_sra_metadata_sidecars", lambda records, output_dir: [])
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")
    monkeypatch.setattr(tui, "_yes_no", lambda title, default=False: False)

    allowed = tui._preflight_sra_metadata_for_download(
        [
            DownloadRequest(accession="SRR1", output_dir=tmp_path),
            DownloadRequest(accession="SRR2", output_dir=tmp_path),
        ],
        tmp_path,
        tui.TuiState(config=tmp_path / "config.yaml", console=Console()),
    )

    assert not allowed


def test_sample_multiselect_blocks_mixed_metadata_groups(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout

    samples = [
        Sample(
            sample_id="S1",
            source_path=tmp_path / "S1.fastq.gz",
            source_paths=[tmp_path / "S1.fastq.gz"],
            layout=SampleLayout.SINGLE,
            metadata={
                "input_type": "fastq",
                "taxid": "1",
                "scientific_name": "Species A",
                "bioproject": "PRJ1",
                "library_layout": "SINGLE",
                "library_source": "TRANSCRIPTOMIC",
            },
        ),
        Sample(
            sample_id="S2",
            source_path=tmp_path / "S2.fastq.gz",
            source_paths=[tmp_path / "S2.fastq.gz"],
            layout=SampleLayout.SINGLE,
            metadata={
                "input_type": "fastq",
                "taxid": "2",
                "scientific_name": "Species B",
                "bioproject": "PRJ2",
                "library_layout": "SINGLE",
                "library_source": "TRANSCRIPTOMIC",
            },
        ),
    ]

    class Dialog:
        def run(self):
            return ["S1", "S2"]

    monkeypatch.setattr(tui, "checkboxlist_dialog", lambda **kwargs: Dialog())
    monkeypatch.setattr(tui, "_yes_no", lambda title, default=False: False)

    assert tui._sample_multiselect("samples", samples) == []


def test_scan_bam_samples_uses_sorted_bam_sample_id(tmp_path):
    from rnaseq_workflow.cli import tui

    bam = tmp_path / "samples" / "S1" / "alignment" / "S1.sorted.bam"
    bam.parent.mkdir(parents=True)
    bam.write_text("", encoding="utf-8")
    (bam.parent / "S1.sorted.bam.bai").write_text("", encoding="utf-8")

    samples = tui._scan_bam_samples(tmp_path, "P1")

    assert len(samples) == 1
    assert samples[0].sample_id == "S1"
    assert samples[0].source_path == bam
    assert samples[0].metadata["input_type"] == "bam"


def test_featurecounts_defaults_use_refseq_gff_gene():
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.references import ReferenceAsset

    asset = ReferenceAsset(
        reference_id="sarscov2",
        root=Path("references/sarscov2"),
        fasta=Path("genome.fna"),
        annotation=Path("genes.gff"),
        hisat2_index=Path("hisat2/genome"),
        created_at="",
        updated_at="",
        provider="refseq",
    )

    assert tui._featurecounts_defaults_for_reference(asset) == {
        "feature_type": "gene",
        "attribute_type": "gene",
        "strandness": "0",
    }


def test_scan_featurecounts_tables_ignores_summary(tmp_path):
    from rnaseq_workflow.cli import tui

    table = tmp_path / "samples" / "S1" / "quantification" / "S1.featureCounts.txt"
    table.parent.mkdir(parents=True)
    table.write_text("", encoding="utf-8")
    (table.parent / "S1.featureCounts.txt.summary").write_text("", encoding="utf-8")

    assert tui._scan_featurecounts_tables(tmp_path) == [table]


def test_report_menu_writes_matrix_and_reports(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui

    featurecounts_dir = tmp_path / "featurecounts"
    table1 = featurecounts_dir / "samples" / "S1" / "quantification" / "S1.featureCounts.txt"
    table2 = featurecounts_dir / "samples" / "S2" / "quantification" / "S2.featureCounts.txt"
    table1.parent.mkdir(parents=True)
    table2.parent.mkdir(parents=True)
    table1.write_text(
        "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1.sorted.bam\n"
        "geneA\tchr1\t1\t10\t+\t10\t1\n",
        encoding="utf-8",
    )
    table2.write_text(
        "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS2.sorted.bam\n"
        "geneA\tchr1\t1\t10\t+\t10\t2\n",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "reports"

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {"featurecounts_dir": featurecounts_dir, "project_id": "demo", "reports_dir": reports_dir},
    )
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: render(Console(file=__import__("io").StringIO())))
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    tui._report_menu(tui.TuiState(config=tmp_path / "config.yaml", console=Console()))

    assert (reports_dir / "count_matrix.tsv").read_text(encoding="utf-8").splitlines() == [
        "Geneid\tS1\tS2",
        "geneA\t1\t2",
    ]
    assert (reports_dir / "report.json").exists()
    assert (reports_dir / "report.md").exists()


def test_report_menu_creates_missing_report_dir(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui

    featurecounts_dir = tmp_path / "featurecounts"
    table = featurecounts_dir / "samples" / "S1" / "quantification" / "S1.featureCounts.txt"
    table.parent.mkdir(parents=True)
    table.write_text(
        "Geneid\tChr\tStart\tEnd\tStrand\tLength\tS1.sorted.bam\n"
        "geneA\tchr1\t1\t10\t+\t10\t1\n",
        encoding="utf-8",
    )
    reports_dir = tmp_path / "missing" / "reports"
    messages = []

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {"featurecounts_dir": featurecounts_dir, "project_id": "demo", "reports_dir": reports_dir},
    )
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "ok")
    monkeypatch.setattr(tui, "_message", lambda title, text: messages.append((title, text)))

    tui._report_menu(tui.TuiState(config=tmp_path / "config.yaml", console=Console()))

    assert reports_dir.exists()
    assert (reports_dir / "count_matrix.tsv").exists()
    assert messages
    assert messages[-1][0] == "结果汇总完成"


def test_download_progress_text_contains_status(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.steps.download.manager import DownloadManager
    from rnaseq_workflow.steps.download.models import DownloadRequest

    manager = DownloadManager()
    requests = [DownloadRequest(accession="SRR000001", output_dir=tmp_path, expected_size_bytes=1024)]

    text = tui._download_progress_text(manager, requests, "下载", dry_run=False, done=False)

    assert "SRR000001" in text
    assert "总进度" in text
    assert "实际下载" in text
    assert "[" in text
    assert "/1.0KB" in text or "PENDING" in text
    assert "估算中" not in text


def test_download_progress_text_shows_cancelled_when_done(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import StepStatus
    from rnaseq_workflow.steps.download.manager import DownloadManager
    from rnaseq_workflow.steps.download.models import DownloadProgress, DownloadRequest

    manager = DownloadManager()
    request = DownloadRequest(accession="SRR000001", output_dir=tmp_path)
    manager._update_progress(DownloadProgress(accession="SRR000001", status=StepStatus.CANCELLED, message="cancelled"))

    text = tui._download_progress_text(manager, [request], "下载", dry_run=False, done=True)

    assert "下载已取消" in text
    assert "下载已完成" not in text


def test_text_progress_bar():
    from rnaseq_workflow.cli.tui import _estimated_percent, _text_progress_bar

    assert _estimated_percent(512, 0, 1024) == 50.0
    assert _text_progress_bar(50, width=10) == "[#####.....]"
    assert _text_progress_bar(None, width=4) == "[....]"


def test_fastqc_menu_scans_and_runs_selected_sample(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout, StepResult, StepStatus

    fastq = tmp_path / "downloads" / "S1_1.fastq.gz"
    fastq.parent.mkdir()
    fastq.write_text("", encoding="utf-8")
    output_dir = tmp_path / "fastqc"
    sample = Sample(
        sample_id="S1",
        source_path=fastq,
        source_paths=[fastq],
        layout=SampleLayout.SINGLE,
        project_id="fastqc_test",
        metadata={"input_type": "fastq"},
    )

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {
            "input_dir": fastq.parent,
            "project_id": "fastqc_test",
            "output_dir": output_dir,
            "threads": 2,
            "max_workers": 2,
            "extract": False,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_choose_fastqc_target", lambda samples: [samples[0]])
    monkeypatch.setattr(tui, "scan_inputs", lambda input_dir, project_id=None: type("Scan", (), {"samples": [sample]})())
    monkeypatch.setattr(
        tui,
        "_run_step_with_tui_progress",
        lambda samples, context, step, title, max_workers=tui.DEFAULT_TUI_CONCURRENCY: [
            StepResult(
                sample_id=samples[0].sample_id,
                step_id="fastqc",
                status=StepStatus.COMPLETED,
                outputs=[context.output_dir / "samples" / samples[0].sample_id / "qc_raw"],
            )
        ],
    )
    captured = {}
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: captured.setdefault("title", title) or "")

    tui._fastqc_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))

    assert captured["title"] == "FastQC 结果"


def test_sra_to_fastq_menu_scans_and_runs_selected_sample(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout, StepResult, StepStatus

    sra = tmp_path / "downloads" / "SRR1.sra"
    sra.parent.mkdir()
    sra.write_text("", encoding="utf-8")
    output_dir = tmp_path / "sra_to_fastq"
    sample = Sample(
        sample_id="SRR1",
        source_path=sra,
        source_paths=[sra],
        layout=SampleLayout.UNKNOWN,
        project_id="sra_to_fastq_test",
        metadata={"input_type": "sra"},
    )

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {
            "input_dir": sra.parent,
            "project_id": "sra_to_fastq_test",
            "output_dir": output_dir,
            "threads": 4,
            "max_workers": 2,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_choose_sra_target", lambda samples: [samples[0]])
    monkeypatch.setattr(tui, "scan_inputs", lambda input_dir, project_id=None: type("Scan", (), {"samples": [sample]})())
    monkeypatch.setattr(
        tui,
        "_run_step_with_tui_progress",
        lambda samples, context, step, title, max_workers=tui.DEFAULT_TUI_CONCURRENCY: [
            StepResult(
                sample_id=samples[0].sample_id,
                step_id="sra_to_fastq",
                status=StepStatus.COMPLETED,
                outputs=[context.output_dir / "samples" / samples[0].sample_id / "raw_fastq"],
            )
        ],
    )
    captured = {}
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: captured.setdefault("title", title) or "")

    tui._sra_to_fastq_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))

    assert captured["title"] == "SRA 转 FASTQ 结果"


def test_trim_galore_menu_scans_and_runs_selected_sample(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout, StepResult, StepStatus

    fastq = tmp_path / "downloads" / "S1_1.fastq.gz"
    fastq.parent.mkdir()
    fastq.write_text("", encoding="utf-8")
    output_dir = tmp_path / "trim"
    sample = Sample(
        sample_id="S1",
        source_path=fastq,
        source_paths=[fastq],
        layout=SampleLayout.SINGLE,
        project_id="trim_test",
        metadata={"input_type": "fastq"},
    )

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {
            "input_dir": fastq.parent,
            "project_id": "trim_test",
            "output_dir": output_dir,
            "quality": 20,
            "cores": 1,
            "max_workers": 6,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_choose_trim_target", lambda samples: [samples[0]])
    monkeypatch.setattr(tui, "scan_inputs", lambda input_dir, project_id=None: type("Scan", (), {"samples": [sample]})())
    monkeypatch.setattr(
        tui,
        "_run_step_with_tui_progress",
        lambda samples, context, step, title, max_workers=tui.DEFAULT_TUI_CONCURRENCY: [
            StepResult(
                sample_id=samples[0].sample_id,
                step_id="trim_galore",
                status=StepStatus.COMPLETED,
                outputs=[context.output_dir / "samples" / samples[0].sample_id / "trimmed_fastq"],
            )
        ],
    )
    captured = {}
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: captured.setdefault("title", title) or "")

    tui._trim_galore_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))

    assert captured["title"] == "Trim Galore 结果"


def test_hisat2_menu_runs_multiple_selected_samples(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout, StepResult, StepStatus
    from rnaseq_workflow.core.references import ReferenceAsset, ReferenceCheckReport

    input_dir = tmp_path / "trim"
    output_dir = tmp_path / "hisat2"
    reference_dir = tmp_path / "references"
    input_dir.mkdir()
    reference_dir.mkdir()
    fastq1 = input_dir / "S1.fastq.gz"
    fastq2 = input_dir / "S2.fastq.gz"
    fastq1.write_text("", encoding="utf-8")
    fastq2.write_text("", encoding="utf-8")
    samples = [
        Sample("S1", fastq1, SampleLayout.SINGLE, source_paths=[fastq1], metadata={"input_type": "fastq"}),
        Sample("S2", fastq2, SampleLayout.SINGLE, source_paths=[fastq2], metadata={"input_type": "fastq"}),
    ]
    asset = ReferenceAsset(
        reference_id="ref",
        root=reference_dir / "ref",
        fasta=reference_dir / "ref" / "genome.fa",
        annotation=reference_dir / "ref" / "annotation.gff",
        hisat2_index=reference_dir / "ref" / "hisat2" / "genome",
        created_at="now",
        updated_at="now",
        provider="refseq",
        annotation_provider="refseq",
        build_status="completed",
    )

    captured = {}

    def fake_run(selected, context, step, title, max_workers=tui.DEFAULT_TUI_CONCURRENCY):
        captured["sample_ids"] = [sample.sample_id for sample in selected]
        captured["index"] = context.config["hisat2_index"]
        return [StepResult(sample_id=sample.sample_id, step_id="hisat2", status=StepStatus.COMPLETED) for sample in selected]

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {
            "input_dir": input_dir,
            "project_id": "hisat2_test",
            "output_dir": output_dir,
            "threads": 4,
            "max_workers": 2,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_choose_hisat2_target", lambda found: found)
    monkeypatch.setattr(tui, "_choose_reference_asset", lambda state, current_reference_id="": (reference_dir, "ref"))
    monkeypatch.setattr(tui, "scan_inputs", lambda input_dir, project_id=None: type("Scan", (), {"samples": samples})())
    monkeypatch.setattr(tui, "load_reference", lambda reference_id, reference_dir: asset)
    monkeypatch.setattr(tui, "check_reference_asset", lambda asset: ReferenceCheckReport(asset.reference_id, ok=True))
    monkeypatch.setattr(tui, "_run_step_with_tui_progress", fake_run)
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")

    tui._hisat2_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))

    assert captured["sample_ids"] == ["S1", "S2"]
    assert captured["index"] == str(asset.hisat2_index)


def test_samtools_menu_runs_multiple_selected_sam_samples(monkeypatch, tmp_path):
    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import StepResult, StepStatus

    input_dir = tmp_path / "hisat2"
    output_dir = tmp_path / "samtools"
    (input_dir / "samples" / "S1" / "alignment").mkdir(parents=True)
    (input_dir / "samples" / "S2" / "alignment").mkdir(parents=True)
    (input_dir / "samples" / "S1" / "alignment" / "S1.sam").write_text("@HD\n", encoding="utf-8")
    (input_dir / "samples" / "S2" / "alignment" / "S2.sam").write_text("@HD\n", encoding="utf-8")

    captured = {}

    def fake_run(selected, context, step, title, max_workers=tui.DEFAULT_TUI_CONCURRENCY):
        captured["sample_ids"] = [sample.sample_id for sample in selected]
        captured["paths"] = [str(sample.source_path) for sample in selected]
        captured["index"] = context.config["samtools_index"]
        return [StepResult(sample_id=sample.sample_id, step_id="samtools_sort", status=StepStatus.COMPLETED) for sample in selected]

    monkeypatch.setattr(
        tui,
        "_tool_run_wizard",
        lambda *args, **kwargs: {
            "input_dir": input_dir,
            "project_id": "samtools_test",
            "output_dir": output_dir,
            "threads": 2,
            "max_workers": 2,
            "actual_run": True,
        },
    )
    monkeypatch.setattr(tui, "_choose_samtools_target", lambda found: found)
    monkeypatch.setattr(tui, "_run_step_with_tui_progress", fake_run)
    monkeypatch.setattr(tui, "_capture_output", lambda state, render, title: "")

    tui._samtools_menu(tui.TuiState(config=Path("config.yaml"), console=Console()))

    assert captured["sample_ids"] == ["S1", "S2"]
    assert captured["index"] is True
    assert all(path.endswith(".sam") for path in captured["paths"])


def test_sample_multiselect_filters_selected_ids(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout

    samples = [
        Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE),
        Sample(sample_id="S2", source_path=tmp_path / "S2.fastq.gz", layout=SampleLayout.SINGLE),
        Sample(sample_id="S3", source_path=tmp_path / "S3.fastq.gz", layout=SampleLayout.SINGLE),
    ]

    class FakeDialog:
        def run(self):
            return ["S1", "S3"]

    monkeypatch.setattr(tui, "checkboxlist_dialog", lambda **kwargs: FakeDialog())

    selected = tui._sample_multiselect("选择样本", samples)

    assert [sample.sample_id for sample in selected] == ["S1", "S3"]


def test_print_scan_result_renders_table():
    from io import StringIO

    from rich.console import Console

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, SampleLayout

    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, width=120, color_system=None)
    samples = [
        Sample(
            sample_id="S1",
            source_path=Path("downloads/S1_1.fastq.gz"),
            source_paths=[Path("downloads/S1_1.fastq.gz"), Path("downloads/S1_2.fastq.gz")],
            layout=SampleLayout.PAIRED,
            metadata={"input_type": "fastq"},
        )
    ]

    tui._print_scan_result(console, samples)

    output = buffer.getvalue()
    assert "输入扫描结果" in output
    assert "S1" in output
    assert "fastq" in output
    assert "paired" in output


def test_run_step_for_sample_marks_running(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, StepResult, StepStatus

    class DummyStep:
        step_id = "dummy"

        def validate_inputs(self, sample, context):
            return None

        def run(self, sample, context):
            assert statuses[sample.sample_id] == StepStatus.RUNNING.value
            return StepResult(sample_id=sample.sample_id, step_id=self.step_id, status=StepStatus.COMPLETED)

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "out", dry_run=True)
    statuses = {"S1": "QUEUED"}

    result = tui._run_step_for_sample(DummyStep(), sample, context, statuses, tui.CancellationToken())

    assert result.status == StepStatus.COMPLETED


def test_run_step_for_sample_respects_cancel_before_start(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, StepStatus

    class DummyStep:
        step_id = "dummy"

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "out", dry_run=True)
    token = tui.CancellationToken()
    token.cancel()

    result = tui._run_step_for_sample(DummyStep(), sample, context, {}, token)

    assert result.status == StepStatus.CANCELLED


def test_step_progress_text_shows_output_activity(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepStatus

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    output = tmp_path / "out" / "samples" / "S1" / "trimmed_fastq"
    output.mkdir(parents=True)
    (output / "S1_trimmed.fq.gz").write_text("abc", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "out", dry_run=False)

    text = tui._step_progress_text(
        [sample],
        {"S1": StepStatus.RUNNING.value},
        context,
        "Trim Galore 修剪",
        max_workers=6,
        done=False,
    )

    assert "output=" in text
    assert "idle=" in text
    assert "S1_trimmed.fq.gz" in text


def test_step_progress_text_shows_waiting_output_and_input_size(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepStatus

    fastq = tmp_path / "S1.fastq.gz"
    fastq.write_text("abcd", encoding="utf-8")
    sample = Sample(sample_id="S1", source_path=fastq, source_paths=[fastq], layout=SampleLayout.SINGLE)
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "out", dry_run=False)

    text = tui._step_progress_text(
        [sample],
        {"S1": StepStatus.RUNNING.value},
        context,
        "Trim Galore 修剪",
        max_workers=6,
        done=False,
    )

    assert "input=" in text
    assert "output=(waiting)" in text


def test_step_progress_text_hides_idle_for_skipped_done(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepStatus

    fastq = tmp_path / "S1.fastq.gz"
    fastq.write_text("abcd", encoding="utf-8")
    sample = Sample(sample_id="S1", source_path=fastq, source_paths=[fastq], layout=SampleLayout.SINGLE)
    output = tmp_path / "out" / "samples" / "S1" / "trimmed_fastq"
    output.mkdir(parents=True)
    (output / ".done.json").write_text("{}", encoding="utf-8")
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "out", dry_run=False)

    text = tui._step_progress_text(
        [sample],
        {"S1": StepStatus.SKIPPED.value},
        context,
        "Trim Galore 修剪",
        max_workers=6,
        done=False,
    )

    assert "done=yes" in text


def test_step_progress_text_shows_cancelled_when_done(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepStatus

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz", layout=SampleLayout.SINGLE)
    context = RunContext(project_id="demo", work_dir=tmp_path, output_dir=tmp_path / "out", dry_run=False)

    text = tui._step_progress_text(
        [sample],
        {"S1": StepStatus.CANCELLED.value},
        context,
        "Trim Galore 修剪",
        max_workers=6,
        done=True,
    )

    assert "已取消" in text
    assert "已完成" not in text
    assert "idle=" not in text


def test_workflow_progress_text_uses_sample_pipeline_view(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz")
    steps = [Step("download"), Step("qc"), Step("align")]
    text = tui._workflow_progress_text(
        [sample],
        steps,
        {("S1", "download"): StepStatus.COMPLETED.value, ("S1", "qc"): StepStatus.RUNNING.value},
        {},
        "Workflow",
        "sample_pipeline",
        max_workers=2,
        done=False,
        elapsed=1.0,
        processing_workers=2,
        download_workers=10,
    )

    assert "按样本流水线" in text
    assert "下载并发: 10" in text
    assert "处理并发: 2" in text
    assert "qc RUNNING" in text
    assert "步骤单位=1.5/3" in text


def test_workflow_progress_text_shows_system_info(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download")],
        {},
        {},
        "Workflow",
        "sample_pipeline",
        max_workers=1,
        done=False,
        elapsed=1.0,
        system_text="CPU: 12.0%  内存: 50.0%  工作盘: OK",
    )

    assert "CPU: 12.0%" in text
    assert "工作盘: OK" in text


def test_workflow_processing_output_dir_builds_backup_root(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.task_params import TaskParams
    import json

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    spill_root = tmp_path / "spill"
    params = TaskParams(disk_guard_strategy="transfer", spill_large_outputs=True, spill_paths=[str(spill_root)])

    output_dir = tui._workflow_processing_output_dir(task, params)
    expected = spill_root / "users" / task.user_id / "tasks" / task.task_id

    assert output_dir == expected
    assert expected.exists()
    records = json.loads((task.metadata_dir / "artifact_locations.json").read_text(encoding="utf-8"))
    assert records[0]["original_path"] == str(task.task_output_dir / "samples")
    assert records[0]["current_path"] == str(expected / "samples")
    assert records[0]["reason"] == "large_outputs_root"


def test_runtime_guard_switches_future_outputs_to_backup_root(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.cancellation import CancellationToken
    from rnaseq_workflow.core.models import RunContext, Sample, StepStatus
    from rnaseq_workflow.core.task_params import TaskParams
    from rnaseq_workflow.core.system_monitor import CpuSnapshot, DiskSnapshot, MemorySnapshot, SystemSnapshot

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    spill_root = tmp_path / "spill"
    params = TaskParams(disk_guard_strategy="transfer", spill_large_outputs=True, spill_paths=[str(spill_root)], disk_guard_min_free_gb=999999)
    context = RunContext("demo", tmp_path, task.task_output_dir, config={})
    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sra", metadata={"_workflow_output_dir": str(task.task_output_dir)})
    snapshot = SystemSnapshot(
        captured_at=1.0,
        cpu=CpuSnapshot(),
        memory=MemorySnapshot(),
        work_disk=DiskSnapshot(path=str(task.root), total_bytes=100, used_bytes=99, free_bytes=1, percent=99.0, warning_level="critical"),
    )
    monkeypatch.setattr(tui, "_system_snapshot_for_params", lambda params, task, sampler=None: snapshot)

    guard = tui._RuntimeResourceGuard(task, params, CancellationToken(), context=context, samples=[sample])
    note = guard.tick({("S1", "download"): StepStatus.COMPLETED.value}, {})

    expected = spill_root / "users" / task.user_id / "tasks" / task.task_id
    assert context.output_dir == expected
    assert sample.metadata["_workflow_output_dir"] == str(expected)
    assert "后续大产物" in note
    assert not guard.cancel_token.is_cancelled()


def test_pipeline_step_output_dir_uses_workflow_output_metadata(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.sra", metadata={"_workflow_output_dir": str(tmp_path / "spill" / "task-1")})

    assert tui._pipeline_step_output_dir("samtools_sort", Path(sample.metadata["_workflow_output_dir"]), sample) == tmp_path / "spill" / "task-1" / "samples" / "S1" / "alignment"


def test_sample_pipeline_progress_inlines_current_step_detail(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="S1", source_path=tmp_path / "S1.fastq.gz")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("qc")],
        {("S1", "download"): StepStatus.RUNNING.value},
        {("S1", "download"): "12.0% 120MB 8MB/s 剩余:1m20s S1.fastq.gz.part"},
        "Workflow",
        "sample_pipeline",
        max_workers=1,
        done=False,
        elapsed=2.0,
    )

    assert "download RUNNING  12.0% 120MB 8MB/s" in text
    assert "最近信息" not in text


def test_workflow_download_progress_detail_matches_download_page_density():
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import StepStatus
    from rnaseq_workflow.steps.download.models import DownloadProgress

    detail = tui._workflow_download_progress_detail(
        DownloadProgress(
            accession="SRR1",
            status=StepStatus.RUNNING,
            downloaded_bytes=512,
            expected_size_bytes=1024,
            speed_bps=128,
            message="downloading",
        )
    )

    assert "50.0%" in detail
    assert "512B/1.0KB" in detail
    assert "128B/s" in detail
    assert "剩余:" in detail


def test_load_manifest_expected_sizes_does_not_fetch_missing_by_default(monkeypatch):
    from rnaseq_workflow.cli import tui

    def fail_fetch(*args, **kwargs):
        raise AssertionError("should not fetch runinfo")

    monkeypatch.setattr("rnaseq_workflow.steps.download.fetch_sra_runinfo_rows", fail_fetch)
    monkeypatch.setattr("rnaseq_workflow.steps.download.fetch_sra_run_size_bytes", fail_fetch)

    sizes = tui._load_manifest_expected_sizes({"accessions": ["SRR1"]})

    assert sizes == {}


def test_update_manifest_expected_sizes_persists_sizes(tmp_path):
    from rnaseq_workflow.cli import tui

    path = tmp_path / "manifest.json"
    path.write_text('{"accessions": ["SRR1"], "metadata": []}', encoding="utf-8")

    tui._update_manifest_expected_sizes(path, {"SRR1": 1024})

    data = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert data["metadata"][0]["run"] == "SRR1"
    assert data["metadata"][0]["expected_size_bytes"] == 1024


def test_enrich_manifest_expected_sizes_uses_runinfo(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(
        "rnaseq_workflow.steps.download.fetch_sra_runinfo_rows",
        lambda accessions, timeout_seconds=8.0: [{"Run": "SRR1", "size_MB": "2"}],
    )
    data = {"accessions": ["SRR1"], "metadata": []}

    assert tui._enrich_manifest_expected_sizes(data)
    assert data["metadata"][0]["expected_size_bytes"] == 2 * 1024 * 1024


def test_workflow_download_progress_detail_compacts_long_messages():
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import StepStatus
    from rnaseq_workflow.steps.download.models import DownloadProgress

    detail = tui._workflow_download_progress_detail(
        DownloadProgress(
            accession="SRR1",
            status=StepStatus.FAILED,
            message="line1\n" + "x" * 300,
        )
    )

    assert "\n" not in detail
    assert "..." in detail
    assert len(detail) < 230


def test_sample_pipeline_progress_shows_download_summary(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(
        sample_id="SRR1",
        source_path=tmp_path / "SRR1",
        metadata={"expected_size_bytes": 1024},
    )
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("qc")],
        {("SRR1", "download"): StepStatus.RUNNING.value},
        {("SRR1", "download"): "50.0% 512B/1.0KB 128B/s 剩余:4s downloading"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=2.0,
        processing_workers=6,
        download_workers=10,
    )

    assert "下载汇总" in text
    assert "样本数: 1" in text
    assert "样本进度" in text
    assert "阶段进度" in text
    assert "阶段进度 [#########.........] 50.0%" in text
    assert "总大小: 512B/1.0KB" in text
    assert "总速度: 128B/s" in text
    assert "下载并发: 10" in text


def test_sample_pipeline_waiting_slot_has_no_running_credit(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="SRR1", source_path=tmp_path / "SRR1")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("qc")],
        {("SRR1", "download"): StepStatus.RUNNING.value},
        {("SRR1", "download"): "排队等待下载槽位 10"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "步骤单位=0.0/2" in text
    assert "样本进度 [..................] 0.0%" in text
    assert "阶段进度 [..................] 0.0%" in text


def test_sample_pipeline_validation_progress_gets_running_credit(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="SRR1", source_path=tmp_path / "SRR1")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("qc")],
        {("SRR1", "download"): StepStatus.RUNNING.value},
        {("SRR1", "download"): "验证 SRA 完整性 3s"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "步骤单位=0.5/2" in text
    assert "样本进度 [####..............] 25.0%" in text
    assert "阶段进度 [#########.........] 50.0%" in text


def test_sample_pipeline_processing_queue_has_no_running_credit(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="SRR1", source_path=tmp_path / "SRR1")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("fastqc")],
        {("SRR1", "download"): StepStatus.COMPLETED.value, ("SRR1", "fastqc"): StepStatus.RUNNING.value},
        {("SRR1", "fastqc"): "排队等待处理槽位 6"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "步骤单位=1.0/2" in text
    assert "样本进度 [#########.........] 50.0%" in text
    assert "阶段进度 [..................] 0.0%" in text


def test_sample_pipeline_processing_running_gets_credit(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="SRR1", source_path=tmp_path / "SRR1")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("fastqc")],
        {("SRR1", "download"): StepStatus.COMPLETED.value, ("SRR1", "fastqc"): StepStatus.RUNNING.value},
        {("SRR1", "fastqc"): "已获得处理槽位，执行 FastQC"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "步骤单位=1.5/2" in text
    assert "样本进度 [#############.....] 75.0%" in text
    assert "阶段进度 [#########.........] 50.0%" in text


def test_sample_pipeline_non_download_step_shows_output_activity(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    fastq = tmp_path / "S1.fastq.gz"
    fastq.write_text("abcd", encoding="utf-8")
    output = tmp_path / "out" / "samples" / "S1" / "qc_raw"
    output.mkdir(parents=True)
    (output / "S1_fastqc.html").write_text("html", encoding="utf-8")
    sample = Sample(
        sample_id="S1",
        source_path=fastq,
        source_paths=[fastq],
        metadata={"_workflow_output_dir": str(tmp_path / "out")},
    )

    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("fastqc")],
        {("S1", "download"): StepStatus.COMPLETED.value, ("S1", "fastqc"): StepStatus.RUNNING.value},
        {("S1", "fastqc"): "已获得处理槽位，执行 FastQC"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "fastqc RUNNING" in text
    assert "input=4B" in text
    assert "output=4B" in text
    assert "last=S1_fastqc.html" in text


def test_sample_pipeline_output_activity_ignores_internal_markers(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    fastq = tmp_path / "S1.fastq.gz"
    fastq.write_text("abcd", encoding="utf-8")
    output = tmp_path / "out" / "samples" / "S1" / "qc_raw"
    output.mkdir(parents=True)
    (output / ".lock").write_text("lock", encoding="utf-8")
    (output / ".done.json").write_text("{}", encoding="utf-8")
    sample = Sample(
        sample_id="S1",
        source_path=fastq,
        source_paths=[fastq],
        metadata={"_workflow_output_dir": str(tmp_path / "out")},
    )

    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("fastqc")],
        {("S1", "download"): StepStatus.COMPLETED.value, ("S1", "fastqc"): StepStatus.RUNNING.value},
        {("S1", "fastqc"): "已获得处理槽位，执行 FastQC"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "output=0B" in text
    assert "last=.lock" not in text


def test_sample_pipeline_download_row_uses_expected_size_metadata(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(
        sample_id="SRR1",
        source_path=tmp_path / "SRR1",
        metadata={"expected_size_bytes": 2 * 1024**3},
    )

    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("sra_to_fastq")],
        {("SRR1", "download"): StepStatus.RUNNING.value},
        {("SRR1", "download"): "1.0GB 512.0KB/s 剩余:估算中 downloading attempt 1"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "50.0%" in text
    assert "1.0GB/2.0GB" in text
    assert "downloading attempt 1" in text


def test_sample_pipeline_skipped_download_advances_to_next_step(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="SRR1", source_path=tmp_path / "SRR1")
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("sra_to_fastq"), Step("fastqc")],
        {("SRR1", "download"): StepStatus.SKIPPED.value, ("SRR1", "sra_to_fastq"): StepStatus.RUNNING.value},
        {("SRR1", "download"): "cached SRA file found"},
        "Workflow",
        "sample_pipeline",
        max_workers=10,
        done=False,
        elapsed=1.0,
    )

    assert "sra_to_fastq RUNNING" in text
    assert "download SKIPPED" not in text
    assert "步骤单位=1.5/3" in text


def test_sample_pipeline_progress_compacts_multiline_failures(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import Sample, StepStatus

    class Step:
        def __init__(self, step_id):
            self.step_id = step_id

    sample = Sample(sample_id="SRR1", source_path=tmp_path / "SRR1.sra")
    long_error = "line1\n" + ("x" * 220) + "\nlast line"
    text = tui._workflow_progress_text(
        [sample],
        [Step("download"), Step("sra_to_fastq")],
        {("SRR1", "download"): StepStatus.COMPLETED.value, ("SRR1", "sra_to_fastq"): StepStatus.FAILED.value},
        {("SRR1", "sra_to_fastq"): long_error},
        "Workflow",
        "sample_pipeline",
        max_workers=1,
        done=False,
        elapsed=2.0,
    )

    assert "\nline1" not in text
    assert "..." in text
    assert "sra_to_fastq FAILED" in text


def test_manifest_download_step_updates_sample_paths(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, StepStatus
    from rnaseq_workflow.steps.download.models import DownloadResult

    class Downloader:
        def download(self, request, dry_run=False, progress_callback=None, cancellation_token=None):
            out = request.output_dir / request.accession
            out.mkdir(parents=True)
            (out / f"{request.accession}_1.fastq.gz").write_text("x", encoding="utf-8")
            if progress_callback:
                from rnaseq_workflow.steps.download.models import DownloadProgress

                progress_callback(DownloadProgress(request.accession, StepStatus.RUNNING, message="downloading"))
            return DownloadResult(accession=request.accession, status=StepStatus.COMPLETED)

    sample = Sample("SRR1", tmp_path / "remote", metadata={"accession": "SRR1"})
    step = tui._ManifestDownloadStep(Downloader(), tmp_path / "downloads")
    result = step.run(sample, RunContext("demo", tmp_path, tmp_path / "out"))

    assert result.status == StepStatus.COMPLETED
    assert sample.source_paths == [tmp_path / "downloads" / "SRR1" / "SRR1_1.fastq.gz"]
    assert sample.metadata["input_type"] == "fastq"


def test_manifest_download_step_respects_own_concurrency(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, StepStatus
    from rnaseq_workflow.steps.download.models import DownloadResult

    active = 0
    max_active = 0
    lock = threading.Lock()

    class Downloader:
        def download(self, request, dry_run=False, progress_callback=None, cancellation_token=None):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            try:
                threading.Event().wait(0.05)
                out = request.output_dir / request.accession
                out.mkdir(parents=True, exist_ok=True)
                (out / f"{request.accession}.sra").write_text("x", encoding="utf-8")
                return DownloadResult(accession=request.accession, status=StepStatus.COMPLETED)
            finally:
                with lock:
                    active -= 1

    step = tui._ManifestDownloadStep(Downloader(), tmp_path / "downloads", max_workers=1)
    context = RunContext("demo", tmp_path, tmp_path / "out")
    samples = [Sample(f"SRR{i}", tmp_path / f"SRR{i}", metadata={"accession": f"SRR{i}"}) for i in range(3)]

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(lambda sample: step.run(sample, context), samples))

    assert max_active == 1


def test_manifest_download_step_cancel_while_waiting_for_slot(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.models import RunContext, Sample, StepStatus

    class Downloader:
        def download(self, *args, **kwargs):
            raise AssertionError("download should not start after cancellation")

    step = tui._ManifestDownloadStep(Downloader(), tmp_path / "downloads", max_workers=1)
    step._semaphore.acquire()
    token = tui.CancellationToken()
    token.cancel()
    context = RunContext("demo", tmp_path, tmp_path / "out", config={"cancellation_token": token})

    try:
        result = step.run(Sample("SRR1", tmp_path / "remote", metadata={"accession": "SRR1"}), context)
    finally:
        step._semaphore.release()

    assert result.status == StepStatus.CANCELLED
    assert "download slot" in result.message


def test_current_or_new_task_cancel_does_not_create(monkeypatch):
    from rnaseq_workflow.cli import tui
    from rich.console import Console

    state = tui.TuiState(config=Path("config.yaml"), console=Console())
    monkeypatch.setattr(tui, "_ensure_user", lambda _state: "user-1")
    monkeypatch.setattr(tui, "_menu", lambda *args, **kwargs: None)
    called = {"created": False}

    def fake_create_task(_state):
        called["created"] = True
        raise AssertionError("should not create task on cancel")

    monkeypatch.setattr(tui, "_create_task", fake_create_task)

    assert tui._current_or_new_task(state) is None
    assert called["created"] is False


def test_current_or_new_task_can_select_existing_task(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rich.console import Console

    workspace = tui.build_asset_workspace(tmp_path / "workspace")
    task = workspace.ensure_user("user-1").create_task(task_name="existing")
    state = tui.TuiState(config=Path("config.yaml"), console=Console(), asset_root=tmp_path / "workspace", user_id="user-1")
    seen = {}

    def fake_menu(title, text, values):
        seen[title] = [value for value, _label in values]
        return "select" if title == "当前没有任务" else task.task_id

    monkeypatch.setattr(tui, "_menu", fake_menu)

    assert tui._current_or_new_task(state).task_id == task.task_id
    assert seen["当前没有任务"] == ["select", "new", "back"]


def test_current_or_new_task_without_existing_task_offers_create(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rich.console import Console

    workspace = tui.build_asset_workspace(tmp_path / "workspace")
    workspace.ensure_user("user-1")
    state = tui.TuiState(config=Path("config.yaml"), console=Console(), asset_root=tmp_path / "workspace", user_id="user-1")
    seen = {}
    monkeypatch.setattr(tui, "_menu", lambda title, text, values: seen.setdefault(title, [value for value, _label in values]) and None)

    assert tui._current_or_new_task(state) is None
    assert seen["当前没有任务"] == ["new", "back"]


def test_ensembl_division_custom_cancel_returns_to_menu(monkeypatch):
    from rnaseq_workflow.cli import tui

    calls = {"menu": 0}

    def fake_menu(title, text, values):
        calls["menu"] += 1
        return "__custom__" if calls["menu"] == 1 else "plants"

    monkeypatch.setattr(tui, "_menu", fake_menu)
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": None)

    assert tui._ensembl_division_input() == "plants"
    assert calls["menu"] == 2


def test_ensembl_division_custom_value(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(tui, "_menu", lambda title, text, values: "__custom__")
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": "fungi")

    assert tui._ensembl_division_input() == "fungi"


def test_choice_with_custom_keeps_custom_option_as_value(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(tui, "_menu", lambda title, text, values: "custom")

    assert (
        tui._choice_with_custom_input(
            "provider",
            "选择来源。",
            [("custom", "custom")],
            "输入自定义来源。",
        )
        == "custom"
    )


def test_register_reference_wizard_cancel_edit_does_not_advance(monkeypatch):
    from rnaseq_workflow.cli import tui

    choices = iter(["edit", "next", "back"])
    seen_titles = []

    def fake_menu(title, text, values):
        seen_titles.append(title)
        return next(choices)

    monkeypatch.setattr(tui, "_menu", fake_menu)
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": None)
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    assert tui._register_reference_wizard() is None
    assert seen_titles == [
        "登记本地 Reference 1/8",
        "登记本地 Reference 1/8",
        "登记本地 Reference 1/8",
    ]


def test_prepare_reference_wizard_cancel_edit_does_not_advance(monkeypatch):
    from rnaseq_workflow.cli import tui

    choices = iter(["edit", "next", "back"])
    seen_titles = []

    def fake_menu(title, text, values):
        seen_titles.append(title)
        return next(choices)

    monkeypatch.setattr(tui, "_menu", fake_menu)
    monkeypatch.setattr(tui, "_input", lambda title, text, default="": None)
    monkeypatch.setattr(tui, "_message", lambda title, text: None)

    assert tui._prepare_reference_wizard() is None
    assert seen_titles == [
        "准备 Reference 1/13",
        "准备 Reference 1/13",
        "准备 Reference 1/13",
    ]


def test_build_reference_index_wizard_cancel_edit_does_not_advance(monkeypatch):
    from rnaseq_workflow.cli import tui

    choices = iter(["edit", "next", "back"])
    seen_titles = []

    def fake_menu(title, text, values):
        seen_titles.append(title)
        return next(choices)

    monkeypatch.setattr(tui, "_menu", fake_menu)
    monkeypatch.setattr(tui, "_message", lambda title, text: None)
    monkeypatch.setattr(tui, "_execution_mode_input", lambda default="docker": None)
    monkeypatch.setattr(tui, "_docker_image_input", lambda default="rnaseq-workflow:tools": None)
    monkeypatch.setattr(tui, "_optional_yes_no", lambda title, default: None)
    monkeypatch.setattr(tui, "_int_input", lambda title, default, minimum=None, cancel_returns_default=False: None)

    assert tui._build_reference_index_wizard() is None
    assert seen_titles == [
        "构建 HISAT2 index 1/5",
        "构建 HISAT2 index 1/5",
        "构建 HISAT2 index 2/5",
    ]


def test_tool_run_wizard_direct_choice_then_next(monkeypatch):
    from rnaseq_workflow.cli import tui

    seen_titles = []

    def fake_choice_page(title, field_title, text, values, current_value="", has_previous=False, is_last=False):
        seen_titles.append(title)
        return "stage_batch"

    monkeypatch.setattr(tui, "_tool_choice_page", fake_choice_page)

    assert tui._tool_run_wizard(
        "工具",
        {"execution_mode": "sample_pipeline"},
        [
            (
                "execution_mode",
                "执行模式",
                "选择执行模式。",
                "choice",
                None,
                (("sample_pipeline", "按样本流水线"), ("stage_batch", "按阶段批量")),
            )
        ],
    ) == {"execution_mode": "stage_batch"}
    assert seen_titles == ["工具 1/1"]


def test_tool_run_wizard_back_returns_none(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(tui, "_tool_choice_page", lambda *args, **kwargs: None)

    assert (
        tui._tool_run_wizard(
            "工具",
            {"execution_mode": "sample_pipeline"},
            [("execution_mode", "执行模式", "选择执行模式。", "choice", None, (("sample_pipeline", "按样本流水线"),))],
        )
        is None
    )


def test_tool_run_wizard_allows_empty_download_proxy(monkeypatch):
    from rnaseq_workflow.cli import tui

    monkeypatch.setattr(tui, "_tool_input_page", lambda *args, **kwargs: "")

    result = tui._tool_run_wizard(
        "工具参数配置",
        {"download_proxy": ""},
        [("download_proxy", "下载代理", "可留空。", "str", None, ())],
    )

    assert result == {"download_proxy": ""}


def test_tool_run_wizard_input_page_previous(monkeypatch):
    from rnaseq_workflow.cli import tui

    seen = []

    def fake_choice(*args, **kwargs):
        seen.append("choice")
        return "a"

    answers = iter(["__prev__", "ok"])
    monkeypatch.setattr(tui, "_tool_choice_page", fake_choice)
    monkeypatch.setattr(tui, "_tool_input_page", lambda *args, **kwargs: next(answers))

    result = tui._tool_run_wizard(
        "工具参数配置",
        {"mode": "a", "name": ""},
        [
            ("mode", "模式", "选择模式。", "choice", None, (("a", "A"), ("b", "B"))),
            ("name", "名称", "输入名称。", "str", None, ()),
        ],
    )

    assert seen == ["choice", "choice"]
    assert result["name"] == "ok"


def test_existing_output_root_is_reused(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    mapped = tmp_path / "spill" / "users" / "user-1" / "tasks" / task.task_id
    tui._record_output_root(task, task.task_output_dir, mapped, reason="large_outputs_root")

    assert tui._existing_output_root(task) == mapped


def test_migrate_sample_outputs_rewrites_progress_paths(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.models import Sample, StepResult, StepStatus
    from rnaseq_workflow.persistence.json_state import JsonStateRepository

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    source_dir = task.samples_dir / "S1" / "alignment"
    source_dir.mkdir(parents=True, exist_ok=True)
    sam_path = source_dir / "S1.sam"
    sam_path.write_text("sam", encoding="utf-8")
    sample = Sample("S1", sam_path, source_paths=[sam_path], metadata={"_workflow_output_dir": str(task.task_output_dir)})
    repo = JsonStateRepository(task.progress_path)

    class Step:
        step_id = "hisat2"
        name = "hisat2"

    repo.mark_running(sample, Step())
    repo.save_step_result(Step(), StepResult("S1", "hisat2", StepStatus.COMPLETED, outputs=[sam_path]))
    target_root = tmp_path / "spill" / "users" / "user-1" / "tasks" / task.task_id

    moved = tui._migrate_sample_outputs_to_root(task, sample, target_root)
    target_sam = target_root / "samples" / "S1" / "alignment" / "S1.sam"

    assert moved is True
    assert target_sam.exists()
    assert sample.source_path == target_sam
    progress_text = task.progress_path.read_text(encoding="utf-8")
    assert json.dumps(str(target_sam))[1:-1] in progress_text


def test_trim_galore_cached_result_updates_sample_paths(tmp_path):
    from rnaseq_workflow.core.models import RunContext, Sample, SampleLayout, StepRecord, StepStatus
    from rnaseq_workflow.steps.read_trimming.trim_galore import TrimGaloreStep

    output_dir = tmp_path / "samples" / "S1" / "trimmed_fastq"
    output_dir.mkdir(parents=True)
    trimmed = output_dir / "S1_trimmed.fq.gz"
    trimmed.write_text("fastq", encoding="utf-8")
    sample = Sample("S1", tmp_path / "raw.fq.gz")
    record = StepRecord("S1", "trim_galore", "Trim Galore", StepStatus.COMPLETED, outputs=[str(output_dir)])

    TrimGaloreStep().apply_cached_result(sample, RunContext("demo", tmp_path, tmp_path), record)

    assert sample.source_paths == [trimmed]
    assert sample.layout == SampleLayout.SINGLE


def test_workflow_finalize_readiness_waits_for_all_featurecounts(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.models import Sample, StepResult, StepStatus
    from rnaseq_workflow.persistence.json_state import JsonStateRepository
    from rnaseq_workflow.steps.quantification.featurecounts import FeatureCountsStep

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    repo = JsonStateRepository(task.progress_path)
    samples = [Sample("S1", tmp_path / "S1.bam"), Sample("S2", tmp_path / "S2.bam")]
    step = FeatureCountsStep()
    repo.mark_running(samples[0], step)
    repo.save_step_result(step, StepResult("S1", "featurecounts", StepStatus.COMPLETED))

    message = tui._workflow_finalize_readiness(task, samples)

    assert "需等待全部样本 featureCounts 完成" in message
    assert "S2=PENDING" in message


def test_finalize_completed_workflow_runs_after_all_featurecounts(monkeypatch, tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.assets import AssetWorkspace
    from rnaseq_workflow.core.finalize import FinalizeResult
    from rnaseq_workflow.core.models import Sample, StepResult, StepStatus
    from rnaseq_workflow.persistence.json_state import JsonStateRepository
    from rnaseq_workflow.steps.quantification.featurecounts import FeatureCountsStep

    task = AssetWorkspace(tmp_path / "workspace").ensure_user("user-1").create_task()
    repo = JsonStateRepository(task.progress_path)
    samples = [Sample("S1", tmp_path / "S1.bam"), Sample("S2", tmp_path / "S2.bam")]
    step = FeatureCountsStep()
    for sample in samples:
        repo.mark_running(sample, step)
        repo.save_step_result(step, StepResult(sample.sample_id, "featurecounts", StepStatus.COMPLETED))
    called = {}

    def fake_finalize(project_id, output_dir, final_samples, counts_matrix=None, report_json=None, report_markdown=None, state_path=None):
        called["args"] = (project_id, output_dir, final_samples, counts_matrix, report_json, report_markdown, state_path)
        return FinalizeResult([], counts_matrix, report_json, report_markdown, 2, 10)

    monkeypatch.setattr(tui, "finalize_project", fake_finalize)

    result, message = tui._finalize_completed_workflow(task, tmp_path / "spill-task", samples)

    assert message == "汇总完成"
    assert result is not None
    assert called["args"][0] == task.task_id
    assert called["args"][1] == tmp_path / "spill-task"
    assert called["args"][3] == task.reports_dir / "count_matrix.tsv"
    assert called["args"][6] == task.progress_path


def test_workflow_finalize_display_text_contains_output_paths(tmp_path):
    from rnaseq_workflow.cli import tui
    from rnaseq_workflow.core.finalize import FinalizeResult

    result = FinalizeResult(
        count_tables=[],
        counts_matrix=tmp_path / "reports" / "count_matrix.tsv",
        report_json=tmp_path / "reports" / "report.json",
        report_markdown=tmp_path / "reports" / "report.md",
        sample_count=2,
        gene_count=10,
    )

    text = tui._workflow_finalize_display_text(result, "汇总完成")

    assert "汇总完成" in text
    assert "count_matrix:" in text
    assert "report_json:" in text
    assert "report_markdown:" in text
