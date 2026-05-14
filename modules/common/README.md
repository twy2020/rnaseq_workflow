# common 模块

公共基础模块，负责配置解析、路径规范、日志、命令执行、状态模型和异常类型。

## 输入

- YAML/JSON 配置文件
- 项目运行目录
- 模块运行上下文

## 输出

- 标准化配置对象
- 日志文件
- 命令执行结果
- 状态记录对象

## 测试重点

- 配置默认值和必填项校验
- 路径规范化
- 命令执行结果封装
- 日志输出格式

## 当前实现状态

已实现第一阶段公共能力：

- `workflow/rnaseq_workflow/core/config.py`：YAML 配置加载与基础校验
- `workflow/rnaseq_workflow/core/models.py`：样本、运行上下文、步骤状态与结果模型
- `workflow/rnaseq_workflow/core/command.py`：结构化命令执行与 dry-run
- `workflow/rnaseq_workflow/core/paths.py`：项目和样本标准路径生成
- `workflow/rnaseq_workflow/core/errors.py`：公共异常类型
- `workflow/rnaseq_workflow/core/logging.py`：Rich 终端日志与文件日志

对应测试：

- `test/common/test_config.py`
- `test/common/test_paths.py`
- `test/common/test_command.py`
- `test/common/test_models.py`

## 当前 CLI 入口

生成项目配置模板：

```powershell
python -m rnaseq_workflow.cli.main init-config config.yaml --project-id rnaseq_demo
```

检查 CLI 环境：

```powershell
python -m rnaseq_workflow.cli.main doctor
```

查看解析后的配置：

```powershell
python -m rnaseq_workflow.cli.main config-show config.yaml
```

修改配置参数：

```powershell
python -m rnaseq_workflow.cli.main config-set config.yaml hisat2_threads 8
python -m rnaseq_workflow.cli.main config-set config.yaml samples.0.source_path data/S1.fastq.gz
```

查看执行计划：

```powershell
python -m rnaseq_workflow.cli.main plan config.yaml
```

校验配置：

```powershell
python -m rnaseq_workflow.cli.main validate-config config.yaml
```

执行样本级 pipeline 并收尾：

```powershell
python -m rnaseq_workflow.cli.main run config.yaml --no-dry-run --finalize
python -m rnaseq_workflow.cli.main run config.yaml --no-dry-run --no-progress
```

当前 CLI UI 已统一主流程输出：

- `plan`：项目概览、步骤展开、样本列表。
- `validate-config`：配置校验结果。
- `run`：运行起始信息、实时进度条和运行摘要。
- `finalize`：项目级矩阵和报告产物。
- `download-batch`：批量下载结果摘要和 accession 状态表。
- `download-batch --no-progress`：禁用实时下载进度，适合日志和 CI。
- `merge-counts`：raw counts 矩阵合并摘要。
- `report-summary`：报告摘要输出。
