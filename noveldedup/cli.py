"""命令行：--scan / --csv / --json / --selftest / --verify。

设计原则
--------
1. 默认只读：不带 --delete 时绝不改动任何文件（本版还没有删除功能，删除在 M4 跟着界面做）。
2. 报告优先：CSV 给 Excel，JSON 给脚本，控制台给人看。
3. --selftest 与 --verify 不依赖任何真实语料，可在任何机器上跑。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import __version__, core, report


def _fmt_int(n: int) -> str:
    return f"{n:,}"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="novel_dedup",
        description=f"小说查重 v{__version__} —— 本地离线、按内容判重，不依赖文件名。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例：\n"
            "  python novel_dedup.py --scan D:\\小说 --csv 重复清单.csv\n"
            "  python novel_dedup.py --scan D:\\小说 --preset 精细 --json out.json\n"
            "  python novel_dedup.py --selftest\n"
            "  python novel_dedup.py --verify\n"
        ),
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--scan", metavar="目录", help="扫描指定目录下的小说")
    g.add_argument("--import", dest="import_file", metavar="文件",
                   help="导入上次导出的 JSON：重新分组后输出，不重新扫描")
    g.add_argument("--selftest", action="store_true", help="跑 11 组已知答案语料自检")
    g.add_argument("--verify", action="store_true", help="跑内核不变量校验（密度/保证性/归一化）")

    p.add_argument("--csv", metavar="文件", help="导出 CSV（UTF-8-BOM，Excel 直接打开）")
    p.add_argument("--json", metavar="文件", help="导出 JSON（含证据段，供脚本消费）")
    p.add_argument("--preset", choices=list(core.PRESETS), help="指纹档位（默认自动）")
    p.add_argument("--threads", type=int, default=0, help="进程数（默认 8 或 CPU 核数）")
    p.add_argument("--min-bytes", type=int, default=1024, help="忽略小于此字节的文件（默认 1024）")
    p.add_argument("--no-ad-filter", action="store_true", help="不剔除站点广告行")
    p.add_argument("--no-evidence", action="store_true", help="不算证据段（更快）")
    p.add_argument("--budget-mb", type=int, default=core.INDEX_BUDGET_MB,
                   help=f"索引体积预算，超过则自动升档（默认 {core.INDEX_BUDGET_MB}）")
    p.add_argument("--top", type=int, default=20, help="控制台最多显示几组（默认 20）")
    p.add_argument("--t2s", action="store_true",
                   help="繁简视为同一本（需要 opencc-python-reimplemented；默认关）")
    p.add_argument("--no-reclassify", action="store_true",
                   help="导入时沿用文件里的关系标签，不按当前判据重判")
    p.add_argument("--clean-empty-dirs", action="store_true",
                   help="清理书库里的空文件夹（默认只列出，加 --yes 才真删）")
    p.add_argument("--yes", action="store_true",
                   help="配合 --clean-empty-dirs / --clear-cache 真正执行")
    p.add_argument("--clear-cache", action="store_true",
                   help="清除缓存（配合 --scan 指定书库；加 --all 清全部书库）")
    p.add_argument("--all", action="store_true", help="配合 --clear-cache：清除所有书库的缓存")
    p.add_argument("--with-candidates", action="store_true",
                   help="导出时包含全部候选对（含被拒的），可离线改判据重判，体积更大")
    p.add_argument("--full", action="store_true",
                   help="强制全量重算，忽略历史关系表（怀疑缓存有问题时用）")
    p.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    p.add_argument("-V", "--version", action="version", version=f"NovelDedup {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    log = core.setup_logging(args.verbose, logfile=core.data_dir() / "run.log")

    if args.selftest:
        from . import selftest

        print(f"小说查重 v{__version__} —— 自检（11 组已知答案语料）\n")
        t0 = time.perf_counter()
        passed, total, lines = selftest.run(log)
        print("\n".join(lines))
        print(f"\n自检结果：{passed}/{total} 通过，用时 {time.perf_counter() - t0:.1f}s")
        return 0 if passed == total else 1

    if args.verify:
        from . import selftest

        print(f"小说查重 v{__version__} —— 内核校验\n")
        print(selftest.verify_text())
        return 0

    if args.clear_cache:
        from . import deleter

        root = Path(args.scan).expanduser() if args.scan else None
        if not args.all and (not root or not root.is_dir()):
            print("[错误] 需要 --scan 指定书库，或加 --all 清除全部缓存")
            return 2
        if args.all:
            print(f"全部书库缓存合计 {deleter.cache_total_mb():,.0f} MB")
        else:
            print(f"{root} 的缓存 {core.dir_size_mb(core.cache_dir_for(root)):,.0f} MB")
        if not args.yes:
            print("\n这只是预览。确认后加 --yes 真正删除（只删缓存，不动书）。")
            return 0
        n, freed = deleter.clear_cache(root, everything=args.all)
        print(f"\n已清除 {n} 个书库的缓存，释放 {freed:,.0f} MB")
        print("下次扫描会重新计算。")
        return 0

    if args.clean_empty_dirs:
        from . import deleter

        if not args.scan:
            print("[错误] --clean-empty-dirs 需要配合 --scan 指定目录")
            return 2
        root = Path(args.scan).expanduser()
        if not root.is_dir():
            print(f"[错误] 目录不存在：{root}")
            return 2
        dirs, junk = deleter.find_empty_dirs(root)
        print(f"{root}\n空文件夹 {len(dirs)} 个，缩略图缓存 {len(junk)} 个\n")
        for d in dirs[:50]:
            print(f"  {d}")
        if len(dirs) > 50:
            print(f"  …… 还有 {len(dirs) - 50} 个")
        if not args.yes:
            print("\n这只是预览。确认无误后加 --yes 真正删除"
                  "（空文件夹与缩略图缓存**直接删除**，不进回收站）。")
            return 0
        res = deleter.to_recycle_bin([*junk, *dirs], root=root, log=log,
                                     reason="清理空文件夹", allow_dirs=True)
        # 结果里混着空目录和 Thumbs.db，按请求清单分别计数，别把垃圾文件报成"空文件夹"
        ok = {str(p) for p in res.deleted}
        n_dir = sum(1 for d in dirs if str(d) in ok)
        n_junk = sum(1 for j in junk if str(j) in ok)
        print(f"\n已直接删除 {n_dir:,} 个空文件夹"
              + (f"、{n_junk:,} 个缩略图缓存" if n_junk else "")
              + (f"；{len(res.failed):,} 个失败" if res.failed else ""))
        for p, why in res.failed[:10]:
            print(f"  失败：{p}（{why}）")
        return 0 if not res.failed else 1

    if args.import_file:
        from . import loader      # 不能写 "import loader, report"：
                                  # 那会把 report 变成局部变量，遮蔽模块级导入

        ld = loader.load_result(Path(args.import_file),
                                reclassify=not args.no_reclassify, log=log)
        if not ld.ok:
            for w in ld.warnings:
                print(f"[错误] {w}")
            return 2
        for w in ld.warnings:
            print(f"[提示] {w}")
        print()
        print(report.summary_text(ld.result, top=args.top))
        if args.csv:
            n = report.write_csv(ld.result, Path(args.csv))
            print(f"\n已导出 CSV：{args.csv}（{_fmt_int(n)} 行）")
        if args.json:
            report.write_json(ld.result, Path(args.json), with_candidates=args.with_candidates)
            print(f"已导出 JSON：{args.json}")
        return 0

    if not args.scan:
        build_parser().print_help()
        print("\n提示：双击「小说查重.pyw」可打开图形界面（含删除功能）。")
        return 2

    root = Path(args.scan).expanduser()
    if not root.is_dir():
        print(f"[错误] 目录不存在：{root}")
        return 2

    from . import engine

    t0 = time.perf_counter()
    res = engine.scan(
        root,
        preset=args.preset,
        workers=(args.threads or None),
        drop_ads=not args.no_ad_filter,
        min_bytes=args.min_bytes,
        log=log,
        budget_mb=args.budget_mb,
        with_evidence=not args.no_evidence,
        full=args.full,
        t2s=args.t2s,
    )
    print()
    print(report.summary_text(res, top=args.top))

    if args.csv:
        n = report.write_csv(res, Path(args.csv))
        print(f"\n已导出 CSV：{args.csv}（{_fmt_int(n)} 行）")
    if args.json:
        report.write_json(res, Path(args.json), with_candidates=args.with_candidates)
        print(f"已导出 JSON：{args.json}")

    print(f"\n总用时 {time.perf_counter() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
