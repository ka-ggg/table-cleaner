"""用户可见的错误类型，以及协作式超时工具。"""

from __future__ import annotations

import time


class TableError(ValueError):
    """可以直接展示给用户的中文错误提示。"""


class Deadline:
    """协作式硬超时：在分块循环、大步骤前后调 ``check()``。

    手机端卡死的元凶是单次超大的解析/导出，所以超时只能在「块与块之间」生效：
    一次 pandas 的 C 调用（比如 read_csv 内部）没法从外部打断。
    要做到真正的抢占得把任务丢进子进程，那会多一份数据拷贝，手机内存更紧张，
    这里选了协作式超时——够用，且不会悄悄丢数据（超时是整批失败，不是部分成功）。
    """

    def __init__(self, seconds: float | None = None) -> None:
        self.seconds = float(seconds) if seconds and seconds > 0 else None
        self.started = time.monotonic()

    @property
    def expired(self) -> bool:
        return self.seconds is not None and time.monotonic() - self.started > self.seconds

    def check(self, what: str = "处理") -> None:
        if not self.expired:
            return
        raise TableError(
            f"{what}已经超过 {self.seconds:g} 秒还没完成，为免手机卡死已停止。"
            "请把文件拆小（或一次少勾几张工作表/文件），"
            "也可以在左侧「高级设置 → 处理超时」里调大秒数。"
        )
