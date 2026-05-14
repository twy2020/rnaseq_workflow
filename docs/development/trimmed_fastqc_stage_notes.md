# Trimmed FastQC 阶段记录

## 目标

对 Trim Galore 输出的 trimmed FASTQ 进行二次 FastQC，确认修剪后 reads 是否可以进入比对阶段。

当前阶段用于 workflow 功能验证和质量信号记录，不等同于最终生物学质控结论。

## 输入与输出

输入目录：

```text
runtime_logs/trim_test
```

FastQC 输出目录：

```text
runtime_logs/fastqc_test2
```

输出结构：

```text
runtime_logs/fastqc_test2/samples/{sample_id}/qc_raw
```

每个 FastQC 成功样本目录包含：

```text
.done.json
*_fastqc.html
*_fastqc.zip
```

## 完成状态

当前 6 个样本均完成二次 FastQC：

```text
SRR11047173  done=True  lock=False  html=2  zip=2
SRR19820386  done=True  lock=False  html=1  zip=1
SRR19820387  done=True  lock=False  html=1  zip=1
SRR1982039   done=True  lock=False  html=2  zip=2
SRR19820396  done=True  lock=False  html=2  zip=2
SRR19820397  done=True  lock=False  html=2  zip=2
```

## FastQC 汇总

```text
sample       file                              PASS  WARN  FAIL  TotalSeq  Length   GC
SRR11047173  SRR11047173_1_val_1_fastqc.zip      6     2     2  22600531  18-141   46
SRR11047173  SRR11047173_2_val_2_fastqc.zip      7     2     1  22600531  18-141   46
SRR19820386  SRR19820386_1_trimmed_fastqc.zip    4     2     4     91238  20-1229  37
SRR19820387  SRR19820387_1_trimmed_fastqc.zip    3     3     4    130344  20-1268  37
SRR1982039   SRR1982039_1_val_1_fastqc.zip       6     2     3  32337722  20-101   50
SRR1982039   SRR1982039_2_val_2_fastqc.zip       7     1     3  32337722  20-101   50
SRR19820396  SRR19820396_1_val_1_fastqc.zip      6     2     2  24390033  20-151   54
SRR19820396  SRR19820396_2_val_2_fastqc.zip      6     2     2  24390033  20-151   54
SRR19820397  SRR19820397_1_val_1_fastqc.zip      6     2     2  21458225  20-151   54
SRR19820397  SRR19820397_2_val_2_fastqc.zip      6     2     2  21458225  20-151   54
```

## 主要 QC 信号

大样本主要问题：

```text
Sequence Duplication Levels
Per base sequence content
Per sequence GC content
Sequence Length Distribution
```

小样本 `SRR19820386` 和 `SRR19820387`：

```text
reads 数较少
GC content / duplication / overrepresented sequences 信号更明显
```

## 初步解释

Trim 后仍存在 FastQC FAIL/WARN 并不一定表示样本不可用。RNA-seq 中以下模块经常需要结合下游结果解释：

```text
Sequence Duplication Levels
Per base sequence content
Per sequence GC content
Overrepresented sequences
```

可能原因包括：

```text
高表达基因导致 reads 集中
PCR duplication
rRNA 或特定转录本富集
低复杂度样本
样本生物来源 GC 偏移
reads 数较少导致统计波动
```

## 阶段结论

作为 workflow 功能测试，这批 trimmed FASTQ 可以进入下一阶段：

```text
Reference 准备
-> HISAT2 index
-> HISAT2 alignment
```

但作为正式生物学分析，后续还需要结合以下指标继续判断：

```text
比对率
唯一比对率
featureCounts assigned rate
样本间相关性
PCA / clustering
重复样本一致性
```

## 遗留问题

- `SRR11047173` 的 FASTQ 来源目录仍异常，当前位于 `downloads/SRR000001/` 下，后续需要整理下载/转换输出路径。
- FastQC 对大文件运行时间较长，建议默认样本并发为 `2`，FastQC 线程数为 `2`。
- 后续可增加 MultiQC 汇总，将 FastQC/Trim Galore 报告统一成项目级 HTML。

## 下一步

建议进入 reference/alignment 阶段：

```text
1. 选择或准备参考基因组
2. 下载/登记 FASTA + GTF
3. 构建 HISAT2 index
4. 使用 trimmed FASTQ 进行 HISAT2 alignment
```
