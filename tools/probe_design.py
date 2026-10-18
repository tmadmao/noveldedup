"""设计核定探针（纯标准库版）：用数值实验检验开发计划 v1.1 的核心假设。

运行：
    python tools/probe_design.py

为什么是纯标准库：本机 pip 不通（无法访问 PyPI），所以这一版用 Python 整数运算
手写 Rabin 滚动哈希 + 单调队列 winnowing。好处是顺便验证了「核心链路零第三方依赖」
这条设计目标是否站得住（代价是吞吐数字偏悲观，见 P6）。

检验项
------
P1  密度：winnowing 实测密度是否接近理论值 2/(w+1)
P2  保证：任意 >= w+k-1 的公共子串是否必产生共同指纹（Schleimer 的 guarantee）
P3  ★ 采样：v1.1 的「S=3 窗口 × L=5000」在 7 类真实差异下的命中/漏判
P4  ★ 替代方案：「全书 coarse winnowing」在同样 7 类差异下的表现
P5  索引量：两种方案在 2000 本规模下的指纹条数与内存占用
P6  吞吐：纯 Python 单核实测（作为下限），据此外推 2000 本耗时
"""

from __future__ import annotations

import random
import sys
import time
from collections import deque
from dataclasses import dataclass

MASK = (1 << 64) - 1
BASE = 1000003


# ----------------------------------------------------------------- 哈希与指纹


def kgram_hashes(cs: list[int], k: int) -> list[int]:
    """Rabin 滚动哈希，h[i] = sum c[i+j]*BASE^(k-1-j) mod 2^64。O(n)。"""
    n = len(cs)
    if n < k:
        return []
    pwk = pow(BASE, k - 1, 1 << 64)
    h = 0
    for j in range(k):
        h = (h * BASE + cs[j]) & MASK
    out = [h] * (n - k + 1)
    for i in range(n - k):
        h = ((h - cs[i] * pwk) * BASE + cs[i + k]) & MASK
        out[i + 1] = h
    return out


def winnow(h: list[int], w: int) -> list[int]:
    """教科书 winnowing：宽 w 窗口取最小值（并列取最右），相邻窗口不重复记录。"""
    n = len(h)
    if n < w:
        return list(range(n))
    dq: deque[int] = deque()
    out: list[int] = []
    last = -1
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
                out.append(p)
                last = p
    return out


def fp_of(text: str, k: int, w: int) -> tuple[list[int], list[int]]:
    """返回 (指纹位置, 指纹值)，位置相对 text 起点。"""
    cs = [ord(ch) for ch in text]
    h = kgram_hashes(cs, k)
    pos = winnow(h, w)
    return pos, [h[p] for p in pos]


# ----------------------------------------------------------------- 语料

