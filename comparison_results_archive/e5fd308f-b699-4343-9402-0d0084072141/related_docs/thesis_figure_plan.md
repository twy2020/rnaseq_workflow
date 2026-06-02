# 精简版论文配图规划与 AI 生图提示词

本文档依据 `docs/转录组测序分析流程的开发_精简版.docx`、`docs/thesis/chapter3_system_requirements_and_design.md`、`docs/module_system_design.md` 以及当前项目代码与模块 README 编写，用于规划论文中“需要配图”和“适合配图”的位置，并给出可直接用于 AI 制图的提示词。

## 1. 配图边界

配图只表现当前项目已经实现或论文正文明确描述的系统能力：

- Python 模块化 RNA-seq 工作流；
- SRA/ENA 下载、本地 SRA/FASTQ 输入扫描、SRA 转 FASTQ；
- FastQC、Trim Galore、HISAT2、samtools、featureCounts；
- 可选 StringTie 步骤与 StringTie abundance 表合并能力；
- featureCounts raw counts、CPM、FPKM、TPM 矩阵汇总；
- `progress.json` 状态持久化、已完成步骤跳过、输出路径恢复；
- Reference 资产管理，包括 FASTA、GTF/GFF、HISAT2 index 和元数据；
- Docker/local 执行模式、CLI/TUI、资源监控、跨盘产物记录和产物统计清理。

配图不要表现以下尚未作为当前核心成果完成的内容：

- DESeq2、edgeR、差异表达分析、火山图、热图、PCA 图等下游分析结果；
- Web 平台、多人在线任务队列、Celery/PostgreSQL 生产级后端；
- MultiQC 统一 HTML 报告作为已完成结果；
- RSeQC 结果作为已完成质控结果；
- 云端集群、Kubernetes、GPU 加速或大规模分布式调度；
- 自动完成所有物种参考数据选择且无需用户确认的能力。

## 2. 全局绘图风格

建议统一采用计算机科学论文中的系统框图风格：

- 白底或极浅灰底；
- 蓝灰色、青绿色、深灰色为主色，少量橙色表示告警或可选分支；
- 扁平矢量图、细线框、圆角矩形、清晰箭头；
- 中英文术语可混用，但图内核心模块建议使用英文短标签，图注使用中文解释；
- 避免真实显微照片、DNA 双螺旋装饰、复杂三维效果和过度渐变；
- 每张图应保持 16:9 或论文双栏可裁剪比例，元素留白充足。

通用负面提示词：

```text
不要生成真实人物、实验室照片、显微镜照片、复杂背景、夸张 3D 图标、装饰性 DNA 螺旋、热图/火山图/PCA 图、Web 仪表盘、云集群、未在系统中实现的分析功能；不要使用密集小字，不要让箭头交叉混乱。
```

## 3. 必须配图

### 图 3-1 系统总体架构图

插入位置：第 3 章 `3.2 系统总体架构` 中“图3-1 系统总体架构图”所在位置。

必要性：论文正文已经显式预留该图。该图用于总览系统分层结构，是第 3 章最关键的系统设计图。

图中应包含：

- 用户入口：CLI / TUI；
- 任务与资产管理层：TaskWorkspace、Sample manifest、Reference asset、metadata；
- 流水线调度层：WorkflowRunner、Pipeline、LocalExecutor；
- 步骤执行层：download、sra_to_fastq、fastqc、trim_galore、hisat2、samtools_sort、featureCounts、optional StringTie；
- 外部工具层：Docker/local execution、SRA Toolkit、FastQC、Trim Galore、HISAT2、samtools、Subread featureCounts、StringTie；
- 状态与资源侧边模块：progress.json、resource monitor、artifact_locations.json；
- 结果输出层：expression matrices、report.json、report.md。

AI 生图提示词：

