# RNA-seq 上游分析流程、工具、命令与参数说明

本文档整理当前项目实现的 RNA-seq 上游分析流程，包括样本输入、数据下载、格式转换、质量控制、序列修剪、比对、SAM/BAM 处理、表达量统计和项目级汇总。命令与参数以当前代码实现为准，主要对应 `workflow/rnaseq_workflow/steps/` 下的各 Step。

## 1. 流程总览

系统按样本组织分析流程。默认样本级流程如下：

```text
FASTQ/SRA 输入
  -> SRA 转 FASTQ（仅 SRA 输入需要）
  -> FastQC 原始质控
  -> Trim Galore 序列修剪
  -> FastQC 修剪后二次质控
  -> HISAT2 参考基因组比对
  -> samtools sort/index
  -> featureCounts read counts 统计
  -> 可选 StringTie gene abundance
  -> 项目级表达矩阵与报告汇总
```

默认步骤由 `DEFAULT_SAMPLE_STEPS` 定义：

```text
quality_control -> fastqc
read_trimming -> trim_galore
trimmed_quality_control -> fastqc_trimmed
alignment -> hisat2, samtools_sort
quantification -> featurecounts
```

SRA 下载和 SRA 转 FASTQ 属于输入准备阶段。若样本输入已经是 FASTQ，则可直接进入 FastQC；若输入是 `.sra`，则先执行 `fasterq-dump` 转换。

## 2. 目录结构

任务输出目录按样本分层组织：

```text
<output_dir>/
  progress.json
  logs/
  reports/
  samples/
    <sample_id>/
      raw_fastq/
      qc_raw/
      trimmed_fastq/
      qc_trimmed/
      alignment/
      quantification/
```

主要目录含义如下：

| 目录 | 内容 |
|---|---|
| `raw_fastq/` | SRA 转换得到的原始 FASTQ |
| `qc_raw/` | 原始 FASTQ 的 FastQC HTML/ZIP 报告 |
| `trimmed_fastq/` | Trim Galore 输出的 clean FASTQ 和修剪报告 |
| `qc_trimmed/` | clean FASTQ 的二次 FastQC HTML/ZIP 报告 |
| `alignment/` | HISAT2 SAM、HISAT2 日志、sorted BAM 和 BAI 索引 |
| `quantification/` | featureCounts 和 StringTie 定量结果 |
| `reports/` | 表达矩阵、HISAT2 汇总表、JSON/Markdown 报告 |

系统使用 `.done.json` 标记步骤完成，用 `progress.json` 记录样本、步骤、命令、输入输出和状态。

## 3. 工具与命令

### 3.1 SRA 下载：prefetch

用途：从 NCBI SRA 下载 `.sra` 文件。

命令模板：

```bash
prefetch <accession> --output-directory <download_dir>
```

可选参数：

```bash
--max-size <size>
--transport <transport>
--force yes
```

对应实现：

```text
workflow/rnaseq_workflow/steps/download/prefetch.py
build_prefetch_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `download_source` | `auto` | 下载来源选择，支持自动判断 |
| `download_workers` | `2` | 下载并发数 |
| `download_max_size` | `5G` | SRA 下载大小上限 |
| `force` | `false` | 是否强制重新下载 |
| `resume_partial` | `true` | 是否保留部分下载文件用于续传 |

### 3.2 SRA 转 FASTQ：fasterq-dump

用途：将 `.sra` 文件转换为 FASTQ。

命令模板：

```bash
fasterq-dump <sample.sra> \
  --outdir <output_dir>/samples/<sample_id>/raw_fastq \
  --threads <threads> \
  --split-files \
  --temp <output_dir>/samples/<sample_id>/raw_fastq/_fasterq_tmp
