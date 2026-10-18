"""增量扫描测试：**增量结果必须和全量重扫逐对一致**。

这是增量功能唯一的验收标准。只要有一条边漏了，用户就会以为"没有重复"。

步骤：
1. 10 本书全量扫描（含 2 对重复）
2. 加 2 本（1 本与已有书内容相同、1 本全新）→ 再扫（应走增量）
3. 把同一个目录复制一份、用干净缓存全量扫 → 与增量结果逐对比较
4. 一个文件都不改再扫一次（应走"全命中缓存"路径）
5. 删掉一本书再扫（它的边应被清理）

跑：py -3 tools/incremental_smoketest.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import engine, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def pairs_of(res) -> set[tuple[str, str, str]]:
    names = {b.id: b.name for b in res.books.values()}
    out = set()
    for s in res.dup_pairs:
        a, b = names.get(s.a, "?"), names.get(s.b, "?")
        out.add((a, b, s.rel) if a <= b else (b, a, s.rel))
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_inc_"))
    print(f"临时书库：{tmp}\n")

    # ① 10 本：A/B 是同一本的两个副本；C 是另一本；其余 7 本互不相关
    base = selftest.make_book(40_000, 11)
    (tmp / "A1.txt").write_bytes(base.encode("gb18030"))
    (tmp / "A2.txt").write_bytes(base.encode("utf-8"))
    other = selftest.make_book(40_000, 12)
    (tmp / "C1.txt").write_bytes(other.encode("gb18030"))
    (tmp / "C2.txt").write_bytes(other.encode("gb18030"))
    for i in range(7):
        (tmp / f"Z{i}.txt").write_bytes(selftest.make_book(40_000, 100 + i).encode("utf-8"))

    print("[1] 首次全量扫描")
    t0 = time.perf_counter()
    res1 = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    t1 = time.perf_counter() - t0
    check("首扫 11 本", len(res1.books) == 11, f"实际 {len(res1.books)}")
    check("首扫发现 2 对重复", len(res1.dup_pairs) == 2,
          f"实际 {len(res1.dup_pairs)}：{sorted(pairs_of(res1))}")
    print(f"     用时 {t1:.2f}s，{len(res1.groups)} 组")

    # ② 加两本：一本与 A 内容相同（换名），一本全新
    (tmp / "A3_新副本.txt").write_bytes(base.encode("gb18030"))
    (tmp / "D_新书.txt").write_bytes(selftest.make_book(40_000, 13).encode("utf-8"))

    print("\n[2] 加 2 本后增量扫描")
    t0 = time.perf_counter()
    res2 = engine.scan(tmp, preset="标准", workers=1, with_evidence=True)
    t2 = time.perf_counter() - t0
    check("增量后 13 本", len(res2.books) == 13, f"实际 {len(res2.books)}")
    inc_pairs = pairs_of(res2)
    check("发现 A 的三副本", sum(1 for p in inc_pairs if "A" in p[0] and "A" in p[1]) == 3,
          f"{sorted(p for p in inc_pairs if 'A' in p[0])}")
    print(f"     用时 {t2:.2f}s（首扫 {t1:.2f}s）")

    # ③ 同一目录复制一份，用干净缓存全量扫，逐对比对
    print("\n[3] 全量重扫对照（干净缓存）")
    mirror = Path(tempfile.mkdtemp(prefix="nd_inc_full_"))
    for p in tmp.iterdir():
        shutil.copy2(p, mirror / p.name)
    res3 = engine.scan(mirror, preset="标准", workers=1, with_evidence=False)
    full_pairs = pairs_of(res3)
    check("增量与全量的重复对**完全一致**", inc_pairs == full_pairs,
          f"增量 {len(inc_pairs)} / 全量 {len(full_pairs)}"
          + ("" if inc_pairs == full_pairs
             else f"\n      只出现在增量：{sorted(inc_pairs - full_pairs)}"
                  f"\n      只出现在全量：{sorted(full_pairs - inc_pairs)}"))
    g_inc = sorted(sorted(res2.books[m].name for m in g.members if m in res2.books)
                   for g in res2.groups)
    g_full = sorted(sorted(res3.books[m].name for m in g.members if m in res3.books)
                    for g in res3.groups)
    check("分组结构一致", g_inc == g_full, f"{len(g_inc)} 组 vs {len(g_full)} 组")

    # ④ 不改任何文件再扫（应走"全命中缓存"）
    print("\n[4] 无变化重扫（应完全不碰指纹）")
    t0 = time.perf_counter()
    res4 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    t4 = time.perf_counter() - t0
    check("无变化重扫结果一致", pairs_of(res4) == inc_pairs,
          f"{len(pairs_of(res4))} 对")
    check("无变化重扫明显更快", t4 < max(1.0, t2),
          f"{t4:.2f}s（上次 {t2:.2f}s）")

    # ④b 按需证据：无变化重扫没预存证据，点开某一对应能现算出来
    print("\n[4b] 按需证据（增量后老的重复对没有预存证据）")
    if res4.dup_pairs:
        st = res4.dup_pairs[0]
        check("重扫结果里确实没有预存证据", not res4.segs, f"{len(res4.segs)} 条")
        segs = engine.evidence_for(res4, st.a, st.b)
        check("按需算出了证据段", bool(segs), f"{len(segs)} 段")
        if segs:
            print(f"     首段：{segs[0].readable}")

    # ⑤ 删一本书（模拟用户在资源管理器里删）→ 它的边应被清掉
    print("\n[5] 删掉一本书后重扫")
    (tmp / "A3_新副本.txt").unlink()
    res5 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    left = pairs_of(res5)
    check("结果回到 12 本", len(res5.books) == 12, f"实际 {len(res5.books)}")
    check("被删书的边已清理", not any("A3_新副本" in p[0] or "A3_新副本" in p[1] for p in left),
          str(sorted(p for p in left if "A3" in str(p))))

    print("\n" + ("增量扫描测试：通过" if ok_all else "增量扫描测试：失败"))
    print(f"（临时目录：{tmp} / {mirror}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
