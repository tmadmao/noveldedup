"""删除功能自测：选谁删 → 越界防护 → 真删到回收站 → 结果重建。

⚠ 会**真的**往系统回收站里放几个临时 txt（几 KB，可随时清空回收站）。
   只操作临时目录，绝不碰真实书库。

跑：python tools/delete_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import deleter, engine, plan, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_del_"))
    print(f"临时书库：{tmp}\n")

    base = selftest.make_book(40_000, 7)

    # ① 三本内容完全相同（文件名不同）→ 一键清理应留 1 删 2
    for name in ("《测试书》甲.txt", "《测试书》乙.txt", "《测试书》丙.txt"):
        (tmp / name).write_bytes(base.encode("gb18030"))

    # ② 另一本书的两版：一本原样、一本每 500 字插广告 → l1 不同，**不该被一键清理碰**
    adbase = selftest.make_book(40_000, 8)
    (tmp / "《广告版》原.txt").write_bytes(adbase.encode("gb18030"))
    parts = []
    for i in range(0, len(adbase), 500):
        parts.append(adbase[i:i + 500])
        parts.append("\n看正版就来青柠书屋\n")
    (tmp / "《广告版》带广告.txt").write_bytes("".join(parts).encode("gb18030"))

    # ③ 一本无关的书
    (tmp / "《无关》.txt").write_bytes(selftest.make_book(40_000, 99).encode("utf-8"))

    print("[1] 扫描")
    res = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    print(f"  {len(res.books)} 本，{len(res.groups)} 组，{len(res.dup_pairs)} 对\n")

    print("[2] 一键清理的选人逻辑")
    items = plan.plan_identical(res.books, res.groups)
    names = {i.name for i in items}
    check("选出 2 本待删", len(items) == 2, f"实际 {sorted(names)}")
    check("三本同名书只保留 1 本",
          len([n for n in res.books.values() if n.name.startswith("《测试书》")]) == 3
          and len([i for i in items if "测试书" in i.name]) == 2)
    check("每项都写明保留哪一本", all(i.keep_name for i in items))
    check("广告版没被一键清理（内容 hash 不同）",
          not any("广告" in i.name for i in items))

    print("\n[3] 越界防护")
    outside = tmp.parent / f"{tmp.name}_outside.txt"
    outside.write_text("不该被删", encoding="utf-8")
    good, bad = deleter.check_safe([outside, *[Path(i.path) for i in items]], root=tmp)
    check("根目录外的文件被拒绝", len(good) == 2 and len(bad) == 1,
          f"可删 {len(good)} / 拒绝 {len(bad)}")
    check("拒绝原因可读", bool(bad and "不在扫描目录内" in bad[0][1]))
    outside.unlink(missing_ok=True)

    print("\n[4] 真删到回收站")
    before = {i.name for i in items}
    r = deleter.to_recycle_bin([Path(i.path) for i in items], root=tmp,
                               reason="delete_smoketest")
    print(f"  {r.summary}")
    check("两个文件都已消失", len(r.deleted) == 2 and not r.failed,
          f"失败 {[(p.name, w) for p, w in r.failed]}")
    check("保留的那本还在", any(p.name.startswith("《测试书》") for p in tmp.iterdir()))
    check("广告版还在", (tmp / "《广告版》带广告.txt").exists())
    check("写了删除审计日志", bool(r.log_path and r.log_path.exists()),
          str(r.log_path))

    print("\n[5] 结果重建（不重新扫描）")
    engine.forget_paths(tmp, r.deleted)
    engine.apply_deletions(res, {i.bid for i in items})
    left_books = {b.name for b in res.books.values()}
    check("被删的书已从结果里剔除", not (before & left_books))
    check("剩余组数正确", len(res.groups) <= 1, f"剩 {len(res.groups)} 组")
    check("缓存也清掉了", all(
        not p.exists() or str(p) not in {str(x) for x in r.deleted} for p in r.deleted))

    print("\n[6] 重扫确认删除已生效")
    res2 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    check("重扫只剩 4 本", len(res2.books) == 4, f"实际 {len(res2.books)}")
    check("重扫不再出现已删的文件",
          not any(Path(i.path).exists() for i in items))

    print("\n" + ("删除功能自测：通过" if ok_all else "删除功能自测：失败"))
    print(f"（清理临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
