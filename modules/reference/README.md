# reference 模块

参考基因组资产管理模块，负责统一保存和复用 genome FASTA、GTF/GFF 注释文件，以及从 FASTA 自动构建 HISAT2 index。

## 为什么需要

真实项目里最麻烦的不是只跑一次 `hisat2-build`，而是后面反复找：

- 这套 index 是哪个 FASTA 生成的
- featureCounts 用的是哪个 GTF/GFF
- 当前 `config.yaml` 指向的文件是否是一套匹配的参考资产

reference 模块把这些文件收进统一目录，并用 `reference.json` 记录元数据。

当前 metadata 还会记录：

- `provider` 和 `annotation_provider`
- `species`、`assembly`、`release`、`taxon_id`
- `source_urls`
- `build_status`
- `warnings`

## 文件角色

HISAT2 相关文件容易混在一起，当前约定如下：

```text
genome FASTA
  用途: hisat2-build 的原始输入
  示例: genome.fa / genome.fasta / genome.fa.gz

HISAT2 index
  用途: hisat2 比对时的 -x 前缀
  来源: 由 genome FASTA 通过 hisat2-build 生成，或登记已有 index
  示例: hisat2/genome.1.ht2 ... hisat2/genome.8.ht2
  配置值: hisat2_index = hisat2/genome

GTF/GFF annotation
  用途: featureCounts 定量
  注意: 不参与 HISAT2 index 构建
  配置值: featurecounts_annotation
```

所以：

```text
FASTA -> hisat2-build -> HISAT2 index
GTF/GFF -> featureCounts
```

## 目录结构

默认结构：

```text
references/
  demo_reference/
    genome.fa
    annotation.gtf
    reference.json
    hisat2/
      genome.1.ht2
      genome.2.ht2
      ...
```

## CLI

登记 FASTA 和注释文件：

```powershell
$env:PYTHONPATH='workflow'
python -m rnaseq_workflow.cli.main reference-register demo_reference `
  --fasta path\to\genome.fa `
  --annotation path\to\genes.gtf
```

如果这套资产来自 Ensembl/RefSeq，也可以顺手把来源记录进去：

```powershell
python -m rnaseq_workflow.cli.main reference-register demo_reference `
  --fasta path\to\genome.fa `
  --annotation path\to\genes.gtf `
  --provider ensembl `
  --annotation-provider ensembl `
  --species glycine_max `
  --assembly GCF_xxx `
  --source-url https://example.org/genome.fa.gz `
  --source-url https://example.org/genes.gtf.gz
```

登记 FASTA/GTF，同时登记已有 HISAT2 index 前缀：

```powershell
python -m rnaseq_workflow.cli.main reference-register demo_reference `
  --fasta path\to\genome.fa `
  --annotation path\to\genes.gtf `
  --hisat2-index path\to\hisat2_index\genome
```

`--hisat2-index` 填的是 index prefix，不是某一个 `.ht2` 文件。比如目录里有：

```text
path\to\hisat2_index\genome.1.ht2
path\to\hisat2_index\genome.2.ht2
...
path\to\hisat2_index\genome.8.ht2
```

则 prefix 是：

```text
path\to\hisat2_index\genome
```

构建 HISAT2 index：

```powershell
python -m rnaseq_workflow.cli.main reference-build-hisat2 demo_reference `
  --execution-mode docker `
  --docker-workspace . `
  --no-dry-run
```

检查 reference 是否完整：

```powershell
python -m rnaseq_workflow.cli.main reference-check demo_reference
```

把当前工作流配置指向这套 reference：

```powershell
python -m rnaseq_workflow.cli.main reference-use config.yaml demo_reference
```

查看已管理的 reference：

```powershell
python -m rnaseq_workflow.cli.main reference-list
python -m rnaseq_workflow.cli.main reference-show demo_reference
```

一条龙从 Ensembl 下载、登记、构建 HISAT2 index，并写入配置：

```powershell
python -m rnaseq_workflow.cli.main reference-prepare tair10 `
  --species arabidopsis_thaliana `
  --division plants `
  --config config.yaml `
  --execution-mode docker `
  --docker-workspace . `
  --no-dry-run-index
```

大豆和谷子建议先用 Ensembl Plants：

- `glycine_max`
- `setaria_italica`

如果是 NCBI/RefSeq 测试数据，可以直接用 URL 模式登记，并把 `--provider refseq` 写进去，避免和 Ensembl 资产混在一起。

如果不是 Ensembl 物种，或者已有可靠下载链接，也可以直接给 URL：

```powershell
python -m rnaseq_workflow.cli.main reference-prepare custom_ref `
  --fasta-url https://example.org/genome.fa.gz `
  --annotation-url https://example.org/genes.gtf.gz `
  --config config.yaml `
  --execution-mode docker `
  --docker-workspace .
```

在交互式终端里也可以完成同样流程：

```powershell
python -m rnaseq_workflow.cli.main ui
```

然后选择：

```text
3 参考基因组 reference
7 一条龙下载 FASTA+GTF 并构建 index
```

## 边界

- HISAT2 index 可以从 genome FASTA 自动生成。
- GTF/GFF 不能从 FASTA 可靠推断出来，当前版本支持从 Ensembl 自动查找和下载，或者由用户提供 URL。
- 如果以前用 TBtools 或其他方式已经生成过 HISAT2 index，可以直接用 `--hisat2-index` 登记，不需要重建。
- `reference-check` 会帮助确认 FASTA、GTF 和 HISAT2 index 是否都在。
- 后续可继续扩展 NCBI Datasets 的专用选择器。
