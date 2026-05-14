# 老项目核心功能梳理

本文档根据 `old_prj/RNAseq_Pipeline2.1` 代码整理，用于提取核心业务目标、模块化测试思路和论文系统设计章节。

`old_prj` 是历史原型，只读参考，不作为后续开发基线。后续不在老项目中修 bug，也不直接复制其实现；新代码从 `workflow/rnaseq_workflow` 开始独立开发。

## 1. 项目定位

老项目是一个可运行的 RNA-seq 上游分析脚本型流水线，主要面向本地已有 `.sra` 文件的批量处理。它将 SRA 转换、质控、修剪、比对、BAM 排序、StringTie 定量和 FPKM 汇总串联为一个自动化流程，并提供日志、进度文件和 TUI 进度面板。

## 2. 入口与配置

### 2.1 主入口

入口文件：

- `old_prj/RNAseq_Pipeline2.1/main.py`

启动参数：

- `--config`：必填，指定 YAML 配置文件。
- `--no-tui`：可选，禁用 Textual TUI，便于调试终端输出。

主入口职责：

1. 读取配置文件。
2. 创建日志目录和输出目录。
3. 检查 GFF/GFF3 注释文件。
4. 检查 HISAT2 索引文件。
5. 初始化 `progress.json`。
6. 检查 CPU、内存、磁盘等系统资源。
7. 扫描输入目录下的 `.sra` 文件。
8. 启动 TUI 进度界面。
9. 使用 `multiprocessing.Pool` 并行处理每个 SRA 样本。
10. 对输出目录下的 SRP/SRR 结果再次执行 StringTie 和 FPKM 汇总。

### 2.2 配置文件

配置文件：

- `old_prj/RNAseq_Pipeline2.1/config.yaml`

核心配置项：

| 配置项 | 含义 |
|---|---|
| `input_dir` | SRA 文件所在目录 |
| `output_dir` | 分析结果输出目录 |
| `log_dir` | 日志目录 |
| `project_name` | 项目名称 |
| `project_creator` | 项目创建者 |
| `retain_intermediate` | 是否保留 FASTQ、SAM、FastQC 等中间文件 |
| `fastq_dump_params` | `fastq-dump` 参数 |
| `fastqc_threads` | FastQC 线程数 |
| `trimgalore_params` | Trim Galore 参数 |
| `hisat2_index` | HISAT2 索引前缀或目录 |
| `hisat2_threads` | HISAT2 线程数 |
| `samtools_threads` | samtools 线程数 |
| `gff3_file` | 基因组注释文件 |

注意：当前配置中写的是 `stringtie_threadi`，但 `process_sra.py` 使用的是 `stringtie_threads`，存在命名不一致问题。

## 3. 老项目处理流程

单个样本由 `pipeline/process_sra.py` 中的 `process_sra()` 处理，步骤如下：

| 步骤 | 名称 | 外部工具 | 主要输出 |
|---|---|---|---|
| 1 | SRA->FASTQ | `fastq-dump` | `.fastq.gz` |
| 2 | FASTQC | `fastqc` | FastQC 报告 |
| 3 | TrimGalore | `trim_galore` | 修剪后的 FASTQ |
| 4 | HISAT2 | `hisat2` | `.sam`、比对日志 |
| 5 | SAM2BAM | `samtools sort` | `.bam` |
| 6 | StringTie | `stringtie` | `.gtf` |

随后 `main.py` 会遍历输出目录中以 `SRP` 开头的项目目录和以 `SRR` 开头的样本目录，再执行：

1. `stringtie -e -G ...`
2. Perl 命令从 GTF 中解析每个 gene 的 FPKM。
3. 使用 pandas 合并每个 SRP 下所有 SRR 的 FPKM。
4. 生成 `{SRP}_FPKM_summary.xlsx`。

## 4. 日志与进度

### 4.1 日志

实现位置：

- `pipeline/utils.py`

能力：