```text
Create a clean academic system architecture diagram for a Python-based RNA-seq workflow management system. Use a white background, flat vector style, blue-gray and teal color palette, thin arrows, and clear rectangular layers.

Show the following layers from top to bottom:
1. User interface layer: CLI and terminal TUI.
2. Task and asset management layer: TaskWorkspace, sample manifest, task metadata, ReferenceAsset with FASTA, GTF/GFF, HISAT2 index.
3. Workflow scheduling layer: WorkflowRunner, Pipeline, LocalExecutor, two execution modes named sample_pipeline and stage_batch.
4. Step execution layer: download, sra_to_fastq, FastQC, Trim Galore, HISAT2, samtools sort/index, featureCounts, optional StringTie branch shown with dashed line.
5. External tool layer: Docker or local execution, SRA Toolkit, FastQC, Trim Galore, HISAT2, samtools, Subread featureCounts, StringTie.
6. Result output layer: raw_counts.tsv, cpm.tsv, fpkm.tsv, tpm.tsv, optional stringtie_fpkm.tsv/stringtie_tpm.tsv, report.json, report.md.

Place side modules connected to the scheduling layer: progress.json state persistence, resource monitor for CPU memory disk, artifact_locations.json for cross-disk artifacts.

Make it suitable for a bioinformatics/computer science journal paper. Keep text short, aligned, readable, and organized. Do not include DESeq2, edgeR, volcano plot, heatmap, PCA, Web platform, cloud cluster, or MultiQC result dashboard.
```

### 图 3-2 系统工作流程图

插入位置：第 3 章 `3.3 系统工作流程` 中“图3-2 系统工作流程图”所在位置。

必要性：论文正文已经显式预留该图。该图用于说明用户从创建任务到获得矩阵和报告的完整操作路径。

已生成版本的主要问题：

- 信息密度偏高，每个阶段内部 bullet 过多，论文缩放后可读性会下降；
- 第 7 步内部流程中 `download` 与 `sra_to_fastq` 的关系容易被理解为所有样本都必须先下载，建议明确“远程 accession 才下载，SRA 输入才转换”；
- `featureCounts` 是单样本计数步骤，不应写成“生成计数矩阵”；矩阵应放在第 8 步项目级汇总；
- StringTie 分支箭头不宜回连到 featureCounts，二者应作为同一 BAM 输入后的并列定量分支，最终进入项目级汇总；
- 下方 `progress.json`、Docker/local 和取消控制辅助框过于贴近主流程，建议改成横向支撑机制，连到“执行分析流程”即可；
- 图题“图3-2 系统工作流程图”建议由论文排版生成，图片内部不要包含大号图题。

图中应包含：

- 创建或选择任务；
- 提交样本清单；
- 更新样本元数据；
- 选择或准备 Reference；
- 配置参数与表达矩阵输出类型；
- 资源检查和物种一致性检查；
- 执行分析流程；
- 汇总表达矩阵；
- 产物统计与清理。

AI 生图提示词：

