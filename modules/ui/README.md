# ui 模块

交互模块，负责 CLI/TUI 进度展示和运行状态查看。

## 输入

- `progress.json`
- 运行日志

## 输出

- 终端进度表
- 错误提示
- 运行摘要

## 测试重点

- 进度文件读取
- 任务状态展示
- 刷新逻辑
- 用户退出行为
## 增强终端 UI

推荐入口：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_ui.ps1
```

这个脚本会先检查 Docker、启动 `rnaseq-workflow-tools` 容器、运行工具烟雾测试，然后打开 TUI。

如果只想打开界面，不检查容器，也可以直接运行：

```powershell
$env:PYTHONPATH='workflow'
python -m rnaseq_workflow.cli.main ui
```

也可以显式打开新版 TUI：

```powershell
python -m rnaseq_workflow.cli.main tui
```

新版终端 UI 基于 `prompt_toolkit`，支持：

- 方向键选择菜单
- Enter 确认
- Cancel/返回上一级
- 彩色对话框、输入框、确认框
- 配置、reference、下载、扫描输入、运行 workflow 的集中入口
- SRA 转 FASTQ 单模块真实运行入口
- FastQC 单模块真实运行入口
- Trim Galore 单模块真实运行入口
- 操作结果会在界面内弹窗显示
- 主菜单提供“查看最近输出”，用于回看上一条命令/操作输出

下载入口分两层：

- `下载 SRA`：普通入口，只需要输入目标，例如 `SRR11047173`；也可以在同一行输入多个目标，例如 `SRR11047173 SRR000001`、`SRR11047173,SRR000001`、`SRR11047173;SRR000001`。默认输出到 `downloads`，默认 Docker，默认 `max-size=5G`，默认实时总并发 `6`。
- `高级下载设置`：需要修改输出目录、max-size、force、并发、Docker 镜像等参数时使用，`清单并发数` 默认 `6`。

下载目标输入框是单行输入框，不需要也不建议换行。少量目标用空格、英文逗号或英文分号分隔；目标很多时，把目标写进 `templates/download_manifests` 下的 TXT/CSV/JSON 清单，然后在输入框里填写清单路径。

下载运行期间会显示进度页：

- 总任务进度
- 每个 accession 的状态
- 文本进度条
- 自动查询 SRA RunInfo 获取总大小时显示真实百分比
- 已下载大小
- 当前速度
- 用时
- 按 `c` 取消下载，半成品会保留用于续传
- 下载结束后按 `q` 返回主菜单

SRA 转 FASTQ 入口：

- 主菜单选择 `SRA 转 FASTQ`
- 默认扫描 `downloads`
- 只展示 `.sra` 样本
- 支持选择单个样本或全部 SRA 样本
- 默认 Docker 镜像：`rnaseq-workflow:tools`
- 默认使用 `fasterq-dump --split-files`
- 样本并发数默认 `6`，运行前会询问，可按机器资源调低或调高
- 默认输出目录：`runtime_logs/sra_to_fastq`
- 输出位置形如 `runtime_logs/sra_to_fastq/samples/{sample_id}/raw_fastq`
- 转换后的 FASTQ 可以继续用 `FastQC 质控` 扫描运行

FastQC 入口：

- 主菜单选择 `FastQC 质控`
- 默认扫描 `downloads`
- 只展示 FASTQ 样本，自动跳过 `.sra`
- 支持选择单个样本或全部 FASTQ 样本
- 默认 Docker 镜像：`rnaseq-workflow:tools`
- 默认输出目录：`runtime_logs/fastqc_test`
- 样本并发数默认 `6`，运行前会询问，可按机器资源调低或调高
- 运行期间显示样本状态页，完成后按 `q` 返回
- 输出位置形如 `runtime_logs/fastqc_test/samples/{sample_id}/qc_raw`

如果下载结果是 `.sra`，需要先运行 `SRA 转 FASTQ`，再对转换后的 FASTQ 运行 `FastQC 质控`。

Trim Galore 入口：

- 主菜单选择 `Trim Galore 修剪`
- 默认扫描 `downloads`
- 只展示 FASTQ 样本，自动跳过 `.sra`
- 支持选择单个样本或全部 FASTQ 样本
- 默认 Docker 镜像：`rnaseq-workflow:tools`
- 默认 `quality=20`、`phred33`、`stringency=3`、gzip 输出
- 默认输出目录：`runtime_logs/trim_test`
- 样本并发数默认 `6`，运行前会询问
- 输出位置形如 `runtime_logs/trim_test/samples/{sample_id}/trimmed_fastq`
- 修剪完成后建议对 trimmed FASTQ 再运行一次 `FastQC 质控`

并发说明：

- TUI 默认并发常量在 `workflow/rnaseq_workflow/cli/tui.py` 的 `DEFAULT_TUI_CONCURRENCY`，当前值为 `6`
- 下载、SRA 转 FASTQ、FastQC 都按“实时总并发”运行：完成一个任务后会立即补上下一个，不会等待同一批全部结束
- 临时修改：在 TUI 的 `高级下载设置`、`SRA 转 FASTQ`、`FastQC 质控` 中按提示输入并发数
- 代码默认值修改：调整 `DEFAULT_TUI_CONCURRENCY`
- CLI 下载并发可用 `--max-workers`，例如 `python -m rnaseq_workflow.cli.main download "SRR1,SRR2" --max-workers 6`

中断与重跑：

- `SRA 转 FASTQ` 和 `FastQC 质控` 运行页支持按 `c` 取消
- 取消后正在运行的 Docker/local 命令会被终止，未开始的样本标记为 `CANCELLED`
- 每个样本输出目录会创建 `.lock`，防止同一个样本被重复写入
- 真实运行成功后写入 `.done.json`
- 重跑时如果发现 `.done.json`，会跳过该样本
- 失败或取消时默认清理当前样本输出目录，避免半成品进入后续分析
- `Trim Galore` 在清理前会检查 trimmed FASTQ 和 trimming report 是否齐全；如果齐全，会恢复为完成并写 `.done.json`
- dry-run 不写 `.done.json`

如果终端不是交互式 TTY，例如管道执行或测试环境，`ui` 会自动退回简单行菜单。

旧版行菜单仍然保留：

```powershell
python -m rnaseq_workflow.cli.main simple-ui
```
