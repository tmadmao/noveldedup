"""清除缓存测试 —— 这是"删"的功能，必须验证只删缓存、不碰书。

覆盖：
  ① 扫描后缓存存在且体积 > 0
  ② 清本库缓存后：缓存目录消失、**书一本没少**
  ③ 清全部缓存：所有书库的缓存目录都没了
  ④ 传入一个不在缓存根下的路径，绝不能删（越界防护）
  ⑤ 清完再扫一次，结果和第一次一致（说明缓存是可重建的）

跑：py -3 tools/cache_clear_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import core, deleter, engine, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="nd_cache_"))
    a = selftest.make_book(40_000, 81)
    (tmp / "甲.txt").write_bytes(a.encode("gb18030"))
    (tmp / "甲_改名.txt").write_bytes(a.encode("utf-8"))
    (tmp / "乙.txt").write_bytes(selftest.make_book(40_000, 82).encode("gb18030"))
    print(f"临时书库：{tmp}\n")

    print("[1] 扫描并产生缓存")
    res1 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    cache_dir = core.cache_dir_for(tmp)
    size1 = core.dir_size_mb(cache_dir)
    print(f"  缓存：{cache_dir}")
    check("缓存目录存在", cache_dir.exists())
    check("缓存体积 > 0", size1 > 0, f"{size1:.1f} MB")
    check("有指纹文件", any((cache_dir / "fp").glob("*.bin")) if (cache_dir / "fp").exists() else False)
    pairs1 = len(res1.dup_pairs)
    books_on_disk = len(list(tmp.glob("*.txt")))

    print("\n[2] 清本库缓存")
    n, freed = deleter.clear_cache(tmp)
    check("清掉了 1 个书库的缓存", n == 1, f"{n} 个")
    check("缓存目录已消失", not cache_dir.exists())
    check("释放体积与之前相符", freed >= size1 * 0.5, f"{freed:.1f} MB（原 {size1:.1f}）")
    check("**书一本没少**", len(list(tmp.glob("*.txt"))) == books_on_disk,
          f"{len(list(tmp.glob('*.txt')))} 本")
    check("缓存根目录还在", (core.data_dir() / "cache").exists())

    print("\n[3] 越界防护")
    outside = Path(tempfile.mkdtemp(prefix="nd_outside_"))
    (outside / "重要.txt").write_text("别删我", encoding="utf-8")
    n2, _f2 = deleter.clear_cache(outside)      # 它不是缓存目录，只会算出缓存目录
    check("不会删用户目录", (outside / "重要.txt").exists())
    check("越界时不会报错", True, f"返回 {n2}")

    print("\n[4] 清完重扫，结果一致（缓存可重建）")
    res2 = engine.scan(tmp, preset="标准", workers=1, with_evidence=False)
    check("重扫结果与首次一致", len(res2.dup_pairs) == pairs1,
          f"{len(res2.dup_pairs)} vs {pairs1}")
    check("缓存又建起来了", core.dir_size_mb(cache_dir) > 0,
          f"{core.dir_size_mb(cache_dir):.1f} MB")

    print("\n[5] 清全部缓存")
    total_before = deleter.cache_total_mb()
    n3, freed3 = deleter.clear_cache(everything=True)
    check("全部缓存已清", deleter.cache_total_mb() < max(0.05, total_before * 0.05),
          f"清前 {total_before:.1f} MB → 清后 {deleter.cache_total_mb():.1f} MB")
    check("清掉了至少 1 个书库", n3 >= 1, f"{n3} 个 / {freed3:.1f} MB")
    check("书仍然一本没少", len(list(tmp.glob("*.txt"))) == books_on_disk)

    print("\n" + ("清除缓存测试：通过" if ok_all else "清除缓存测试：失败"))
    print(f"（临时目录：{tmp} / {outside}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
