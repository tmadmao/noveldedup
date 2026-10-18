"""缓存：SQLite 元信息 + 每本一个指纹二进制文件。

为什么要缓存
------------
近一亿字全量算一遍指纹约 90 秒；上万本书时，重复扫描是不可接受的。
缓存按 (路径, 大小, mtime, FEAT_VER, NORM_VER) 判定是否可复用，
换档位（改 FEAT_VER）或改归一化规则（改 NORM_VER）会自动失效重算。

为什么不用 SQLite 存指纹
------------------------
1000 万条指纹在 SQLite 里是上千万行、GB 级文件、还要维护 B-tree。
改成"每本一个 uint32 排序数组"后，顺序写、mmap 读，索引体积只剩原文字数的 1.7%。
"""

from __future__ import annotations

import sqlite3
from array import array
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

FP_MAGIC = b"NDFP"
FP_VERSION = 1  # 指纹文件格式版本（与算法无关）


@dataclass
class Book:
    id: int = 0
    path: str = ""
    size: int = 0
    mtime: float = 0.0
    enc: str = ""
    fmt: str = "txt"
    raw_chars: int = 0
    norm_chars: int = 0
    word_chars: int = 0   # 非空白字符数（= 起点/阅文"总字数"口径：汉字+标点+数字）
    cjk: float = 0.0
    common: float = 0.0
    chapters: int = 0
    ad_lines: int = 0
    fps: int = 0
    feat_ver: str = ""
    norm_ver: str = ""
    l1: str = ""
    file_hash: str = ""
    status: str = "ok"
    note: str = ""
    scanned_at: str = ""

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def ok(self) -> bool:
        """完全正常：无告警。"""
        return self.status == "ok"

    @property
    def usable(self) -> bool:
        """内容确实解析出来了，可以参与"能不能删"的判断。

        `ok` 与 `suspect` 都算可用：
          - suspect 只是**判据可靠性**的提示（短文本 / 编码可疑），
            不代表文件内容不可比。两个**一字不差**的短篇，仍然应当能被一键清理。
          - skip / error 没有文本、也没有 l1，天然进不了任何重复组。
        """
        return self.status in ("ok", "suspect")

    @property
    def short(self) -> bool:
        """短文本（判据不稳，需要提示用户）。"""
        return self.note.startswith("短文本") or "；短文本" in self.note


