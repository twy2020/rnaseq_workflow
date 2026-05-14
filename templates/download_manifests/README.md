# 下载清单模板

这个目录存放可直接复制使用的下载清单模板。普通下载入口支持：

- 单个 accession：直接输入 `SRR11047173`
- 少量多个 accession：在同一行输入，用空格、英文逗号或英文分号分隔
- TXT 清单
- CSV 清单
- JSON 清单

## 直接输入多个目标

CLI 和 TUI 的下载目标输入规则一致。少量目标不需要写清单，放在同一行即可：

```powershell
python -m rnaseq_workflow.cli.main download "SRR11047173 SRR000001"
python -m rnaseq_workflow.cli.main download "SRR11047173,SRR000001"
python -m rnaseq_workflow.cli.main download "SRR11047173;SRR000001"
```

TUI 中选择 `下载 SRA` 后，在单行输入框中输入同样的内容即可。不要用换行；如果目标很多，推荐使用本目录的 TXT/CSV/JSON 清单。

## 推荐格式

少量 accession 用 TXT：

```powershell
python -m rnaseq_workflow.cli.main download templates\download_manifests\accessions.txt
```

需要记录来源、预计大小或自定义输出目录时用 CSV：

```powershell
python -m rnaseq_workflow.cli.main download templates\download_manifests\accessions.csv
```

需要程序生成或后续扩展字段时用 JSON：

```powershell
python -m rnaseq_workflow.cli.main download templates\download_manifests\accessions.json
```

TUI 中选择 `下载 SRA`，然后把清单路径作为下载目标输入即可。

## 字段说明

CSV 必填：

- `accession`

CSV 可选：

- `source`：默认 `sra`
- `output_dir`：单独指定该 accession 的输出目录
- `expected_size_bytes`：预计大小，用于显示百分比

JSON 支持简单数组或对象数组，见本目录示例。
