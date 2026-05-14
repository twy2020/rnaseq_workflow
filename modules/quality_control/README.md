# quality_control 模块

质控模块，负责原始 reads 和修剪后 reads 的质量评估，并为统一报告模块提供 QC 输出。

## 输入

- 原始 FASTQ
- 修剪后 FASTQ

## 输出

- FastQC 报告
- RSeQC 报告，后续扩展
- MultiQC 输入文件

## 外部工具

- `fastqc`
- `multiqc`
- `rseqc`，后续扩展

## 测试重点

- FastQC 命令构造
- 报告目录生成
- 工具失败时的状态记录

## 当前实现状态

已实现 FastQC 第一阶段：

- `workflow/rnaseq_workflow/steps/quality_control/fastqc.py`
  - `build_fastqc_command`
  - `FastQCOptions`
  - `FastQCStep`

支持：

- 单端 FASTQ
- 双端 FASTQ
- `.fastq`、`.fq`、`.fastq.gz`、`.fq.gz`
- dry-run
- 输出到标准目录 `samples/{sample_id}/qc_raw`

工具镜像：

- `docker/Dockerfile.tools`
- `scripts/build_tools_image.ps1`
- `scripts/run_tools.ps1`
