"""双击这个文件 → 直接打开界面（不留黑色命令行窗口）。

.pyw 由 pythonw.exe 运行，所以没有控制台。如果界面起不来（比如这个 Python
没装 tkinter），会用系统弹窗告诉你，而不是"双击没反应"。
"""

from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _alert(msg: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, msg, "小说查重", 0x10)
    except Exception:
        print(msg)


def main() -> int:
    try:
        from noveldedup.gui import main as gui_main
    except Exception:  # noqa: BLE001
        _alert(
            "无法启动界面。\n\n"
            + traceback.format_exc(limit=2)
            + "\n请改用「运行工具.bat」，或用命令行：\n"
            'python novel_dedup.py --scan "D:////小说" --csv "重复清单.csv"'
        )
        return 1
    return gui_main()


if __name__ == "__main__":
    # ★ 必须放在最前面：PyInstaller 打包后 ProcessPoolExecutor 的子进程
    #   会重新执行这个入口，没有 freeze_support() 就会弹出 N 个界面窗口
    #   （实测 6 个 worker → 6 个窗口 + 6 条「界面启动」日志）。
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