_POOL = (
    "的一是不了在人有我他这个们中来上大为和国地到以说时要就出会可也你对生能而子那得于着下自之年过发后作里用道行所然家种事成方多经么去法学如都同现当没动面起看定天分还进好小部其些主样理心她本前开但因只从想实日军者意无力它与长把机十民第公此已工使情明性知全三又关点正业外将两高间由问很最重并物手应战向头文体政美相见被利什二等产或新己制身果加西斯月话合回特代内信表化老给世位次度门任常先海通教儿原东声提立及比员解水名真论处走义各入几口认条平系气题活尔更别打女变四神总何电数安少报才结反受目太量再感建务做接必场件计管期市直德资命山金指克许统区保至队形社便空决治展马科司五基眼书非则听白却界达光放强即像难且权思王象完设式色路记南品住告类求据程北边死张该交规万取拉格望觉术领共确传师观清今切院让识候带导争运笑飞风步改收根干造言联持组每济车亲极林服快办议往元英士证近失转夫令准布始怎呢存未远叫台单影具罗字爱击流备兵连调深商算质团集百需价花党华城石级整府离况亚请技际约示复病息究线似官火断精满支视消越器容照须九增研写称企八功吗包片史委乎查轻易早曾除农找装广显吧阿李标谈吃图念六引历首医局突专费号尽另周较注语仅考落青随选列武红响虽推势参希古众构房半节土投某案黑维革划敌致陈律足态护七兴派孩验责营星够章音跟志底站严巴例防族供效续施留讲型料终答紧黄绝奇察母京段依批群项故按河米围江织害斗双境客纪采举杀攻父苏密低朝友诉止细愿千值仍男钱破网热助倒育属坐帝限船脸职速刻乐否刚威毛状率甚独球般普怕弹校苦创假久错承印晚兰试股拿脑预谁益阳若哪微尼继送急血惊伤素药适波夜省初喜卫源食险待述陆习置居劳财环排福纳欢雷警获模充负云停木游龙树疑层冷洲冲射略范竟句室异激汉村哈策演简卡罪判担州静退既衣您宗积余痛检差富灵协角占配征修皮挥胜降阶审沉坚善妈刘读啊超免压银买皇养伊怀执副乱抗犯追帮宣佛岁航优怪香著田铁控税左右份穿艺背阵草脚概恶块顿敢守酒岛托央户烈洋哥索胡款靠评版宝座释景顾弟登货互付伯慢欧换闻危忙核暗姐介坏讨丽良序升监临亮露永呼味野架域沙掉括舰鱼杂误湾吉减编楚肯测败屋跑梦散温困剑渐封救贵枪缺楼县尚毫移娘朋画班智亦耳恩短掌恐遗固席松秘谢鲁遇康虑幸均销钟诗藏赶剧票损忽巨炮旧端探湖录叶春乡附吸予礼港雨呀板庭妇归睛饭额含顺输摇招婚脱补谓督毒油疗旅泽材灭逐莫笔亡鲜词圣择寻厂睡博勒烟授诺伦岸奥唐卖俄炸载洛健堂旁宫喝借君禁阴园谋宋避抓荣姑孙逃牙束跳顶玉镇雪午练迫爷篇肉嘴馆遍凡础洞卷坦牛宁纸诸训私庄祖丝翻暴森塔默握戏隐熟骨访弱蒙歌店鬼软典欲萨伙遭盘爸扩盖弄雄稳忘亿刺拥徒姆杨齐赛趣曲刀床迎冰虚玩析窗醒妻透购替塞努休虎扬途侵刑绿兄迅套贸毕唯谷轮库迹尤竞街促延震弃甲伟麻川申缓潜闪售灯针哲络抵朱埃抱鼓植纯夏忍页杰筑折郑贝尊吴秀混臣雅振染盛怒舞圆搞狂措姓残秋培迷诚宽宇猛摆梅毁伸摩盟末乃悲拍丁赵硬麦蒋操耶阻订彩抽赞魔纷沿喊违妹浪汇币丰蓝殊献桌啦瓦莱援译夺汽烧距裁偏符勇触课敬哭懂墙袭召罚侠厅拜巧侧韩冒债曼融惯享戴童犹乘挂奖绍厚纵障讯涉彻刊丈爆乌役描洗玛患妙镜唱烦签仙彼弗症仿倾牌陷鸟轰咱菜闭奋庆撤泪茶疾缘播朗杜奶季丹狗尾仪偷奔珠虫驻孔宜艾桥淡翼恨繁寒伴叹旦愈潮粮缩罢聚径恰挑袋灰捕徐珍幕映裂泰隔启尖忠累炎暂估泛荒偿横拒瑞忆孤鼻闹羊呆厉衡胞零穷舍码赫婆魂灾洪腿胆津俗辩胸晓劲贫仁偶辑邦恢赖圈摸仰润堆碰艇稍迟辆废净凶署壁御奉旋冬矿抬蛋晨伏吹鸡倍糊秦盾杯租骑乏隆诊奴摄丧污渡旗甘耐凭扎抢绪粗肩梁幻菲皆碎宙叔岩荡综爬荷悉蒂返井壮薄悄扫敏碍殖详迪矛霍允幅撒剩凯颗骂赏液番箱贴漫酸郎腰舒眉忧浮辛恋餐吓挺励辞艘键伍峰尺昨黎辈贯侦滑券崇扰宪绕趋慈乔阅汗枝拖墨胁插箭腊粉泥氏彭拔骗凤慧媒佩愤扑龄驱惜豪掩兼跃尸肃帕驶堡届欣惠册储飘桑闲惨洁踪勃宾频仇磨递邪撞拟滚奏巡颜剂绩贡疯坡瞧截燃焦殿伪柳锁逼颇昏劝呈搜勤戒驾漂饮曹朵仔柔俩孟腐幼践籍牧凉牲佳娜浓芳稿竹腹跌逻垂遵脉貌柏狱猜怜惑陶兽帐饰贷昌叙躺钢沟寄扶铺邓寿惧询汤盗肥尝匆辉奈扣廷澳嘛董迁凝慰厌脏腾幽怨鞋丢埋泉涌辖躲晋紫艰魏吾慌祝邮吐狠鉴曰械咬邻赤挤弯椅陪割揭韦悟聪雾锋梯猫祥阔誉筹丛牵鸣沈阁穆屈旨袖猎臂蛇贺柱抛鼠瑟戈牢逊迈欺吨琴衰瓶恼燕仲诱狼池疼卢仗冠粒遥吕玄尘冯抚浅敦纠钻晶岂峡苍喷耗凌敲菌赔涂粹扁亏寂煤熊恭湿循暖糖赋抑秩帽哀宿踏烂袁侯抖夹昆肝擦猪炼恒慎搬纽纹玻渔磁铜齿跨押怖漠疲叛遣兹祭醉拳弥斜档稀捷肤疫肿豆削岗晃吞宏癌肚隶履涨耀扭坛拨沃绘伐堪仆郭牺歼墓雇廉契拼惩捉覆刷劫嫌瓜歇雕闷乳串娃缴唤赢莲霸桃妥瘦搭赴岳嘉舱俊址庞耕锐缝悔邀玲惟斥宅添挖呵讼氧浩羽斤酷掠妖祸侍乙妨贪挣汪尿莉悬唇翰仓轨枚盐览傅帅庙芬屏寺胖璃愚滴疏萧姿颤丑劣柯寸扔盯辱匹俱辨饿蜂哦腔郁溃谨糟葛苗肠忌溜鸿爵鹏鹰笼丘桂滋聊挡纲肌茨壳痕碗穴膀卓贤卧膜毅锦欠哩函茫昂薛皱夸豫胃舌剥傲拾窝睁携陵哼棉晴铃填饲渴吻扮逆脆喘罩卜炉柴愉绳胎蓄眠竭喂傻慕浑奸扇柜悦拦诞饱乾泡贼亭夕爹酬儒姻卵氛泄杆挨僧蜜吟猩遂狭肖甜霞驳裕顽摘矮秒卿畜咽披辅勾盆疆赌塑畏吵囊嗯泊肺骤缠冈羞瞪吊贾漏斑涛悠鹿俘锡卑葬铭滩嫁催璇翅盒蛮矣潘歧赐鲍锅廊拆灌勉盲宰佐啥胀扯禧辽抹筒棋裤唉朴咐孕誓喉妄拘链驰栏逝窃艳臭纤玑棵趁匠盈翁愁瞬婴孝颈倘浙谅蔽畅赠妮莎尉冻跪闯葡後厨鸭颠遮谊圳吁仑辟瘤嫂陀框谭亨钦庸歉芝吼甫衫摊宴嘱衷娇陕矩浦讶耸裸碧摧薪淋耻胶屠鹅饥盼脖虹翠崩账萍逢赚撑翔倡绵猴枯巫昭怔渊凑溪蠢禅阐旺寓藤匪伞柄柏"
)

