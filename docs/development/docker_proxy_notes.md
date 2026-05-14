# Docker 代理与工具镜像说明

## 1. 当前策略

生信工具不要求用户在 Windows 本机安装，而是放进 Docker 镜像。CLI 在 PowerShell 下运行，真实工具通过容器执行或后续在容器内直接运行完整 workflow。

## 2. 代理注意事项

宿主机上常见代理是：

```text
http://127.0.0.1:10808
```

但容器里的 `127.0.0.1` 指向容器自身，不是宿主机。因此 Docker build 或容器内 apt/pip 需要访问宿主代理时，应使用：

```text
http://host.docker.internal:10808
```

本项目提供：

- `docker/proxy.env.example`
- `scripts/build_sra_tools_image.ps1`

可以通过环境文件传入代理和镜像源。

## 3. 构建 SRA Toolkit 镜像

无代理：

```powershell
.\scripts\build_sra_tools_image.ps1
```

使用代理：

```powershell
Copy-Item docker\proxy.env.example docker\proxy.env
.\scripts\build_sra_tools_image.ps1 -ProxyEnv docker\proxy.env
```

如果宿主机设置了 `HTTP_PROXY/HTTPS_PROXY`，但 Docker build 访问该代理不稳定，可以显式禁用继承宿主代理：

```powershell
.\scripts\build_tools_image.ps1 -NoHostProxy
.\scripts\build_sra_tools_image.ps1 -NoHostProxy
```

## 4. 测试工具

```powershell
docker run --rm rnaseq-workflow:sra-tools fasterq-dump --version
```

若该命令能输出版本号，说明容器内 SRA Toolkit 可用。

也可以使用项目脚本：

```powershell
.\scripts\run_sra_tools.ps1
.\scripts\run_sra_tools.ps1 -Command @("python3", "-m", "pip", "--version")
```

## 5. 当前实测结果

在当前开发环境中已验证：

- Docker daemon 可运行容器。
- `hello-world` 可拉取并运行。
- `rnaseq-workflow:sra-tools` 构建成功。
- 容器内 `fasterq-dump --version` 可用。
- 容器内 `python3 -m pip --version` 可用。
- 容器内 `apt-get update` 可用。

## 6. 通用工具镜像

从 FastQC 模块开始，项目新增通用工具镜像：

- `docker/Dockerfile.tools`
- `scripts/build_tools_image.ps1`
- `scripts/run_tools.ps1`

构建：

```powershell
.\scripts\build_tools_image.ps1
```

测试 FastQC：

```powershell
.\scripts\run_tools.ps1 -Command @("fastqc", "--version")
```

当前已验证：

- `rnaseq-workflow:tools` 构建成功。
- 容器内 `FastQC v0.12.1` 可用。
- 使用合成 FASTQ 可生成 `*_fastqc.html` 和 `*_fastqc.zip`。
- 容器内 `Trim Galore 0.6.10` 可用。
- 容器内 `cutadapt 4.4` 可用。
- 使用含 Illumina adapter 的合成 FASTQ 可生成 `*_trimmed.fq.gz` 和 trimming report。
- 容器内 `HISAT2 2.2.1` 可用。
- 容器内 `samtools 1.19.2` 可用。
- 使用极小参考 FASTA 和合成 FASTQ 完成 `hisat2-build -> hisat2 -> samtools sort -> samtools index` 冒烟测试，可生成 SAM、排序 BAM 和 BAI。
- 容器内 `featureCounts v2.0.6` 可用。
- 使用唯一比对的合成 BAM 和极小 GTF 完成 featureCounts 冒烟测试，`geneB` 的 assigned count 为 1。

Alignment 冒烟测试产物：

```text
runtime_logs/alignment_demo/output/demo.sam
runtime_logs/alignment_demo/output/demo.sorted.bam
runtime_logs/alignment_demo/output/demo.sorted.bam.bai
runtime_logs/alignment_demo/output/demo.hisat2.log
```

featureCounts 冒烟测试产物：

```text
runtime_logs/featurecounts_demo/unique_output/unique.featureCounts.txt
runtime_logs/featurecounts_demo/unique_output/unique.featureCounts.txt.summary
```

## 7. Pipeline 容器执行模式

样本级 pipeline 已支持通过配置切换到 Docker 执行外部生信工具：

```yaml
execution_mode: docker
docker_image: rnaseq-workflow:tools
docker_workspace: .
```

执行时会将 `docker_workspace` 挂载到容器 `/workspace`，并把工作区内路径转换为 `/workspace/...`。

已完成真实容器 pipeline 冒烟测试：

```text
FastQC -> HISAT2 -> samtools sort -> featureCounts
```

测试输入为 1 条唯一比对 read，输出结果：

- HISAT2 overall alignment rate: 100.00%
- featureCounts `geneB` count: 1

产物：

```text
runtime_logs/docker_pipeline_demo/output/samples/S1/qc_raw/S1_fastqc.html
runtime_logs/docker_pipeline_demo/output/samples/S1/alignment/S1.sam
runtime_logs/docker_pipeline_demo/output/samples/S1/alignment/S1.sorted.bam
runtime_logs/docker_pipeline_demo/output/samples/S1/quantification/S1.featureCounts.txt
runtime_logs/docker_pipeline_demo/output/progress.json
```