```text
Create a clean academic workflow diagram for Figure 3-2, titled "System Workflow" only if a small title is needed. Do not put a large caption inside the image. Use a white background, flat vector style, thin arrows, and a restrained blue-gray / teal palette. The diagram should be readable when inserted into a thesis page.

Layout requirement:
Use two horizontal rows.
Top row: nine main workflow stages, numbered 1 to 9, connected left to right.
Bottom row: three supporting mechanisms connected only to stage 7 "Run analysis workflow".
Keep each main stage to a short title plus at most two very short keywords. Avoid long bullet lists.

Top row stages:
1. Create / Select Task
   keywords: task workspace, metadata
2. Submit Samples
   keywords: accession, local SRA/FASTQ, URL manifest
3. Update Sample Metadata
   keywords: organism / TaxID, BioProject / BioSample
4. Select / Prepare Reference
   keywords: FASTA, GTF/GFF, HISAT2 index
5. Configure Parameters
   keywords: tool threads, expression formats
6. Preflight Check
   keywords: resources, species-reference consistency
7. Run Analysis Workflow
   contains the nested pipeline described below
8. Finalize Matrices
   keywords: raw counts, CPM, FPKM, TPM, reports
9. Artifact Statistics / Cleanup
   keywords: sizes, registered paths, cleanup

Nested pipeline inside stage 7:
Show a compact sub-flow from left to right:
remote accession -> download; SRA input -> sra_to_fastq; FASTQ -> FastQC -> Trim Galore -> HISAT2 -> samtools sort/index -> sorted BAM.
From sorted BAM, split into two parallel quantification branches:
featureCounts -> per-sample count table;
optional StringTie, shown as a dashed branch -> gene abundance table.
Both quantification outputs should point to stage 8 Finalize Matrices.
Do not make StringTie point back to featureCounts.
Do not label featureCounts as producing the project-level expression matrix; matrix generation belongs to stage 8.

Bottom row supporting mechanisms:
A. progress.json: records RUNNING / COMPLETED / FAILED / SKIPPED / CANCELLED and supports resume.
B. Docker or local execution: runs external tools such as SRA Toolkit, FastQC, Trim Galore, HISAT2, samtools, featureCounts, StringTie.
C. Cancellation control: can stop running local or Docker commands and mark remaining work as cancelled.

Use dashed connectors from the bottom mechanisms to stage 7 only. Use solid arrows for the main workflow. Make the diagram formal, simple, and suitable for a bioinformatics/computer science thesis.

Negative constraints:
Do not include downstream differential expression analysis, DESeq2, edgeR, volcano plots, heatmaps, PCA, GO enrichment, MultiQC dashboard, Web platform, cloud cluster, Kubernetes, or any unimplemented feature. Do not use decorative DNA helices, real lab photos, people, 3D icons, or dense paragraphs.
```

## 4. 优先建议配图

### 图 2-1 RNA-seq 上游分析基本流程图

插入位置：第 2 章 `2.3 RNA-seq分析基本流程` 段落之后。

适合原因：第 2 章介绍理论基础，读者需要先理解 SRA/FASTQ 到表达矩阵的通用流程。该图可作为后续系统流程图的生物信息背景铺垫。

已生成版本的修改建议：

- 该图更适合作为图 2-1，而不是图 3-2；它讲的是 RNA-seq 分析技术路线，不是完整系统操作工作流；
- `fasterq-dump` 不能画成所有输入的必经步骤。只有输入为 SRA 文件时才需要转换，FASTQ 输入应直接进入 FastQC；
- 远程数据源建议标为 `ENA FASTQ / NCBI SRA`，避免让读者误解所有远程 accession 都下载成 SRA；
- StringTie 的输入应来自 `sorted BAM`，并与 featureCounts 并列作为可选定量分支；不要从 HISAT2/SAM 阶段直接连到 StringTie；
- 注释文件 `GTF/GFF` 应同时作为 featureCounts 和 StringTie 的输入；
- featureCounts 输出是单样本 count 表或项目级 raw counts 汇总的输入，最终“表达矩阵”应作为项目级合并结果，而不是单个 featureCounts 步骤直接生成全部矩阵。

图中应包含：

- Public database / local data；
- SRA / FASTQ；
- fasterq-dump；
- FastQC；
- Trim Galore；
- HISAT2；
- samtools；
- featureCounts；
- expression matrix。

AI 生图提示词：

