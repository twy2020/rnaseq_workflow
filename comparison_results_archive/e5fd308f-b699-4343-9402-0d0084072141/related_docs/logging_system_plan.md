# 日志系统完善计划

## 1. 背景与目标

当前系统已经具备 `progress.json` 状态记录、`StepResult.extra` 命令输出记录、`.done.json` 完成标记、`.error.txt` 错误标记、`artifact_locations.json` 跨盘产物记录和 TUI 最近输出缓存。这些机制能够支撑恢复运行和基础排错，但日志分散在不同位置，缺少统一的任务级事件流、命令审计日志和样本步骤详细日志。

日志系统完善的目标是建立一套分层、可追踪、可恢复、可用于论文说明的日志体系：

- 运行中可观察：TUI 能快速定位当前任务、样本、阶段和错误原因。
- 运行后可追溯：能够复盘每一步执行命令、输入、输出、返回码和耗时。
- 故障时可定位：下载失败、Docker 失败、磁盘不足、进度文件损坏、工具异常退出等场景有完整证据链。
- 清理时可控制：日志与大产物分离，支持保留失败日志、压缩历史日志和导出日志包。

## 2. 日志分层设计

日志分为五层，避免所有信息混入单一文件。

```text
logs/
  events.jsonl
  commands.jsonl
  tui.log
  resource.jsonl
  downloads.jsonl
  samples/
    <sample_id>/
      <step_id>.log
  archive/
```

### 2.1 状态日志：progress.json

`progress.json` 继续作为状态恢复的核心文件，记录每个样本每个步骤的状态、输入、输出、命令、返回码和简要消息。它不是普通日志文件，而是运行恢复和跳过已完成步骤的状态源。

后续增强项：

- 在每个步骤记录中加入 `log_file` 字段，指向样本步骤详细日志。
- 在 `extra` 中加入 `command_id` 和 `command_log_file`，用于关联命令审计日志。

### 2.2 事件日志：events.jsonl

`events.jsonl` 保存任务生命周期和调度事件，每行一个 JSON 对象，便于追加写入和后期解析。

示例：

```json
{
  "time": "2026-05-15T21:10:03",
  "level": "INFO",
  "event": "step_started",
  "task_id": "task-1",
  "user_id": "user-1",
  "sample_id": "SRR11047173",
  "step_id": "hisat2",
  "message": "step started"
}
```

建议事件类型：

```text
task_created
task_selected
manifest_submitted
sample_metadata_updated
reference_selected
reference_prepared
params_saved
resource_check_completed
workflow_started
step_started
step_completed
step_failed
step_skipped
step_cancelled
workflow_cancelled
workflow_completed
finalize_started
finalize_completed
artifact_moved
artifact_cleaned
disk_guard_triggered
state_recovered
```

### 2.3 命令审计日志：commands.jsonl

`commands.jsonl` 记录每次外部命令调用，不保存完整 stdout/stderr，只保存摘要和详细日志路径。

示例：

```json
{
  "time": "2026-05-15T21:12:31",
  "task_id": "task-1",
  "sample_id": "SRR11047173",
  "step_id": "samtools_sort",
  "command_id": "cmd-20260515211231-0001",
  "execution_mode": "docker",
  "command": ["samtools", "sort", "-@", "2", "-o", "..."],
  "return_code": 0,
  "duration_seconds": 132.4,
  "stdout_log": "logs/samples/SRR11047173/samtools_sort.log",
  "stderr_log": "logs/samples/SRR11047173/samtools_sort.log",
  "status": "COMPLETED"
}
```

### 2.4 样本步骤日志：samples/<sample_id>/<step_id>.log

样本步骤日志面向人工排错，保存单个样本单个步骤的完整上下文。

建议格式：

```text
[meta]
task_id=...
sample_id=SRR11047173
step_id=hisat2
started_at=...
finished_at=...
duration_seconds=...
return_code=...

[inputs]
...

[outputs]
...

[command]
hisat2 ...

[stdout]
...

[stderr]
...
```

原则：

- 成功步骤可保留完整日志，也可后续压缩。
- 失败步骤必须保留完整 stdout/stderr。
- `.error.txt` 只保留短错误摘要，完整错误进入步骤日志。

### 2.5 TUI 交互日志：tui.log

