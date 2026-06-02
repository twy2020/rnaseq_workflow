# 本文系统与 nf-core/rnaseq Docker 镜像体积对比

## 1. 统计目的

为比较本文系统与 nf-core/rnaseq 在本地部署时的镜像体积开销，本节统计两套流程在 Docker Desktop 环境下所需的主要镜像大小。统计口径采用 `docker image ls` 显示的 `DISK USAGE`，该值更接近 Docker Desktop 本地镜像占用情况。

## 2. 本文系统镜像

| 镜像 | 用途 | 大小 |
|---|---|---:|
| `rnaseq-workflow:tools` | RNA-seq 上游分析工具镜像 | 1.11 GB |
| `rnaseq-workflow:sra-tools` | SRA Toolkit 数据下载与转换镜像 | 317 MB |
| **合计** |  | **约 1.43 GB** |

## 3. nf-core/rnaseq 主要运行镜像

| 镜像 | 用途 | 大小 |
|---|---|---:|
| `nfcore-dood-runner:25.10.2` | Nextflow Docker-outside-of-Docker 运行环境 | 553 MB |
| `community.wave.seqera.io/library/trim-galore` | Trim Galore 修剪 | 182 MB |
| `community.wave.seqera.io/library/htslib_samtools` | samtools/htslib 相关步骤 | 284 MB |
| `community.wave.seqera.io/library/multiqc` | MultiQC 报告 | 2.01 GB |
| `community.wave.seqera.io/library/hisat2_samtools` | HISAT2 比对及 samtools | 788 MB |
| `community.wave.seqera.io/library/rsem_star` | RSEM/STAR 相关模块镜像 | 2.10 GB |
| `community.wave.seqera.io/library/bedtools_coreutils` | bedtools 与基础工具 | 232 MB |
| `quay.io/biocontainers/stringtie` | StringTie 定量 | 330 MB |
| `quay.io/biocontainers/qualimap` | Qualimap RNA-seq QC | 2.48 GB |
| `quay.io/biocontainers/fastqc` | FastQC 质控 | 939 MB |
| 其他小工具镜像 | `fq`、`perl`、`python`、UCSC 工具等 | 约 636 MB |
| **合计** |  | **约 10.53 GB** |

## 4. nf-core/Nextflow 辅助镜像

| 镜像 | 用途 | 大小 |
|---|---|---:|
| `nfcore-runner:25.10.2` | nf-core 辅助运行镜像 | 493 MB |
| `nextflow/nextflow:25.10.2` | Nextflow 官方镜像 | 1.05 GB |
| `docker:29.1.3-cli` | Docker CLI 镜像 | 237 MB |
| **合计** |  | **约 1.78 GB** |

## 5. 总体对比

| 对比项 | 镜像总大小 |
|---|---:|
| 本文系统 | 约 1.43 GB |
| nf-core/rnaseq 主运行镜像 | 约 10.53 GB |
| nf-core/rnaseq 含辅助镜像 | 约 12.31 GB |

按主运行镜像计算，nf-core/rnaseq 的镜像体积约为本文系统的 **7.4 倍**；若将 Nextflow 与辅助运行镜像一并纳入统计，nf-core/rnaseq 的镜像体积约为本文系统的 **8.6 倍**。

## 6. 结果说明

nf-core/rnaseq 采用模块化、多镜像的标准化流程设计，包含 MultiQC、Qualimap、RSEM/STAR、UCSC 工具等扩展分析和报告模块，因此镜像数量和总体体积明显高于本文系统。本文系统将 RNA-seq 上游分析所需的核心工具封装为少量本地镜像，部署体积更小，更适合本地轻量化运行和教学实验场景。

需要注意的是，Docker 镜像存在共享层机制，不同统计命令对镜像体积的显示口径可能略有差异。本报告采用 Docker Desktop `docker image ls` 显示的 `DISK USAGE` 作为统计依据。
