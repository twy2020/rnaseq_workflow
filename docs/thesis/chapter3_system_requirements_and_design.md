# 第3章 系统需求分析与总体设计

本章从系统技术实现角度，对 RNA-seq 工作流管理系统的需求、总体架构、工作流程、数据输入输出以及核心功能模块进行分析与设计。系统以转录组测序数据处理流程为主要对象，围绕 SRA/FASTQ 数据获取、质量控制、序列修剪、参考基因组比对、表达量计算和结果汇总等任务，构建具备自动化执行、可恢复运行、任务级状态管理、终端交互和资源监控能力的分析平台。

系统采用 Python 作为主要开发语言，通过模块化流水线方式组织各处理阶段，并利用 Docker 容器统一外部生物信息学工具运行环境。系统提供命令行和终端交互式界面两类入口，其中终端界面面向实际分析任务管理，支持任务创建、样本清单提交、样本元数据更新、参考基因组资产管理、工具参数配置、资源检查、正式运行、实时进度显示、产物统计清理和结果汇总。系统在运行过程中将每个样本、每个步骤的状态写入持久化进度文件，从而支持中断恢复、跳过已完成步骤和异常定位。

## 3.1 系统需求分析

RNA-seq 数据分析流程通常由多个独立工具串联完成，不同工具之间存在输入输出依赖关系，且中间文件体积较大，运行时间较长。传统手工执行方式容易出现参数管理混乱、任务中断后难以恢复、样本批量处理状态不透明、磁盘空间不足导致失败等问题。因此，本系统需要在保证分析流程完整性的基础上，提供统一的任务组织、自动化调度、运行状态追踪和异常处理能力。

### 3.1.1 功能需求

系统功能需求主要包括以下几个方面。

1. 任务与项目管理需求

系统需要支持以任务为单位组织 RNA-seq 分析过程。每个任务具有独立的任务编号、用户编号、元数据、输入目录、下载目录、样本目录、报告目录和进度文件。实际实现中，任务工作区由 `TaskWorkspace` 描述，其目录结构包括：

```text
workspace/
  users/
    <user_id>/
      tasks/
        <task_id>/
          inputs/
          downloads/
          samples/
          logs/
          reports/
          metadata/
          progress.json
```

该结构使不同用户、不同任务之间的输入、输出和运行状态相互隔离，便于恢复、清理和结果归档。

2. 样本输入管理需求

系统需要支持远程样本和本地样本两类输入方式。远程样本主要以 SRA accession 为输入，例如 `SRR`、`ERR`、`DRR` 等运行编号；本地样本则可来自用户指定目录中的 `.sra`、`.fastq`、`.fq`、`.fastq.gz` 或 `.fq.gz` 文件。系统需要自动识别单端或双端测序布局，并将样本组织为统一的 `Sample` 数据对象。`Sample` 对象记录样本编号、源文件路径、测序布局、项目编号以及附加元数据，为后续流水线步骤提供统一输入接口。

系统还需要支持任务级样本元数据维护。对于旧任务或手工提交的样本清单，系统可重新查询 SRA RunInfo、BioProject、BioSample、物种名称和 TaxID 等信息，并写回任务清单；当自动获取失败或用户需要修正信息时，系统应允许手动填写或修改样本元数据。样本元数据用于后续参考基因组物种一致性检查，降低样本物种与 reference 不匹配导致表达矩阵异常的风险。

3. 数据下载需求

系统需要支持从 NCBI SRA 和 ENA 下载测序数据。对于 NCBI SRA，系统调用 SRA Toolkit 的 `prefetch` 获取 `.sra` 文件；对于 ENA，系统查询 ENA API 获取 FASTQ 下载地址，并支持直接下载 FASTQ 文件。下载模块需要具备并发下载、断点续传、缓存识别、完整性校验、下载进度回调、速度统计和取消控制能力。

4. 数据预处理需求

当输入为 `.sra` 文件时，系统需要调用 `fasterq-dump` 将 SRA 文件转换为 FASTQ 文件。转换后系统应自动识别生成的 FASTQ 文件，并更新样本对象的输入路径和测序布局，保证后续质量控制、序列修剪和比对步骤使用正确输入。

5. 质量控制需求

系统需要对原始 FASTQ 文件进行质量控制分析，生成 FastQC 的 HTML 和 ZIP 结果文件。系统应支持多线程参数配置、输出完整性检查以及步骤完成标记，避免重复运行已完成的质量控制步骤。

6. 序列修剪需求

系统需要调用 Trim Galore 对 FASTQ 文件进行接头去除和低质量序列修剪。系统应根据样本布局自动决定单端或双端修剪方式，并将修剪后的 FASTQ 文件作为后续比对步骤的输入。

7. 序列比对需求