_SCHEMA = """
CREATE TABLE IF NOT EXISTS books(
  id INTEGER PRIMARY KEY,
  path TEXT UNIQUE NOT NULL,
  size INTEGER, mtime REAL,
  enc TEXT, fmt TEXT, raw_chars INTEGER, norm_chars INTEGER,
  cjk REAL, common REAL, chapters INTEGER, ad_lines INTEGER,
  fps INTEGER,
  feat_ver TEXT, norm_ver TEXT,
  l1 TEXT, file_hash TEXT,
  status TEXT, note TEXT,
  scanned_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_books_feat ON books(feat_ver, norm_ver);
CREATE TABLE IF NOT EXISTS edges(
  a INTEGER NOT NULL, b INTEGER NOT NULL,
  shared INTEGER, jac REAL, cont_ab REAL, cont_ba REAL, chain INTEGER,
  cov_a REAL, cov_b REAL, seg_a INTEGER, seg_b INTEGER,
  rel TEXT, feat_ver TEXT, norm_ver TEXT, judge_ver TEXT,
  PRIMARY KEY(a, b)
);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


class Cache:
    def __init__(self, cache_dir: Path):
        self.dir = cache_dir
        self.fp_dir = cache_dir / "fp"
        self.fp_dir.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(cache_dir / "books.db"))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        # 老缓存升级：补上 word_chars 列
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(books)")}
        for ddl in ("ALTER TABLE books ADD COLUMN word_chars INTEGER DEFAULT 0",
                    "ALTER TABLE books ADD COLUMN fmt TEXT",
                    "ALTER TABLE edges ADD COLUMN judge_ver TEXT"):
            col = ddl.split("ADD COLUMN ")[1].split()[0]
            if col not in cols:
                try:
                    self.conn.execute(ddl)
                except sqlite3.OperationalError:
                    pass
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------ 元信息

    def get(self, path: str) -> Book | None:
        r = self.conn.execute("SELECT * FROM books WHERE path=?", (path,)).fetchone()
        return self._row(r) if r else None

    def fresh(self, path: str, size: int, mtime: float, feat_ver: str, norm_ver: str) -> Book | None:
        """缓存是否可直接复用（文件没变 + 算法版本一致 + 上次成功）。"""
        b = self.get(path)
        if not b:
            return None
        if b.status != "ok":
            return None
        if b.size != size or abs(b.mtime - mtime) > 1e-6:
            return None
        if b.feat_ver != feat_ver or b.norm_ver != norm_ver:
            return None
        if b.word_chars <= 0:      # 老缓存没有这一列 → 重算一次
            return None
        if not (self.fp_dir / f"{b.id}.bin").exists():
            return None
        return b

    def upsert(self, b: Book) -> int:
        now = b.scanned_at or datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            """INSERT INTO books(path,size,mtime,enc,fmt,raw_chars,norm_chars,word_chars,cjk,common,
                                 chapters,ad_lines,fps,feat_ver,norm_ver,l1,file_hash,
                                 status,note,scanned_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(path) DO UPDATE SET
                 size=excluded.size, mtime=excluded.mtime, enc=excluded.enc, fmt=excluded.fmt,
                 raw_chars=excluded.raw_chars, norm_chars=excluded.norm_chars,
                 word_chars=excluded.word_chars,
                 cjk=excluded.cjk, common=excluded.common, chapters=excluded.chapters,
                 ad_lines=excluded.ad_lines, fps=excluded.fps,
                 feat_ver=excluded.feat_ver, norm_ver=excluded.norm_ver,
                 l1=excluded.l1, file_hash=excluded.file_hash,
                 status=excluded.status, note=excluded.note, scanned_at=excluded.scanned_at""",
            (b.path, b.size, b.mtime, b.enc, b.fmt, b.raw_chars, b.norm_chars,
             b.word_chars, b.cjk, b.common,
             b.chapters, b.ad_lines, b.fps, b.feat_ver, b.norm_ver, b.l1, b.file_hash,
             b.status, b.note, now),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT id FROM books WHERE path=?", (b.path,)).fetchone()
        b.id = int(row["id"]) if row else int(cur.lastrowid or 0)
        return b.id

    def all_books(self, feat_ver: str | None = None, norm_ver: str | None = None) -> list[Book]:
        sql = "SELECT * FROM books"
        args: list = []
        if feat_ver and norm_ver:
            sql += " WHERE feat_ver=? AND norm_ver=?"
            args = [feat_ver, norm_ver]
        sql += " ORDER BY id"
        return [self._row(r) for r in self.conn.execute(sql, args)]

    def drop(self, path: str) -> None:
        r = self.conn.execute("SELECT id FROM books WHERE path=?", (path,)).fetchone()
        if r:
            fid = int(r["id"])
            self.conn.execute("DELETE FROM books WHERE id=?", (fid,))
            self.conn.commit()
            try:
                (self.fp_dir / f"{fid}.bin").unlink()
            except OSError:
                pass

    @staticmethod
    def _row(r: sqlite3.Row) -> Book:
        return Book(
            id=int(r["id"]), path=r["path"], size=int(r["size"] or 0),
            mtime=float(r["mtime"] or 0), enc=r["enc"] or "",
            fmt=(r["fmt"] or "txt") if "fmt" in r.keys() else "txt",
            raw_chars=int(r["raw_chars"] or 0), norm_chars=int(r["norm_chars"] or 0),
            word_chars=int(r["word_chars"] or 0) if "word_chars" in r.keys() else 0,
            cjk=float(r["cjk"] or 0), common=float(r["common"] or 0),
            chapters=int(r["chapters"] or 0), ad_lines=int(r["ad_lines"] or 0),
            fps=int(r["fps"] or 0), feat_ver=r["feat_ver"] or "", norm_ver=r["norm_ver"] or "",
            l1=r["l1"] or "", file_hash=r["file_hash"] or "",
            status=r["status"] or "ok", note=r["note"] or "", scanned_at=r["scanned_at"] or "",
        )

    # ------------------------------------------------------------ 关系边（增量扫描用）

    def _meta_get(self, k: str) -> str:
        r = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r["v"] if r else ""

    def _meta_set(self, k: str, v: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES(?,?)", (k, v))
        self.conn.commit()

    @staticmethod
    def _edge_key(feat: str, norm_ver: str, judge_ver: str = "") -> str:
        return f"edges_ok:{feat}:{norm_ver}:{judge_ver}"

    def edges_complete(self, feat: str, norm_ver: str, judge_ver: str = "") -> bool:
        """上一轮是否**完整**跑完并落盘了关系表。

        ⚠ 这个标志不能省：如果进程在 Pass2 中途被杀，关系表里只有一部分边，
          下次增量扫描会把它当"完整"用 → **静默漏掉一部分重复**。
          宁可多花一次全量扫描，也不能让用户以为查过了。
        """
        return self._meta_get(self._edge_key(feat, norm_ver, judge_ver)) == "1"

    def mark_edges(self, feat: str, norm_ver: str, complete: bool,
                   judge_ver: str = "") -> None:
        self._meta_set(self._edge_key(feat, norm_ver, judge_ver), "1" if complete else "0")

    def load_edges(self, feat: str, norm_ver: str, judge_ver: str = "") -> list:
        from .judge import PairStat

        out = []
        for r in self.conn.execute(
                "SELECT * FROM edges WHERE feat_ver=? AND norm_ver=? AND judge_ver=?",
                (feat, norm_ver, judge_ver)):
            out.append(PairStat(
                a=int(r["a"]), b=int(r["b"]), shared=int(r["shared"] or 0),
                jac=float(r["jac"] or 0), cont_ab=float(r["cont_ab"] or 0),
                cont_ba=float(r["cont_ba"] or 0), chain=int(r["chain"] or 0),
                cov_a=float(r["cov_a"] or 0), cov_b=float(r["cov_b"] or 0),
                seg_a=int(r["seg_a"] or 0), seg_b=int(r["seg_b"] or 0),
                rel=r["rel"] or ""))
        return out

    def save_edges(self, stats, feat: str, norm_ver: str, judge_ver: str = "") -> int:
        """写入（覆盖）本轮算出的关系边；顺手清掉**其它档位或其它判据版本**的旧边。"""
        self.conn.execute(
            "DELETE FROM edges WHERE feat_ver<>? OR norm_ver<>? OR "
            "judge_ver IS NULL OR judge_ver<>?", (feat, norm_ver, judge_ver))
        rows = [((s.a, s.b) if s.a < s.b else (s.b, s.a)) +
                (s.shared, s.jac, s.cont_ab, s.cont_ba, s.chain,
                 s.cov_a, s.cov_b, s.seg_a, s.seg_b, s.rel, feat, norm_ver, judge_ver)
                for s in stats]
        if rows:
            self.conn.executemany(
                "INSERT OR REPLACE INTO edges(a,b,shared,jac,cont_ab,cont_ba,chain,"
                "cov_a,cov_b,seg_a,seg_b,rel,feat_ver,norm_ver,judge_ver) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()
        return len(rows)

    def drop_edges(self, ids: set[int], feat: str, norm_ver: str,
                   judge_ver: str = "") -> int:
        """删掉涉及这些书的关系边（书变了/被删了，旧边就作废）。"""
        if not ids:
            return 0
        cur = 0
        for i in ids:
            cur += self.conn.execute(
                "DELETE FROM edges WHERE feat_ver=? AND norm_ver=? AND judge_ver=? "
                "AND (a=? OR b=?)", (feat, norm_ver, judge_ver, i, i)).rowcount
        self.conn.commit()
        return cur

    # ------------------------------------------------------------ 指纹

    def fp_path(self, book_id: int) -> Path:
        return self.fp_dir / f"{book_id}.bin"

    def write_fp(self, book_id: int, hashes: array) -> None:
        """写 uint32 排序数组。hashes 里的值会被截断到 32 位。"""
        tmp = self.fp_path(book_id).with_suffix(".tmp")
        with open(tmp, "wb") as f:
            f.write(FP_MAGIC)
            f.write(bytes([FP_VERSION]))
            f.write(len(hashes).to_bytes(4, "little"))
            arr = array("I", (h & 0xFFFFFFFF for h in hashes))
            arr.tofile(f)
        tmp.replace(self.fp_path(book_id))

    def read_fp(self, book_id: int) -> array:
        p = self.fp_path(book_id)
        try:
            with open(p, "rb") as f:
                if f.read(4) != FP_MAGIC:
                    return array("I")
                if f.read(1)[0] != FP_VERSION:
                    return array("I")
                n = int.from_bytes(f.read(4), "little")
                arr = array("I")
                arr.fromfile(f, n)
                return arr
        except (OSError, EOFError, ValueError):
            return array("I")