```text
Create a simple academic bioinformatics pipeline diagram for upstream RNA-seq analysis implemented by this project. White background, flat vector style, thin arrows, blue-gray and teal palette. This is a technical pipeline figure, not a user-operation workflow.

Main data entry:
Show two source groups on the left:
1. Public database: ENA FASTQ or NCBI SRA accession.
2. Local data: local FASTQ or local SRA files.

Then show a conditional input branch:
SRA file -> fasterq-dump -> FASTQ reads.
Existing FASTQ reads bypass fasterq-dump and go directly to FastQC.
Use a dashed arrow or label "only for SRA input" for fasterq-dump.

Main processing flow after FASTQ:
FASTQ reads -> FastQC quality control -> Trim Galore read trimming -> HISAT2 alignment -> SAM -> samtools sort/index -> sorted BAM.

Reference side inputs:
Genome FASTA -> hisat2-build -> HISAT2 index -> HISAT2.
Annotation GTF/GFF -> featureCounts.
Annotation GTF/GFF -> optional StringTie.

Quantification branches from sorted BAM:
sorted BAM -> featureCounts -> per-sample count table -> project-level expression matrix.
sorted BAM -> optional StringTie, shown as a dashed green branch -> gene abundance table -> optional StringTie FPKM/TPM matrix.

Final output:
gene-by-sample expression matrices, such as raw_counts.tsv, CPM, FPKM, TPM, and optional StringTie FPKM/TPM.

Important correctness constraints:
Do not make fasterq-dump mandatory for FASTQ input.
Do not connect StringTie directly from HISAT2/SAM; StringTie uses sorted BAM.
Do not make StringTie depend on featureCounts.
Do not imply GTF/GFF is generated from FASTA automatically.

Do not include DESeq2, edgeR, volcano plot, heatmap, PCA, GO enrichment, MultiQC dashboard, Web platform, cloud cluster, or any unimplemented feature.
```

### 图 3-3 数据输入输出与任务目录结构图

插入位置：第 3 章 `3.4 数据输入输出设计` 中“系统输出包括样本级中间产物和项目级结果文件”之后。

适合原因：论文中目录结构和输入输出关系较多，适合用一张树状结构图表达任务隔离、样本级产物和报告产物。

图中应包含：

- `workspace/users/<user_id>/tasks/<task_id>/`；
- `inputs/`、`downloads/`、`samples/`、`logs/`、`reports/`、`metadata/`、`progress.json`；
- 样本目录下的 `raw_fastq/`、`qc_raw/`、`trimmed_fastq/`、`alignment/`、`quantification/`；
- `reports/` 下的矩阵和报告文件。

AI 生图提示词：

```text
Create a clean academic file-system and data-flow diagram for an RNA-seq workflow task workspace. Use a white background, flat vector style, thin blue-gray outlines, simple folder/file icons, and readable labels. The figure should look like a computer science thesis diagram, not a decorative poster.

Layout:
Use a three-column structure.
Left column: input and configuration area.
Middle column: per-sample processing outputs.
Right column: project-level reports and final matrices.

Root directory at the top:
workspace/users/<user_id>/tasks/<task_id>/

Under the root, show exactly these first-level entries:
inputs/
downloads/
samples/
logs/
reports/
metadata/
progress.json

Expand metadata/ with:
task.json
params.json
manifest.json
artifact_locations.json

Expand samples/<sample_id>/ with:
raw_fastq/
qc_raw/
trimmed_fastq/
alignment/
quantification/

Show representative files inside sample subdirectories, using small muted labels:
raw_fastq/*.fastq
qc_raw/*_fastqc.html and *_fastqc.zip
trimmed_fastq/*.fq.gz
alignment/*.sam, *.sorted.bam, *.bai, HISAT2 log
quantification/*.featureCounts.txt and optional *.stringtie.gene_abund.tsv

Expand reports/ with:
raw_counts.tsv
cpm.tsv
fpkm.tsv
tpm.tsv
optional stringtie_fpkm.tsv
optional stringtie_tpm.tsv
report.json
report.md

Data-flow arrows:
inputs/ and downloads/ point to samples/<sample_id>/.
samples/<sample_id>/quantification/ points to reports/.
metadata/ and progress.json connect with dashed lines to the whole task workspace, indicating configuration and state tracking.
artifact_locations.json may have a dashed arrow to an external registered spill path, labeled "registered external artifacts", but do not draw arbitrary external directories.

Important constraints:
Keep the tree compact and not too text-heavy.
Do not add unlisted directories such as database/, web/, multiqc/, differential_expression/, plots/, html_dashboard/, celery/, postgres/, or cloud storage.
Do not show DESeq2, edgeR, volcano plots, heatmaps, PCA, GO enrichment, Web platform, or MultiQC dashboard as implemented outputs.
Do not imply that every expression matrix is always generated; mark CPM/FPKM/TPM and StringTie matrices as configurable or optional outputs.
```

