"""指纹：Rabin 滚动哈希 + Winnowing（Schleimer 2003）。

为什么是 winnowing 而不是"按标点切块取 hash"
------------------------------------------------
切块方案的块边界由标点位置决定。一旦某版本插入一句广告，**后面所有块的边界
整体后移**，块 hash 全部不同 → 命中率归零。而广告恰恰是盗版小说最常见的差异。

winnowing 的指纹位置**只由内容决定**，与文档长度、插入位置无关，且有一条可证明的
保证：任意长度 ≥ t = w + k − 1 的公共子串，必定产生至少一个共同指纹。
这条保证可以直接写进产品说明，也是本项目相对漫画/视频查重的优势所在。

实现要点
--------
纯 Python，无第三方依赖。滚动哈希 O(1)/位置，winnowing 用单调队列 O(n)。
实测 157 万归一化字/秒/核（少量真书 近一亿字，指纹环节 50 秒）。
"""

from __future__ import annotations

from array import array
from collections import deque

BASE = 1000003
MASK = (1 << 64) - 1


def kgram_hashes(text: str, k: int) -> array:
    """逐 k-gram 的多项式滚动哈希，返回 array('Q')。

    h[i] = Σ c[i+j] · BASE^(k-1-j)  (mod 2^64)
    用 utf-32 小端 buffer 取码点，避免 Python 逐字符 ord() 的开销。
    """
    n = len(text)
    if n < k:
        return array("Q")
    mv = memoryview(text.encode("utf-32-le")).cast("I")
    pwk = pow(BASE, k - 1, 1 << 64)
    out = array("Q", bytes(8 * (n - k + 1)))

    h = 0
    for j in range(k):
        h = (h * BASE + mv[j]) & MASK
    out[0] = h
    for i in range(n - k):
        h = ((h - mv[i] * pwk) * BASE + mv[i + k]) & MASK
        out[i + 1] = h
    return out


def winnow(h: array, w: int) -> array:
    """宽 w 的窗口取最小值（并列取最右），相邻窗口不重复记录。

    返回指纹位置（相对 text 起点），升序。
    """
    n = len(h)
    if n == 0:
        return array("q")
    if n < w:
        return array("q", range(n))
    pos = array("q")
    dq: deque[int] = deque()
    last = -1
    app = pos.append
    for i in range(n):
        x = h[i]
        while dq and h[dq[-1]] >= x:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - w:
            dq.popleft()
        if i >= w - 1:
            p = dq[0]
            if p > last:
                app(p)
                last = p
    return pos


def fingerprints(text: str, k: int, w: int) -> tuple[array, array]:
    """返回 (位置, 哈希)，一一对应，按位置升序。"""
    h = kgram_hashes(text, k)
    pos = winnow(h, w)
    return pos, array("Q", (h[p] for p in pos))


def fingerprints_no_pos(text: str, k: int, w: int) -> array:
    """只要哈希（粗指纹建索引时用，不存位置）。"""
    return fingerprints(text, k, w)[1]
