"""用上次导出的 JSON **重放**新判据与分组规则（不重新扫描）。

为什么需要这个工具
------------------
真实书库扫一遍 40 分钟。改判据时如果每次都重扫，一轮验证要一小时。
导出的 JSON 里已经存了每对书的完整指标（jac / 包含度 / 覆盖率 / 链长 / 共享数），
足够离线重跑 `classify()` 和 `group.build()` —— 只会**减少**边、不会新增边，
所以能立刻回答"这次改动会不会把误判消灭掉、会不会误伤真重复"。

用法：
    py -3 tools/replay_json.py 重复清单.json
    py -3 tools/replay_json.py 重复清单.json --focus 关键词1 关键词2
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from noveldedup import group as group_mod   # noqa: E402
from noveldedup.judge import NO_REL, PairStat, classify   # noqa: E402


@dataclass
class FakeBook:
    """只带分组/建议保留需要的字段（从 JSON members 里还原）。"""

    id: int
    path: str
    chapters: int = 0
    norm_chars: int = 0
    word_chars: int = 0
    size: int = 0
    ad_lines: int = 0
    enc: str = ""
    fmt: str = "txt"
    l1: str = ""
    file_hash: str = ""
    status: str = "ok"
    note: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def ok(self) -> bool:
        return True


def load(path: Path):
    d = json.loads(path.read_text(encoding="utf-8"))
    books: dict[int, FakeBook] = {}
    id_of: dict[str, int] = {}
    for g in d["groups"]:
        for m in g["members"]:
            p = m["path"]
            if p not in id_of:
                bid = len(id_of) + 1
                id_of[p] = bid
                books[bid] = FakeBook(
                    id=bid, path=p,
                    chapters=int(m.get("chapters") or 0),
                    norm_chars=int(m.get("cjk_chars") or 0),
                    word_chars=int(m.get("chars") or 0),
                    size=int(m.get("size") or 0),
                    ad_lines=int(m.get("ad_lines") or 0),
                    enc=m.get("enc") or "",
                    fmt=m.get("fmt") or "txt")
    stats: list[PairStat] = []
    for g in d["groups"]:
        for p in g["pairs"]:
            a, b = id_of.get(p["a"]), id_of.get(p["b"])
            if not a or not b:
                continue
            stats.append(PairStat(
                a=a, b=b,
                shared=int(p.get("shared_fingerprints") or 0),
                raw_shared=int(p.get("shared_fingerprints") or 0),
                jac=float(p.get("jaccard") or 0),
                cont_ab=min(1.0, float(p.get("containment_ab") or 0)),
                cont_ba=min(1.0, float(p.get("containment_ba") or 0)),
                chain=int(p.get("chain") or 0),
                cov_a=float(p.get("coverage_a") or 0),
                cov_b=float(p.get("coverage_b") or 0),
                rel=p.get("relation") or ""))
    return d, books, stats


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    path = Path(argv[1])
    focus: list[str] = []
    if "--focus" in argv:
        focus = argv[argv.index("--focus") + 1:]

    d, books, stats = load(path)
    old_groups = d["groups"]
    print(f"{path}：{len(books):,} 本 / {len(old_groups):,} 组 / {len(stats):,} 条边")
    print(f"档位 {d.get('preset')}  保证 {d.get('guarantee_chars')} 字\n")

    old_sizes = sorted((len(g["members"]) for g in old_groups), reverse=True)
    print("【重放前】")
    print(f"  组数 {len(old_groups):,}  最大组 {old_sizes[0]:,} 本  "
          f"≥100 本的组 {sum(1 for s in old_sizes if s >= 100)}")
    old_rel = Counter(p["relation"] for g in old_groups for p in g["pairs"])
    for rel, n in old_rel.most_common():
        print(f"    {rel:<24}{n:>7,}")

    # ---- 重放：用新判据重新打标签
    kept: list[PairStat] = []
    dropped = Counter()
    for s in stats:
        s.rel = classify(s, 0, 0, d.get("window") or 40)
        if s.rel == NO_REL:
            dropped["判为无关"] += 1
            continue
        kept.append(s)

    print("\n【重放后】")
    print(f"  边数 {len(stats):,} → {len(kept):,}（移除 {len(stats) - len(kept):,}）")
    new_rel = Counter(s.rel for s in kept)
    for rel, n in new_rel.most_common():
        print(f"    {rel:<24}{n:>7,}")

    groups = group_mod.build(kept, books)
    sizes = sorted((g.size for g in groups), reverse=True)
    print(f"\n  组数 {len(groups):,}  成员 {sum(sizes):,} 本  "
          f"最大组 {sizes[0] if sizes else 0} 本  "
          f"≥100 本的组 {sum(1 for s in sizes if s >= 100)}")
    print(f"  组大小 Top8：{sizes[:8]}")

    if focus:
        print("\n【重点检查】")
        for kw in focus:
            hits = [(s, books[s.a].name, books[s.b].name) for s in kept
                    if kw in books[s.a].name or kw in books[s.b].name]
            print(f"  「{kw}」幸存 {len(hits)} 条")
            for s, a, b in hits[:8]:
                print(f"    {s.rel:<22} jac={s.jac:.4f}  {a[:26]} ↔ {b[:26]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