### 图 4-1 Reference 资产管理模型图

插入位置：第 4 章 `4.3 样本与参考资产管理` 中 Reference 资产管理段落之后。

适合原因：Reference 管理是系统区别于普通脚本的重要功能，适合用模型图展示 FASTA、GTF/GFF、HISAT2 index 和元数据之间的关系。

图中应包含：

- ReferenceAsset；
- genome FASTA；
- annotation GTF/GFF；
- HISAT2 index prefix；
- `reference.json` metadata；
- species、TaxID、assembly、provider、annotation_provider、source_urls、build_status；
- 运行前检查与任务配置写入。

AI 生图提示词：

```text
Create a clean academic model diagram for Reference asset management in a Python RNA-seq workflow system. Use a white background, flat vector style, blue-gray boxes, teal operation arrows, and small file icons. The figure should be compact and suitable for Chapter 4 implementation details.

Layout:
Use a central box named "ReferenceAsset".
Arrange three groups around it:
1. Managed files
2. Metadata
3. Workflow operations

Managed files group:
Show genome FASTA as an input file.
Show an arrow: genome FASTA -> hisat2-build -> HISAT2 index prefix.
Show annotation GTF/GFF as a separate input file, parallel to FASTA, not derived from FASTA.
Connect HISAT2 index prefix to HISAT2 alignment.
Connect annotation GTF/GFF to featureCounts and optional StringTie.

Metadata group:
Show reference.json linked to ReferenceAsset.
Inside reference.json, list concise fields:
reference_id, species, TaxID, assembly, provider, annotation_provider, source_urls, build_status, warnings.

Workflow operations group:
Show "reference-register": registers or copies FASTA, annotation, and optional existing HISAT2 index.
Show "reference-build-hisat2": builds HISAT2 index from genome FASTA.
Show "reference-check": validates FASTA, annotation, and HISAT2 index completeness.
Show "reference-use": writes hisat2_index and featurecounts_annotation into config.yaml.
Show "reference-prepare": optional one-stop download/register/build flow from Ensembl or URL.

Consistency check:
Add a small side box named "Sample metadata" with organism and TaxID.
Draw a dashed arrow from Sample metadata to ReferenceAsset metadata, labeled "species / TaxID consistency check".

Important correctness constraints:
Do not imply that annotation GTF/GFF is generated from genome FASTA.
Do not imply that a HISAT2 index can replace annotation files.
Do not show automatic species inference without user confirmation.
Do not include downstream differential expression, Web platform, cloud database, or unimplemented reference databases.
Keep labels short and avoid dense paragraphs.
```

### 图 4-2 状态持久化与断点续跑机制图

插入位置：第 4 章 `4.7 任务调度与状态管理` 中 progress.json 介绍之后。

适合原因：`progress.json`、跳过已完成步骤、`apply_cached_result`、失败停止是系统可靠性的核心，适合以状态机或流程图表示。

图中应包含：

- PENDING / RUNNING / COMPLETED / FAILED / SKIPPED / CANCELLED；
- 执行前读取历史记录；
- 已完成则跳过并恢复输出路径；
- 未完成则校验输入、运行步骤、保存结果；
- 失败时停止当前样本后续步骤；
- 其他样本可继续执行。

AI 生图提示词：

