#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章号解析与生成的**唯一真相来源**。

设计原则：**不补字符类，补完整算法。**

此前 `count_chars.py` / `md2docx.py` / `inventory.py` 各有一份内联正则与
`cn2int`，字符类 `[0-9一二三四五六七八九十百]` 缺「零」「两」「千」，导致：
  - `第二百章` 解析成 2（塌陷）-> 输出 `第二章`，与第 2 章撞名覆盖
  - `第一百零五章` 匹配不上 -> 静默跳过，章节从交付里消失
  - `第百章` 解析成 0 -> 输出 `第零章`（非法章号）

字符类只能表达「单字集合」，无法表达「计数」，所以补字是死路
（补了「千」还有「万」）。这里改成按「千/百/十/个位」四段解析，覆盖 1~9999。

对外只暴露四个函数：
  cn2int(s)            中文/阿拉伯章号 -> int（必须 >=1，否则 raise）
  num2cn(n)            int -> 中文章号（1~9999，超出报错，不静默降级）
  parse_chapter(name)  从文件名/标题解析 (n, title)，不匹配则 raise
  expected_docx(n,t)   生成规范 docx 名 —— inventory 的正向判定用它
"""
import os
import re

CN = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
      "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
D = "零一二三四五六七八九"          # 输出用，不含「两」（规范写法是「二百」）
UNITS = [(1000, "千"), (100, "百"), (10, "十")]

# 章号字符集：必须含「零」「两」「千」，否则「一百零五」「两百」会被静默跳过
_CH = "0-9零〇一二两三四五六七八九十百千万"
CHAPTER_CHARS = _CH
CHAPTER_FILE_RE = re.compile(r"第([" + _CH + r"]+)章")
CHAPTER_HEAD_RE = re.compile(r"^(#{0,6})\s*第([" + _CH + r"]+)章")
CHAPTER_TITLE_STRIP = re.compile(r"^第[" + _CH + r"]+章[　:：\s]*")

MAX_CHAPTER = 9999
_FULLWIDTH = str.maketrans("０１２３４５６７８９", "0123456789")

# 合法但不带章号的内容：**预期内**，不是错误。
# md2docx 遇到它们会跳过，inventory 见到它们也必须同样放行 ——
# 两处必须共用这一份常量，否则会出现「转换脚本说正常、体检脚本说违规」的相反定性。
NON_CHAPTER_MARKERS = ("序章", "番外", "前言", "后记", "楔子")


def is_non_chapter(name):
    """序章/番外一类：本就不该有「第N章」，属正常内容。"""
    return any(k in os.path.basename(str(name)) for k in NON_CHAPTER_MARKERS)


class ChapterError(ValueError):
    """章号无法解析，或解析结果非法（<=0 / >9999）。"""


def cn2int(s):
    """'一百零五' / '105' / '两百' -> int。非法输入抛 ChapterError。

    '百'、'零'、'0'、'' 一律报错，**不再静默返回 0**。
    """
    if s is None:
        raise ChapterError("章号为空")
    s = str(s).strip().translate(_FULLWIDTH)
    if not s:
        raise ChapterError("章号为空")
    if s.isdigit():
        n = int(s)
    elif len(s) > 1 and all(ch in CN for ch in s):
        # **逐位写法**：一一九 = 119、一二零 = 120、一〇一 = 101。
        # 此前按位权解析会把「一一九」算成 9、「一二零」算成 0 并报错——
        # 《红楼梦》第 119/120 回正是这两种写法（实测算错）。
        n = int("".join(str(CN[ch]) for ch in s))
    else:
        for ch in s:
            if ch not in CN and ch not in "十百千万":
                raise ChapterError(f"章号含非法字符 {ch!r}（{s!r}）")
        section = 0
        number = 0
        started = False
        for ch in s:
            if ch in CN:
                number = CN[ch]
                started = True
            else:
                if ch == "十" and not started:
                    number = 1          # 「十五」= 15
                    started = True
                unit = dict(UNITS_AND_WAN)[ch]
                section += number * unit
                number = 0
                started = False
        n = section + number
    if n < 1:
        raise ChapterError(f"章号必须 >= 1，得到 {n}（来自 {s!r}）")
    if n > MAX_CHAPTER:
        raise ChapterError(f"章号 {n} 超出支持范围 1~{MAX_CHAPTER}")
    return n


UNITS_AND_WAN = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def num2cn(n):
    """1 -> '一'，35 -> '三十五'，105 -> '一百零五'。

    > 9999 抛 ChapterError，**不静默降级成阿拉伯数字**
    （静默降级会与 inventory 的命名校验冲突，让用户无解）。
    """
    n = int(n)
    if n < 1:
        raise ChapterError(f"章号必须 >= 1，得到 {n}")
    if n > MAX_CHAPTER:
        raise ChapterError(
            f"章号 {n} 超出 {MAX_CHAPTER}；请用 --pattern 指定 {{n}} 阿拉伯数字命名")
    # 标准中文数字：逐段取出「千/百/十」位，**某位为 0 且还有更低位有值时补「零」**。
    # 15=  十 + 5        -> 「十五」   （十位=1，十位本身不为 0，不补零）
    # 35 = 三十 + 5      -> 「三十五」 （同上）
    # 105 = 一百 + 十位0 + 5 -> 「一百零五」（百位非0、十位为0、个位有值 -> 补零）
    # 1010 = 一千 + 百位0 + 十位1 + 个位0 -> 「一千零一十」
    digits = []
    rest = n
    for unit, _ in UNITS:
        d, rest = divmod(rest, unit)
        digits.append(d)
    digits.append(rest)                    # 个位

    parts = []
    zero_pending = False                   # 遇到空段，暂存一个待补的「零」
    for i, d in enumerate(digits):
        unit_cn = UNITS[i][1] if i < len(UNITS) else ""
        if d == 0:
            # 只有在更低位确实有值时才补零（否则 1000 不该变成「一千零」）
            if parts and any(x for x in digits[i + 1:]):
                zero_pending = True
            continue
        if zero_pending:
            parts.append("零")
            zero_pending = False
        if unit_cn:
            # 「十」在最高非零段且值为 1 时省略「一」：15 -> 十五
            if not parts and unit_cn == "十" and d == 1:
                parts.append(unit_cn)
            else:
                parts.append(D[d] + unit_cn)
        else:
            parts.append(D[d])
    return "".join(parts)


def parse_chapter(name):
    """从文件名或首行标题解析 -> (n, title)。不匹配抛 ChapterError。

    **不返回 9999 之类的兜底假值** —— 那会让「缺章」伪装成「第 9999 章」。
    """
    stem = re.sub(r"\.md$", "", os.path.basename(str(name)))
    # 分隔符只吃「章号与标题之间」的那一段（全角冒号 / 半角冒号 / 空白 / 连字符）。
    # 此前用 `group(2).replace("-", "")` 剥连字符 —— 那是**无差别全文剥离**：
    # 合法标题「第一章：重启-1998」会被改成「重启1998」，于是 inventory 拿
    # 规范名去比对永远对不上，反过来建议用户删掉连字符。连字符是标题的一部分。
    m = re.match(r"第([" + _CH + r"]+)章[：:\s\-—–]*(.*)", stem)
    if m:
        return cn2int(m.group(1)), m.group(2).strip()
    m2 = CHAPTER_HEAD_RE.match(str(name).strip())
    if m2:
        return cn2int(m2.group(2)), CHAPTER_TITLE_STRIP.sub("", str(name).strip()).strip()
    raise ChapterError(f"文件名不含「第N章」格式：{os.path.basename(str(name))}")


def expected_docx_name(n, title, sep="："):
    """规范 docx 名。inventory 用它做**正向判定**，取代第二份正则。"""
    return f"第{num2cn(n)}章{sep}{title}.docx"


def strip_chapter_prefix(title):
    """从标题里去掉「第N章」前缀（含全角冒号与空格）。"""
    return CHAPTER_TITLE_STRIP.sub("", str(title).strip()).strip()
