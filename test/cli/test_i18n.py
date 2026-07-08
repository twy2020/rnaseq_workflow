from rnaseq_workflow.cli.i18n import normalize_language, set_language, translate, translate_values


def test_normalize_language_aliases() -> None:
    assert normalize_language("en") == "en"
    assert normalize_language("English") == "en"
    assert normalize_language("zh-CN") == "zh"
    assert normalize_language("unknown") == "zh"


def test_translate_dynamic_tui_status_to_english() -> None:
    set_language("en")

    assert translate("登录: 未登录") == "Login: not logged in"
    assert translate("任务: 未选择") == "Task: not selected"
    assert translate("界面语言: English") == "Interface language: English"


def test_translate_menu_values_to_english() -> None:
    set_language("en")

    assert translate_values([("doctor", "环境检查 doctor"), ("exit", "退出")]) == [
        ("doctor", "Environment check: doctor"),
        ("exit", "Exit"),
    ]
    assert translate("确认环境已就绪。") == "Check readiness."
    assert translate("Enter 打开，Esc 返回。") == "Enter opens. Esc goes back."
    assert translate("选择你想使用的语言。") == "Choose your preferred language."


def test_translate_common_tui_errors_to_english() -> None:
    set_language("en")

    assert translate("注册失败") == "Registration failed"
    assert translate("密码不能为空。") == "Password cannot be empty."
    assert translate("请先选择任务。") == "Select a task first."
    assert translate("D:\\data 下没有可用于 HISAT2 的 FASTQ 文件。") == "No FASTQ files for HISAT2 were found under D:\\data."


def test_translate_resource_guidance_to_english() -> None:
    set_language("en")

    assert translate("网络连通。") == "Network reachable."
    assert translate("磁盘空间满足当前估算。") == "Disk space satisfies the current estimate."
    assert translate("请重建镜像：docker build -f docker/Dockerfile.tools -t rnaseq-workflow:tools .") == (
        "Please rebuild the image: docker build -f docker/Dockerfile.tools -t rnaseq-workflow:tools ."
    )


def test_chinese_mode_keeps_source_text() -> None:
    set_language("zh")

    assert translate("用户与任务管理") == "用户与任务管理"
