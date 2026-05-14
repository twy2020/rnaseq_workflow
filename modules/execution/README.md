# execution 模块

执行调度模块，负责流程步骤编排、并行执行、状态记录、断点续跑和失败处理。

## 输入

- 样本列表
- 流程步骤列表
- 运行配置

## 输出

- `progress.json`
- 每步状态记录
- 运行日志

## 测试重点

- 任务状态流转
- 已完成步骤跳过
- 失败步骤停止或重试
- 多进程并发写入安全

## 容器控制脚本

以下脚本均为无参数 PowerShell 脚本，直接运行即可。脚本会显示 Docker 状态、镜像/容器状态和工具烟雾测试结果。

```powershell
# 一键启动。镜像不存在时会先增量构建。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\container_start.ps1

# 查看状态和测试结果。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\container_status.ps1

# 停止常驻工具容器。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\container_stop.ps1

# 重启常驻工具容器。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\container_restart.ps1

# 增量重建镜像，复用 Docker cache，然后重建容器。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\container_incremental_rebuild.ps1

# 完全重建镜像，不使用 cache，并重建容器。
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\container_full_rebuild.ps1
```

默认资源：

- 镜像：`rnaseq-workflow:tools`
- 常驻容器：`rnaseq-workflow-tools`
- 工作目录挂载：项目根目录 -> `/workspace`

烟雾测试会检查：

- `python3`
- `prefetch`
- `fasterq-dump`
- `fastqc`
- `trim_galore`
- `hisat2`
- `samtools`
- `featureCounts`
