from __future__ import annotations

import os
import re
from typing import Iterable


SUPPORTED_LANGUAGES = ("zh", "en")
LANGUAGE_ENV = "RNASEQ_UI_LANG"

_CURRENT_LANGUAGE = "zh"


def normalize_language(value: object | None) -> str:
    raw = str(value or "").strip().lower().replace("_", "-")
    if raw in {"en", "eng", "english"}:
        return "en"
    if raw in {"zh", "cn", "chinese", "zh-cn", "zh-hans", "中文", "简体中文"}:
        return "zh"
    return "zh"


def default_language() -> str:
    return normalize_language(os.environ.get(LANGUAGE_ENV))


def set_language(language: object | None) -> str:
    global _CURRENT_LANGUAGE
    _CURRENT_LANGUAGE = normalize_language(language)
    return _CURRENT_LANGUAGE


def get_language() -> str:
    return _CURRENT_LANGUAGE


def language_name(language: object | None = None) -> str:
    language = normalize_language(language if language is not None else _CURRENT_LANGUAGE)
    return "English" if language == "en" else "简体中文"


def language_status(language: object | None = None) -> str:
    language = normalize_language(language if language is not None else _CURRENT_LANGUAGE)
    return "English" if language == "en" else "简体中文"


def translate_values(values: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    return [(value, translate(label)) for value, label in values]


def translate(text: object, language: object | None = None) -> str:
    value = str(text)
    target = normalize_language(language if language is not None else _CURRENT_LANGUAGE)
    if target == "zh" or not value:
        return value
    return _translate_to_english(value)


def _translate_to_english(text: str) -> str:
    if text in _EXACT_EN:
        return _EXACT_EN[text]
    result = text
    for source, target in _REGEX_EN:
        result = source.sub(target, result)
    for source, target in _PHRASE_EN:
        result = result.replace(source, target)
    return result


def has_chinese(text: object) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", str(text)))


_EXACT_EN = {
    "简体中文": "Simplified Chinese",
    "English": "English",
    "语言": "Language",
    "切换界面语言": "Switch interface language",
    "选择界面显示语言。该设置会保存到 workspace/ui_settings.json，也可通过 RNASEQ_UI_LANG=en|zh 指定。": "Choose your preferred language.",
    "选择你想使用的语言。": "Choose your preferred language.",
    "中文": "Chinese",
    "英文": "English",
    "确认": "Confirm",
    "取消": "Cancel",
    "返回": "Back",
    "返回/取消": "Back/Cancel",
    "退出": "Exit",
    "已退出终端工作台": "Terminal workspace closed",
    "环境检查 doctor": "Environment check: doctor",
    "用户与任务管理": "User and task management",
    "基础配置": "Basic configuration",
    "工具调试": "Tool debugging",
    "系统信息与资源策略": "System information and resource strategy",
    "查看最近输出": "View recent output",
    "登录/注册用户": "Login/register user",
    "任务管理": "Task management",
    "旧测试产物清理 dry-run": "Old test artifact cleanup dry-run",
    "下载 SRA": "Download SRA",
    "高级下载设置": "Advanced download settings",
    "SRA 元数据预检/分组": "SRA metadata precheck/grouping",
    "继续未完成下载": "Resume unfinished download",
    "扫描输入 FASTQ/SRA": "Scan FASTQ/SRA inputs",
    "SRA 转 FASTQ": "SRA to FASTQ",
    "FastQC 质控": "FastQC quality control",
    "Trim Galore 修剪": "Trim Galore trimming",
    "HISAT2 对齐": "HISAT2 alignment",
    "featureCounts 定量": "featureCounts quantification",
    "结果汇总/报告": "Result summary/report",
    "运行旧 workflow": "Run legacy workflow",
    "创建/选择任务": "Create/select task",
    "选择 Reference": "Select Reference",
    "参考基因组": "Reference assets",
    "提交清单": "Submit manifest",
    "样本元数据更新/手动修改": "Update/edit sample metadata",
    "工具配置": "Tool parameters",
    "资源检查": "Resource check",
    "正式运行": "Run workflow",
    "已配置": "configured",
    "未配置": "not configured",
    "已完成": "completed",
    "未完成": "not completed",
    "未登录": "not logged in",
    "未选择": "not selected",
    "未设置": "not set",
    "未命名任务": "unnamed task",
    "无": "none",
    "暂无输出": "no output yet",
    "(无输出)": "(no output)",
    "错误": "Error",
    "输入错误": "Input error",
    "路径不存在": "Path does not exist",
    "不是目录": "Not a directory",
    "请输入整数": "Please enter an integer",
    "按 Enter 继续...": "Press Enter to continue...",
    "继续": "Continue",
    "输入": "Input",
    "内容": "Content",
    "确认内容": "Confirmation content",
    "说明": "Note",
    "默认": "Default",
    "是": "Yes",
    "否": "No",
    "选择": "Select",
    "Enter 打开，Esc 返回。": "Enter opens. Esc goes back.",
    "空格勾选或取消。": "Use Space to select or clear.",
    "选择中包含多个元数据分组，仍继续？": "The selection contains multiple metadata groups. Continue?",
    "Space 勾选/取消，Enter 确认，Esc 返回。": "Space selects/clears, Enter confirms, Esc returns.",
    "搜索": "Search",
    "上一页": "Previous page",
    "下一页": "Next page",
    "使用此资产": "Use this asset",
    "返回列表": "Back to list",
    "公共资产": "Shared asset",
    "名称": "Name",
    "资产库": "Asset library",
    "目录": "Directory",
    "来源": "Source",
    "物种": "Species",
    "状态": "Status",
    "描述": "Description",
    "注释": "Annotation",
    "检查": "Check",
    "通过": "passed",
    "需要处理": "needs action",
    "未记录": "not recorded",
    "未登记": "not registered",
    "任务完成后清理": "Clean after task completion",
    "每步成功后清理": "Clean after each successful step",
    "不自动清理": "Do not clean automatically",
    "按样本流水线": "Per-sample pipeline",
    "按阶段批量": "Stage-wise batch",
    "非链特异": "unstranded",
    "正向链特异": "forward stranded",
    "反向链特异": "reverse stranded",
}


_REGEX_EN = [
    (re.compile(r"登录: ([^\n]+)"), r"Login: \1"),
    (re.compile(r"任务: ([^\n]+)"), r"Task: \1"),
    (re.compile(r"配置: ([^\n]+)"), r"Config: \1"),
    (re.compile(r"目录: ([^\n]+)"), r"Directory: \1"),
    (re.compile(r"界面语言: ([^\n]+)"), r"Interface language: \1"),
    (re.compile(r"Language / 语言: ([^\n]+)"), r"Language: \1"),
    (re.compile(r"\[资产根目录\]"), "[Asset root]"),
    (re.compile(r"\[账号\]"), "[Account]"),
    (re.compile(r"\[用户ID\]"), "[User ID]"),
    (re.compile(r"\[当前任务\]"), "[Current task]"),
    (re.compile(r"\[任务目录\]"), "[Task directory]"),
    (re.compile(r"\[清单\]"), "[Manifest]"),
    (re.compile(r"\[参数\]"), "[Parameters]"),
    (re.compile(r"\[资源检查\]"), "[Resource check]"),
    (re.compile(r"共 (\d+) 个资产，第 (\d+)/(\d+) 页。关键词: (.+)"), r"\1 assets, page \2/\3. Keyword: \4"),
    (re.compile(r"\.\.\. 上方还有 (\d+) 行"), r"... \1 more lines above"),
    (re.compile(r"\.\.\. 下方还有 (\d+) 行"), r"... \1 more lines below"),
    (re.compile(r"\.\.\. 上方还有 (\d+) 项"), r"... \1 more items above"),
    (re.compile(r"\.\.\. 下方还有 (\d+) 项"), r"... \1 more items below"),
    (re.compile(r"不能小于 (\d+)"), r"Must be at least \1"),
]


_PHRASE_EN = [
    ("↑↓ 选择    Enter 确认    Esc 返回", "Up/Down select    Enter confirm    Esc back"),
    ("从用户与任务管理开始。", "Start with a task."),
    ("从任务开始。", "Start with a task."),
    ("单项工具入口用于排错和局部重跑；正式任务建议使用 Workflow。", "Run one step when needed."),
    ("需要时单独运行一步。", "Run one step when needed."),
    ("尚未选择任务。", "No task has been selected."),
    ("登录后使用个人任务与资产。", "Log in to use personal tasks and assets."),
    ("登录已有用户", "Login existing user"),
    ("注册新用户", "Register new user"),
    ("临时 UUID 用户", "Temporary UUID user"),
    ("创建新任务", "Create new task"),
    ("创建/选择任务", "Create/select task"),
    ("选择已有任务", "Select existing task"),
    ("选择 Reference", "Select Reference"),
    ("提交清单", "Submit manifest"),
    ("样本元数据更新/手动修改", "Update/edit sample metadata"),
    ("工具配置", "Tool parameters"),
    ("资源检查", "Resource check"),
    ("正式运行", "Run workflow"),
    ("工作目录", "Work directory"),
    ("修改当前任务名称/描述", "Edit current task name/description"),
    ("删除当前任务", "Delete current task"),
    ("查看当前任务", "View current task"),
    ("日志中心", "Log center"),
    ("产物统计", "Artifact statistics"),
    ("产物清理", "Artifact cleanup"),
    ("进入 Workflow 向导", "Open Workflow wizard"),
    ("继续进入 Workflow 向导？", "Continue to Workflow wizard?"),
    ("Docker 镜像", "Docker image"),
    ("用于检查工具镜像是否存在", "Used to check whether the tool image exists"),
    ("环境检查结果", "Environment check results"),
    ("检查本机环境、Docker 与常用工具。", "Check readiness."),
    ("确认环境已就绪。", "Check readiness."),
    ("管理登录状态、任务和任务目录。", "Manage accounts and tasks."),
    ("管理账号和任务。", "Manage accounts and tasks."),
    ("配置项目、执行环境、样本和参考文件。", "Set up the project."),
    ("设置项目基础信息。", "Set up the project."),
    ("按任务完成清单、参数、检查和运行。", "Move through the workflow."),
    ("按步骤完成分析。", "Move through the workflow."),
    ("管理参考基因组、注释和 HISAT2 索引。", "Manage reusable references."),
    ("管理参考基因组、注释和 HISAT2 index。", "Manage reference genomes, annotations and HISAT2 indexes."),
    ("单独运行某一步，用于排查问题。", "Run one step independently for troubleshooting."),
    ("查看上一次命令或检查结果。", "View the previous command or check result."),
    ("关闭终端工作台。", "Close the terminal workspace."),
    ("任务成功后清理大体积中间文件。", "Clean large intermediate files after task success."),
    ("每一步成功后清理上一步大体积文件。", "Clean large upstream files after each successful step."),
    ("保留全部产物，便于复查。", "Keep all outputs for review."),
    ("使用容器中的工具，环境更一致。", "Use tools inside the container for a more consistent environment."),
    ("使用本机已安装的工具。", "Use tools installed on the local machine."),
    ("输入路径。", "Enter a path."),
    ("容器能访问的项目目录。", "A project folder Docker can access."),
    ("选择流程工具镜像。", "Choose the tool image."),
    ("Docker 更稳，本机更轻。", "Docker is steadier; local is lighter."),
    ("Auto 会自动选择。", "Auto chooses for you."),
    ("例如 5G 或 20G。", "For example, 5G or 20G."),
    ("留空表示直连。", "Leave blank for direct access."),
    ("用于标记本次分析。", "Names this analysis."),
    ("留空则使用默认名称。", "Leave blank to use the default."),
    ("给参考资产取个名字。", "Name this reference."),
    ("使用数据库接受的名称。", "Use the database species name."),
    ("植物选 plants。", "Use plants for plant genomes."),
    ("可填 current。", "Use current if unsure."),
    ("记录文件来源。", "Records the file source."),
    ("可与参考来源一致。", "Usually matches the reference source."),
    ("输入 SRR、ERR 或 DRR。", "Enter SRR, ERR or DRR."),
    ("GTF 通常选 exon。", "Use exon for most GTF files."),
    ("GTF 通常选 gene_id。", "Use gene_id for most GTF files."),
    ("不确定时选 0。", "Choose 0 if unsure."),
    ("网络不稳时用 1-2。", "Use 1-2 on unstable networks."),
    ("越大占用越多资源。", "Higher uses more resources."),
    ("更高会更快，也更占资源。", "Higher is faster and heavier."),
    ("2-4 通常足够。", "2-4 is usually enough."),
    ("20 是常用默认值。", "20 is a common default."),
    ("过高会增加 I/O 压力。", "Very high values increase I/O pressure."),
    ("越高越快，也更占 CPU。", "Higher is faster and uses more CPU."),
    ("排序会占用较多 I/O。", "Sorting uses more I/O."),
    ("不确定时先选 0。", "Choose 0 if unsure."),
    ("paired-end 通常开启。", "Usually enabled for paired-end data."),
    ("网络不稳时设为 1-3。", "Use 1-3 on unstable networks."),
    ("越大网络压力越高。", "Higher adds network pressure."),
    ("大基因组会占更多内存。", "Large genomes need more memory."),
    ("越高越快，也更占资源。", "Higher is faster and heavier."),
    ("路径不存在", "Path does not exist"),
    ("不是目录", "Not a directory"),
    ("请输入整数。", "Please enter an integer."),
    ("请输入整数", "Please enter an integer"),
    ("Enter 会换行。", "Enter inserts a newline."),
    ("确认 Enter", "Confirm Enter"),
    ("确认 F2/Ctrl+S", "Confirm F2/Ctrl+S"),
    ("返回 Esc", "Back Esc"),
    ("进入 Enter", "Open Enter"),
    ("查看上方输出后继续", "Review the output above, then continue"),
    ("继续 Enter", "Continue Enter"),
    ("Space 勾选", "Space to select"),
    ("未登录", "not logged in"),
    ("未选择", "not selected"),
    ("未设置", "not set"),
    ("未完成", "not completed"),
    ("已完成", "completed"),
    ("已配置", "configured"),
    ("未配置", "not configured"),
    ("确认", "confirm"),
    ("取消", "clear"),
    ("返回", "back"),
    ("返回/取消", "Back/Cancel"),
    ("选择编号后按 Enter", "Enter a number and press Enter"),
    ("请输入编号。", "Please enter a number."),
    ("编号超出范围。", "Number out of range."),
    ("说明: ", "Note: "),
    ("Enter 进入，PgUp/PgDn 翻选项，Ctrl+U/Ctrl+D 翻状态文本。", "Enter opens. Esc goes back."),
    ("Enter 打开，Esc 返回。", "Enter opens. Esc goes back."),
    ("默认内容如下，直接输入空行会使用默认内容。", "Press Enter to keep the default."),
    ("粘贴多行后，单独输入一行 END 结束；输入 CANCEL 取消。", "Type END when done, or CANCEL to leave."),
    ("输入编号，用逗号/空格分隔；直接 Enter 使用默认全选；0/q 返回。", "Pick items, then press Enter."),
    ("请输入有效编号。", "Please enter valid numbers."),
    ("选择:", "Select:"),
    ("选择 reference", "Select reference"),
    ("浏览 reference", "Browse reference"),
    ("登记本地 FASTA/GTF", "Register local FASTA/GTF"),
    ("构建 HISAT2 index", "Build HISAT2 index"),
    ("检查 reference 资产", "Check reference asset"),
    ("写入当前 config", "Write to current config"),
    ("清理失效 reference 记录", "Clean invalid reference records"),
    ("一条龙下载 FASTA+GTF 并构建 index", "Download FASTA+GTF and build index"),
    ("从 Ensembl 或 URL 获取 FASTA/GTF，并生成 HISAT2 index，适合新物种或新版本。", "Fetch FASTA/GTF from Ensembl or URLs and build a HISAT2 index for a new species or release."),
    ("把已有本地 FASTA、GTF/GFF 或 HISAT2 index 登记为可复用资产，可复制入库。", "Register existing local FASTA, GTF/GFF or HISAT2 index files as reusable assets; files can be copied into the asset library."),
    ("对已登记的 FASTA 运行 hisat2-build，生成后续比对使用的 index prefix。", "Run hisat2-build for a registered FASTA to generate the index prefix for alignment."),
    ("检查 FASTA、注释文件和 HISAT2 index 是否存在且非空，并清理失效记录。", "Check whether FASTA, annotation and HISAT2 index files exist and are non-empty; clean invalid records."),
    ("把选中的 reference 路径写入传统 config.yaml，主要用于旧 CLI/调试流程。", "Write the selected reference paths into config.yaml for legacy CLI or debugging workflows."),
    ("移除文件已丢失或索引不完整的 reference 记录，避免列表显示不可用资产。", "Remove reference records with missing files or incomplete indexes."),
    ("在线下载 FASTA+GTF 并构建 index", "Download FASTA+GTF online and build index"),
    ("检查 reference 资产", "Check reference asset"),
    ("写入当前 config", "Write to current config"),
    ("清理失效 reference 记录", "Clean invalid reference records"),
    ("输入名称、物种或描述关键词。", "Enter a name, species or description keyword."),
    ("没有匹配项。关键词:", "No matches. Keyword:"),
    ("关键词:", "Keyword:"),
    ("无", "none"),
    ("未记录", "not recorded"),
    ("未登记", "not registered"),
    ("需要处理", "needs action"),
    ("通过", "passed"),
    ("样本并发数", "sample concurrency"),
    ("工作流样本并发数", "workflow sample concurrency"),
    ("下载并发数", "download concurrency"),
    ("线程数", "threads"),
    ("失败重试次数", "failed retry count"),
    ("链特异性", "strandness"),
    ("按片段计数", "count fragments"),
    ("修剪质量阈值", "trimming quality threshold"),
    ("项目 ID", "project ID"),
    ("物种名称", "species name"),
    ("版本", "release"),
    ("来源", "source"),
    ("注释来源", "annotation source"),
    ("下载代理", "download proxy"),
    ("下载大小上限", "download size limit"),
    ("当前工具使用的线程数量。提高后可能更快，也会占用更多资源。", "Number of threads used by the current tool. Higher values may be faster but consume more resources."),
    ("网络连通。", "Network reachable."),
    ("工具可用。", "Tool available."),
    ("容器内工具可用。", "Tool available inside the container."),
    ("磁盘空间满足当前估算。", "Disk space satisfies the current estimate."),
    ("内存满足常规流程验证。", "Memory is sufficient for routine workflow validation."),
    ("CPU 核心数可满足常规小样本验证。", "CPU cores are sufficient for routine small-sample validation."),
    ("估算基于输入体量和常见 RNA-seq 中间产物倍率。", "The estimate is based on input size and common RNA-seq intermediate-output multipliers."),
    ("建议至少", "Recommended at least"),
    ("请检查", "Please check"),
    ("请安装", "Please install"),
    ("或切换到可用执行环境。", "or switch to an available execution environment."),
]
