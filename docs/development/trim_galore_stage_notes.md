# Trim Galore 阶段记录

## 目标

将 read trimming 阶段从 dry-run 推进到真实数据可运行，并接入 TUI。

当前模块使用 Trim Galore + Cutadapt，对下载或转换得到的 FASTQ 进行接头和低质量序列修剪。

## 当前链路

```text
下载
-> SRA 转 FASTQ，必要时
-> FastQC 原始质控
-> Trim Galore 修剪
-> FastQC 修剪后质控，下一步
```

## TUI 行为

入口：

```text
Trim Galore 修剪
```

默认值：

```text
输入目录: downloads
输出目录: runtime_logs/trim_test
执行模式: docker
镜像: rnaseq-workflow:tools
quality: 20
phred: 33
stringency: 3
gzip: true
样本并发: 6，可运行前修改
```

运行页显示：

```text
input=输入文件总大小
output=当前输出大小
idle=RUNNING 样本距离最近写入的秒数
last=最近更新文件
```

`SKIPPED` 和 `COMPLETED` 样本不应再显示 idle。

## 稳固措施

已实现：

- 样本级 `.lock`
- 成功后 `.done.json`
- 重跑跳过已完成样本
- 按 `c` 取消
- 取消后终止正在运行的 Docker/local 命令
- 未开始样本标记 `CANCELLED`
- 失败/取消时清理半成品
- 失败时保留 `.error.txt`
- 若 trimmed FASTQ 和 trimming report 齐全，可恢复 `.done.json` 并释放旧锁

## 已修 bug

### TUI 外层一直 RUNNING

现象：

```text
Trim Galore 实际输出已齐全
Docker 容器已经结束
TUI 仍显示 RUNNING
.lock 未释放
.done.json 未写入
```

原因：

`run_command()` 使用 `Popen(..., stdout=PIPE, stderr=PIPE)`，但进程运行期间没有持续读取 stdout/stderr。Trim Galore 输出较多时可能阻塞外层进程。

修复：

`run_command()` 增加 stdout/stderr reader 线程，持续 drain 输出，避免 PIPE 死锁。

### 完整输出未写 done

现象：

trimmed FASTQ 和 trimming report 已存在，但 `.done.json` 缺失。

修复：

`TrimGaloreStep.run()` 在重跑时检查输出完整性：

```text
paired: 至少 2 个 trimmed FASTQ + 2 个 trimming_report
single: 至少 1 个 trimmed FASTQ + 1 个 trimming_report
```

若齐全，则恢复为 `COMPLETED`、写 `.done.json`、释放 `.lock`。

## 当前真实数据状态

`runtime_logs/trim_test` 当前完成状态：

```text
SRR11047173  done=True  lock=False
SRR19820386  done=True  lock=False
SRR19820387  done=True  lock=False
SRR1982039   done=True  lock=False
SRR19820396  done=True  lock=False
SRR19820397  done=True  lock=False
```

## 注意事项

- `SRR11047173` 的 FASTQ 当前位于 `downloads/SRR000001/` 下，目录归属异常，后续需要整理下载/转换输出路径。
- 大样本 paired-end 修剪耗时较长。真实批量运行建议先用较小并发，例如样本并发 `2-3`，`Trim Galore cores=1-2`。
- Trim Galore 结束后，下一步应对 `runtime_logs/trim_test` 下的 trimmed FASTQ 再运行 FastQC。

## 相关测试

```text
test/common/test_command.py
test/read_trimming/test_trim_galore.py
test/common/test_tui.py
```
