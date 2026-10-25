"""运行器适配器：谁来执行一轮任务。"""

from .dsh import DshRunner, default_dsh_cmd, kill_tree

__all__ = ["DshRunner", "default_dsh_cmd", "kill_tree"]