`tui.log` 记录用户在终端界面中的关键操作，例如创建任务、选择 Reference、提交清单、保存参数、开始运行、取消运行、清理产物等。该日志用于解释“用户做了什么”，不替代命令日志。

## 3. 专项日志

### 3.1 资源日志：resource.jsonl

记录 CPU、内存、工作盘和备用盘使用情况。建议采样间隔为 5 到 10 秒，避免日志过大。

示例：

```json
{
  "time": "2026-05-15T21:15:00",
  "cpu_percent": 48.2,
  "memory_percent": 32.1,
  "work_disk_percent": 78.3,
  "work_disk_free_bytes": 105000000000,
  "spill_disks": [
    {
      "path": "H:\\rnaseq",
      "percent": 30.1,
      "free_bytes": 500000000000
    }
  ],
  "warning_level": "ok"
}
```

资源守护触发时，还应写入 `events.jsonl`：

```text
disk_guard_triggered
artifact_moved
workflow_cancelled
```

### 3.2 下载日志：downloads.jsonl

下载阶段单独记录，便于分析掉速、重连、校验失败和重试问题。

示例：

```json
{
  "time": "2026-05-15T21:20:00",
  "accession": "SRR11047173",
  "source": "ncbi",
  "status": "RUNNING",
  "downloaded_bytes": 123456789,
  "expected_bytes": 1987654321,
  "speed_bytes_per_sec": 1048576,
  "attempt": 1,
  "message": "downloading"
}
```

## 4. 日志等级与脱敏规则

### 4.1 日志等级

```text
DEBUG    路径重写、缓存命中、跳过原因等调试细节
INFO     正常生命周期事件
WARNING  可恢复异常，例如元数据获取失败、下载重试、磁盘接近阈值
ERROR    步骤失败、校验失败、命令非零退出
CRITICAL 任务级中止，例如磁盘耗尽、进度文件不可恢复、容器无法终止
```

### 4.2 脱敏规则

日志中应避免泄漏以下内容：

- 用户密码；
- 带用户名密码的代理 URL；
- token、cookie、authorization header；
- 不必要的长 stdout/stderr 重复内容。

代理 URL 脱敏示例：

```text
http://user:pass@host:port -> http://***:***@host:port
```

TUI 中可使用工作目录编号缩短长路径，但日志文件应保留真实路径，便于复现和排错。

## 5. 与现有机制的关系

| 现有机制 | 保留方式 | 增强方向 |
|---|---|---|
| `progress.json` | 继续作为状态恢复源 | 增加日志文件引用 |
| `StepResult.extra.stdout/stderr` | 保留摘要或兼容字段 | 完整输出写入样本步骤日志 |
| `.done.json` | 继续用于跳过已完成步骤 | 写入关联 command_id |
| `.error.txt` | 继续用于快速查看错误 | 仅保存短摘要，完整错误在 `.log` |
| `artifact_locations.json` | 继续记录跨盘路径 | 产物移动和清理写入事件日志 |
| TUI `output_log` | 继续作为最近输出缓存 | 关键操作写入 `tui.log` |

## 6. 实现阶段

### 阶段一：日志管理器

新增 `TaskLogManager`，负责：

- 创建任务日志目录；
- 写入 `events.jsonl`；
- 写入 `commands.jsonl`；
- 写入样本步骤日志；
- 提供脱敏和路径规范化工具；
- 保证多线程写入安全。

建议接口：

```python
class TaskLogManager:
    def event(...)
    def command(...)
    def sample_step_log(...)
    def resource(...)
    def download(...)
```

验收标准：

- 新建任务后能创建 `logs/` 目录；
- 可并发写入 JSONL 且文件内容合法；
- 单元测试覆盖 JSONL 写入和脱敏规则。

### 阶段二：命令日志接入

接入位置：

- `run_context_command`
- 或各 Step 在收到 `CommandResult` 后调用日志管理器。

建议优先在 Step 层接入，因为 Step 层能提供 `sample_id` 和 `step_id`。

验收标准：

- HISAT2、samtools、featureCounts、StringTie 等步骤运行后生成对应样本步骤日志；
- `commands.jsonl` 能记录 return_code、duration 和日志路径；
- `progress.json` 中能找到对应日志文件引用。

### 阶段三：Pipeline 与 TUI 事件接入

接入事件：

- workflow started/completed/cancelled；
- step started/completed/failed/skipped/cancelled；
- 参数保存；
- 样本清单提交；
- Reference 选择或准备；
- 产物清理。

