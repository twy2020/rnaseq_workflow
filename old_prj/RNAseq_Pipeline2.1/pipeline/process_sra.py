# pipeline/process_sra.py
import os
import subprocess
import time

from pipeline import utils

PIPELINE_STEPS = [
    "SRA->FASTQ",
    "FASTQC",
    "TrimGalore",
    "HISAT2",
    "SAM2BAM",
    "StringTie"
]

def run_command(cmd, log_prefix):
    utils.log_message(f"{log_prefix} 开始执行: {cmd}")
    try:
        result = subprocess.run(cmd, shell=True, check=True,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE,
                                universal_newlines=True)
        utils.log_message(f"{log_prefix} 成功: {result.stdout.strip()}")
        return True, result.stdout.strip()
    except subprocess.CalledProcessError as e:
        utils.log_message(f"{log_prefix} 失败: {e.stderr.strip()}", level="ERROR")
        return False, e.stderr.strip()

def process_sra(task_id, sra_path, config, hisat2_lock):
    total_steps = len(PIPELINE_STEPS)
    output_dir = os.path.join(config["output_dir"], task_id)
    os.makedirs(output_dir, exist_ok=True)

    utils.log_message(f"开始处理任务 {task_id}，输入文件: {sra_path}")

    current_step = utils.get_task_progress(task_id)

    # 步骤1：SRA -> FASTQ 转换
    if current_step < 1:
        utils.update_progress_locked(task_id, 0, total_steps, PIPELINE_STEPS[0], "Running")
        fastq_cmd = f"fastq-dump {config['fastq_dump_params']} -O {output_dir} {sra_path}"
        success, _ = run_command(fastq_cmd, f"{task_id} {PIPELINE_STEPS[0]}")
        if not success:
            utils.update_progress_locked(task_id, 0, total_steps, PIPELINE_STEPS[0], "FAILED")
            return
        utils.update_progress_locked(task_id, 1, total_steps, PIPELINE_STEPS[0], "Completed")
        current_step = 1

    # 步骤2：FASTQC 质控
    if current_step < 2:
        utils.update_progress_locked(task_id, 1, total_steps, PIPELINE_STEPS[1], "Running")
        fastq_files = [f for f in os.listdir(output_dir) if f.endswith(".fastq.gz")]
        if fastq_files:
            for fq in fastq_files:
                fastqc_cmd = f"fastqc -t {config['fastqc_threads']} {os.path.join(output_dir, fq)}"
                success, _ = run_command(fastqc_cmd, f"{task_id} {PIPELINE_STEPS[1]} {fq}")
                if not success:
                    utils.update_progress_locked(task_id, 1, total_steps, PIPELINE_STEPS[1], "FAILED")
                    return
        utils.update_progress_locked(task_id, 2, total_steps, PIPELINE_STEPS[1], "Completed")
        current_step = 2

    # 步骤3：TrimGalore 排污处理
    if current_step < 3:
        utils.update_progress_locked(task_id, 2, total_steps, PIPELINE_STEPS[2], "Running")
        fq1 = os.path.join(output_dir, f"{task_id}_1.fastq.gz")
        fq2 = os.path.join(output_dir, f"{task_id}_2.fastq.gz")
        if os.path.exists(fq1) and os.path.exists(fq2):
            trim_cmd = f"trim_galore {config['trimgalore_params']} {fq1} {fq2} -o {output_dir}"
        else:
            files = [f for f in os.listdir(output_dir) if f.endswith(".fastq.gz")]
            fq = files[0] if files else ""
            trim_cmd = f"trim_galore {config['trimgalore_params'].replace('--paired','')} {os.path.join(output_dir, fq)} -o {output_dir}"
        success, _ = run_command(trim_cmd, f"{task_id} {PIPELINE_STEPS[2]}")
        if not success:
            utils.update_progress_locked(task_id, 2, total_steps, PIPELINE_STEPS[2], "FAILED")
            return
        utils.update_progress_locked(task_id, 3, total_steps, PIPELINE_STEPS[2], "Completed")
        current_step = 3

    # 步骤4：HISAT2 比对（串行执行）
    if current_step < 4:
        utils.update_progress_locked(task_id, 3, total_steps, PIPELINE_STEPS[3], "Running")
        utils.log_message(f"{task_id}: 等待 hisat2 资源锁...")
        with hisat2_lock:
            utils.log_message(f"{task_id}: 获得 hisat2 资源锁，开始执行 hisat2 比对")
            val1 = os.path.join(output_dir, f"{task_id}_1_val_1.fq.gz")
            val2 = os.path.join(output_dir, f"{task_id}_2_val_2.fq.gz")
            hisat2_cmd = f"hisat2 -p {config['hisat2_threads']} -x {config['hisat2_index']} -1 {val1} -2 {val2} -S {os.path.join(output_dir, task_id)}.sam 2>{os.path.join(output_dir, task_id)}.log"
            success, _ = run_command(hisat2_cmd, f"{task_id} {PIPELINE_STEPS[3]}")
        if not success:
            utils.update_progress_locked(task_id, 3, total_steps, PIPELINE_STEPS[3], "FAILED")
            return
        utils.update_progress_locked(task_id, 4, total_steps, PIPELINE_STEPS[3], "Completed")
        current_step = 4

    # 步骤5：SAM 转 BAM（排序）
    if current_step < 5:
        utils.update_progress_locked(task_id, 4, total_steps, PIPELINE_STEPS[4], "Running")
        sam_file = os.path.join(output_dir, f"{task_id}.sam")
        bam_file = os.path.join(output_dir, f"{task_id}.bam")
        samtools_cmd = f"samtools sort -@ {config['samtools_threads']} -o {bam_file} {sam_file}"
        success, _ = run_command(samtools_cmd, f"{task_id} {PIPELINE_STEPS[4]}")
        if not success:
            utils.update_progress_locked(task_id, 4, total_steps, PIPELINE_STEPS[4], "FAILED")
            return
        utils.update_progress_locked(task_id, 5, total_steps, PIPELINE_STEPS[4], "Completed")
        current_step = 5

    # 步骤6：StringTie 计算 FPKM
    if current_step < 6:
        utils.update_progress_locked(task_id, 5, total_steps, PIPELINE_STEPS[5], "Running")
        bam_file = os.path.join(output_dir, f"{task_id}.bam")
        gtf_output = os.path.join(output_dir, f"{task_id}.gtf")
        stringtie_cmd = f"stringtie -e -B -p {config['stringtie_threads']} -G {config['gff3_file']} -o {gtf_output} {bam_file}"
        success, _ = run_command(stringtie_cmd, f"{task_id} {PIPELINE_STEPS[5]}")
        if not success:
            utils.update_progress_locked(task_id, 5, total_steps, PIPELINE_STEPS[5], "FAILED")
            return
        utils.update_progress_locked(task_id, 6, total_steps, PIPELINE_STEPS[5], "Completed")
        current_step = 6

    # 删除中间文件（如果配置不保留）
    if not config.get("retain_intermediate", False):
        for f in os.listdir(output_dir):
            if f.endswith(".fastq.gz") or f.endswith(".sam") or "fastqc" in f or "val_" in f:
                try:
                    os.remove(os.path.join(output_dir, f))
                except Exception:
                    pass

    utils.log_message(f"任务 {task_id} 所有步骤已成功完成！")
