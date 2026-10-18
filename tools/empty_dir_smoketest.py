"""空文件夹清理测试 —— 这是"删"的功能，安全边界必须逐条验证。

覆盖：
  ① 级联空目录（a/b/c 全空）应按由深到浅全部列出
  ② 只有 Thumbs.db / desktop.ini 的目录也算空，且垃圾文件一并清掉
  ③ 非空目录绝不能被删
  ④ 扫描根目录本身绝不能被删
  ⑤ 真删之后：空目录消失、非空目录还在、根目录还在
  ⑥ dry-run 不动任何东西

跑：py -3 tools/empty_dir_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import deleter   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nd_dir_")).resolve()
    print(f"临时书库：{root}\n")

    # ① 级联空目录
    deep = root / "A" / "B" / "C"
    deep.mkdir(parents=True)
    # ② 只有垃圾文件的目录
    junkdir = root / "J"
    junkdir.mkdir()
    (junkdir / "Thumbs.db").write_bytes(b"x" * 100)
    (junkdir / "desktop.ini").write_text("[.ShellClassInfo]", encoding="utf-8")
    # ③ 非空目录
    keepdir = root / "K"
    keepdir.mkdir()
    (keepdir / "书.txt").write_text("正文", encoding="utf-8")
    # ④ 混合：空目录里再放一个非空子目录 → 父目录不该被判空
    mix = root / "M"
    (mix / "sub").mkdir(parents=True)
    (mix / "sub" / "书.txt").write_text("正文", encoding="utf-8")

    print("[1] 找出空目录")
    dirs, junk = deleter.find_empty_dirs(root)
    names = [d.name for d in dirs]
    print(f"  目录：{names}")
    print(f"  垃圾：{[j.name for j in junk]}")
    check("找到 C/B/A 三个级联空目录", {"A", "B", "C"} <= set(names), str(names))
    check("由深到浅（C 在 A 前面）",
          names.index("C") < names.index("B") < names.index("A")
          if {"A", "B", "C"} <= set(names) else False, str(names))
    check("只有 Thumbs.db 的目录也算空", "J" in names, str(names))
    check("垃圾文件被识别", len(junk) == 2, f"{len(junk)} 个")
    check("非空目录不在列表里", "K" not in names and "M" not in names, str(names))
    check("扫描根目录不在列表里", root not in dirs and root.name not in names, str(names))

    print("\n[2] dry-run 不动任何东西")
    r0 = deleter.clean_empty_dirs(root, dry_run=True)
    check("dry-run 列出了待删项", len(r0.deleted) == len(dirs), f"{len(r0.deleted)} 个")
    check("dry-run 后目录都还在", deep.exists() and junkdir.exists())

    print("\n[3] 安全检查")
    good, bad = deleter.check_safe([root], root=root, allow_dirs=True)
    check("根目录被拒绝", not good and any("根目录" in why for _p, why in bad),
          str([w for _p, w in bad]))
    # 「空不空」是在**删除前逐个校验**的（级联删除必须如此），所以这里直接试删
    r_bad = deleter.to_recycle_bin([keepdir], root=root, allow_dirs=True)
    check("非空目录删不掉", not r_bad.deleted
          and any("不是空目录" in why for _p, why in r_bad.failed),
          str([(p.name, w) for p, w in r_bad.failed]))
    check("非空目录里的文件安然无恙", (keepdir / "书.txt").exists())
    good, bad = deleter.check_safe([deep], root=root, allow_dirs=False)
    check("不允许删目录时一律拒绝", not good, str([w for _p, w in bad]))
    outside = root.parent / f"{root.name}_外面"
    outside.mkdir(exist_ok=True)
    good, bad = deleter.check_safe([outside], root=root, allow_dirs=True)
    check("根目录外的目录被拒绝", not good and any("不在扫描目录内" in w for _p, w in bad),
          str([w for _p, w in bad]))
    outside.rmdir()

    print("\n[4] 真删")
    res = deleter.to_recycle_bin([*junk, *dirs], root=root, log=None,
                                 reason="empty_dir_smoketest", allow_dirs=True)
    print(f"  {res.summary}")
    check("全部成功", not res.failed, str([(p.name, w) for p, w in res.failed]))
    check("级联空目录已消失", not (root / "A").exists())
    check("垃圾目录已消失", not junkdir.exists())
    check("非空目录还在", (keepdir / "书.txt").exists())
    check("混合目录还在", (mix / "sub" / "书.txt").exists())
    check("扫描根目录还在", root.exists() and root.is_dir())

    print("\n[5] 再查一遍应为空")
    dirs2, junk2 = deleter.find_empty_dirs(root)
    check("没有剩余空目录", not dirs2, f"{[d.name for d in dirs2]}")

    print("\n[6] 全空的根目录：只清子目录，根不动")
    solo = Path(tempfile.mkdtemp(prefix="nd_dir2_")).resolve()
    (solo / "X" / "Y").mkdir(parents=True)
    r = deleter.clean_empty_dirs(solo)
    check("子目录被清掉", not (solo / "X").exists(), f"删了 {len(r.deleted)} 个")
    check("根目录保留", solo.exists())

    print("\n" + ("空文件夹清理测试：通过" if ok_all else "空文件夹清理测试：失败"))
    print(f"（临时目录：{root} / {solo}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
