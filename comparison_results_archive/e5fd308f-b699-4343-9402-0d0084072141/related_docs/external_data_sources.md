# 系统外部数据下载来源说明

## 1. 文档目的

本文档说明本 RNA-seq 工作流系统在运行过程中可能访问或下载的外部数据来源，包括测序样本数据、样本元数据、参考基因组、注释文件以及工具镜像构建时的软件源。该说明可用于论文中“数据来源与参考资源管理”相关章节，也可作为系统部署和网络访问排查依据。

系统支持远程 accession 下载、本地 SRA/FASTQ 导入、参考资源下载与登记。对于用户已经准备好的本地文件，系统只进行扫描、复制或登记，不再访问外部数据库。

## 2. 样本测序数据来源

### 2.1 ENA FASTQ 数据

系统默认可优先尝试从 ENA 获取 FASTQ 下载链接。ENA 来源用于直接下载 `.fastq.gz` 或 `.fq.gz` 文件，避免先下载 SRA 再转换的额外步骤。

对应模块：

```text
workflow/rnaseq_workflow/steps/download/ena.py
workflow/rnaseq_workflow/steps/download/auto.py
```

访问接口：

```text
https://www.ebi.ac.uk/ena/portal/api/filereport
```

查询参数：

| 参数 | 值 | 说明 |
|---|---|---|
| `accession` | SRR/ERR/DRR run accession | 样本运行编号 |
| `result` | `read_run` | 查询 read run 记录 |
| `fields` | `run_accession,fastq_ftp,fastq_md5,fastq_bytes,library_layout` | 获取 FASTQ 链接、MD5、大小和文库类型 |
| `format` | `tsv` | 返回 TSV |
| `download` | `false` | 只获取元数据，不直接下载表格 |

系统从 ENA 返回结果中读取：

| 字段 | 用途 |
|---|---|
| `fastq_ftp` | FASTQ 文件下载地址 |
| `fastq_md5` | 下载后校验 MD5 |
| `fastq_bytes` | 文件大小，用于进度显示和完整性检查 |
| `library_layout` | 判断单端/双端信息 |

下载方式：

```text
curl -L -C - --fail --retry 5 --retry-connrefused <FASTQ_URL>
```

当系统检测到本机有 `curl` 时，会优先调用 `curl` 下载，并支持断点续传、重试、代理和下载进度记录。若没有 `curl`，则使用 Python `urllib.request` 下载。

下载产物一般保存为：

```text
<task_dir>/downloads/<accession>/<fastq_file>.fastq.gz
```

### 2.2 NCBI SRA 数据

当 ENA FASTQ 链接不可用，或用户指定使用 SRA 来源时，系统通过 SRA Toolkit 的 `prefetch` 从 NCBI SRA 下载 `.sra` 文件。

对应模块：

```text
workflow/rnaseq_workflow/steps/download/prefetch.py
workflow/rnaseq_workflow/steps/download/auto.py
```

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

支持的 accession 形式：

```text
SRRxxxx
ERRxxxx
DRRxxxx
```

下载完成后，系统会使用 `vdb-validate` 校验 SRA 文件完整性。如果本地已存在通过校验的 SRA 文件，系统会复用缓存并跳过重复下载。

下载产物一般保存为：

```text
<task_dir>/downloads/<accession>/<accession>.sra
```

## 3. 样本元数据来源

系统会从 NCBI SRA RunInfo 接口查询样本元数据，用于记录样本来源、物种、TaxID、BioProject、BioSample、文库类型和数据大小等信息。

对应模块：

```text
workflow/rnaseq_workflow/steps/download/runinfo.py
```

访问接口：

```text
https://trace.ncbi.nlm.nih.gov/Traces/sra-db-be/runinfo
```

查询形式：

```text
https://trace.ncbi.nlm.nih.gov/Traces/sra-db-be/runinfo?acc=<accession>
```

系统读取的主要字段：

| 字段 | 说明 |
|---|---|
| `Run` | Run accession |
| `BioProject` | BioProject 编号 |
| `BioSample` | BioSample 编号 |
| `Experiment` | 实验编号 |
| `Sample` | 样本编号 |
| `TaxID` | 物种 TaxID |
| `ScientificName` | 物种名称 |
| `LibraryStrategy` | 文库策略，如 RNA-Seq |
| `LibrarySelection` | 文库筛选方式 |
| `LibrarySource` | 文库来源 |
| `LibraryLayout` | SINGLE/PAIRED |
| `Platform` | 测序平台 |
| `Model` | 测序仪型号 |
| `CenterName` | 数据提交中心 |
| `size_MB` | 数据大小 |
| `spots` | spot 数 |
| `bases` | 碱基数 |

元数据会写入样本侧文件：

```text
<download_dir>/<accession>/metadata.json
```

该元数据也用于样本分组和物种/参考资源一致性检查。

## 4. SRA 转 FASTQ

SRA 转 FASTQ 不是远程下载步骤，但属于外部数据准备流程。若输入为 `.sra` 文件，系统调用 SRA Toolkit 的 `fasterq-dump` 转换为 FASTQ。

