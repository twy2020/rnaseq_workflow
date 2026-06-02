# nf-core/rnaseq 与本文系统 StringTie FPKM 对比实验报告

## 1. 实验目的

为验证本文实现的本地轻量化 RNA-seq 上游分析流程是否能够产生与标准化工作流一致的表达定量结果，本实验选取 nf-core/rnaseq 作为对照流程，在相同输入样本和相同参考资源条件下，对两套流程生成的 StringTie gene-level FPKM 矩阵进行比较。

本实验的核心比较对象为 StringTie FPKM，而不是 featureCounts raw counts。featureCounts 结果仅作为辅助检查产物保留。

## 2. 对比范围与样本

本文系统主流程：

```text
FASTQ/SRA -> FastQC -> Trim Galore -> HISAT2 -> samtools -> featureCounts/StringTie -> 表达矩阵
```

nf-core/rnaseq 对照流程：

```text
FASTQ -> FastQC -> Trim Galore -> HISAT2 -> samtools -> StringTie -> MultiQC/报告
```

为保证对比可解释性，本实验仅比较两套流程共同产生的核心结果：

- HISAT2 比对结果；
- StringTie gene-level FPKM 表达矩阵；
- 共同基因上的 Pearson/Spearman 相关性；
- FPKM 绝对差异。

样本列表：

| 样本ID |
|---|
| SRR11047173 |
| SRR11047174 |
| SRR11047175 |
| SRR11047176 |

输入数据为本地已下载的 paired-end FASTQ 文件，不重新通过网络下载 SRA，以避免网络波动影响实验结果。

## 3. nf-core/rnaseq 运行环境与参数

nf-core 对比实验目录：

```text
F:\rnaseq_nfcore_compare\e5fd308f-b699-4343-9402-0d0084072141
```

运行方式：

| 项目 | 值 |
|---|---|
| 工作流 | nf-core/rnaseq |
| nf-core/rnaseq 版本 | 3.26.0 |
| Nextflow 版本 | 25.10.2 |
| Docker 版本 | 29.1.3 |
| 执行方式 | Windows Docker Desktop + Linux runner container |
| runner 镜像 | nfcore-dood-runner:25.10.2 |
| 输入类型 | 本地 paired-end FASTQ gzip |
| strandedness | unstranded |
| aligner | hisat2 |
| trimmer | trimgalore |
| 最大 CPU | 4 |
| 最大内存 | 20.GB |
| 参考基因组 | `ref/genome.fa` |
| 注释文件 | `ref/annotation.gtf` |

主要 nf-core 参数：

```bash
nextflow run rnaseq-3.26.0 \
  -profile docker \
  -c nfcore_local_resources.config \
  -w work \
  --input samplesheet.csv \
  --outdir nfcore_rnaseq_out \
  --fasta ref/genome.fa \
  --gtf ref/annotation.gtf \
  --aligner hisat2 \
  --trimmer trimgalore \
  --skip_pseudo_alignment \
  --skip_rseqc \
  --skip_biotype_qc \
  --skip_dupradar \
  --skip_preseq \
  --skip_markduplicates \
  --save_align_intermeds \
  --save_reference \
  --igenomes_ignore true \
  --max_cpus 4 \
  --max_memory 20.GB \
  -resume
```

资源限制配置文件 `nfcore_local_resources.config`：

```groovy
process {
  resourceLimits = [
    cpus: 4,
    memory: 20.GB,
    time: 48.h
  ]
}
```

运行过程中 nf-core 提示：由于 HISAT2 index build 可用内存为 20 GB，低于 nf-core 默认判断阈值 200 GB，因此构建 HISAT2 索引时未使用 splice sites 和 exons。该提示会影响 HISAT2 比对率的精确可比性，但不影响本实验对 StringTie FPKM 矩阵整体一致性的评价。

## 4. nf-core 运行结果与资源记录

nf-core 主流程完成状态：

| 项目 | 值 |
|---|---:|
| started_at | 2026-05-19T04:30:34+00:00 |
| finished_at | 2026-05-19T06:31:27+00:00 |
| exit_code | 0 |
| elapsed_seconds | 7253 |
| wall time | 约 2 h 0 min 53 s |

Nextflow trace 摘要：

| 项目 | 值 |
|---|---:|
| trace 任务总数 | 71 |
| CACHED 任务数 | 22 |
| COMPLETED 任务数 | 49 |
| 峰值内存 | 3.4 GB |
| nf-core 输出目录大小 | 22.5 GB |

说明：本次 nf-core 使用 `-resume` 方式恢复运行，因此 trace 中包含 cached 任务。该记录适合作为 nf-core 本次对比实验的运行记录；若论文需要严格比较两套流程从零开始的完整运行时间，应另行对两套流程使用相同输入重新计时。

耗时较长的 nf-core 步骤：

