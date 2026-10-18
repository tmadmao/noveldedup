"""繁简开关测试。

两种环境都覆盖：
- 装了 opencc：真做「简体 → 繁体」两份，验证开/关的判定差异
- 没装 opencc：验证不崩、能降级（这是"可选依赖"必须做到的事）

跑：py -3 tools/t2s_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core, engine, normalize   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def make_book(n: int, seed: int) -> str:
    """带章节、用词日常的小说正文（词汇要比 selftest 的合成语料分散）。"""
    import random

    rnd = random.Random(seed)
    words = ("他走进屋子的时候天已经黑了 窗外的风声像有人在低声说话 桌上放着一封没有署名的信 "
             "她抬起头看了我一眼 什么也没有说 门口的狗忽然叫了起来 远处传来一阵急促的脚步声 "
             "这个消息让所有人都愣住了 我们得马上离开这里 夜色里什么都看不清").split()
    out = []
    for c in range(1, n // 800 + 2):
        out.append(f"第{c}章 第{c}回")
        for _ in range(8):
            out.append("，".join(rnd.choice(words) for _ in range(6)) + "。")
    return "\n".join(out)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_t2s_"))
    print(f"临时书库：{tmp}\n")

    simp = make_book(40_000, 5)
    has_opencc = normalize.t2s_available()
    if has_opencc:
        from opencc import OpenCC

        trad = OpenCC("s2t").convert(simp)
        print("环境：已安装 opencc，用真实转换生成繁体版")
    else:
        trad = simp.translate(str.maketrans({
            "这": "這", "个": "個", "们": "們", "来": "來", "为": "為", "说": "說",
            "时": "時", "会": "會", "对": "對", "后": "後", "开": "開", "发": "發",
            "现": "現", "点": "點", "电": "電", "门": "門"}))
        print("环境：未安装 opencc，用替身映射模拟繁体版")

    diff = sum(1 for a, b in zip(simp, trad) if a != b)
    print(f"简体 {len(simp):,} 字，繁体版 {diff:,} 字不同（{diff / len(simp):.1%}）\n")
    (tmp / "简体版.txt").write_bytes(simp.encode("gb18030"))
    (tmp / "繁体版.txt").write_bytes(trad.encode("utf-8"))

    print("[1] 关闭繁简（默认）")
    r1 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False, full=True)
    check("不判为重复", len(r1.dup_pairs) == 0, f"{len(r1.dup_pairs)} 对")

    print("\n[2] 打开繁简")
    r2 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False, full=True, t2s=True)
    if has_opencc:
        check("判为同一本", len(r2.dup_pairs) == 1, f"{len(r2.dup_pairs)} 对")
        if r2.dup_pairs:
            st = r2.dup_pairs[0]
            check("关系是「同一版本」", st.rel.startswith("同一版本"),
                  f"{st.rel}  jac={st.jac:.3f}")
            check("Jaccard 接近 1", st.jac > 0.99, f"{st.jac:.4f}")
    else:
        check("没有 opencc 时不崩", r2 is not None and len(r2.books) == 2, f"{len(r2.books)} 本")

    print("\n[3] 繁简规则变了，缓存必须失效")
    check("NORM_VER 与开关挂钩", core.norm_ver(True) != core.norm_ver(False),
          f"{core.norm_ver(False)} vs {core.norm_ver(True)}")

    print("\n[4] 缺失/损坏 opencc 时能降级")
    normalize._CONV["c"] = None          # 强制模拟"装了但不可用"
    try:
        r4 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False,
                         full=True, t2s=True)
        check("降级后仍能跑完", r4 is not None and len(r4.books) == 2, f"{len(r4.books)} 本")
        check("降级后按「不转换」处理（不判重）", len(r4.dup_pairs) == 0,
              f"{len(r4.dup_pairs)} 对")
    finally:
        normalize._CONV.pop("c", None)

    print("\n" + ("繁简开关测试：通过" if ok_all else "繁简开关测试：失败"))
    print(f"（临时目录：{tmp}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
