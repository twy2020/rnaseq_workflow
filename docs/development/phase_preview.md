# 阶段预览与技术路线决策

本文档记录当前阶段的开发取舍，用于指导后续核心施工。

## 1. 总体结论

项目主语言采用 Python。第一阶段优先实现 CLI、本地执行器和轻量持久化；后续再扩展 FastAPI、Celery 和 PostgreSQL。

核心原则：

1. CLI、后端 API 和未来前端都复用同一套 `core` 业务接口。
2. Celery 不进入核心业务层，只作为后续可替换的执行器。
3. PostgreSQL 作为后端/多人任务管理的目标方案，CLI 初期默认使用 JSON 状态文件或 SQLite。
4. 生信工具优先通过 Docker 固定运行环境，实现跨平台复现。
5. PowerShell 作为当前 CLI 主要兼容环境。

## 2. 为什么主语言选择 Python

Python 更适合本项目：

1. 老项目已经验证了 Python 适合串联生信工具，新系统继续使用 Python 可降低技术风险。
2. 生信流程、文件处理、矩阵处理和统计分析生态更成熟。
3. `pandas` 适合表达矩阵汇总。
4. `Typer`、`Rich` 适合 CLI。
5. `FastAPI`、`SQLAlchemy`、`Celery` 能覆盖后续 Web 与任务队列需求。
6. 调用外部命令和组织本地工作流比 Node.js 更自然。

Node.js 可用于后续前端工程，但不建议作为 workflow 核心语言。

## 3. 执行层规划

### 3.1 第一阶段：LocalExecutor

第一阶段不引入 Celery，先实现本地执行器。

职责：

- 按样本和步骤执行流程。
- 控制并发数量。
- 记录步骤状态。
- 支持失败停止。
- 预留资源锁，例如 HISAT2 高资源步骤。

### 3.2 第二阶段：CeleryExecutor

当项目进入后端服务阶段，再引入 Celery。

适用场景：

- Web 前端提交任务。
- 多用户任务队列。
- 多 worker 分发执行。
- 任务状态长期查询。

Celery 只负责调度和异步执行，不承载流程业务逻辑。流程定义、步骤依赖、输入输出校验仍然保留在 `core` 层。

## 4. 持久化规划

### 4.1 第一阶段

CLI 阶段默认使用：

- `progress.json`
- `run_metadata.json`
- 后续可选 SQLite

原因：

- 部署简单。
- 便于调试。
- 不要求用户本机安装数据库。

### 4.2 第二阶段

引入 SQLAlchemy 抽象持久化层，同时支持：

- SQLite：单机 CLI 默认。
- PostgreSQL：Web 后端和多人任务管理。

PostgreSQL 适合存储：

- 项目配置
- 样本属性
- accession 元数据
- 任务参数
- 步骤状态
- 运行历史
- 审计日志

## 5. Docker 规划

Docker 用于固定生信工具版本，提升跨平台可复现性。

容器内建议包含：

- Python 项目
- SRA Toolkit
- FastQC
- Trim Galore
- HISAT2
- samtools
- StringTie
- Subread/featureCounts
- MultiQC

PowerShell 下通过 volume 挂载数据目录和输出目录。

## 6. 推荐代码组织

```text
workflow/rnaseq_workflow/
  core/
    config.py
    models.py
    pipeline.py
    steps.py
  executors/
    local.py
    celery.py
  persistence/
    base.py
    json_state.py
    sqlite.py
    postgres.py
  steps/
    download/
    data_ingestion/
    quality_control/
    read_trimming/
    alignment/
    quantification/
    reporting/
  cli/
    main.py
```

## 7. 核心阶段施工顺序

第一批施工：

1. 建立 Python 包结构。
2. 定义核心模型：`ProjectConfig`、`Sample`、`PipelineStep`、`StepResult`。
3. 定义持久化接口。
4. 实现 JSON 状态仓库。
5. 实现本地执行器。
6. 实现 CLI 最小入口。

第二批施工：

1. 重新实现样本扫描和 SRA 转 FASTQ。
2. 重新实现 FastQC、Trim Galore、HISAT2、samtools、StringTie 的命令构造。
3. 增加 dry-run 和 mock-run，便于 Windows/无生信工具环境测试。

第三批施工：

1. 增加真实小样本端到端测试。
2. 增加 SQLite。
3. 增加 Dockerfile 和 docker compose。