系统需要调用 HISAT2 将修剪后的 reads 比对到参考基因组，生成 SAM 文件和比对日志。系统应在运行前检查 HISAT2 索引是否存在，并根据单端或双端数据构造不同命令参数。随后系统需要调用 samtools 对 SAM 文件进行排序，生成 sorted BAM 文件及索引文件。

8. 表达量计算需求

系统需要调用 featureCounts 对 BAM 文件进行基因或外显子层面的表达量计数，并根据用户配置的注释文件、feature type、attribute type、链特异性参数和是否双端测序等参数生成每个样本的 featureCounts 表格。系统还需要支持可选的 StringTie 表达量估计，根据用户选择输出 StringTie FPKM 或 TPM 矩阵。表达矩阵输出类型应采用多选方式配置，并至少选择一种输出，避免运行完成后没有有效表达矩阵。

9. 结果汇总需求

系统需要在全部样本完成表达量计算后，统一执行项目级汇总。汇总过程需要根据输出配置读取 featureCounts 表格或 StringTie gene abundance 表，生成原始 read counts、CPM、featureCounts FPKM、featureCounts TPM、StringTie FPKM 和 StringTie TPM 等一种或多种表达矩阵，并生成 JSON 和 Markdown 格式报告。系统要求最后汇总必须等待所有样本对应的定量步骤完成或跳过后才能执行，避免部分样本未完成时产生不完整矩阵。

10. 参考资产管理需求

系统需要将参考基因组、注释文件和 HISAT2 索引作为可复用资产进行管理。Reference 资产需要记录 reference ID、存储目录、FASTA 路径、GTF/GFF 路径、HISAT2 index prefix、来源数据库、注释来源、物种名称、TaxID、组装版本、下载 URL 和构建状态等元数据。系统应支持登记本地 FASTA/GTF、从 Ensembl 或 URL 一条龙下载并构建 HISAT2 index、检查 reference 完整性、清理失效 reference 记录，以及将 reference 写入当前任务。创建新 reference 时不应预填其他物种名称，需由用户明确选择或输入。

11. 任务调度与恢复需求

系统需要支持两种执行模式：按样本流水线模式和按阶段批量模式。按样本流水线模式中，每个样本按下载、转换、质控、修剪、比对、排序、定量顺序执行；按阶段批量模式中，所有样本完成当前阶段后再进入下一阶段。系统应记录每个样本每个步骤的状态，并在重新运行时跳过已完成步骤，同时通过 `apply_cached_result` 将已完成步骤的输出重新挂接到样本对象，保证恢复运行时输入输出链条一致。

12. 实时进度显示需求

系统需要在终端界面中显示整体进度、样本进度、当前阶段进度、下载总大小、下载速度、预计剩余时间、运行样本数、失败数、取消数以及系统资源状态。对于下载阶段，需要显示总下载进度和单样本下载进度；对于非下载阶段，需要显示输入输出文件大小、输出目录活动状态和当前步骤状态。

终端界面还需要处理长路径、长日志和长列表显示问题。对于长路径，系统应使用工作目录编号代替完整父路径，并在页面底部单独列出目录编号与真实路径的对应关系；对于长内容弹窗、菜单状态区和多选列表，系统应支持滚动或分页，避免内容被终端窗口遮挡。

13. 资源监控与预警需求

RNA-seq 数据处理会产生大量中间文件，尤其是 FASTQ、SAM 和 BAM 文件。因此系统需要实时监控 CPU、内存和任务工作盘空间。当工作盘空间不足时，系统应支持两类策略：取消并终止当前运行，或将后续大产物转移到预设备用路径。跨路径转移需要记录原路径和当前路径，保证后续步骤能够在新路径中继续查找上游产物。

14. 产物统计与清理需求

系统需要提供任务级产物统计和清理能力，统计下载文件、输入记录、样本中间产物、日志、报告以及跨盘转移产物的文件数和大小。清理功能应保留任务配置和元数据，支持清理中间产物或指定类别产物。对于跨盘路径，系统只能清理 `artifact_locations.json` 明确记录且属于当前任务的路径，避免误删其他用户或其他任务的数据。

15. 日志与异常处理需求

系统需要记录命令、返回码、标准输出、错误输出、开始时间、结束时间、运行耗时、输入文件和输出文件等信息。对于运行失败的步骤，应保存错误信息并停止当前样本后续步骤。对于进度文件损坏、Docker 命令取消、下载中断、磁盘空间不足等情况，系统需要提供恢复或明确提示机制。

### 3.1.2 非功能需求

1. 可维护性

系统采用模块化设计，不同功能阶段被封装为独立的 Step 类，例如 `SraToFastqStep`、`FastQCStep`、`TrimGaloreStep`、`Hisat2AlignStep`、`SamtoolsSortStep` 和 `FeatureCountsStep`。每个 Step 实现统一接口，包括 `validate_inputs` 和 `run` 方法，降低新增工具或替换工具的成本。