_AD_LINES = ["看正版就来青柠书屋", "本章还有三千字免费读", "书友们记得收藏本站"]

_CLAUSE = "的一是在不了有和人这中大为上个国我以要他时来用们生到作地于出就分对成会可主发年动"


def make_book(n_chars: int, seed: int) -> str:
    """合成一本「像小说」的中文文本：常用字簇 + 大池子采样 + 章节标题。"""
    rng = random.Random(seed)
    n = len(_POOL)
    out: list[str] = []
    total = 0
    chapter = 1
    while total < n_chars:
        head = f"第{chapter}章　风起于青萍之末\n\n"
        out.append(head)
        total += len(head)
        body = int(rng.uniform(1800, 3200))
        buf: list[str] = []
        got = 0
        while got < body:
            if rng.random() < 0.60:
                s = "".join(rng.choice(_CLAUSE) for _ in range(rng.randint(6, 14)))
            else:
                s = "".join(_POOL[rng.randrange(n)] for _ in range(rng.randint(4, 12)))
            buf.append(s)
            buf.append(rng.choice("，。！？；：、"))
            got += len(s) + 1
        text = "".join(buf) + "\n\n"
        out.append(text)
        total += len(text)
        chapter += 1
    return "".join(out)


def norm(text: str) -> str:
    """计划 §3.1 的归一化：NFKC → 只留 CJK 基本区/扩展A + 数字 + 小写字母。"""
    import unicodedata

    keep = []
    for ch in unicodedata.normalize("NFKC", text):
        o = ord(ch)
        if 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF or 0x30 <= o <= 0x39 or 0x61 <= o <= 0x7A:
            keep.append(ch)
        elif 0x41 <= o <= 0x5A:  # NFKC 后大写字母仍在，统一小写
            keep.append(chr(o + 32))
    return "".join(keep)


