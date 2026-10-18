#!/usr/bin/env python
"""小说查重（NovelDedup）入口。

双击本文件 → 打开图形界面。
带参数运行 → 走命令行：

    python novel_dedup.py --scan D:\\小说 --csv 重复清单.csv
    python novel_dedup.py --selftest
    python novel_dedup.py --verify

注意：界面用的是 Python 自带的 tkinter。如果你用的是一个"精简版" Python
（没有 tkinter 的解释器），双击会提示改用命令行，命令行的功能是完整的。
"""

from __future__ import annotations

import multiprocessing
import sys


def _try_gui() -> int | None:
    """能起界面就起界面；起不来返回 None 交给命令行。"""
    try:
        from noveldedup.gui import main as gui_main
    except ImportError as exc:
        if "tkinter" in str(exc).lower():
            print("=" * 60)
            print("这个 Python 没有内置 tkinter，无法打开图形界面。")
            print(f"当前解释器：{sys.executable}")
            print()
            print("两个办法：")
            print("  1) 用「运行工具.bat」启动（它会自动挑带 tkinter 的 Python）")
            print("  2) 直接用命令行（功能完全一样）：")
            print('     python novel_dedup.py --scan "D:\\小说" --csv "重复清单.csv"')
            print("=" * 60)
            return None
        raise
    return gui_main()


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("--gui", "-g"):
        rc = _try_gui()
        if rc is not None:
            return rc
        # 界面起不来 → 打印用法，别让人对着一闪而过的黑框
        from noveldedup.cli import build_parser

        build_parser().print_help()
        try:
            input("\n按回车键关闭…")
        except EOFError:
            pass
        return 2

    from noveldedup.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    # Windows 上 ProcessPoolExecutor 用 spawn，缺这行会在子进程里递归创建进程池
    multiprocessing.freeze_support()
    sys.exit(main())