2. 可扩展性

系统通过步骤注册表组织模块，可根据步骤编号构建流水线。由于各步骤均遵循统一的 `PipelineStep` 协议，后续可扩展新的质量控制工具、比对工具或定量工具。同时，下载模块也通过不同 Downloader 实现 ENA、SRA 和自动模式切换。

3. 可恢复性

系统将运行状态持久化到 `progress.json`，每个步骤执行前标记为 `RUNNING`，执行后保存为 `COMPLETED`、`FAILED`、`SKIPPED` 或 `CANCELLED`。重新运行时，系统读取历史状态并跳过已完成步骤。若进度文件损坏，`JsonStateRepository` 会备份损坏文件并重新初始化状态文件，避免程序直接崩溃。

4. 可靠性

系统在步骤执行前进行输入校验，例如检查 FASTQ、SAM、BAM、参考索引和注释文件是否存在。对于下载文件，系统支持缓存检测和 SRA 完整性验证；对于 FastQC 和 Trim Galore 输出，系统通过输出文件完整性和完成标记判断步骤是否真正完成。

5. 可移植性

系统通过 Docker 统一外部工具运行环境。`run_context_command` 根据执行模式决定直接在本地运行或构建 Docker 命令。Docker 模式下，系统将本地工作目录挂载到容器 `/workspace`，并支持额外挂载备用路径到 `/mnt/rnaseq_extra_*`，解决不同机器工具安装环境不一致的问题。

6. 性能需求

系统支持下载并发和处理并发分离。下载阶段由 `download_workers` 控制，后续处理阶段由 `max_workers` 控制，避免下载并发与计算并发相互干扰。各生信工具还可单独配置线程数，例如 `sra_threads`、`fastqc_threads`、`trim_cores`、`hisat2_threads`、`samtools_threads` 和 `featurecounts_threads`。

7. 易用性

系统提供终端交互界面，用户可通过向导式页面完成任务创建、清单提交、参考选择、参数配置、资源检查和正式运行。界面显示实时进度和系统资源状态，减少用户手工编写命令和跟踪日志的负担。

8. 数据安全性

系统通过 `.gitignore` 排除运行产物、测序数据、参考基因组大文件和本地配置文件，避免将敏感路径或大体积数据误上传到代码仓库。任务数据按用户 ID 和任务 ID 分层组织，降低多任务混淆风险。

## 3.2 系统总体架构设计

系统总体架构采用分层模块化设计，可分为用户交互层、任务与资产管理层、流水线调度层、步骤执行层、外部工具层、状态持久化层、资源监控层和结果汇总层。

1. 用户交互层

用户交互层包括命令行入口和终端 TUI 入口。命令行入口主要用于脚本化执行，终端 TUI 入口用于交互式任务管理。TUI 基于 `prompt_toolkit` 和 `rich` 实现，支持菜单、表单、向导、运行面板、多选控件和结果输出。对于长列表和长文本，TUI 提供分页或滚动显示；对于长路径，TUI 使用工作目录编号显示父路径，并保留编号到真实路径的映射。

2. 任务与资产管理层

任务与资产管理层负责用户工作区、任务工作区和 reference 资产组织。系统通过 `AssetWorkspace`、`UserWorkspace` 和 `TaskWorkspace` 管理 `workspace/users/<user_id>/tasks/<task_id>` 结构。任务元数据写入 `metadata/task.json`，工具参数写入 `metadata/params.json`，样本清单写入 `metadata/manifest.json`，跨路径产物记录写入 `metadata/artifact_locations.json`。

Reference 资产由 `ReferenceAsset` 描述，存储于用户资产库或公共资产库中。系统通过 SQLite 数据库记录任务和 reference 索引信息，并通过 reference metadata JSON 保存物种、TaxID、来源、FASTA、注释和 HISAT2 index 等字段。

3. 流水线调度层

流水线调度层由 `WorkflowRunner`、`Pipeline` 和 `LocalExecutor` 构成。`WorkflowRunner` 负责选择执行模式；`Pipeline` 负责按步骤顺序执行单个样本，并处理跳过已完成步骤、异常捕获和事件回调；`LocalExecutor` 基于线程池实现样本级并发。

系统核心调度逻辑可以概括为：

```text
WorkflowRunner
  ├── sample_pipeline: 多样本并发，每个样本内部按步骤顺序执行
  └── stage_batch: 每个阶段独立构建 Pipeline，所有样本完成当前阶段后进入下一阶段

Pipeline
  ├── 检查取消信号
  ├── 查询 progress.json 中已有步骤状态
  ├── 跳过已完成步骤并恢复输出路径
  ├── 标记 RUNNING
  ├── validate_inputs
  ├── run
  ├── 保存 StepResult
  └── 若失败则停止当前样本后续步骤
```