# ----------------------------------------------------------------- 判据

def chain_len(pairs: list[tuple[int, int]]) -> int:
    """一对一贪心（抑制 burstiness）+ 最长递增子序列，即计划 §3.5。"""
    from bisect import bisect_left

    if not pairs:
        return 0
    pairs.sort()
    used: set[int] = set()
    seq: list[int] = []
    for _a, b in pairs:
        if b in used:
            continue
        used.add(b)
        seq.append(b)
    tails: list[int] = []
    for v in seq:
        i = bisect_left(tails, v)
        if i == len(tails):
            tails.append(v)
        else:
            tails[i] = v
    return len(tails)


def coverage(pairs: list[tuple[int, int]], n_doc: int, w: int = 40, k: int = 12) -> tuple[float, int]:
    """匹配段对**字数**的覆盖率（不是指纹覆盖率）。

    意义：插入型差异（广告、排版）会让指纹覆盖率掉，但匹配段在字符上仍然连续，
    覆盖率接近 1.0；而缺章/改写会在 A 侧留下真实空隙，覆盖率随之下降。
    → 这是区分「只是加了广告」与「真的少了内容」的关键指标。

    ⚠ 阈值必须跟着指纹间距走：winnowing 的平均间距 ≈ (w+1)/2，
      所以 gap_max 取 3 倍间距、min_seg 取保证阈值 t。
      固定 60 字在 w=200 时会把每一对都切成独立段，覆盖率算成 0（踩过）。
    """
    if not pairs:
        return 0.0, 0
    gap_max = 3 * (w + 1) // 2
    min_seg = w + k - 1
    ps = sorted(p[0] for p in pairs)
    total = 0
    nseg = 0
    start = prev = ps[0]
    for x in ps[1:]:
        if x - prev > gap_max:
            if prev - start >= min_seg:
                total += prev - start
                nseg += 1
            start = x
        prev = x
    if prev - start >= min_seg:
        total += prev - start
        nseg += 1
    return total / max(1, n_doc), nseg


def match_stats(pa, ha, pb, hb) -> dict:
    if not ha or not hb:
        return dict(shared=0, cont_ab=0.0, cont_ba=0.0, jac=0.0, chain=0, pairs=[])
    where: dict[int, list[int]] = {}
    for p, x in zip(pb, hb):
        where.setdefault(x, []).append(p)
    shared = 0
    pairs: list[tuple[int, int]] = []
    for p, x in zip(pa, ha):
        lst = where.get(x)
        if lst:
            shared += 1
            pairs.append((p, lst[0]))
    return dict(
        shared=shared,
        cont_ab=shared / len(ha),
        cont_ba=shared / len(hb),
        jac=shared / (len(ha) + len(hb) - shared),
        chain=chain_len(pairs),
        pairs=pairs,
    )


