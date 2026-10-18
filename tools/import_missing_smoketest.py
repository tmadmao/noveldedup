"""导入时「已删除的文件不能再出现」测试 —— 复现用户遇到的场景。

用户操作路径：
  扫描 → 导出 → 全库预勾选 → 删除所有重复项 → 再次导入 → **居然还能看到那些已删的书**

视频查重 / 漫画查重的做法是以磁盘为准：文件没了就不该出现在结果里。
本测试验证这条，并确认"导入后能直接清理空文件夹"这个衔接是通的。

跑：py -3 tools/import_missing_smoketest.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import deleter, engine, loader, report, selftest   # noqa: E402

ok_all = True


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_all
    ok_all &= bool(cond)
    print(f"  {'✅' if cond else '❌'} {label}" + (f"   {extra}" if extra else ""))


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="nd_miss_")).resolve()
    same = selftest.make_book(40_000, 91)
    other = selftest.make_book(40_000, 92)

    (root / "A").mkdir()
    (root / "A" / "甲.txt").write_bytes(same.encode("gb18030"))
    (root / "A" / "甲_副本.txt").write_bytes(same.encode("utf-8"))
    (root / "A" / "乙.txt").write_bytes(other.encode("gb18030"))
    (root / "B").mkdir()
    (root / "B" / "丙.txt").write_bytes(same.encode("gb18030"))   # 与甲重复，B 里只有它
    print(f"临时书库：{root}\n  A/：甲、甲_副本、乙　　B/：丙（与甲重复）\n")

    print("[1] 扫描并导出")
    res = engine.scan(root, preset="标准", workers=1, with_evidence=False)
    js = root / "结果.json"
    report.write_json(res, js, with_candidates=True)
    print(f"  {len(res.books)} 本 / {len(res.groups)} 组 / {len(res.dup_pairs)} 对")

    print("\n[2] 模拟用户：删掉重复文件（甲_副本、丙）")
    (root / "A" / "甲_副本.txt").unlink()
    (root / "B" / "丙.txt").unlink()
    print(f"  删除后 A/：{sorted(p.name for p in (root / 'A').iterdir())}")
    print(f"  B/ 里的文件数：{len(list((root / 'B').iterdir()))}（已空）")

    print("\n[3] 再次导入 —— 已删的文件不该出现")
    ld = loader.load_result(js)
    check("导入成功", ld.ok, "；".join(ld.warnings))
    if not ld.ok:
        return 1
    names = {b.name for b in ld.result.books.values()}
    print(f"  导入后：{sorted(names)}")
    check("**已删文件不再出现**", names == {"甲.txt", "乙.txt"}, str(sorted(names)))
    check("剔除了 2 个", ld.missing == 2, f"{ld.missing} 个")
    check("有提示说明剔除了", any("已不在磁盘" in w for w in ld.warnings),
          "；".join(ld.warnings))
    check("剩余书不再互相重复", len(ld.result.dup_pairs) == 0,
          f"{len(ld.result.dup_pairs)} 对")

    print("\n[4] 导入后直接清理空文件夹（用户的下一步动作）")
    dirs, junk = deleter.find_empty_dirs(root)
    names_d = sorted(d.name for d in dirs)
    print(f"  找到空文件夹：{names_d}")
    check("B 被识别为空文件夹", "B" in names_d, str(names_d))
    check("A 不是空的，不该被删", "A" not in names_d, str(names_d))
    check("书库根目录不在列表里", root.name not in names_d, str(names_d))

    print("\n[5] 全部文件都不在本机时（换机器打开）要降级而不是空白")
    js2 = root / "别处的结果.json"
    report.write_json(res, js2, with_candidates=False)
    import json
    data = json.loads(js2.read_text(encoding="utf-8"))
    for row in data["books_all"]:                 # 伪造成另一个盘上的路径
        row["path"] = row["path"].replace(str(root), str(root) + "_不存在")
    for g in data["groups"]:
        for m in g["members"]:
            m["path"] = m["path"].replace(str(root), str(root) + "_不存在")
    js2.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    ld2 = loader.load_result(js2)
    check("仍能加载（视图模式）", ld2.ok and len(ld2.result.books) > 0,
          f"{len(ld2.result.books) if ld2.ok else 0} 本")
    check("明确告知不能删除", any("不在本机" in w for w in ld2.warnings),
          "；".join(ld2.warnings)[:60])

    print("\n" + ("导入剔除已删文件测试：通过" if ok_all else "导入剔除已删文件测试：失败"))
    print(f"（临时目录：{root}）")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