4. 步骤执行层

步骤执行层由各生信处理模块组成。每个步骤均返回 `StepResult`，其中包含步骤状态、命令、返回码、输入输出路径和额外运行信息。该层不直接管理全局调度，而是专注于当前样本当前步骤的输入校验、命令构造和结果解析。

5. 外部工具层

系统调用的主要外部工具包括 SRA Toolkit、FastQC、Trim Galore、HISAT2、samtools、featureCounts 和 StringTie。系统通过命令构造函数生成工具命令，并由 `run_context_command` 执行。Docker 模式下命令会被转换为 `docker run` 命令，保证工具运行环境一致。Reference 一条龙准备流程还会调用下载逻辑和 `hisat2-build` 完成参考索引构建。

6. 状态持久化层

状态持久化层由 `JsonStateRepository` 实现。系统将每个样本每个步骤的执行记录保存到 `progress.json`，包括状态、消息、命令、返回码、输入、输出、开始时间、结束时间和附加信息。状态文件采用原子写入方式，以减少运行中断造成文件损坏的风险。

7. 资源监控层

资源监控层通过 `system_monitor.py` 实现 CPU、内存和磁盘采样。运行时资源守护逻辑 `_RuntimeResourceGuard` 周期性读取系统资源状态，并根据 `TaskParams` 中的磁盘阈值和策略进行预警或处理。当配置为转移策略时，系统会将后续大产物写入备用路径，并记录跨路径产物映射。

8. 结果汇总层

结果汇总层由 `finalize_project` 及表达矩阵合并模块构成。该层根据任务参数中的表达矩阵输出类型，选择 featureCounts 或 StringTie 输出作为输入，生成一个或多个表达矩阵，并将报告文件写入 `reports` 目录。结果汇总层独立于单样本步骤执行，只有在所有相关样本步骤满足就绪条件后才运行。

系统总体架构可表示为：

```text
用户
  │
  ▼
TUI / CLI 交互层
  │
  ▼
任务与资产管理层（TaskWorkspace / ReferenceAsset / metadata / params / manifest）
  │
  ▼
流水线调度层（WorkflowRunner / Pipeline / LocalExecutor）
  │
  ├──────────────► 状态持久化层（JsonStateRepository / progress.json）
  │
  ├──────────────► 资源监控层（CPU / 内存 / 磁盘 / 转移策略）
  │
  ▼
步骤执行层（Download / SRA to FASTQ / FastQC / Trim / HISAT2 / samtools / featureCounts）
  │
  ▼
外部工具层（Docker / SRA Toolkit / FastQC / Trim Galore / HISAT2 / samtools / Subread）
  │
  ▼
结果输出层（raw_counts.tsv / cpm.tsv / fpkm.tsv / tpm.tsv / stringtie_*.tsv / report.json / report.md）
```

## 3.3 系统工作流程设计

系统工作流程围绕“任务创建、输入准备、样本元数据维护、参考配置、参数配置、资源检查、正式运行、结果汇总和产物管理”展开。

1. 创建或选择任务

用户进入 TUI 后，可创建新任务或选择已有任务。任务创建后，系统为其分配唯一任务 ID，并创建 `inputs`、`downloads`、`samples`、`logs`、`reports` 和 `metadata` 等目录。

2. 提交样本清单

用户可提交 SRA accession、URL 清单或本地数据路径。系统将清单解析为统一记录，并保存在任务元数据目录中。对于本地路径，系统扫描目录下的 SRA 和 FASTQ 文件，并生成样本对象。对于远程 SRA，系统根据 accession 构造下载请求。

3. 更新样本元数据

提交清单后，系统可自动查询样本元数据，并将物种名称、TaxID、BioProject、BioSample 等字段写入 `manifest.json`。如果自动查询失败，用户可以手动修改样本元数据。该步骤为后续物种一致性检查提供依据。

4. 选择或准备参考基因组

用户选择已准备好的 reference，或通过一条龙流程创建新的 reference。新建 reference 时，用户需要明确选择物种、来源、版本、FASTA/GTF URL、是否构建 HISAT2 index 等信息；系统不预填其他物种名称。Reference 选择后，系统检查 HISAT2 索引、注释文件和物种元数据是否存在，并将参考信息写入任务元数据，后续 HISAT2、featureCounts 和 StringTie 将使用该配置。

5. 配置工具参数

用户通过工具配置向导设置下载来源、下载并发、处理并发、各步骤线程数、Trim Galore 质量阈值、featureCounts 参数、StringTie 线程数、表达矩阵输出类型和资源预警策略。表达矩阵输出类型使用多选控件配置，可选择 `raw_counts`、`cpm`、`fpkm`、`tpm`、`stringtie_fpkm` 或 `stringtie_tpm`，并且至少需要选择一种。参数写入 `params.json`，运行时被转换为 `RunContext.config`。