验收标准：

- 正式运行一次任务后，`events.jsonl` 能完整串起任务生命周期；
- TUI 取消运行后有 `workflow_cancelled` 和相关 step cancelled 事件；
- 产物清理后有清理类别、路径和释放空间记录。

### 阶段四：资源与下载日志接入

接入位置：

- `_RuntimeResourceGuard` 写 `resource.jsonl`；
- 下载模块或 TUI 下载进度回调写 `downloads.jsonl`。

验收标准：

- 运行过程中按固定间隔记录资源快照；
- 磁盘策略触发时写入 `disk_guard_triggered`；
- 下载进度可在 `downloads.jsonl` 中重建速度曲线和失败原因。

### 阶段五：日志查看、导出与清理

TUI 增加入口：

```text
日志中心
  查看任务事件
  查看失败步骤日志
  查看命令审计
  查看下载日志
  导出日志包
  清理成功步骤详细日志
```

验收标准：

- 用户可从 TUI 直接查看最近失败步骤；
- 可导出 zip/tar.gz 日志包；
- 清理成功步骤日志不会删除失败日志和事件日志。

## 7. 保留与清理策略

建议默认策略：

| 日志类型 | 默认保留 |
|---|---|
| `events.jsonl` | 永久保留 |
| `commands.jsonl` | 永久保留 |
| `resource.jsonl` | 保留最近一次运行或压缩历史 |
| `downloads.jsonl` | 保留完整记录 |
| 失败步骤 `.log` | 永久保留 |
| 成功步骤 `.log` | 可压缩或按策略清理 |
| `tui.log` | 保留，可按大小轮转 |

## 8. 论文表述要点

论文中可将日志系统描述为“状态日志、事件日志和命令日志相结合”的可追踪机制：

- `progress.json` 解决恢复运行；
- `events.jsonl` 解决任务生命周期追踪；
- `commands.jsonl` 和样本步骤日志解决命令审计和错误定位；
- `resource.jsonl` 和 `downloads.jsonl` 支撑资源预警与下载问题分析；
- 产物清理与跨盘路径记录保证日志追踪不因路径迁移而断链。

该设计能够体现系统在长流程、高 I/O、生信工具链复杂和易中断场景下的可靠性与可维护性。

## 9. 当前实现状态

截至当前开发版本，日志系统主干已经落地：

- 已新增 `TaskLogManager`，统一负责 `events.jsonl`、`commands.jsonl`、`resource.jsonl`、`downloads.jsonl`、`tui.log` 和样本步骤日志写入。
- `progress.json` 的步骤记录已加入 `log_file`，`extra` 中已加入 `command_id`、`command_ids`、`command_log_file` 和 `command_results`。
- Pipeline 已接入 `step_started`、`step_completed`、`step_failed`、`step_skipped`、`step_cancelled`。
- 命令执行层已支持一个步骤内多条外部命令的收集与审计，例如 `samtools sort` 和 `samtools index` 会分别写入 `commands.jsonl`。
- `.done.json` 已回填 `log_file`、`command_id`、`command_ids` 和 `command_log_file`，用于从完成标记追溯命令审计。
- TUI 已记录任务创建、任务选择、清单提交、Reference 选择/清除、参数保存、资源检查、产物清理、跨盘迁移、磁盘守护触发、日志导出和成功日志压缩等事件。
- `_RuntimeResourceGuard` 已按节流策略写入 `resource.jsonl`。
- 下载进度回调已写入 `downloads.jsonl`。
- TUI 已新增“日志中心”，支持查看任务事件、命令审计、下载日志、失败步骤日志、导出日志包和压缩成功步骤日志。

## 10. 收尾策略

成功步骤详细日志可通过日志中心压缩到：

```text
logs/archive/<task_id>_success_step_logs_<timestamp>.zip
```

压缩后会删除原成功步骤 `.log`，但保留：

- 失败步骤 `.log`；
- `events.jsonl`；
- `commands.jsonl`；
- `downloads.jsonl`；
- `resource.jsonl`；
- `tui.log`；
- `progress.json`；
- `.done.json` 和 `.error.txt`。

完整日志包可通过日志中心导出到：

```text
logs/archive/<task_id>_logs_<timestamp>.zip
```

日志包包含任务日志目录、`progress.json` 和关键 metadata 文件，可用于故障复盘、论文展示和问题提交。