| 步骤 | 状态 | duration | CPU | peak_rss |
|---|---|---:|---:|---:|
| FASTQC, SRR11047175 | CACHED | 17m 42s | 52.2% | 1.3 GB |
| FASTQC, SRR11047173 | CACHED | 17m 33s | 48.9% | 1.2 GB |
| FASTQC, SRR11047174 | CACHED | 17m 7s | 53.1% | 1.6 GB |
| FASTQC, SRR11047176 | CACHED | 16m 53s | 52.5% | 1.2 GB |
| QUALIMAP_RNASEQ, SRR11047174 | COMPLETED | 9m 3s | 76.2% | 1.7 GB |

内存占用较高的 nf-core 步骤：

| 步骤 | 状态 | duration | CPU | peak_rss |
|---|---|---:|---:|---:|
| SAMTOOLS_SORT, SRR11047174 | COMPLETED | 2m 41s | 212.4% | 3.4 GB |
| SAMTOOLS_SORT, SRR11047176 | COMPLETED | 2m 24s | 233.4% | 3.4 GB |
| SAMTOOLS_SORT, SRR11047175 | COMPLETED | 2m 27s | 237.7% | 3.4 GB |
| SAMTOOLS_SORT, SRR11047173 | COMPLETED | 2m 15s | 236.7% | 3.4 GB |
| SAMTOOLS_SORT_QUALIMAP, SRR11047174 | COMPLETED | 3m 7s | 265.9% | 3.4 GB |

主要软件版本：

| 软件 | 版本 |
|---|---|
| nf-core/rnaseq | 3.26.0 |
| Nextflow | 25.10.2 |
| FastQC | 0.12.1 |
| Trim Galore | 2.1.0 |
| HISAT2 | 2.2.1 |
| samtools | 1.23.1 / 1.20 |
| StringTie | 2.2.3 |
| Qualimap | 2.3 |
| bedtools | 2.31.1 |

## 5. HISAT2 比对结果对比

HISAT2 比对率对比结果如下：

| 样本ID | 本文系统总 reads 数 | nf-core 总 reads 数 | 本文系统成功比对 reads 数 | nf-core 成功比对 reads 数 | 本文系统比对率(%) | nf-core 比对率(%) | 差值 |
|---|---:|---:|---:|---:|---:|---:|---:|
| SRR11047173 | 22600531 | 22600525 | 21780132 | 21018145 | 96.37 | 93.00 | 3.37 |
| SRR11047174 | 24175138 | 24175130 | 23592517 | 22722696 | 97.59 | 93.99 | 3.60 |
| SRR11047175 | 25144254 | 25144249 | 24596109 | 23750496 | 97.82 | 94.46 | 3.36 |
| SRR11047176 | 24142570 | 24142568 | 23630748 | 22830268 | 97.88 | 94.56 | 3.32 |

nf-core 的 HISAT2 比对率略低，可能与 HISAT2 索引构建策略有关。nf-core 在本次运行中检测到可用内存为 20 GB，因低于 200 GB 阈值而未使用 splice sites 和 exons 构建索引，因此该差异不直接表示本文系统与 nf-core 在核心表达定量结果上存在明显不一致。

## 6. StringTie FPKM 矩阵构建

本文系统 StringTie FPKM 矩阵：

```text
H:\rnaseq\users\657a87c7-2896-4a3e-be29-8b7c6e7cc353\tasks\e5fd308f-b699-4343-9402-0d0084072141\reports\stringtie_fpkm.tsv
```

nf-core StringTie 输出来源：

```text
F:\rnaseq_nfcore_compare\e5fd308f-b699-4343-9402-0d0084072141\nfcore_rnaseq_out\hisat2\stringtie\*.gene.abundance.txt
```

本实验从 nf-core 每个样本的 `gene.abundance.txt` 文件中提取 `Gene ID` 和 `FPKM` 列，合并为与本文系统相同格式的矩阵：

```text
F:\rnaseq_nfcore_compare\e5fd308f-b699-4343-9402-0d0084072141\comparison\nfcore_stringtie_fpkm.tsv
```

## 7. StringTie FPKM 一致性结果

整体 FPKM 矩阵相关性：

| 共同基因数 | 共同样本数 | Pearson | Spearman | 平均绝对差异 | 最大绝对差异 |
|---:|---:|---:|---:|---:|---:|
| 32833 | 4 | 0.998481 | 0.985404 | 0.465812 | 875.120605 |

逐样本 FPKM 相关性：

| 样本ID | 共同基因数 | Pearson | Spearman | 平均绝对差异 | 最大绝对差异 |
|---|---:|---:|---:|---:|---:|
| SRR11047173 | 32833 | 0.998479 | 0.983871 | 0.493692 | 625.616699 |
| SRR11047174 | 32833 | 0.998212 | 0.986399 | 0.472568 | 771.992676 |
| SRR11047175 | 32833 | 0.998754 | 0.986122 | 0.454717 | 672.668457 |
| SRR11047176 | 32833 | 0.998520 | 0.985130 | 0.442272 | 875.120605 |