对应模块：

```text
workflow/rnaseq_workflow/steps/data_ingestion/sra_to_fastq.py
```

命令模板：

```bash
fasterq-dump <sample.sra> \
  --outdir <task_dir>/samples/<sample_id>/raw_fastq \
  --threads <threads> \
  --split-files \
  --temp <task_dir>/samples/<sample_id>/raw_fastq/_fasterq_tmp
```

输出文件：

```text
samples/<sample_id>/raw_fastq/<sample_id>_1.fastq
samples/<sample_id>/raw_fastq/<sample_id>_2.fastq
```

如果用户直接提供本地 FASTQ 文件，则系统跳过 SRA 下载和转换步骤。

## 5. 参考基因组与注释文件来源

系统支持两类参考资源来源：

1. 从 Ensembl/Ensembl Genomes 自动解析并下载；
2. 用户提供自定义 FASTA/GTF/GFF URL 或本地文件后登记。

参考资源会统一登记为 `ReferenceAsset`，并记录 `reference.json` 元数据。

对应模块：

```text
workflow/rnaseq_workflow/core/reference_sources.py
workflow/rnaseq_workflow/core/references.py
```

### 5.1 Ensembl 与 Ensembl Genomes

系统内置的 Ensembl 基础地址：

| division | 基础地址 |
|---|---|
| `vertebrates` | `https://ftp.ensembl.org/pub` |
| `plants` | `https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants` |
| `fungi` | `https://ftp.ensemblgenomes.ebi.ac.uk/pub/fungi` |
| `metazoa` | `https://ftp.ensemblgenomes.ebi.ac.uk/pub/metazoa` |
| `protists` | `https://ftp.ensemblgenomes.ebi.ac.uk/pub/protists` |

系统根据物种名、division 和 release 构造目录：

```text
<base>/<release>/fasta/<species_id>/dna/
<base>/<release>/gtf/<species_id>/
```

例如植物当前版本：

```text
https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/current/fasta/<species_id>/dna/
https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/current/gtf/<species_id>/
```

FASTA 选择规则：

1. 优先选择 `.dna.primary_assembly.fa.gz`；
2. 如果不存在，则选择 `.dna.toplevel.fa.gz`；
3. 也可指定其他 `fasta_kind`。

GTF 选择规则：

1. 选择 `.gtf.gz`；
2. 排除 `abinitio`；
3. 排除 `chr.gtf.gz`。

### 5.2 自定义 URL 参考资源

系统也支持用户直接提供 FASTA 和注释文件 URL：

```text
fasta_url
annotation_url
```

下载后系统会：

1. 保存压缩文件；
2. 如为 `.gz`，自动解压；
3. 将 FASTA 和 GTF/GFF 登记到参考资源目录；
4. 可选调用 `hisat2-build` 构建 HISAT2 索引；
5. 在 `reference.json` 中记录 `source_urls`。

对应函数：

```text
prepare_reference_from_urls()
build_reference_download_plan()
```

下载方式：

```text
urllib.request.urlopen(<url>)
```

参考资源登记后结构示例：

```text
references/<reference_id>/
  genome.fa
  annotation.gtf
  hisat2/
    genome.*
  reference.json
```

### 5.3 已登记参考资源示例

大豆 Ensembl Genomes 示例：

```text
https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/current/fasta/glycine_max/dna/Glycine_max.Glycine_max_v2.1.dna.toplevel.fa.gz
https://ftp.ensemblgenomes.ebi.ac.uk/pub/plants/current/gtf/glycine_max/Glycine_max.Glycine_max_v2.1.62.gtf.gz
```

SARS-CoV-2 RefSeq 示例：

```text
https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/009/858/895/GCF_009858895.2_ASM985889v3/GCF_009858895.2_ASM985889v3_genomic.fna.gz
https://ftp.ncbi.nlm.nih.gov/genomes/all/GCF/009/858/895/GCF_009858895.2_ASM985889v3/GCF_009858895.2_ASM985889v3_genomic.gff.gz
```

这些来源会记录在对应参考资源的：

```text
references/<reference_id>/reference.json
```

其中关键字段包括：

| 字段 | 说明 |
|---|---|
| `provider` | FASTA 来源，如 `ensembl`、`refseq`、`custom` |
| `annotation_provider` | 注释文件来源 |
| `source_urls` | FASTA 和注释文件原始 URL |
| `annotation_format` | `gtf`、`gff` 或 `gff3` |
| `created_by` | `download` 或 `manual` |
| `build_status` | HISAT2 索引构建状态 |

## 6. 本地输入来源

系统支持用户提供本地文件作为输入。这类数据不从外部数据库下载，但会作为样本或参考资源进入工作流。

### 6.1 本地 SRA 文件

用户可提供已有 `.sra` 文件。系统会扫描并将其作为样本输入，然后执行 `fasterq-dump` 转换。

```text
local .sra -> fasterq-dump -> FASTQ
```

### 6.2 本地 FASTQ 文件

