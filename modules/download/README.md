# download 模块

公共数据下载模块，负责根据 accession 或样本清单从公共数据库获取原始 RNA-seq 数据，并管理下载缓存、重试和完整性校验。

## 输入

- SRA/GEO/GSA/ENA accession
- 样本清单
- 下载目录
- 下载参数与网络配置

## 输出

- `.sra` 文件
- 下载日志
- 下载状态记录
- accession 与本地文件路径映射表

## 外部工具

- `prefetch`
- `fasterq-dump`，当下载后需要直接转换 FASTQ 时可与 `data_ingestion` 协作
- `wget` 或 `curl`，后续扩展

## 主要职责

- 根据 accession 批量下载数据
- 跳过已下载且校验通过的文件
- 下载失败后重试
- 记录数据库来源、accession、文件大小和下载时间
- 为断点续跑提供下载状态

## 测试重点

- accession 列表解析
- 下载命令构造
- 已存在文件跳过逻辑
- 下载失败状态记录
- 本地路径映射表生成

## 输入清单格式

支持三种格式。

### TXT

每行一个 accession，空行和 `#` 注释会跳过：

```text
# accessions.txt
SRR000001
SRR000002
```

### CSV

必填列：`accession`

可选列：`source`、`output_dir`、`expected_size_bytes`

```csv
accession,source,expected_size_bytes
SRR000001,sra,325788509
SRR000002,sra,
```

### JSON

简单数组：

```json
["SRR000001", "SRR000002"]
```

或对象数组：

```json
{
  "accessions": [
    {
      "accession": "SRR000001",
      "source": "sra",
      "expected_size_bytes": 325788509
    }
  ]
}
```

## 当前实现状态

已实现第一阶段 SRA 下载能力：

- `workflow/rnaseq_workflow/steps/download/models.py`
  - `DownloadRequest`
  - `DownloadProgress`
  - `DownloadResult`
  - `BatchDownloadSummary`
- `workflow/rnaseq_workflow/steps/download/cache.py`
  - 本地 `.sra` 缓存查找
  - 目录大小统计
- `workflow/rnaseq_workflow/steps/download/prefetch.py`
  - `prefetch` 命令构造
  - Docker/local 执行模式
  - accession 基础校验
  - dry-run
  - 缓存跳过
  - 轮询下载目录估算速度
- `workflow/rnaseq_workflow/steps/download/manager.py`
  - 并发下载
  - 查询单个 accession 进度
  - 查询批量总进度
- `workflow/rnaseq_workflow/steps/download/manifest.py`
  - TXT/CSV/JSON 输入清单读取
  - JSON/CSV 下载结果写出

CLI：

```powershell
$env:PYTHONPATH='workflow'
python -m rnaseq_workflow.cli.main download SRR000001
python -m rnaseq_workflow.cli.main download-accession SRR000001 runtime_logs\downloads --dry-run
python -m rnaseq_workflow.cli.main download-resume SRR000001 runtime_logs\downloads --dry-run
python -m rnaseq_workflow.cli.main download-batch accessions.txt runtime_logs\downloads --max-workers 2 --dry-run
```

推荐入口是 `download`，只需要给一个目标：

```powershell
python -m rnaseq_workflow.cli.main download SRR11047173
```

目标可以是：

- 单个 SRA run accession，例如 `SRR11047173`
- 多个 SRA run accession，写在同一行并用空格、英文逗号或英文分号分隔，例如 `"SRR11047173 SRR000001"`、`"SRR11047173,SRR000001"`
- TXT/CSV/JSON 下载清单路径

少量目标可以直接一行输入：

```powershell
python -m rnaseq_workflow.cli.main download "SRR11047173 SRR000001"
python -m rnaseq_workflow.cli.main download "SRR11047173,SRR000001"
python -m rnaseq_workflow.cli.main download "SRR11047173;SRR000001"
```

TUI 的 `下载 SRA` 输入框也是单行输入框，规则相同。不要在输入框里换行；目标很多时使用 `templates/download_manifests` 里的 TXT/CSV/JSON 清单。

默认行为：

- 输出目录：`downloads`
- 下载源：`auto`，优先 ENA FASTQ，找不到时回退 NCBI SRA `prefetch`
- NCBI SRA 执行模式：Docker
- Docker 镜像：`rnaseq-workflow:tools`
- `prefetch --max-size`: `5G`
- 保留半成品，支持重新运行后续传

可以强制指定来源：

```powershell
python -m rnaseq_workflow.cli.main download SRR11047173 --source ena
python -m rnaseq_workflow.cli.main download SRR11047173 --source sra
```

在 Windows/PowerShell 下默认用 Docker 执行 `prefetch`，避免宿主机单独安装 SRA Toolkit：

```powershell
python -m rnaseq_workflow.cli.main download-accession SRR11047173 downloads `
  --execution-mode docker `
  --docker-workspace . `
  --no-dry-run
```

注意：

- 运行前需要启动 Docker Desktop。
- `output_dir` 建议放在 `docker_workspace` 里面，例如 `--docker-workspace .` 时使用相对目录 `downloads`。
- 如果 Docker 没启动，命令会返回 FAILED 并显示 Docker daemon 连接错误，不会抛 traceback。

稳固措施：

- `--force`：传递给 `prefetch --force`
- `--clean-before-download`：下载前删除该 accession 的旧产物
- `--cleanup-on-fail / --no-cleanup-on-fail`：失败、取消或超时后是否清理本次 accession 产物
- `--resume-partial / --no-resume-partial`：是否保留半成品以便重新运行时继续下载
- `--retries`：失败重试次数
- `--retry-delay`：重试间隔秒数
- `--timeout-seconds`：单个 prefetch 超时时间
- 文件锁：每个 accession 使用 `downloads/.locks/{accession}.lock`
- 取消：`DownloadManager.cancel_all()` 和 `CancellationToken`
- 断点续传：默认保留半成品，再次运行 `download-accession` 或直接用 `download-resume`
- 续传前会清理 accession 目录内陈旧的 SRA Toolkit `.lock` 文件，但保留 `.tmp/.prf` 半成品