FPKM 差异最大的部分基因如下：

| gene_id | sample_id | 本文系统 FPKM | nf-core FPKM | 差值 | 绝对差异 |
|---|---|---:|---:|---:|---:|
| AT5G38410 | SRR11047176 | 7377.745605 | 6502.625000 | 875.120605 | 875.120605 |
| AT5G38410 | SRR11047174 | 6650.763184 | 5878.770508 | 771.992676 | 771.992676 |
| AT5G38430 | SRR11047176 | 1865.377319 | 1122.524292 | 742.853027 | 742.853027 |
| AT5G38410 | SRR11047175 | 5767.328125 | 5094.659668 | 672.668457 | 672.668457 |
| AT5G38430 | SRR11047174 | 1609.772827 | 960.505310 | 649.267517 | 649.267517 |
| AT5G38430 | SRR11047175 | 1597.347412 | 959.248474 | 638.098938 | 638.098938 |
| AT5G38410 | SRR11047173 | 5641.110840 | 5015.494141 | 625.616699 | 625.616699 |

尽管少数高表达基因存在较大的绝对差异，两套流程在全体共同基因上的 Pearson 相关系数达到 0.998481，Spearman 相关系数达到 0.985404，表明两套流程生成的 StringTie gene-level FPKM 矩阵具有高度一致性。

## 8. 产物文件

主要对比产物：

| 文件 | 说明 |
|---|---|
| `comparison\nfcore_stringtie_fpkm.tsv` | nf-core StringTie FPKM 合并矩阵 |
| `comparison\stringtie_fpkm_correlation.tsv` | StringTie FPKM 整体相关性 |
| `comparison\stringtie_fpkm_sample_correlation.tsv` | StringTie FPKM 逐样本相关性 |
| `comparison\stringtie_fpkm_top_differences.tsv` | FPKM 差异最大的基因列表 |
| `comparison\stringtie_fpkm_compare_notes.tsv` | FPKM 对比数据来源说明 |
| `comparison\hisat2_alignment_compare.tsv` | HISAT2 比对率对比 |
| `comparison\run_performance_summary.tsv` | nf-core 运行资源摘要 |

nf-core 运行记录：

| 文件 | 说明 |
|---|---|
| `nfcore_walltime.log` | nf-core 开始、结束时间和退出码 |
| `run_parameters_for_thesis.tsv` | 论文可引用的 nf-core 运行参数记录 |
| `run_nfcore_dood.sh` | nf-core 实际运行命令 |
| `nfcore_nextflow.stdout.log` | nf-core 标准输出日志 |
| `nfcore_rnaseq_out\pipeline_info\execution_trace_2026-05-19_04-30-38.txt` | Nextflow step 级资源记录 |
| `nfcore_rnaseq_out\pipeline_info\execution_report_2026-05-19_04-30-38.html` | Nextflow 执行报告 |
| `nfcore_rnaseq_out\pipeline_info\execution_timeline_2026-05-19_04-30-38.html` | Nextflow 执行时间线 |
| `nfcore_rnaseq_out\multiqc\hisat2\multiqc_report.html` | nf-core MultiQC 报告 |

## 9. 可用于论文的结果表述

可在论文中写为：

> 为进一步验证本文系统表达定量结果的可靠性，本文选取 nf-core/rnaseq 3.26.0 作为标准化工作流对照，在相同 FASTQ 输入、相同参考基因组和注释文件条件下，采用 HISAT2 与 StringTie 进行比对和表达量计算。结果显示，在 32833 个共同基因和 4 个样本上，本文系统与 nf-core/rnaseq 生成的 StringTie gene-level FPKM 矩阵 Pearson 相关系数为 0.998481，Spearman 相关系数为 0.985404。逐样本 Pearson 相关系数均高于 0.998，说明本文系统对 RNA-seq 上游流程的封装未改变主要表达定量结果。

同时可补充说明：

> nf-core/rnaseq 在扩展质量控制、MultiQC 报告和流程规范化方面更加完整；本文系统则侧重本地轻量化部署、终端交互、任务状态恢复和参考资源管理。由于两套系统功能范围不同，本文仅比较共同核心分析结果，不将 nf-core 的扩展 QC 模块纳入性能优劣判断。

## 10. 结论

本次对比实验表明，在相同样本和参考资源条件下，本文系统与 nf-core/rnaseq 生成的 StringTie FPKM 表达矩阵高度一致。整体 Pearson 相关系数为 0.998481，逐样本 Pearson 相关系数均高于 0.998，说明本文系统能够稳定复现标准化 RNA-seq 工作流的核心表达定量结果。

需要注意的是，本次 nf-core 运行使用了 `-resume`，因此其 wall time 和 step trace 包含恢复运行场景下的 cached/completed 任务状态。若论文需要严格比较两套流程从零开始的运行性能，应在相同输入、相同线程数和相同磁盘环境下重新进行完整计时实验。