用户可直接提供 `.fastq`、`.fq`、`.fastq.gz` 或 `.fq.gz` 文件。系统会识别单端/双端样本，并直接进入 FastQC 质控步骤。

```text
local FASTQ -> FastQC -> Trim Galore -> downstream analysis
```

### 6.3 本地参考资源

用户可登记已有 FASTA、GTF/GFF 和 HISAT2 index。系统不会重新下载参考资源，只会检查文件是否存在、注释格式是否可用、HISAT2 index 是否完整。

## 7. 下载清单来源

系统支持通过清单批量指定远程 accession。清单本身不是外部数据库来源，而是用户提供的下载任务列表。

对应模块：

```text
workflow/rnaseq_workflow/steps/download/manifest.py
workflow/rnaseq_workflow/steps/download/smart.py
```

支持格式：

| 格式 | 说明 |
|---|---|
| 直接输入单个 accession | 如 `SRR11047173` |
| 多个 accession | 支持空格、英文逗号、英文分号分隔 |
| TXT | 每行一个 accession |
| CSV | 支持 `accession`、`source`、`output_dir`、`expected_size_bytes` |
| JSON | 支持字符串数组或对象数组 |

模板目录：

```text
templates/download_manifests/
```

## 8. 工具镜像与软件包来源

工具镜像和软件包不属于生物学样本数据，但属于系统部署时会访问的外部资源。

### 8.1 Docker 镜像

本文系统主要镜像：

```text
rnaseq-workflow:tools
rnaseq-workflow:sra-tools
```

这些镜像在本地构建，Dockerfile 位于：

```text
docker/Dockerfile.tools
docker/Dockerfile.sra-tools
```

nf-core 对比实验中还使用了 nf-core/biocontainers 相关镜像，例如：

```text
nfcore-dood-runner:25.10.2
community.wave.seqera.io/library/*
quay.io/biocontainers/*
nextflow/nextflow:25.10.2
```

### 8.2 APT 与 PyPI 源

Dockerfile 中使用 Ubuntu APT 安装工具。默认 APT 来源为 Ubuntu 官方源，也可通过构建参数替换为镜像源：

```text
APT_MIRROR
```

代理与镜像源示例文件：

```text
docker/proxy.env.example
```

其中可配置：

```text
HTTP_PROXY
HTTPS_PROXY
APT_MIRROR
PIP_INDEX_URL
```

## 9. 数据来源与处理关系总结

| 数据类别 | 外部来源 | 系统处理方式 | 主要产物 |
|---|---|---|---|
| FASTQ 样本数据 | ENA Portal API + ENA FASTQ URL | 查询 FASTQ 链接、下载、MD5 校验 | `.fastq.gz` |
| SRA 样本数据 | NCBI SRA, via SRA Toolkit `prefetch` | 下载 `.sra`、`vdb-validate` 校验、必要时转换 FASTQ | `.sra`, `.fastq` |
| 样本元数据 | NCBI SRA RunInfo | 查询 BioProject、BioSample、TaxID、物种、文库信息 | `metadata.json` |
| 参考基因组 FASTA | Ensembl/Ensembl Genomes 或用户 URL | 下载、解压、登记 | `genome.fa` |
| 注释文件 GTF/GFF | Ensembl/Ensembl Genomes、NCBI RefSeq 或用户 URL | 下载、解压、登记 | `annotation.gtf`/`annotation.gff` |
| HISAT2 index | 本地构建 | 由 FASTA 通过 `hisat2-build` 构建 | `hisat2/genome.*.ht2` |
| 本地 SRA/FASTQ | 用户提供 | 扫描、登记、复用 | 样本输入文件 |
| 本地参考资源 | 用户提供 | 登记、检查完整性 | `reference.json` |
| 工具镜像/软件包 | Docker registry、Ubuntu APT、PyPI 等 | 构建或拉取运行环境 | Docker image |

## 10. 论文可用表述

可在论文中写为：

> 系统支持多种外部数据来源。对于公共测序数据，系统可根据 SRR/ERR/DRR accession 自动查询 ENA Portal API 获取 FASTQ 下载链接；若 ENA FASTQ 链接不可用，则调用 SRA Toolkit 的 prefetch 从 NCBI SRA 下载 SRA 文件，并通过 fasterq-dump 转换为 FASTQ。系统同时调用 NCBI SRA RunInfo 接口获取 BioProject、BioSample、物种名称、TaxID、文库类型和数据大小等元数据。对于参考资源，系统支持从 Ensembl/Ensembl Genomes 下载参考基因组 FASTA 和 GTF 注释文件，也支持用户提供自定义 URL 或本地 FASTA/GTF/GFF 文件。所有参考资源均登记为 ReferenceAsset，并在 reference.json 中记录来源 URL、物种、版本、注释格式和 HISAT2 索引状态。

也可补充：

> 当用户提供本地 SRA、FASTQ 或参考资源文件时，系统不会重新访问公共数据库，而是直接扫描、登记并复用本地文件，从而支持离线或半离线分析场景。