6. 资源检查

系统根据样本数量、预计下载大小和流程步骤估算运行资源需求，并记录资源检查结果。正式运行前，系统还会检查样本物种与 reference 物种是否一致、reference 文件是否完整、参数是否有效。资源检查完成后才能进入正式运行，以降低因配置不完整、物种不匹配或资源不足导致的失败风险。

7. 正式运行

正式运行时，系统根据执行模式构建步骤列表。按样本流水线模式下，步骤包括：

```text
download
→ sra_to_fastq
→ fastqc
→ trim_galore
→ hisat2
→ samtools_sort
→ featurecounts
[→ stringtie]
```

其中 StringTie 步骤仅在用户选择 StringTie FPKM 或 TPM 输出时加入流程。系统通过 TUI 运行面板实时显示总进度、样本进度、阶段进度、下载汇总和系统资源状态。用户可按取消键触发 `CancellationToken`，系统会终止当前命令或标记待运行步骤为取消。

7. 自动恢复与跳过

每个步骤完成后，系统将结果保存至 `progress.json`。重新运行时，若步骤已完成且未强制重跑，则系统跳过该步骤，并调用 `apply_cached_result` 将历史输出路径重新绑定到样本对象。例如 SRA 转 FASTQ 完成后，恢复运行时系统会重新扫描 raw_fastq 目录并将 FASTQ 文件作为后续步骤输入。

9. 结果汇总

所有样本完成所需定量步骤后，系统调用 `finalize_project` 执行项目级汇总。汇总步骤根据用户选择生成：

- `raw_counts.tsv`：featureCounts 原始 read counts；
- `cpm.tsv`：基于 featureCounts count 和 library size 计算的 CPM；
- `fpkm.tsv`：基于 featureCounts count、基因长度和 library size 计算的 FPKM；
- `tpm.tsv`：基于 featureCounts count 和基因长度计算的 TPM；
- `stringtie_fpkm.tsv`：由 StringTie `-A` gene abundance 表合并得到的 FPKM；
- `stringtie_tpm.tsv`：由 StringTie `-A` gene abundance 表合并得到的 TPM；
- `report.json`：机器可读报告；
- `report.md`：Markdown 格式项目报告。

如果存在样本 featureCounts 或 StringTie 输出未完成，系统不会执行汇总，并在运行面板中显示未汇总原因。汇总完成后，运行面板会显示主要汇总文件路径。

10. 产物统计与清理

任务完成或调试过程中，用户可进入产物统计页面查看各类产物大小。清理页面可删除下载文件、样本中间产物、日志或已登记的跨盘产物。跨盘产物只在 `artifact_locations.json` 明确记录并属于当前任务时允许清理。

## 3.4 数据输入与输出设计

1. 输入数据设计

系统支持以下输入类型：

- SRA accession：如 `SRR11047173`，用于远程下载；
- SRA 文件：本地 `.sra` 文件；
- FASTQ 文件：本地 `.fastq`、`.fq`、`.fastq.gz`、`.fq.gz` 文件；
- URL 清单：用户提供的文件下载地址；
- 参考基因组索引：HISAT2 索引文件；
- 注释文件：GTF/GFF/GFF3 格式注释文件；
- reference 元数据：reference ID、物种、TaxID、来源、组装版本和下载地址；
- 参数配置：任务级 `params.json`；
- 样本清单：任务级 `manifest.json`。

所有样本在系统内部统一表示为 `Sample` 对象。`Sample` 的关键字段包括：

- `sample_id`：样本编号；
- `source_path`：主要输入路径；
- `source_paths`：多个输入路径，双端测序可包含 R1/R2；
- `layout`：`single`、`paired` 或 `unknown`；
- `metadata`：附加信息，例如 accession、输入类型、预估大小等。

样本元数据还可包含 `scientific_name`、`taxon_id`、`bioproject`、`biosample`、`organism` 等字段。系统在运行前可将这些字段与 reference 资产元数据进行比对，用于发现物种不一致或参考文件选择错误的问题。

2. 输出数据设计

系统按样本组织中间产物和最终产物：

```text
samples/
  <sample_id>/
    raw_fastq/         # SRA 转换或 ENA 下载得到的 FASTQ
    qc_raw/            # FastQC 原始质控结果
    trimmed_fastq/     # Trim Galore 修剪结果
    alignment/         # HISAT2 SAM、samtools sorted BAM 和索引
    quantification/    # featureCounts 和 StringTie 输出
reports/
  raw_counts.tsv
  cpm.tsv
  fpkm.tsv
  tpm.tsv
  stringtie_fpkm.tsv
  stringtie_tpm.tsv
  report.json
  report.md
progress.json
```