- 使用 Rich 在终端输出彩色日志。
- 通过 `init_log()` 设置日志文件。
- `log_message()` 同时输出到终端和日志文件。
- 支持 `INFO`、`WARNING`、`ERROR`、`DEBUG` 等级。

### 4.2 进度文件

进度文件：

- `{output_dir}/progress.json`

更新方式：

- `utils.update_progress_locked()` 使用 `filelock.FileLock` 防止多个进程同时写入。
- `utils.get_task_progress()` 读取样本已完成步骤，支持粗粒度断点续跑。

进度字段包括：

- `current_step`
- `total_steps`
- `step_name`
- `status`
- `timestamp`
- `start_time`

### 4.3 TUI

实现位置：

- `progress_tui.py`

能力：

- 使用 Textual `DataTable` 展示任务 ID、当前步骤、剩余步骤、状态和已耗时。
- 每秒刷新一次。
- 支持 `q` 或 `ctrl+c` 退出。

## 5. 并行与资源控制

老项目使用 `multiprocessing.Pool(processes=multiprocessing.cpu_count())` 按样本并行处理。

HISAT2 步骤使用 `multiprocessing.Manager().Lock()` 做全局锁，使多个样本的 HISAT2 比对串行执行，避免高线程比对同时运行导致资源过载。

## 6. 已有优点

1. 已经具备完整的端到端 RNA-seq 上游分析主线。
2. 支持批量 `.sra` 文件扫描和样本级并行。
3. 已有进度文件和文件锁，具备断点续跑雏形。
4. 已有 TUI 进度展示，具备用户交互基础。
5. 已有系统资源检查和日志输出。
6. 已能生成按项目汇总的 FPKM Excel。

## 7. 主要问题

1. **职责混杂**：`main.py` 同时负责配置检查、调度、TUI、定量和汇总。
2. **定量重复**：`process_sra.py` 已执行 StringTie，`main.py` 后续又执行一次 StringTie。
3. **配置不一致**：`config.yaml` 中为 `stringtie_threadi`，代码读取 `stringtie_threads`。
4. **路径假设较强**：`main.py` 后处理假设输出目录结构为 `SRP/SRR`，但 `process_sra.py` 实际输出为 `{output_dir}/{task_id}`。
5. **命令拼接风险**：大量 shell 命令通过字符串拼接生成，路径含空格或特殊字符时容易失败。
6. **单端/双端处理不完整**：Trim Galore 支持 fallback，但 HISAT2 步骤固定使用双端参数 `-1/-2`。
7. **断点续跑粒度有限**：只记录步骤编号，没有校验输出文件完整性、版本、参数或 checksum。
8. **缺少模块化测试**：外部工具调用、进度更新、文件扫描、FPKM 合并等逻辑尚未拆分成可单测组件。
9. **输出指标偏窄**：当前重点是 FPKM，尚未补齐 TPM 和 raw counts。
10. **缺少统一报告模块**：FastQC、比对日志、StringTie 输出等还未统一汇总为报告。

## 8. 新系统开发方向

根据老项目体现出的业务目标，后续新系统建议拆分为以下层次：

1. `common`：配置、日志、路径、命令执行、状态模型。
2. `download`：SRA/GEO/GSA/ENA accession 下载、缓存、重试和完整性记录。
3. `data_ingestion`：本地 SRA/FASTQ 扫描、样本识别、FASTQ 转换。
4. `quality_control`：FastQC/RSeQC/MultiQC 前置质控。
5. `read_trimming`：Trim Galore/Cutadapt 清洗。
6. `alignment`：HISAT2 比对与 SAM/BAM 处理。
7. `quantification`：StringTie、featureCounts、FPKM/TPM/raw counts 输出。
8. `reporting`：QC、日志、性能指标、表达矩阵汇总。
9. `execution`：任务调度、依赖关系、断点续跑、并行控制。
10. `ui`：TUI 或命令行展示。

该拆分可以直接支持模块化测试，也更适合论文中“需求分析与系统设计”“系统实现”章节展开。