def verdict(st: dict, na: int, nb: int, w: int = 40) -> str:
    """按 §3.5 结构给结论（阈值是占位值，本探针只用于横向对比两种采样）。

    与 v1.1 的差异：关系分类改成**对称的包含度判据**，不再依赖 |A|/|B| 长度比。
    v1.1 用 ``na > nb * 1.4`` 判"一方缺章"，实测在"100 章 vs 80 章"（比值 1.25）
    时会漏判，详见 docs/开发计划-2026-10-17.md 的核定记录。
    """
    if st["shared"] < 8 or st["chain"] < 4:
        return "不重复"
    ca, cb = st["cont_ab"], st["cont_ba"]
    pairs = st["pairs"]
    cov_a, _ = coverage(pairs, na, w)
    cov_b, _ = coverage([(b, a) for a, b in pairs], nb, w)
    if st["chain"] < 0.5 * st["shared"]:
        return "重复·章节顺序不同"
    # 字数覆盖率接近 1 = 只是插入了内容（广告）或换了排版，正文没少
    if cov_a >= 0.97 and cov_b >= 0.97 and st["jac"] >= 0.55:
        return "重复·同版本(仅插入)" if st["jac"] >= 0.90 else "重复·同版本(有细微字符差异)"
    if cb >= 0.95 and ca <= 0.90:
        return "重复·B是A子集(B缺章)"
    if ca >= 0.95 and cb <= 0.90:
        return "重复·A是B子集(A缺章)"
    if st["jac"] >= 0.55 and min(ca, cb) >= 0.98:
        return "重复·同版本"
    if st["jac"] >= 0.55:
        return "重复·疑似修订版"
    if st["jac"] >= 0.25:
        return "重复·疑似改字版"
    return "不重复"


# ----------------------------------------------------------------- 主流程


def window_sample(text: str, s: int, L: int, k: int, w: int):
    """v1.1 的窗口采样：S 个窗口，中心均匀落在 (0,1) 开区间。"""
    n = len(text)
    ps: list[int] = []
    hs: list[int] = []
    for i in range(s):
        center = (i + 0.5) / s
        start = int(center * n - L / 2)
        start = max(0, min(max(0, n - L), start))
        seg = text[start : start + L]
        p, h = fp_of(seg, k, w)
        ps.extend(x + start for x in p)
        hs.extend(h)
    return ps, hs