上述表达矩阵文件并非每次全部生成，而是根据 `expression_output_formats` 配置生成至少一种。默认配置包含原始 count 和 featureCounts FPKM，用户可在 TUI 中通过多选页面增加或减少输出类型。

各步骤主要输入输出关系如下：

| 步骤 | 输入 | 输出 |
|---|---|---|
| download | SRA accession 或 URL | `.sra` 或 FASTQ |
| sra_to_fastq | `.sra` | `raw_fastq/*.fastq` |
| fastqc | FASTQ | `qc_raw/*_fastqc.html`、`*_fastqc.zip` |
| trim_galore | FASTQ | `trimmed_fastq/*.fq.gz`、修剪报告 |
| hisat2 | 修剪后 FASTQ、HISAT2 index | `alignment/<sample>.sam`、HISAT2 日志 |
| samtools_sort | SAM | `alignment/<sample>.sorted.bam`、`.bai` |
| featurecounts | sorted BAM、annotation | `quantification/<sample>.featureCounts.txt` |
| stringtie | sorted BAM、annotation | `quantification/<sample>.stringtie.gtf`、`<sample>.stringtie.gene_abund.tsv` |
| finalize | featureCounts 表和/或 StringTie abundance 表 | 多种表达矩阵、`report.json`、`report.md` |

3. 跨路径产物记录设计

当工作盘空间不足且启用转移策略时，系统会将后续大产物写入备用路径。例如：

```text
H:\rnaseq\users\<user_id>\tasks\<task_id>\samples\...
```

系统通过 `artifact_locations.json` 记录原路径和当前路径，避免后续步骤仍然到旧路径查找上游产物。对于已经完成的上游样本目录，系统会在切换输出根目录时迁移样本产物，并重写进度文件中的相关路径。

产物统计和清理模块也读取该记录文件。因此，即使任务产物分布在默认工作盘和备用盘，系统仍可在任务视角统计文件数和总大小。清理跨盘产物时，系统只允许清理记录文件中属于当前任务的路径，避免跨用户或跨任务资源越界。

## 3.5 系统功能模块设计

### 3.5.1 数据下载模块

数据下载模块负责从公共数据库获取测序数据。模块主要由 `DownloadManager`、`PrefetchDownloader`、`EnaFastqDownloader` 和 `AutoDownloader` 构成。

`DownloadManager` 负责批量下载调度。其内部使用线程池并发执行下载任务，并维护 accession 到 `DownloadProgress` 的映射。系统可通过 `overall_progress` 获取总下载进度，包括总数、完成数、失败数、取消数、跳过数、运行数、已下载字节数和下载速度。

`PrefetchDownloader` 面向 NCBI SRA 数据源。其主要功能包括：

- 校验 accession 格式；
- 构造 `prefetch <accession> --output-directory <dir>` 命令；
- 支持 Docker 模式运行；
- 使用文件锁避免同一 accession 被并发下载；
- 检查已有缓存 `.sra` 文件；
- 对缓存或下载完成文件执行 SRA 完整性验证；
- 支持断点续传和失败后清理；
- 通过 progress callback 回传下载大小、速度、百分比和状态。

`EnaFastqDownloader` 面向 ENA FASTQ 数据源。系统首先访问 ENA filereport API 获取 FASTQ URL、MD5 和文件大小，然后逐文件下载。下载过程中使用 `.part` 临时文件，下载完成并通过大小或 MD5 校验后再替换为正式文件，从而降低中断导致文件损坏的风险。

自动下载模式 `AutoDownloader` 可根据数据源可用性选择 ENA 或 SRA 下载路径，提高下载成功率。

下载模块与后续处理模块使用不同并发参数。`download_workers` 只控制下载样本并发数，`max_workers` 控制后续样本处理调度并发数，二者分离可以避免网络下载、磁盘写入和 CPU 密集型计算相互挤占资源。下载代理由 `download_proxy` 配置，可为空；为空表示直连。

### 3.5.2 质量控制模块

质量控制模块由 `FastQCStep` 实现。该步骤从样本对象中提取 FASTQ 文件，构造 FastQC 命令：

```text
fastqc --threads <threads> --outdir <output_dir> [--quiet] <fastq...>
```

模块在执行前检查 FASTQ 是否存在。执行时将结果写入 `samples/<sample_id>/qc_raw`。系统通过 `_fastqc_outputs_complete` 检查 HTML 和 ZIP 是否存在、大小是否大于 0、ZIP 是否可读，并要求输出稳定一定时间后才认为完成。该设计用于处理部分环境下 FastQC 命令结束状态与文件写入状态不同步的问题。

质量控制步骤完成后，系统写入 `.done.json` 标记。重新运行时若该标记存在且未强制重跑，则直接跳过步骤。

