# alignment 模块

比对模块，负责 HISAT2 索引校验、reads 比对、SAM 转 BAM、排序和索引。

## 输入

- trimmed FASTQ
- HISAT2 索引

## 输出

- SAM，临时文件
- sorted BAM
- BAM index，后续扩展
- HISAT2 比对日志

## 外部工具

- `hisat2`
- `samtools`

## 测试重点

- HISAT2 索引校验
- 单端/双端比对命令构造
- samtools sort 命令构造
- 资源锁和线程参数

## 当前实现状态

已实现 alignment 第一阶段：

- `workflow/rnaseq_workflow/steps/alignment/hisat2.py`
  - `build_hisat2_command`
  - `hisat2_index_exists`
  - `Hisat2AlignStep`
- `workflow/rnaseq_workflow/steps/alignment/samtools.py`
  - `build_samtools_sort_command`
  - `build_samtools_index_command`
  - `SamtoolsSortStep`

支持：

- 单端 FASTQ：`hisat2 -U`
- 双端 FASTQ：`hisat2 -1/-2`
- HISAT2 index 基础校验
- SAM 输出
- samtools sort 生成 sorted BAM
- dry-run
- 标准输出目录 `samples/{sample_id}/alignment`

工具镜像：

- `docker/Dockerfile.tools` 已加入 `hisat2` 和 `samtools`