```text
Draw a clean academic diagram for state persistence and resume execution in a Python RNA-seq workflow. Use a white background, vector flowchart style, thin arrows, and readable labels. The diagram should explain how progress.json controls skip, resume, failure handling, and cancellation.

Layout:
Use three visual areas:
Left: resume decision flow.
Right: step status state machine.
Bottom: concurrent sample behavior and corrupt-state recovery.

Left area, resume decision flow:
Start workflow -> read progress.json -> iterate each sample and step -> check existing StepRecord.
Decision 1: "COMPLETED record exists?"
If yes: skip running the step -> call apply_cached_result -> restore output paths into Sample object -> continue to next step.
If no: mark step as RUNNING in progress.json -> validate_inputs -> run step -> create StepResult -> save StepResult to progress.json.
Decision 2: "StepResult status?"
If COMPLETED or SKIPPED: continue to next step.
If FAILED: stop downstream steps for the current sample.
If CANCELLED: stop current sample or remaining queued work.

Right area, status state machine:
Show status nodes:
PENDING -> RUNNING -> COMPLETED
RUNNING -> FAILED
RUNNING -> CANCELLED
COMPLETED -> SKIPPED on rerun or "skip completed step"
Use colors:
PENDING gray, RUNNING blue, COMPLETED green, FAILED red, SKIPPED light gray, CANCELLED orange.

Bottom area:
Show multiple sample lanes, for example Sample A, Sample B, Sample C.
Indicate that one sample failure stops only that sample's downstream steps, while other concurrent samples may continue.
Show corrupt progress.json recovery:
read progress.json fails -> backup as progress.json.corrupt-<timestamp> -> initialize new progress.json.

Show progress.json as a central file icon connected to mark_running, save_step_result, and get_step_record.

Important constraints:
Do not draw cloud queues, database servers, Celery, PostgreSQL, Web dashboards, or distributed schedulers.
Do not imply that failed steps are automatically retried unless explicitly configured.
Do not imply that completed steps are skipped solely by filename; state records and cached output restoration are part of the mechanism.
Keep text concise and suitable for a computer science thesis figure.
```

### 图 4-3 Docker/local 外部命令执行封装图

插入位置：第 4 章 `4.4 外部命令与Docker封装` 段落之后。

适合原因：该图说明系统如何把 Python 调度与外部生信工具隔离，体现可移植性和复现性。

图中应包含：

- Python Step；
- `run_context_command` / command execution layer；
- local mode；
- Docker mode；
- workspace bind mount to `/workspace`；
- path translation；
- external tools；
- CommandResult 包含 command、return_code、stdout、stderr、started_at、finished_at。

AI 生图提示词：

```text
Create a clean technical architecture diagram explaining Docker/local external command execution in a Python RNA-seq workflow. Use a white background, flat vector style, thin arrows, blue-gray boxes, and teal highlights. The figure should be suitable for Chapter 4 implementation details.

Layout:
Use a left-to-right pipeline with two execution branches in the middle.

Left side: Python workflow layer
Show Pipeline -> PipelineStep -> build structured command list.
Examples of structured command lists:
["fastqc", "--threads", "2", "--outdir", "...", "sample.fastq"]
["hisat2", "-x", "index", "-U", "reads.fq", "-S", "sample.sam"]
Emphasize "list arguments, not shell string".

Middle: command execution layer
Show run_context_command / command runner.
Input fields:
execution_mode, docker_image, docker_workspace, extra mounts, cancellation_token, dry_run.
Decision node: execution_mode = local or docker.

Branch A: local mode
Run external command directly on host operating system.
Capture stdout and stderr continuously.
Return CommandResult.

Branch B: Docker mode
Build docker run command.
Mount docker_workspace to /workspace.
Optionally mount registered extra artifact paths.
Translate host paths inside docker_workspace to /workspace paths.
Run the same external bioinformatics command inside container.
Use cidfile or container id for cancellation when needed.
Return CommandResult.

Right side: external bioinformatics tools
Show tool icons or boxes:
SRA Toolkit / fasterq-dump, FastQC, Trim Galore, HISAT2, samtools, featureCounts, optional StringTie.

Bottom: result and state recording
Show CommandResult fields:
command, return_code, stdout, stderr, started_at, finished_at, duration_seconds, dry_run.
Then show CommandResult -> StepResult -> progress.json.
Show cancellation signal can terminate a local process or Docker container and lead to CANCELLED status.

Important constraints:
Do not show Kubernetes, cloud clusters, remote servers, Celery workers, PostgreSQL, or Web dashboards.
Do not imply that Docker changes the bioinformatics algorithm; Docker only standardizes the tool runtime environment and path access.
Do not use shell-pipeline strings with pipes or redirection as the main command representation.
Keep labels concise and aligned.
```

