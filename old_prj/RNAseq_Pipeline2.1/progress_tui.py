# progress_tui.py
from textual.app import App, ComposeResult
from textual.widgets import DataTable
from textual import events
import time
import asyncio
from pipeline.utils import read_all_progress, get_progress_file
import json

def read_all_progress(path: str):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    tasks = []
    for tid, info in data.get('tasks', {}).items():
        # 获取当前步骤名称和已完成数
        step_name = info.get('step_name', 'N/A')
        total = info.get('total_steps', 6)
        current = info.get('current_step', 0)
        remain = total - current
        status = info.get('status', '')
        # 计算已耗时
        st = info.get('start_time')
        elapsed = ''
        if st:
            secs = int(time.time() - float(st))
            h, m = divmod(secs, 3600)[0], divmod(secs, 3600)[1]
            m, s = divmod(m, 60)
            elapsed = f"{h:02}:{m:02}:{s:02}"
        tasks.append({
            'task_id': tid,
            'current_step': step_name,
            'remaining_steps': str(remain),
            'status': status,
            'elapsed': elapsed
        })
    return tasks

class ProgressApp(App):
    """全屏 TUI 进度面板"""
    def __init__(self, progress_file: str):
        super().__init__()
        self.progress_file = progress_file

    def compose(self) -> ComposeResult:
        table = DataTable()
        table.add_columns('任务ID', '当前步骤', '剩余步骤', '状态', '已耗时')
        yield table

    async def on_mount(self) -> None:
        self.table = self.query_one(DataTable)
        # 1秒刷新一次
        self.set_interval(1, self.refresh_table)

    def refresh_table(self) -> None:
        data = read_all_progress(self.progress_file)
        # 清空并重绘
        self.table.clear()
        for row in data:
            self.table.add_row(
                row['task_id'],
                row['current_step'],
                row['remaining_steps'],
                row['status'],
                row['elapsed']
            )

    async def on_key(self, event: events.Key) -> None:
        if event.key in ('q', 'ctrl+c'):
            await self.action_quit()

if __name__ == '__main__':
    pf = get_progress_file()
    ProgressApp(pf).run()
