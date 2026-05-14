#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
main.py: RNA-seq 数据分析流水线入口，管理任务调度、TUI 进度显示和最终结果汇总
新增功能：启动时打印配置信息，检查 GFF/GFF3 文件和 HISAT2 索引文件，并在每个 SRR 目录下执行 StringTie 定量，生成 .gtf 和 .gtf.FPKM，并在每个 SRP 目录下汇总 FPKM 到 Excel。
可选参数 --no-tui 用于禁用 TUI 界面，方便调试终端输出。
增强调试：捕获并记录 StringTie 和 Perl 命令的 stdout/stderr 及退出状态。
"""

import os
import sys
import time
import yaml
import json
import signal
import subprocess
import psutil
import pandas as pd
import multiprocessing
import threading
import argparse

from pipeline import task_manager, process_sra, utils
import progress_tui


def reset_tasks_timer(progress_file):
    try:
        with open(progress_file, 'r', encoding='utf-8') as pf:
            data = json.load(pf)
        for task in data.get('tasks', {}).values():
            task['start_time'] = time.time()
        with open(progress_file, 'w', encoding='utf-8') as pf:
            json.dump(data, pf, indent=4)
    except Exception as e:
        print(f"重置计时器失败: {e}")


def load_config(path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def system_status_check(output_dir):
    cpu = psutil.cpu_count(logical=True)
    mem_gb = psutil.virtual_memory().available / (1024**3)
    disk_gb = psutil.disk_usage(output_dir).free / (1024**3)
    utils.log_message(
        f"系统状态检查：CPU核心数 {cpu}, 空闲内存 {mem_gb:.2f}GB, 磁盘剩余 {disk_gb:.2f}GB",
        level='INFO'
    )
    if mem_gb < 4.0:
        utils.log_message(f"警告：空闲内存不足，仅 {mem_gb:.2f}GB！", level='WARNING')
    if disk_gb < 100.0:
        utils.log_message(f"警告：磁盘剩余不足，仅 {disk_gb:.2f}GB！", level='WARNING')
    if cpu < 4:
        utils.log_message(f"警告：CPU核心过少，仅 {cpu} 个！", level='WARNING')


def main():
    parser = argparse.ArgumentParser(description="RNA-seq 主流水线脚本")
    parser.add_argument('--config', required=True, help='配置文件路径 config.yaml')
    parser.add_argument('--no-tui', action='store_true', help='禁用 TUI 界面，显示终端输出')
    args = parser.parse_args()

    cfg = load_config(args.config)
    enable_tui = not args.no_tui

    # 打印配置信息
    utils.log_message('加载的配置文件内容：', level='INFO')
    utils.log_message(json.dumps(cfg, indent=2, ensure_ascii=False), level='INFO')

    # 创建目录
    os.makedirs(cfg.get('log_dir', './logs'), exist_ok=True)
    os.makedirs(cfg.get('output_dir', './output'), exist_ok=True)

    # 检查 GFF/GFF3 文件
    gff = cfg.get('gff_file') or cfg.get('gff3_file')
    if not gff or not os.path.isfile(gff) or not gff.lower().endswith(('.gff', '.gff3')):
        utils.log_message(f"找不到有效的 GFF/GFF3 文件: {gff}", level='ERROR')
        sys.exit(1)
    utils.log_message(f"GFF/GFF3 文件检查通过: {gff}", level='INFO')

    # 检查 HISAT2 索引
    idx = cfg.get('hisat2_index')
    if not idx:
        utils.log_message('未提供 hisat2_index 配置！', level='ERROR')
        sys.exit(1)
    if os.path.isdir(idx):
        idx_loc = idx
        prefix = ''
        ht_files = [f for f in os.listdir(idx_loc) if f.endswith(('.ht2', '.ht'))]
    else:
        idx_loc = os.path.dirname(idx) or '.'
        prefix = os.path.basename(idx)
        ht_files = [f for f in os.listdir(idx_loc) if f.startswith(prefix) and f.endswith(('.ht2', '.ht'))]
    if not ht_files:
        utils.log_message(
            f"未找到 HISAT2 索引文件，期望在 {idx_loc} 下找到以 {prefix} 开头并以 .ht2/.ht 结尾的文件",
            level='ERROR'
        )
        sys.exit(1)
    utils.log_message(
        f"HISAT2 索引检查通过: 在 {idx_loc} 发现 {len(ht_files)} 个索引文件",
        level='INFO'
    )

    # 初始化进度文件
    progress_file = os.path.join(cfg['output_dir'], 'progress.json')
    if not os.path.exists(progress_file):
        info = {
            'project_name': cfg.get('project_name', ''),
            'creation_time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'creator': cfg.get('project_creator', '')
        }
        with open(progress_file, 'w', encoding='utf-8') as pf:
            json.dump({'project_info': info, 'tasks': {}}, pf, indent=4)
    utils.set_progress_file(progress_file)

    # 系统检查 & 重置计时器
    system_status_check(cfg['output_dir'])
    reset_tasks_timer(progress_file)

    # 初始化日志
    logf = os.path.join(cfg.get('log_dir', './logs'), f"run_{time.strftime('%Y-%m-%d_%H-%M')}.log")
    utils.init_log(logf)
    utils.log_message('任务开始', extra={'user': os.getenv('USER', '')})

    # 扫描 SRA 文件 & 预处理
    tasks = task_manager.get_sra_files(cfg['input_dir'])
    if not tasks:
        utils.log_message('未找到任何 SRA 文件。', level='ERROR')
        sys.exit(1)
    utils.log_message(f"发现 {len(tasks)} 个 SRA 文件。", level='INFO')

    # 启动 TUI 进度面板（可选）
    if enable_tui:
        progress_tui.PROGRESS_FILE = progress_file
        def _start_tui():
            import signal as _sig
            orig = _sig.signal
            _sig.signal = lambda *args, **kwargs: None
            try:
                app = progress_tui.ProgressApp(progress_file=progress_file)
                app.run()
            finally:
                _sig.signal = orig
        tui_thread = threading.Thread(target=_start_tui, daemon=True)
        tui_thread.start()
    else:
        utils.log_message('TUI 界面已禁用', level='INFO')

    # 并行执行预处理
    manager = multiprocessing.Manager()
    hisat2_lock = manager.Lock()
    def ignore_sigint():
        signal.signal(signal.SIGINT, signal.SIG_IGN)
    pool = multiprocessing.Pool(processes=multiprocessing.cpu_count(), initializer=ignore_sigint)
    for tid, path in tasks.items():
        pool.apply_async(process_sra.process_sra, args=(tid, path, cfg, hisat2_lock))
    pool.close()
    try:
        pool.join()
    except KeyboardInterrupt:
        utils.log_message('用户终止进程', level='ERROR')
        pool.terminate()
        pool.join()
        sys.exit(1)

    utils.log_message('所有预处理任务完成！', level='INFO')

    # =========================
    # StringTie 定量和 FPKM 生成
    # =========================
    utils.log_message('开始 StringTie 定量和 FPKM 生成……', level='INFO')
    for srp in os.listdir(cfg['output_dir']):
        srp_path = os.path.join(cfg['output_dir'], srp)
        if not os.path.isdir(srp_path) or not srp.startswith('SRP'):
            continue
        fpkm_dfs = []
        for srr in os.listdir(srp_path):
            srr_path = os.path.join(srp_path, srr)
            if not os.path.isdir(srr_path) or not srr.startswith('SRR'):
                continue
            bam = os.path.join(srr_path, f"{srr}.bam")
            gtf = os.path.join(srr_path, f"{srr}.gtf")
            if os.path.exists(bam):
                # StringTie 执行并捕获输出
                cmd = f"stringtie -e -G {gff} -o {gtf} {bam}"
                utils.log_message(f"执行命令：{cmd}", level='DEBUG')
                proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                out, err = proc.communicate()
                if proc.returncode == 0:
                    utils.log_message(f"StringTie 成功: {gtf}", level='INFO')
                    if out:
                        utils.log_message(f"StringTie 输出: {out.strip()}", level='DEBUG')
                else:
                    utils.log_message(f"StringTie 失败[{proc.returncode}]: {gtf}", level='ERROR')
                    utils.log_message(f"stdout: {out.strip()}", level='ERROR')
                    utils.log_message(f"stderr: {err.strip()}", level='ERROR')
                    continue
                # 解析 FPKM，同样捕获输出
                cmd2 = f"perl -lane 'next unless $F[2] eq \"transcript\"; /gene_id \"([^\"]+)\".*FPKM \"([\d.]+)\"/; $count{{\1}} += $2; END{{for(sort keys %count){{print join\"\t\",$_,$count{{$_}}}}}}' {gtf} > {gtf}.FPKM"
                utils.log_message(f"执行命令：{cmd2}", level='DEBUG')
                proc2 = subprocess.Popen(cmd2, shell=True, executable='/bin/bash', stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                out2, err2 = proc2.communicate()
                if proc2.returncode == 0:
                    utils.log_message(f"FPKM 文件生成: {gtf}.FPKM", level='INFO')
                else:
                    utils.log_message(f"FPKM 解析失败[{proc2.returncode}]: {gtf}", level='ERROR')
                    utils.log_message(f"stdout: {out2.strip()}", level='ERROR')
                    utils.log_message(f"stderr: {err2.strip()}", level='ERROR')
                    continue
                df = pd.read_csv(f"{gtf}.FPKM", sep='\t', header=None, names=['GeneID', srr])
                fpkm_dfs.append(df)
        # 汇总每个 SRP 下的 FPKM
        if fpkm_dfs:
            merged = fpkm_dfs[0]
            for df in fpkm_dfs[1:]:
                merged = pd.merge(merged, df, on='GeneID', how='outer')
            merged.fillna(0, inplace=True)
            out_excel = os.path.join(srp_path, f"{srp}_FPKM_summary.xlsx")
            merged.to_excel(out_excel, index=False)
            utils.log_message(f"生成 {srp} 汇总 Excel: {out_excel}", level='INFO')

    utils.log_message('任务结束', extra={'user': os.getenv('USER', '')})


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('用户终止进程')
        sys.exit(1)