### 图 4-4 表达矩阵汇总流程图

插入位置：第 4 章 `4.6 项目级结果汇总` 段落之后。

适合原因：论文中矩阵输出类型较多，图示可以帮助读者区分 featureCounts 后处理矩阵和 StringTie abundance 矩阵。

图中应包含：

- 各样本 `*.featureCounts.txt`；
- `merge_featurecounts_files`；
- raw_counts；
- CPM / FPKM / TPM 后处理；
- 可选 `*.stringtie.gene_abund.tsv`；
- StringTie FPKM / TPM 合并；
- `report.json` 和 `report.md`。

AI 生图提示词：

```text
Draw an expression matrix finalization flowchart for an RNA-seq workflow. Use a white background, clean academic vector graphics, teal and blue-gray colors.

Left side: per-sample quantification outputs:
S1.featureCounts.txt, S2.featureCounts.txt, ... SN.featureCounts.txt.
These feed into merge_featurecounts_files and generate raw_counts.tsv.
From the same featureCounts matrix, show three normalization outputs: cpm.tsv, fpkm.tsv, tpm.tsv.

Add a separate optional dashed branch:
S1.stringtie.gene_abund.tsv, S2.stringtie.gene_abund.tsv, ... -> merge StringTie abundance -> stringtie_fpkm.tsv and stringtie_tpm.tsv.

Right side: report.json and report.md summarize project id, matrix dimensions, output paths, and artifacts.

Add a validation gate before finalization: all required sample quantification files must exist; otherwise no incomplete matrix is generated.
Do not include differential expression, volcano plots, heatmaps, PCA, or gene ontology enrichment.
```

## 5. 可选增强配图

### 图 4-5 CLI/TUI 交互与任务管理界面抽象图

插入位置：第 4 章 `4.8 资源监控与终端交互` 末尾。

适合原因：终端交互是系统易用性设计的一部分。若论文篇幅允许，可画抽象界面流，而不是伪造真实截图。

图中应包含：

- 主菜单；
- Reference 管理；
- 下载；
- SRA 转 FASTQ；
- FastQC；
- Trim Galore；
- 运行 workflow；
- 运行进度面板；
- 最近输出；
- 资源状态。

AI 生图提示词：

```text
Create a schematic terminal TUI interaction diagram for a local RNA-seq workflow system. It should look like an abstract interface map, not a fake screenshot.

Center: terminal TUI main menu.
Connected menu items: task management, reference management, SRA download, SRA to FASTQ, FastQC quality control, Trim Galore trimming, run workflow, view recent output, artifact statistics and cleanup.

Show a run monitor panel with simple indicators: total progress, sample status table, current step, download speed, CPU memory disk status, cancel key.

Use a formal computer science paper style, monochrome terminal-like panels with subtle blue/green accents. Keep text readable and minimal. Do not show a Web browser or graphical desktop app.
```

### 图 5-1 测试与对比评价框架图

插入位置：第 5 章 `5.2 对比实验设计` 或 `5.3 功能完整性测试` 之前。

适合原因：第 5 章多为测试表格，增加一张评价框架图可以把功能完整性、传统方式对比、结果一致性和异常处理串联起来。

图中应包含：

- 功能完整性测试；
- 传统方式对比；
- 成熟工作流工具对比；
- 运行效率与结果一致性；
- 稳定性与异常处理；
- 输出为评估结论。

AI 生图提示词：

```text
Draw an evaluation framework diagram for a Python RNA-seq workflow system. White background, clean vector style, academic layout.

Center: System evaluation.
Five surrounding evaluation blocks:
1. Functional completeness: task creation, sample input, Reference management, workflow execution, matrix finalization, artifact cleanup.
2. Comparison with manual command line and Bash scripts: command count, parameter management, state recovery, result aggregation.
3. Comparison with Snakemake Nextflow Galaxy: positioning, deployment cost, local customization, suitable scenarios.
4. Runtime and result consistency: same input data, same tool versions, same parameters, compare featureCounts raw counts and matrix dimensions.
5. Stability and exception handling: missing input, missing reference, inconsistent species, repeated run, cancellation, low disk space, corrupt progress.json.

Use arrows from all blocks to a final box named usability and applicability assessment. Do not include fabricated numeric results.
```