### 3.5.3 序列修剪模块

序列修剪模块由 `TrimGaloreStep` 实现。该模块根据样本布局决定单端或双端参数，构造命令：

```text
trim_galore --quality <q> --stringency <n> --cores <cores> --output_dir <dir> --phred33 [--paired] [--gzip] <fastq...>
```

执行前系统检查 FASTQ 文件数量是否与样本布局一致。执行完成后，模块查找输出目录中的 `.fq`、`.fastq`、`.fq.gz` 或 `.fastq.gz` 文件，并将修剪后的 FASTQ 作为后续 HISAT2 比对输入。

模块还设计了输出恢复机制。如果 Trim Galore 命令返回异常但输出文件和修剪报告已经完整，系统会将该步骤恢复为 `COMPLETED`，避免因命令退出码或终端中断造成重复处理。失败时，系统写入 `.error.txt`，并清理不完整输出但保留错误文件。

### 3.5.4 序列比对模块

序列比对模块包括 HISAT2 比对和 samtools 排序两个步骤。

HISAT2 比对由 `Hisat2AlignStep` 实现。系统首先检查 FASTQ 输入、测序布局和 HISAT2 索引是否有效。对于单端样本构造 `-U` 参数，对于双端样本构造 `-1` 和 `-2` 参数。输出包括：

```text
alignment/<sample_id>.sam
alignment/<sample_id>.hisat2.log
```

命令形式为：

```text
hisat2 -p <threads> -x <index_prefix> -1 <R1> -2 <R2> -S <sample.sam> --summary-file <sample.hisat2.log>
```

samtools 排序由 `SamtoolsSortStep` 实现。该步骤读取 HISAT2 生成的 SAM 文件，生成 sorted BAM 和 BAI 索引：

```text
samtools sort -@ <threads> -o <sample.sorted.bam> <sample.sam>
samtools index <sample.sorted.bam>
```

模块支持跳过已存在且有效的 BAM/BAI 文件。恢复运行时，`apply_cached_result` 会将 BAM 文件重新绑定为样本输入，使 featureCounts 能够直接使用 sorted BAM。

### 3.5.5 表达量计算模块

表达量计算模块包括 featureCounts 计数和 StringTie 表达量估计两类能力。

1. featureCounts 计数

featureCounts 计数由 `FeatureCountsStep` 实现。该模块使用 sorted BAM 和参考注释文件计算基因或外显子 read counts。执行前系统检查 annotation 文件和 BAM 文件是否存在。命令构造如下：

```text
featureCounts -T <threads> -a <annotation> -o <output> -t <feature_type> -g <attribute_type> -s <strandness> [-p] <bam>
```

输出文件包括：

```text
quantification/<sample_id>.featureCounts.txt
quantification/<sample_id>.featureCounts.txt.summary
```

featureCounts 输出的原始 counts 是整数，表示落入指定 feature 的 reads 或 fragments 数。系统在项目级汇总时可以基于 featureCounts 的 `Length` 和 count 进一步计算 CPM、FPKM 和 TPM：CPM 只按 library size 归一化；FPKM 同时按基因长度和 library size 归一化；TPM 先按长度计算 RPK，再按样本内 RPK 总量归一化。

2. StringTie 表达量估计

StringTie 表达量估计由 `StringTieStep` 实现，仅当用户选择 `stringtie_fpkm` 或 `stringtie_tpm` 输出时加入流水线。该步骤读取 sorted BAM 和注释文件，构造命令：

```text
stringtie <sample.sorted.bam> -p <threads> -G <annotation> -o <sample.stringtie.gtf> -e -A <sample.stringtie.gene_abund.tsv>
```

其中 `-e` 表示在给定注释指导下估计表达量，`-A` 输出 gene abundance 表。StringTie 输出的 FPKM/TPM 来自 StringTie 自身的表达量估计算法，featureCounts FPKM/TPM 则是系统基于 read counts、注释长度和 library size 进行后处理计算。二者输入和算法不同，因此数值不要求完全一致，适用于不同分析需求和结果比对。

3. 项目级表达矩阵汇总

所有样本完成所需定量步骤后，系统进入项目级汇总。`finalize_project` 根据 `expression_output_formats` 读取每个样本的 featureCounts 表或 StringTie abundance 表，调用矩阵合并逻辑生成对应 TSV 文件，并通过报告模块生成 `report.json` 和 `report.md`。汇总前系统检查全部样本定量状态，若存在未完成样本或缺失输出文件，则不执行汇总并给出缺失路径。

### 3.5.6 任务调度模块

任务调度模块由 `WorkflowRunner`、`Pipeline`、`LocalExecutor`、`JsonStateRepository` 和 TUI 运行控制逻辑共同组成。

`WorkflowRunner` 提供两种运行模式：

