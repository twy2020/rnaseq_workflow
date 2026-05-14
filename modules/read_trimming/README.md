# read_trimming 模块

reads 修剪模块，负责接头去除、低质量序列过滤和输出清洗后的 FASTQ。

## 输入

- 原始 FASTQ
- 修剪参数

## 输出

- trimmed FASTQ
- Trim Galore 报告

## 外部工具

- `trim_galore`
- `cutadapt`

## 测试重点

- 单端命令构造
- 双端命令构造
- 修剪后文件发现
- 参数变更记录

## 当前实现状态

已实现 Trim Galore 真实运行阶段：

- `workflow/rnaseq_workflow/steps/read_trimming/trim_galore.py`
  - `build_trim_galore_command`
  - `TrimGaloreOptions`
  - `TrimGaloreStep`
  - `find_trimmed_fastq_outputs`
  - `is_trim_galore_output_complete`

支持：

- 单端 FASTQ
- 双端 FASTQ
- `.fastq`、`.fq`、`.fastq.gz`、`.fq.gz`
- dry-run
- Docker/local 执行模式
- 输出到标准目录 `samples/{sample_id}/trimmed_fastq`
- `.done.json` 完成标记
- `.lock` 样本级锁
- 重跑时跳过已完成样本
- 取消/失败时清理半成品
- 若 trimmed FASTQ 和 trimming report 已齐全，可恢复 `.done.json` 并释放旧锁

工具镜像：

- `docker/Dockerfile.tools` 已加入 `cutadapt` 和 `trim-galore`

TUI：

- 主菜单入口：`Trim Galore 修剪`
- 默认输入目录：`downloads`
- 默认输出目录：`runtime_logs/trim_test`
- 默认 Docker 镜像：`rnaseq-workflow:tools`
- 默认参数：`quality=20`、`phred33`、`stringency=3`、gzip 输出
- 默认样本并发：`6`，运行前可修改
- 支持按 `c` 取消
- 状态页显示输入大小、输出大小、最近写入文件和 running idle

真实测试记录：

- 小样本 `SRR19820386` 真实运行通过
- 当前 `runtime_logs/trim_test` 中 6 个样本均已完成并写入 `.done.json`
- 当前完成状态：

```text
SRR11047173  done=True  lock=False
SRR19820386  done=True  lock=False
SRR19820387  done=True  lock=False
SRR1982039   done=True  lock=False
SRR19820396  done=True  lock=False
SRR19820397  done=True  lock=False
```

已修问题：

- Windows 宿主机未安装 `trim_galore` 时，CLI 默认改为 Docker 执行
- 命令找不到时返回失败结果，不再直接 traceback
- TUI 并发改为实时总并发，完成一个样本后立即补下一个
- TUI 运行页增加输出活动监控
- 修复 `Popen(stdout=PIPE, stderr=PIPE)` 未持续读取导致的潜在死锁
- 修复工具已完成但 TUI 未收尾时的恢复逻辑

遗留/待观察：

- `SRR11047173` 的 FASTQ 当前位于 `downloads/SRR000001/` 下，目录归属需要后续整理
- 大样本 Trim Galore 仍耗时较长，建议真实批量运行时降低样本并发或 cores
- 后续应对 trimmed FASTQ 再跑一次 FastQC