### 图 5-2 异常处理与安全清理机制图

插入位置：第 5 章 `5.7 稳定性与异常处理测试` 段落之后。

适合原因：磁盘空间、取消、进度文件损坏、跨盘清理是项目针对长流程风险的工程特征，适合展示为可靠性机制图。

图中应包含：

- 输入/参考文件缺失；
- 下载中断或取消；
- 低磁盘空间；
- `progress.json` 损坏；
- 跨盘产物清理；
- 对应处理：校验失败、保留半成品续传、取消或备用路径、备份损坏状态、只清理已登记路径。

AI 生图提示词：

```text
Create a reliability and exception handling diagram for an RNA-seq workflow management system. White background, flat vector style, concise labels.

Left column: exception scenarios:
missing input or reference file, download interruption, user cancellation, low disk space, corrupt progress.json, cross-disk artifact cleanup request.

Right column: system responses:
validate before execution and record error, keep resumable partial download, stop running local or Docker command and mark CANCELLED, cancel task or write large outputs to registered spill path, backup corrupt progress file and initialize new state, delete only current task directory files or paths registered in artifact_locations.json.

Show progress.json, artifact_locations.json, and .done/.lock/.error markers as small supporting artifacts.

Use restrained colors: red/orange for exceptions, green/blue for recovery actions. Do not show unrelated security or cloud backup features.
```

## 6. 不建议单独绘制的内容

以下内容更适合保留为表格或文字，不建议单独制图：

- 第 2 章数据格式表：SRA、FASTQ、SAM/BAM、GTF/GFF、TSV/JSON/Markdown 用表格已经足够；
- 第 2 章表达量指标表：Raw counts、CPM、FPKM、TPM 的差异适合表格；
- 第 5 章测试环境表：需要填写真实环境参数，不适合 AI 图；
- 第 5 章运行时间对比表：需要真实测试数据支撑，不应先画概念图；
- 第 6 章展望：可以文字说明，不应画成已实现功能路线图。

## 7. 推荐最终图表清单

| 优先级 | 图号建议 | 插入位置 | 图名 | 类型 |
|---|---|---|---|---|
| 必须 | 图3-1 | 3.2 | 系统总体架构图 | 分层架构图 |
| 必须 | 图3-2 | 3.3 | 系统工作流程图 | 工作流图 |
| 高 | 图2-1 | 2.3 | RNA-seq 上游分析基本流程图 | 生信流程图 |
| 高 | 图3-3 | 3.4 | 数据输入输出与任务目录结构图 | 数据流/目录树 |
| 高 | 图4-1 | 4.3 | Reference 资产管理模型图 | 实体关系图 |
| 高 | 图4-2 | 4.7 | 状态持久化与断点续跑机制图 | 状态/流程图 |
| 高 | 图4-3 | 4.4 | Docker/local 外部命令执行封装图 | 执行架构图 |
| 高 | 图4-4 | 4.6 | 表达矩阵汇总流程图 | 数据处理流程图 |
| 中 | 图4-5 | 4.8 | CLI/TUI 交互与任务管理界面抽象图 | 界面流程图 |
| 中 | 图5-1 | 5.2/5.3 | 测试与对比评价框架图 | 评价框架图 |
| 中 | 图5-2 | 5.7 | 异常处理与安全清理机制图 | 可靠性机制图 |

若论文篇幅有限，建议保留 6 张：图2-1、图3-1、图3-2、图3-3、图4-2、图4-4。这样既覆盖生物信息流程、系统架构、用户工作流、数据组织、恢复机制和结果生成，又不会让论文显得图件过多。
