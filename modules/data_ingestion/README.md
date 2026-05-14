# data_ingestion 模块

输入接入模块，负责扫描本地 `.sra`、FASTQ 文件，识别样本 ID 和单端/双端布局，并执行 SRA 到 FASTQ 的标准化转换。

## 输入

- 已下载的 SRA 文件目录
- FASTQ 文件目录
- 样本清单

## 输出

- 样本对象列表
- 原始 FASTQ 文件

## 外部工具

- `fasterq-dump` 或 `fastq-dump`

## 测试重点

- SRA 文件扫描
- 样本 ID 提取
- 单端/双端 FASTQ 识别
- FASTQ 输出命名规则

## 当前实现状态

已实现第一阶段纯 Python 输入扫描能力：

- `workflow/rnaseq_workflow/steps/data_ingestion/scanner.py`
  - 递归扫描 `.sra`
  - 递归扫描 `.fastq`、`.fq`、`.fastq.gz`、`.fq.gz`
  - 根据文件名推断 `sample_id`
  - 根据 R1/R2、1/2 标记推断 single/paired/unknown
- `workflow/rnaseq_workflow/steps/data_ingestion/manifest.py`
  - 输出 JSON 样本清单
  - 输出 CSV 样本清单

该阶段不调用外部生信工具，因此暂不需要 Docker。进入 SRA 转 FASTQ、FastQC 等真实工具步骤时再补 Docker 镜像并做工具级测试。

已实现第二阶段 SRA 转 FASTQ 命令封装：

- `workflow/rnaseq_workflow/steps/data_ingestion/sra_to_fastq.py`
  - `build_fasterq_dump_command`
  - `SraToFastqOptions`
  - `SraToFastqStep`

工具镜像：

- `docker/Dockerfile.sra-tools`
- `scripts/build_sra_tools_image.ps1`
- `docker/proxy.env.example`

该阶段的 Python 测试使用 dry-run，不依赖真实 SRA 文件。真实工具可用性通过 Docker 中的 `fasterq-dump --version` 验证。

CLI 局部模块测试：

```powershell
$env:PYTHONPATH='workflow'
python -m rnaseq_workflow.cli.main sra-to-fastq data\SRR_DEMO_1.sra runtime_logs\sra_to_fastq_cli_demo --sample-id SRR_DEMO_1 --threads 2 --dry-run --result-json runtime_logs\sra_to_fastq_cli_demo\result.json
```

真实执行时使用：

```powershell
python -m rnaseq_workflow.cli.main sra-to-fastq path\to\sample.sra output_dir --no-dry-run
```
