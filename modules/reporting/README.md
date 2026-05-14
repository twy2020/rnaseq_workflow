# reporting 模块

报告模块，负责汇总质控、比对、定量和运行统计结果，生成项目级报告。

## 输入

- FastQC/MultiQC 输出
- HISAT2 日志
- StringTie/featureCounts 输出
- 运行状态文件
- raw counts 矩阵

## 输出

- JSON 报告摘要
- Markdown 报告摘要
- 后续扩展：HTML 报告
- CSV/Excel 表达矩阵
- 运行摘要
- 错误摘要

## 当前 CLI

```powershell
python -m rnaseq_workflow.cli.main report-summary PROJECT_ID OUTPUT_DIR --counts-matrix raw_counts.tsv --json-output report.json --markdown-output report.md
python -m rnaseq_workflow.cli.main finalize config.yaml
```

常用参数：

- `--state`：运行状态文件，默认读取 `OUTPUT_DIR/progress.json`。
- `--counts-matrix`：raw counts 矩阵 TSV。
- `--artifact`：需要纳入报告的产物路径，可重复传入。
- `--json-output`：写出 JSON 报告。
- `--markdown-output`：写出 Markdown 报告。

`finalize` 会读取配置中的样本列表，收集默认位置的 featureCounts 输出：

```text
OUTPUT_DIR/samples/{sample_id}/quantification/{sample_id}.featureCounts.txt
```

默认生成：

```text
OUTPUT_DIR/reports/raw_counts.tsv
OUTPUT_DIR/reports/report.json
OUTPUT_DIR/reports/report.md
```

## 测试重点

- 多样本结果聚合
- progress JSON 状态统计
- counts 矩阵概览
- artifact 存在性和大小统计
- JSON/Markdown 报告写出
- Excel/CSV 输出
- 缺失样本或失败样本处理