def main() -> int:
    K = 12
    S, L = 3, 5000

    print("=" * 76)
    print("P1/P2  密度与保证性")
    print("=" * 76)
    book = norm(make_book(200_000, seed=1))
    print(f"合成语料：{len(book):,} 归一化字符")

    for w in (40, 200):
        t0 = time.perf_counter()
        pos, hs = fp_of(book, K, w)
        dt = time.perf_counter() - t0
        n_kg = len(book) - K + 1
        print(
            f"  w={w:<4} 指纹 {len(pos):>7,}  密度 {len(pos)/n_kg:.3%} "
            f"(理论 {2/(w+1):.3%})  t={K+w-1} 字  耗时 {dt:.2f}s"
        )

    print("\n  P2 保证性抽检：随机截取长度恰为 t 的子串，检查是否必产生共同指纹")
    for w in (40, 200):
        t = K + w - 1
        _p, hs = fp_of(book, K, w)
        fset = set(hs)
        rng = random.Random(7)
        miss = 0
        trials = 200
        for _ in range(trials):
            s = rng.randrange(0, len(book) - t)
            _pp, hh = fp_of(book[s : s + t], K, w)
            if not (set(hh) & fset):
                miss += 1
        flag = "✅ 保证成立" if miss == 0 else "❌ 保证不成立"
        print(f"    w={w:<4} t={t} 字：未命中 {miss}/{trials}  {flag}")

    # ---------------------------------------------------------------- 场景
    print()
    print("=" * 76)
    print("P3/P4  7 类真实差异：S=3 窗口采样  vs  全书 coarse winnowing")
    print("=" * 76)

    A_raw = make_book(200_000, seed=11)
    A = norm(A_raw)
    print(f"基准书 A：原文 {len(A_raw):,} 字符 → 归一化 {len(A):,} 字")

    # ② 编码/空白差异：段首全角空格 + 全角标点 + CRLF（归一化后应与 A 相同）
    styled = "\r\n".join("　　" + ln for ln in A_raw.split("\n"))
    styled = styled.replace("，", "，").replace("。", "。")
    assert norm(styled) == A, "归一化后应完全一致"

    # ③ 每 3000 字插 1 条广告（关键词规则抓不到的那种）
    buf = []
    for i in range(0, len(A_raw), 3000):
        buf.append(A_raw[i : i + 3000])
        buf.append("\n" + _AD_LINES[(i // 3000) % 3] + "\n")
    ad_text = norm("".join(buf))

    # ④ 前加 8 万字前言（真实场景常见 20 万字，等比缩小以控制耗时）
    preface = norm(make_book(80_000, seed=99))

    # ⑤ 单卷 vs 三卷合订
    v2 = norm(make_book(200_000, seed=22))
    v3 = norm(make_book(200_000, seed=33))
    omnibus = A + v2 + v3

    # ⑥ 断更：B 只有 A 的前 75%
    truncated = A[: int(len(A) * 0.75)]

    # ⑦ 负样本
    other = norm(make_book(200_000, seed=77))

    # ⑧ 章节顺序不同：把章节按固定步长重排，内容集合不变
    import re

    chs = [x for x in re.split(r"(?=第\d+章)", A_raw) if x.strip()]
    order = list(range(len(chs)))
    rng = random.Random(5)
    # 交换相距较远的章节，制造"内容全在、顺序全乱"
    for i in range(0, len(order) - 1, 2):
        j = min(len(order) - 1, i + 5)
        order[i], order[j] = order[j], order[i]
    shuffled = norm("".join(chs[k] for k in order))

    # ⑨ 修订版：随机替换 5% 的章节
    keep = []
    for i, seg in enumerate(chs):
        if i % 20 == 3:
            keep.append(make_book(2500, seed=1000 + i))
        else:
            keep.append(seg)
    revised = norm("".join(keep))

    # ⑩ OCR 错字版：每 200 字随机改 1 个字（长公共段被切碎，考验 t 阈值）
    rng2 = random.Random(9)
    ocr: list[str] = []
    for i, ch in enumerate(A):
        if i % 200 == 100:
            ocr.append(_POOL[rng2.randrange(len(_POOL))])
        else:
            ocr.append(ch)
    ocr_text = "".join(ocr)

    # ⑪ 负样本：A 与一本无关的书共享 3000 字套路描写（考验"共享片段"是否误判）
    boiler = norm(make_book(3000, seed=555))
    other_b = other[:100_000] + boiler + other[100_000:]

    cases: list[tuple[str, str, str, bool]] = [
        ("①复制改名（同文）", "重复", A, True),
        ("②编码/空白/全角差异", "重复", styled, True),
        ("③每3千字插广告", "重复", ad_text, False),
        ("④前加8万字前言", "重复·A是B子集", preface + A, False),
        ("⑤单卷A vs 三卷合订", "重复·A是B子集", omnibus, False),
        ("⑥A完整 vs B前75%", "重复·B是A子集", truncated, False),
        ("⑦无关的两本书", "不重复", other, False),
        ("⑧章节顺序打乱", "重复·章节顺序不同", shuffled, False),
        ("⑨5%章节被改写", "重复·疑似修订版", revised, False),
        ("⑩每200字1个OCR错字", "重复", ocr_text, False),
        ("⑪不同书共享3000字套路", "不重复", other_b, False),
    ]

    samplers = [("S=3窗口×5000(w=40)", "window", 40), ("全书coarse(w=200)", "full", 200),
                ("全书fine(w=40)", "full", 40)]

    summary: list[tuple[str, int, int]] = []
    for label, mode, w in samplers:
        print(f"\n--- {label} ---")
        if mode == "window":
            pa, ha = window_sample(A, S, L, K, w)
        else:
            pa, ha = fp_of(A, K, w)
        print(f"A 指纹数 {len(ha):,}")
        hit = ok = 0
        for name, expect, txt, is_self in cases:
            if is_self:
                # 同文/归一化同文：指纹集合必然完全相同，直接给结论，不重复算
                print(f"  ✅ {name:<22} 期望 {expect:<12} 实判 {'重复（指纹集合相同）':<13} "
                      f"共享 {len(ha):>6,}  A含于B 1.00  Jaccard 1.00")
                ok += 1
                continue
            if mode == "window":
                pb, hb = window_sample(txt, S, L, K, w)
            else:
                pb, hb = fp_of(txt, K, w)
            st = match_stats(pa, ha, pb, hb)
            v = verdict(st, len(A), len(txt), w)
            cov_a, _ = coverage(st["pairs"], len(A), w)
            cov_b, _ = coverage([(b, a) for a, b in st["pairs"]], len(txt), w)
            want_dup = "不重复" not in expect
            got_dup = "不重复" not in v
            good = want_dup == got_dup
            ok += int(good)
            hit += int(good)
            print(
                f"  {'✅' if good else '❌'} {name:<22} 期望 {expect:<12} 实判 {v:<13} "
                f"共享 {st['shared']:>6,}  A含于B {st['cont_ab']:.2f}  "
                f"Jaccard {st['jac']:.2f}  链长 {st['chain']:>5,}  覆盖 {cov_a:.2f}/{cov_b:.2f}"
            )
        summary.append((label, ok, len(cases)))

    print()
    print("=" * 76)
    print("P5  索引体积：与书库规模无关的换算（每 1000 万归一化字占用多少 MB）")
    print("=" * 76)
    for w in (300, 200, 150, 100, 40):
        per_10m = 10_000_000 * (2 / (w + 1)) * 4 / 2 ** 20
        print(
            f"  w={w:<4} 保证 t={K + w - 1:<4}  {per_10m:5.2f} MB/千万字"
            f"   → 1 亿字 {per_10m * 10:7.1f} MB   → 10 亿字 {per_10m * 100:8.1f} MB"
        )

    print()
    print("=" * 76)
    print("P6  吞吐：与书库规模无关的换算")
    print("=" * 76)
    t0 = time.perf_counter()
    _p, _h = fp_of(book, K, 200)
    dt = time.perf_counter() - t0
    cps = len(book) / dt
    print(f"  单核 {cps:,.0f} 归一化字/秒")
    print(
        f"  每 1 亿归一化字：单核 {1e8 / cps / 60:.1f} 分钟，"
        f"8 进程 {1e8 / cps / 8:.0f} 秒，16 进程 {1e8 / cps / 16:.0f} 秒"
    )

    # ---------------------------------------------------------------- P7
    print()
    print("=" * 76)
    print("P7  广告密度 / 排版差异的鲁棒性（这正是「是不是同一本书」的核心）")
    print("=" * 76)
    ad_unit = norm(_AD_LINES[0])
    print(f"用例：每 N 字插入一条 {len(ad_unit)} 字的纯汉字广告（关键词规则抓不到的）")

    for w in (150, 40):
        pa, ha = fp_of(A, K, w)
        print(f"\n  --- 档位 w={w}，保证 t={K + w - 1} 字 ---")
        print("      广告间隔   共享指纹   c(A,B)   c(B,A)   Jaccard  字数覆盖(A/B)  判定")
        for interval in (10000, 5000, 3000, 2000, 1000, 500, 300, 200, 120):
            parts = []
            for i in range(0, len(A), interval):
                parts.append(A[i : i + interval])
                parts.append(ad_unit)
            adtxt = "".join(parts)
            pb, hb = fp_of(adtxt, K, w)
            st = match_stats(pa, ha, pb, hb)
            cov_a, _ = coverage(st["pairs"], len(A), w)
            cov_b, _ = coverage([(b, a) for a, b in st["pairs"]], len(adtxt), w)
            v = verdict(st, len(A), len(adtxt), w)
            print(
                f"       每 {interval:>5} 字   {st['shared']:>8,}   {st['cont_ab']:6.2f}   "
                f"{st['cont_ba']:6.2f}   {st['jac']:6.2f}   {cov_a:5.2f}/{cov_b:5.2f}   {v}"
            )

    print("\n  排版差异（应完全不影响：归一化后两串完全相同）")
    import re as _re

    variants = {
        "段首全角缩进 + 去空行 + CRLF": "\r\n".join(
            "　　" + ln.strip() for ln in A_raw.split("\n") if ln.strip()
        ),
        "每句一行": "\n".join(s for s in _re.split(r"(?<=[。！？])", A_raw) if s.strip()),
        "半角标点": A_raw.replace("，", ",").replace("。", ".").replace("！", "!"),
        "全角字母数字": A_raw.replace("A", "Ａ").replace("1", "１"),
    }
    for name, txt in variants.items():
        same = norm(txt) == A
        print(f"    {name:<26} 归一化后与 A 相同: {'✅ 是' if same else '❌ 否'}（Jaccard 恒为 1.00）")

    print()
    print("=" * 76)
    print("汇总")
    print("=" * 76)
    for label, ok, tot in summary:
        print(f"  {label:<24} 场景通过 {ok}/{tot}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
