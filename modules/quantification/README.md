# quantification 模块

表达定量模块，负责 raw counts 生成，后续扩展 StringTie 定量以及 FPKM/TPM/counts 表达矩阵汇总。

## 输入

- sorted BAM
- GFF/GTF 注释文件

## 输出

- 单样本 featureCounts raw count 表
- featureCounts summary 文件
- 多样本 raw counts 矩阵
- 后续扩展：GTF、gene abundance 文件、FPKM 矩阵、TPM 矩阵

## 外部工具

- `featureCounts`
- `stringtie`，后续扩展

## 当前 CLI

```powershell
python -m rnaseq_workflow.cli.main feature-counts SAMPLE_ID OUTPUT_DIR --annotation genes.gtf --bam sample.sorted.bam --dry-run
python -m rnaseq_workflow.cli.main merge-counts S1.featureCounts.txt S2.featureCounts.txt --output raw_counts.tsv
```

`feature-counts` 常用参数：

- `--annotation`：GTF/GFF 注释文件。
- `--bam`：输入 sorted BAM。不提供时默认读取 `OUTPUT_DIR/samples/{sample_id}/alignment/{sample_id}.sorted.bam`。
- `--feature-type`：默认 `exon`，对应 featureCounts `-t`。
- `--attribute-type`：默认 `gene_id`，对应 featureCounts `-g`。
- `--strandness`：默认 `0`，可用 `0/1/2`。
- `--paired`：启用 featureCounts `-p`。
- `--threads`：线程数。

`merge-counts` 输入：

- 一个或多个 `*.featureCounts.txt` 文件。
- sample id 默认从文件名推断，例如 `S1.featureCounts.txt` 推断为 `S1`。
- 输出 TSV 第一列为 `Geneid`，后续列为样本；缺失 gene 自动填充为 `0`。

## 测试重点

- featureCounts 命令构造
- 单样本 BAM + GTF 的 raw counts 输出
- featureCounts 输出解析
- 多样本 raw counts 矩阵合并
- 缺失 gene 填充为 0
- StringTie 命令构造，后续扩展
- GTF/abundance 解析