第四批施工：

1. 增加 FastAPI。
2. 增加 PostgreSQL。
3. 增加 CeleryExecutor。

## 8. 当前整体 pipeline 状态

### 配置模板

可用 `init-config` 生成标准 YAML 模板：

```powershell
$env:PYTHONPATH='workflow'
python -m rnaseq_workflow.cli.main init-config config.yaml --project-id rnaseq_demo
```

模板默认启用 Docker 执行模式，包含样本配置、执行步骤、FastQC、Trim Galore、HISAT2、samtools 和 featureCounts 参数。

运行前可用 `validate-config` 检查配置：

```powershell
python -m rnaseq_workflow.cli.main validate-config config.yaml
python -m rnaseq_workflow.cli.main validate-config config.yaml --no-check-files
```

严格校验会检查：

- 必需参数，例如 `hisat2_index` 和 `featurecounts_annotation`。
- 样本 ID 是否重复。
- `layout` 是否为 `single/paired/unknown`。
- paired 样本是否提供两个输入文件。
- 样本文件、HISAT2 index、GTF/GFF 注释是否存在。
- Docker 模式下关键路径是否位于 `docker_workspace` 内。

CLI `run` 已从 placeholder 切换为真实 step registry。配置里的 stage 名会展开为样本级具体步骤：

- `data_ingestion` -> `sra_to_fastq`
- `quality_control` -> `fastqc`
- `read_trimming` -> `trim_galore`
- `alignment` -> `hisat2`, `samtools_sort`
- `quantification` -> `featurecounts`

示例配置：

```yaml
project_id: pipeline_demo
work_dir: runtime_logs/pipeline_demo
output_dir: runtime_logs/pipeline_demo/output

samples:
  - sample_id: S1
    source_path: runtime_logs/pipeline_demo/input/S1.fastq
    layout: single

steps:
  - quality_control
  - alignment
  - quantification

hisat2_index: runtime_logs/pipeline_demo/index/genome
hisat2_threads: 1
samtools_threads: 1
featurecounts_annotation: runtime_logs/pipeline_demo/genes.gtf
```

运行：

```powershell
$env:PYTHONPATH='workflow'
python -m rnaseq_workflow.cli.main run runtime_logs\pipeline_demo\config.yaml --dry-run --max-workers 1
```

输出：

```text
runtime_logs/pipeline_demo/output/progress.json
```

当前边界：

- `run` 目前负责样本级 step 串联。
- `merge-counts` 和 `report-summary` 是项目级聚合命令，暂时独立执行。
- 真实 run 需要本机可直接调用外部工具，或后续加入容器执行适配层；当前 Docker 工具已完成独立验证。

### 容器执行模式

配置中加入以下字段后，样本级 pipeline 会通过 Docker 执行外部工具：

```yaml
execution_mode: docker
docker_image: rnaseq-workflow:tools
docker_workspace: .
```

`docker_workspace` 会挂载到容器 `/workspace`，工作区内路径会自动转换为容器路径。

当前已完成真实容器 pipeline 冒烟测试：

```text
FastQC -> HISAT2 -> samtools sort -> featureCounts
```

测试结果：

- HISAT2 overall alignment rate: 100.00%
- featureCounts `geneB` count: 1

当前边界：

- Docker 执行模式适合外部工具步骤。
- `merge-counts` 和 `report-summary` 仍由 CLI 直接运行。
- 后续可继续抽象为更完整的 ExecutionBackend，以支持本地、Docker、Celery worker 的统一调度。

### 项目级收尾

样本级 pipeline 完成后，可用 `finalize` 自动执行项目级收尾：

```powershell
python -m rnaseq_workflow.cli.main finalize runtime_logs\docker_pipeline_demo\config.yaml
```

默认输出：

```text
OUTPUT_DIR/reports/raw_counts.tsv
OUTPUT_DIR/reports/report.json
OUTPUT_DIR/reports/report.md
```

也可以在 `run` 后追加自动收尾：

```powershell
python -m rnaseq_workflow.cli.main run config.yaml --no-dry-run --finalize
```

当前 `finalize` 行为：

- 按配置中的 samples 顺序收集 `samples/{sample_id}/quantification/{sample_id}.featureCounts.txt`。
- 合并为项目级 raw counts 矩阵。
- 生成 JSON/Markdown 报告。
- 若任一样本缺少 featureCounts 输出，会明确报错并停止。