- `sample_pipeline`：样本级并发，每个样本内部按完整流程执行；
- `stage_batch`：阶段级执行，每次只构造一个步骤的 Pipeline，使所有样本完成同一阶段后再进入下一阶段。

`LocalExecutor` 使用 `ThreadPoolExecutor` 实现样本并发。并发数由任务参数 `max_workers` 控制。下载阶段使用独立的 `download_workers`，在 TUI 中通过 `_ManifestDownloadStep` 和 `_ProcessingConcurrencyStep` 将下载并发与后续处理并发分离。系统构建步骤计划时会根据表达矩阵输出类型动态决定是否加入 `stringtie` 步骤，避免未选择 StringTie 输出时执行不必要计算。

`Pipeline` 是调度核心。对于每个样本，它按步骤顺序执行：

1. 检查取消令牌；
2. 读取历史步骤记录；
3. 对已完成步骤执行跳过逻辑；
4. 将步骤标记为 `RUNNING`；
5. 调用 `validate_inputs`；
6. 调用 `run`；
7. 保存 `StepResult`；
8. 若步骤失败，则停止当前样本后续步骤。

TUI 运行面板通过事件回调和进度回调更新界面，显示样本进度、阶段进度、下载统计和系统资源状态。用户取消运行时，`CancellationToken` 会通知当前命令终止；Docker 模式下系统通过 `cidfile` 找到对应容器并执行强制停止，避免容器后台残留。

任务调度模块还负责运行结束后的 finalize 调用。对于按样本流水线模式，系统只有在所有样本相关定量步骤完成后才触发最终汇总；若用户取消或任一样本失败，则运行面板显示未完成状态，不生成不完整报告。

### 3.5.7 日志与异常处理模块

日志与异常处理模块贯穿整个系统，主要包括命令结果记录、状态持久化、错误输出保存、取消处理、进度文件恢复和资源异常处理。

1. 命令执行记录

系统通过 `run_command` 和 `run_context_command` 执行外部工具命令。每次执行返回 `CommandResult`，包含：

- command；
- return_code；
- stdout；
- stderr；
- started_at；
- finished_at；
- duration_seconds；
- dry_run。

这些信息会被写入 `StepResult.extra` 和 `progress.json`，便于后续定位错误。

2. 状态持久化

`JsonStateRepository` 负责写入步骤状态。每个步骤运行前调用 `mark_running`，运行后调用 `save_step_result`。写入采用临时文件替换方式，降低中途崩溃导致 JSON 文件不完整的概率。若读取时发现 JSON 损坏，系统会将原文件备份为 `progress.json.corrupt-<timestamp>`，并重新创建空状态文件。

3. 步骤级异常处理

`Pipeline` 捕获步骤执行中的异常，并将其转换为失败的 `StepResult`，避免异常直接使整个程序崩溃。若某个步骤状态为 `FAILED`，系统停止该样本后续步骤，但其他并发样本可继续执行。

4. 输出锁与完成标记

部分步骤使用 `.lock` 文件避免同一输出目录被重复写入，使用 `.done.json` 标记步骤完成。失败时根据策略清理不完整输出，或保留错误日志文件 `.error.txt`。

5. 下载异常处理

下载模块处理缓存、断点续传、校验失败、下载停滞、取消和重试。对于缓存 SRA 文件，系统会验证完整性并写入验证标记；对于校验失败文件，系统可清理后重新下载；对于取消操作，系统保留可恢复的部分下载。

6. 资源异常处理

系统运行时周期性监控工作盘空间。若剩余空间低于阈值，默认策略是取消并终止当前任务；若启用转移策略，则系统选择可用备用路径，将后续大产物写入备用盘，并迁移已完成样本产物，保证后续步骤能继续运行。跨路径迁移信息写入 `artifact_locations.json`。

7. 产物统计与清理异常处理

产物统计模块读取默认任务目录和 `artifact_locations.json` 中登记的外部路径。清理模块在删除前会检查目标路径是否位于当前任务目录下；如果目标位于任务目录外，则必须是当前任务记录过的跨盘路径。该校验避免了备用盘路径配置错误或多用户共享目录下误删其他任务产物。

8. 最终汇总异常处理

结果汇总前，系统检查所有样本所需定量步骤是否完成。若未满足条件，系统不生成矩阵，并在界面提示未就绪样本。若汇总过程缺少 featureCounts 表或 StringTie abundance 表，则抛出明确的 `FileNotFoundError`，提示缺失路径。

综上，本系统通过统一状态模型、模块化步骤接口、Docker 环境封装、任务级目录规范、实时资源监控和异常恢复机制，实现了 RNA-seq 分析流程从数据获取到表达矩阵生成的自动化管理，为后续实验分析和论文结果复现提供了可追踪、可维护的技术基础。