```

若启用进度显示，会额外加入：

```bash
--progress
```

对应实现：

```text
workflow/rnaseq_workflow/steps/data_ingestion/sra_to_fastq.py
build_fasterq_dump_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `fasterq_dump_threads` / `sra_threads` | `4` | SRA 转 FASTQ 线程数 |
| `fasterq_dump_split_files` | `true` | paired-end 数据拆分为 R1/R2 |
| `fasterq_dump_progress` | `false` | 是否显示 fasterq-dump 进度 |

输出：

```text
samples/<sample_id>/raw_fastq/<sample_id>_1.fastq
samples/<sample_id>/raw_fastq/<sample_id>_2.fastq
```

转换完成后，系统会自动将样本输入路径更新为生成的 FASTQ。

### 3.3 原始质控：FastQC

用途：评估原始 FASTQ 测序质量。

命令模板：

```bash
fastqc \
  --threads <threads> \
  --outdir <output_dir>/samples/<sample_id>/qc_raw \
  --quiet \
  <R1.fastq> <R2.fastq>
```

若启用解压输出，会额外加入：

```bash
--extract
```

对应实现：

```text
workflow/rnaseq_workflow/steps/quality_control/fastqc.py
FastQCStep
build_fastqc_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `fastqc_threads` | `2` | FastQC 线程数 |
| `fastqc_quiet` | `true` | 是否使用 `--quiet` |
| `fastqc_extract` | `false` | 是否解压 FastQC ZIP |
| `fastqc_completion_grace_seconds` | `15` | 输出稳定后才认为完成 |

输出：

```text
samples/<sample_id>/qc_raw/*_fastqc.html
samples/<sample_id>/qc_raw/*_fastqc.zip
```

系统会读取 FastQC ZIP 中的 `summary.txt`，记录 `WARN` 和 `FAIL` 模块到步骤结果中。

### 3.4 序列修剪：Trim Galore

用途：去除接头和低质量序列，生成 clean FASTQ。

命令模板：

```bash
trim_galore \
  --quality <quality> \
  --stringency <stringency> \
  --cores <cores> \
  --output_dir <output_dir>/samples/<sample_id>/trimmed_fastq \
  --phred33 \
  --paired \
  --gzip \
  <R1.fastq> <R2.fastq>
```

单端样本不加 `--paired`。

对应实现：

```text
workflow/rnaseq_workflow/steps/read_trimming/trim_galore.py
TrimGaloreStep
build_trim_galore_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `trim_galore_quality` / `trim_quality` | `20` | 低质量碱基修剪阈值 |
| `trim_galore_phred` | `33` | Phred 编码 |
| `trim_galore_stringency` | `3` | 接头匹配严格度 |
| `trim_galore_cores` / `trim_cores` | `1` | Trim Galore 线程数 |
| `trim_galore_gzip` | `true` | 输出 gzip 压缩 FASTQ |

输出：

```text
samples/<sample_id>/trimmed_fastq/*_val_1.fq.gz
samples/<sample_id>/trimmed_fastq/*_val_2.fq.gz
samples/<sample_id>/trimmed_fastq/*trimming_report.txt
```

修剪完成后，系统会把样本输入路径更新为 `trimmed_fastq/` 中的 clean FASTQ，供二次质控和比对使用。

### 3.5 修剪后二次质控：FastQC after trimming

用途：对 Trim Galore 输出的 clean FASTQ 进行二次质量评估。

命令模板：

```bash
fastqc \
  --threads <threads> \
  --outdir <output_dir>/samples/<sample_id>/qc_trimmed \
  --quiet \
  <R1_val_1.fq.gz> <R2_val_2.fq.gz>
```

对应实现：

```text
workflow/rnaseq_workflow/steps/quality_control/fastqc.py
TrimmedFastQCStep
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `trimmed_fastqc_policy` | `run_keep` | 二次质控策略 |
| `fastqc_threads` | `2` | 二次 FastQC 线程数 |

二次质控策略：

| 策略 | 含义 |
|---|---|
| `run_keep` | 默认策略。执行二次质控，保留结果；若存在 WARN/FAIL，在日志和报告中说明，但继续后续步骤 |
| `pause_on_fail` | 执行二次质控，若出现 WARN/FAIL，则将样本标记为 `PAUSED`，跳过该样本后续步骤，等待人工处理 |
| `disabled` | 不执行二次质控 |

输出：

```text
samples/<sample_id>/qc_trimmed/*_fastqc.html
samples/<sample_id>/qc_trimmed/*_fastqc.zip
```

### 3.6 参考基因组比对：HISAT2

用途：将 clean reads 比对到参考基因组。

paired-end 命令模板：

```bash
hisat2 \
  -p <threads> \
  -x <hisat2_index_prefix> \
  -1 <R1_val_1.fq.gz> \
  -2 <R2_val_2.fq.gz> \
  -S <output_dir>/samples/<sample_id>/alignment/<sample_id>.sam \
  --summary-file <output_dir>/samples/<sample_id>/alignment/<sample_id>.hisat2.log
```

single-end 命令模板：

```bash
hisat2 \
  -p <threads> \
  -x <hisat2_index_prefix> \
  -U <sample.fastq.gz> \
  -S <sample_id>.sam \
  --summary-file <sample_id>.hisat2.log
```

若配置剪接位点文件，会额外加入：

```bash
--known-splicesite-infile <splicesites.txt>
```

对应实现：

```text
workflow/rnaseq_workflow/steps/alignment/hisat2.py
Hisat2AlignStep
build_hisat2_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `hisat2_index` | 必填 | HISAT2 索引前缀 |
| `hisat2_threads` | `4` | HISAT2 线程数 |
| `hisat2_splicesites` | 空 | 可选剪接位点文件 |

输出：

```text
samples/<sample_id>/alignment/<sample_id>.sam
samples/<sample_id>/alignment/<sample_id>.hisat2.log
```

项目汇总阶段会解析 HISAT2 日志，生成：

```text
reports/hisat2_alignment_summary.tsv
```

字段包括：

```text
样本ID    总reads数    成功比对reads数    比对率
```

### 3.7 SAM/BAM 处理：samtools sort/index

用途：将 HISAT2 输出的 SAM 排序为 BAM，并建立索引。

排序命令模板：

```bash
samtools sort \
  -@ <threads> \
  -o <output_dir>/samples/<sample_id>/alignment/<sample_id>.sorted.bam \
  <output_dir>/samples/<sample_id>/alignment/<sample_id>.sam
```

索引命令模板：

```bash
samtools index <output_dir>/samples/<sample_id>/alignment/<sample_id>.sorted.bam
```

对应实现：

```text
workflow/rnaseq_workflow/steps/alignment/samtools.py
SamtoolsSortStep
build_samtools_sort_command()
build_samtools_index_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `samtools_threads` | `2` | samtools sort 线程数 |
| `samtools_index` | `true` | 是否生成 BAM 索引 |
| `skip_completed` | `true` | 若 BAM/BAI 已存在则跳过 |

输出：

```text
samples/<sample_id>/alignment/<sample_id>.sorted.bam
samples/<sample_id>/alignment/<sample_id>.sorted.bam.bai
```

### 3.8 read counts 统计：featureCounts

用途：基于排序 BAM 和 GTF/GFF 注释文件统计基因层面的 read counts。

命令模板：

```bash
featureCounts \
  -T <threads> \
  -a <annotation.gtf> \
  -o <output_dir>/samples/<sample_id>/quantification/<sample_id>.featureCounts.txt \
  -t <feature_type> \
  -g <attribute_type> \
  -s <strandness> \
  <sample_id>.sorted.bam
```

若按 paired-end fragment 计数，会额外加入：

```bash
-p
```

对应实现：

```text
workflow/rnaseq_workflow/steps/quantification/featurecounts.py
FeatureCountsStep
build_featurecounts_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `featurecounts_annotation` / `annotation` | 必填 | GTF/GFF 注释文件 |
| `featurecounts_threads` | `2` | featureCounts 线程数 |
| `featurecounts_feature_type` | `exon` | `-t` 参数，统计 feature 类型 |
| `featurecounts_attribute_type` | `gene_id` | `-g` 参数，分组属性 |
| `featurecounts_strandness` | `0` | 链特异性：0 非链特异，1 正链，2 反链 |
| `featurecounts_paired` | `false` | 是否添加 `-p` |

输出：

```text
samples/<sample_id>/quantification/<sample_id>.featureCounts.txt
samples/<sample_id>/quantification/<sample_id>.featureCounts.txt.summary
```

项目汇总阶段会合并各样本 featureCounts 表，生成表达矩阵。

### 3.9 可选转录本/基因丰度估计：StringTie

用途：基于 BAM 和注释文件估计表达丰度，生成 gene abundance 表。该步骤用于 `stringtie_fpkm` 或 `stringtie_tpm` 表达矩阵。

命令模板：

```bash
stringtie \
  <sample_id>.sorted.bam \
  -p <threads> \
  -G <annotation.gtf> \
  -o <output_dir>/samples/<sample_id>/quantification/<sample_id>.stringtie.gtf \
  -e \
  -A <output_dir>/samples/<sample_id>/quantification/<sample_id>.stringtie.gene_abund.tsv
```

对应实现：

```text
workflow/rnaseq_workflow/steps/quantification/stringtie.py
StringTieStep
build_stringtie_command()
```

主要配置：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `stringtie_annotation` | 空 | StringTie 注释文件；未配置时使用 `featurecounts_annotation` |
| `stringtie_threads` | `2` | StringTie 线程数 |
| `stringtie_estimate_only` | `true` | 是否添加 `-e`，只基于参考注释估计表达 |
| `stringtie_gene_abundance` | `true` | 是否添加 `-A` 输出 gene abundance |

输出：

```text
samples/<sample_id>/quantification/<sample_id>.stringtie.gtf
samples/<sample_id>/quantification/<sample_id>.stringtie.gene_abund.tsv
```

### 3.10 项目级汇总与报告

用途：合并样本级结果，生成表达矩阵、比对统计和报告。

对应实现：

```text
workflow/rnaseq_workflow/core/finalize.py
finalize_project()
```

支持的表达矩阵格式：

| 格式 | 来源 |
|---|---|
| `raw_counts` | featureCounts 原始 counts |
| `cpm` | featureCounts counts 归一化 |
| `fpkm` | featureCounts counts 归一化 |
| `tpm` | featureCounts counts 归一化 |
| `stringtie_fpkm` | StringTie `gene_abund.tsv` 合并 |
| `stringtie_tpm` | StringTie `gene_abund.tsv` 合并 |

默认输出格式：

```text
raw_counts, fpkm
```

项目级输出：

```text
reports/raw_counts.tsv
reports/cpm.tsv
reports/fpkm.tsv
reports/tpm.tsv
reports/stringtie_fpkm.tsv
reports/stringtie_tpm.tsv
reports/hisat2_alignment_summary.tsv
reports/report.json
reports/report.md
```

其中具体生成哪些矩阵取决于 `expression_output_formats` 配置。

## 4. 执行环境

系统支持本地执行和 Docker 执行。

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `execution_mode` | `docker` | `docker` 或 `local` |
| `docker_image` | `rnaseq-workflow:tools` | 包含 SRA Toolkit、FastQC、Trim Galore、HISAT2、samtools、featureCounts、StringTie 的工具镜像 |
| `docker_workspace` | `.` | Docker 挂载到 `/workspace` 的宿主机目录 |
| `docker_extra_mounts` | 空 | 额外挂载目录，例如转移盘输出目录 |

Docker 模式下，系统会将原始命令包装为：

```bash
docker run --rm \
  -v <docker_workspace>:/workspace \
  -v <extra_mount>:/mnt/rnaseq_extra_0 \
  -w /workspace \
  rnaseq-workflow:tools \
  <tool command>
```

## 5. 任务运行参数

TUI 任务参数由 `TaskParams` 管理，常用字段如下：

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `execution_mode` | `sample_pipeline` | 按样本流水线或按阶段批处理 |
| `max_workers` | `2` | 样本处理并发数 |
| `download_workers` | `2` | 下载并发数 |
| `cleanup_policy` | `cleanup_after_task` | 清理策略 |
| `resource_guard_enabled` | `true` | 是否启用资源保护 |
| `disk_guard_min_free_gb` | `20.0` | 最小剩余磁盘空间 GB |
| `disk_guard_min_free_percent` | `10.0` | 最小剩余磁盘比例 |
| `disk_guard_strategy` | `cancel` | 磁盘不足时取消或转移 |
| `spill_large_outputs` | `true` | 是否将大产物写入/转移到备用路径 |

## 6. 状态恢复与跳过规则

系统通过以下机制避免重复运行：

| 机制 | 说明 |
|---|---|
| `progress.json` | 记录每个样本、每个步骤的运行状态、命令、输入输出、日志路径 |
| `.done.json` | 写在步骤输出目录中，表示该步骤已完成 |
| `skip_completed` | 对部分步骤，若目标文件已存在且非空，则跳过 |
| `apply_cached_result` | 状态恢复时重新挂载上一步产物，例如 SRA 转 FASTQ、Trim Galore、samtools sort 的输出 |

二次 FastQC 会优先从 `samples/<sample_id>/trimmed_fastq` 读取 clean FASTQ，避免状态恢复时误用原始 FASTQ。

## 7. 典型配置示例

```yaml
project_id: rnaseq_project
work_dir: .
output_dir: output

samples:
  - sample_id: SRR11047173
    source_path: data/SRR11047173.sra
    layout: paired

steps:
  - quality_control
  - read_trimming
  - trimmed_quality_control
  - alignment
  - quantification

execution_mode: docker
docker_image: rnaseq-workflow:tools
docker_workspace: .

fastqc_threads: 2
fastqc_quiet: true
trimmed_fastqc_policy: run_keep

trim_galore_quality: 20
trim_galore_stringency: 3
trim_galore_cores: 1
trim_galore_gzip: true

hisat2_index: references/arabidopsis_thaliana/hisat2/genome
hisat2_threads: 4
samtools_threads: 2

featurecounts_annotation: references/arabidopsis_thaliana/annotation.gtf
featurecounts_threads: 2
featurecounts_feature_type: exon
featurecounts_attribute_type: gene_id
featurecounts_strandness: 0
featurecounts_paired: false

stringtie_threads: 2
expression_output_formats:
  - raw_counts
  - fpkm
```

## 8. 论文描述可用简表

| 阶段 | 工具 | 输入 | 输出 |
|---|---|---|---|
| 数据下载 | SRA Toolkit `prefetch` / ENA 下载 | SRA accession 或 URL | `.sra` 或 FASTQ |
| 格式转换 | SRA Toolkit `fasterq-dump` | `.sra` | 原始 FASTQ |
| 原始质控 | FastQC | 原始 FASTQ | `qc_raw/*_fastqc.html/zip` |
| 序列修剪 | Trim Galore | 原始 FASTQ | clean FASTQ、trimming report |
| 二次质控 | FastQC | clean FASTQ | `qc_trimmed/*_fastqc.html/zip` |
| 比对 | HISAT2 | clean FASTQ、HISAT2 index | SAM、HISAT2 log |
| 排序索引 | samtools | SAM | sorted BAM、BAI |
| read counts | featureCounts | sorted BAM、GTF/GFF | gene counts 表 |
| 表达丰度 | StringTie | sorted BAM、GTF/GFF | gene abundance 表 |
| 汇总报告 | 内置汇总模块 | 样本级结果 | 表达矩阵、比对统计、JSON/Markdown 报告 |

