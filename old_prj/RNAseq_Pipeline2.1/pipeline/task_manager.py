# pipeline/task_manager.py
import os

def get_sra_files(input_dir):
    """
    扫描 input_dir 目录，返回字典：
      {统一任务ID: SRA 文件完整路径}
    统一任务ID为去掉扩展名后的文件名，例如 "SRR19820397"
    """
    tasks = {}
    for root, dirs, files in os.walk(input_dir):
        for file in files:
            if file.lower().endswith(".sra"):
                task_id = file.rsplit(".", 1)[0]  # 去掉扩展名
                tasks[task_id] = os.path.join(root, file)
    return tasks
