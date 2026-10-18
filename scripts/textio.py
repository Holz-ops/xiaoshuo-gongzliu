#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一的文件读取：按 utf-8-sig → utf-8 → gb18030 有序**严格**回退。

为什么不用 errors="ignore" / "replace"：
  实测 GBK 稿用 errors="ignore" 会**丢掉 54%~65% 的中文**，而下游的长度
  校验是拿「同一份坏文本」两边比 —— 两边都坏所以"一致"，校验通过，
  于是**静默交付一份乱码 docx**。响亮失败比静默损坏安全。

为什么 utf-8-sig 排第一：
  带 BOM 的 UTF-8 若按 utf-8 读，BOM 会变成 '\ufeff' 混进首行标题，
  污染章号匹配与字数统计（实测每章虚高约 8 字）。

为什么 gb18030 而不是 gbk：
  gb18030 是 GBK/GB2312/GB18030 的超集，覆盖面更广且 round-trip 一致。

为什么 gb18030 兜底之后还要做「乱码体检」：
  gb18030 几乎是**全量编码**——Big5 / Shift-JIS / EUC-KR 的字节流它都能解出来，
  只是解成人看不懂的汉字。此时字没丢、文件也不报错，于是下游的字数统计、
  一致性比对、lint 全部拿这份**乱码当"一致"来比，然后全部通过**。
  这比"解码失败"危险得多：解码失败会被看见，乱码不会。
  所以 gb18030 解出的内容必须过一遍可信度检查，可疑即按「无法解码」处理。
  （可信度检查只针对 gb18030 这一档：UTF-8 解出来就是可信的。）

已知残留（诚实标注）：EUC-KR 一类**带 ASCII 空格**的非中文编码仍可能蒙混过关
  —— 判据是"全汉字且无中文标点"，而韩文解码结果里有空格，CJK 占比不够高。
  中文网文场景里真正会撞上的是**繁体 Big5 稿**，那一类已被 PUA 判据稳稳抓住。
"""
import os

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030")


def _bom_encoding(raw):
    """按 BOM 精确判定编码；无 BOM 返回 None。

    为什么只认 BOM、不"猜"：UTF-16 的字节流在无 BOM 时与 GBK 高度混淆，
    强行试解会得到"能解但是乱码"的结果——正是本模块最想避免的静默损坏。
    有 BOM 才是确定性证据。
    """
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    if raw.startswith(b"\xff\xfe\x00\x00") or raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32"
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return "utf-16"
    return None

# 中文正文里正常会出现的标点。一份"全是汉字、一个中文标点都没有"的长文本，
# 几乎不可能是人写的中文稿 —— 那是别的编码被 gb18030 硬解出来的结果。
_CN_PUNCT = set("。，、；：？！…—“”‘’（）《》〈〉『』【】")

_MOJIBAKE_MIN_LEN = 30       # 太短的文本不做统计判断（样本不足）
_PUA_RATIO = 0.02            # 私用区字符占比阈值
_RARE_RATIO = 0.30           # 扩展 A / 兼容 / 扩展 B+ 区汉字占比阈值
_CJK_RATIO = 0.80            # 触发"全汉字无标点"判据所需的 CJK 占比


def _mojibake_reason(text):
    """返回「为什么认为这是乱码」；看起来可信则返回 None。

    判据分两类：
      · **绝对信号**（私用区 / 假名 / 谚文）——与长度无关，出现即判乱码。
        正常中文稿里不会有任何 PUA 字符，也不会有假名或谚文。
      · **统计信号**（生僻字占比 / 通篇无中文标点）——需要足够样本量才成立，
        短文本不做统计判断，否则会把「纯汉字的短词典行」误杀。
    """
    n = len(text)
    pua = kana = hangul = rare = cjk = punct = 0
    pua_chars = {}
    for ch in text:
        o = ord(ch)
        if 0xE000 <= o <= 0xF8FF or o >= 0xF0000:
            pua += 1
            pua_chars[ch] = pua_chars.get(ch, 0) + 1
        elif 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF:
            kana += 1
        elif 0xAC00 <= o <= 0xD7A3 or 0x1100 <= o <= 0x11FF:
            hangul += 1
        elif 0x3400 <= o <= 0x4DBF or 0xF900 <= o <= 0xFAFF or o >= 0x20000:
            rare += 1
        elif 0x4E00 <= o <= 0x9FFF:
            cjk += 1
        if ch in _CN_PUNCT:
            punct += 1
    if not n:
        return None
    # —— 绝对信号：多短都算数，但要求不是偶发单字 ——
    # 例外：老 txt 常拿**同一个**私用区字符当缩进（如 U+E5E5 顶替全角空格）。
    # 这类"单一 PUA 反复出现"是排版残留，不是乱码；Big5 误解出的 PUA 会散落在整个私用区。
    indent_style = (len(pua_chars) <= 3 and pua and
                    max(pua_chars.values()) / pua > 0.8)
    if pua and pua / n > _PUA_RATIO and not indent_style:
        return f"含私用区字符 {pua}/{n}（多为 Big5 被误按 GB18030 解出）"
    if kana:
        return f"含日文假名 {kana} 处"
    if hangul:
        return f"含谚文 {hangul} 处"
    # —— 统计信号：样本不足时不做判断 ——
    if n < _MOJIBAKE_MIN_LEN:
        return None
    if rare / n > _RARE_RATIO:
        return f"生僻/扩展区汉字占 {rare}/{n}"
    if punct == 0 and cjk / n >= _CJK_RATIO:
        return f"通篇 {cjk}/{n} 个汉字却一个中文标点都没有"
    return None


def read_text(path):
    """读文本文件。无法解码或解出乱码时抛 ValueError（**不返回坏内容**）。"""
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = _bom_encoding(raw)
    if bom in ("utf-16", "utf-32"):
        # 有 BOM 是确定性证据，可以直接解；解完照样过乱码体检
        text = raw.decode(bom)
        why = _mojibake_reason(text)
        if why:
            raise ValueError(f"疑似乱码（{why}）：{os.path.basename(path)}")
        return text
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            continue
        # 只有 gb18030 这一档需要体检：前两档解出来就是原样。
        if enc == "gb18030":
            why = _mojibake_reason(text)
            if why:
                raise ValueError(
                    f"疑似非 GB18030 编码，按乱码拒绝（{why}）："
                    f"{os.path.basename(path)} —— 请另存为 UTF-8 后重试")
        return text
    raise ValueError(
        f"无法解码（既非 UTF-8 也非 GB18030）：{os.path.basename(path)}"
        f" —— 请另存为 UTF-8 后重试")


def write_text(path, text):
    """统一以 UTF-8（无 BOM）写文件。"""
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def read_text_dirty(path):
    """**脏源专用**：在每种编码上都试容错解码，挑"损坏最少、且不像乱码"的那一份。

    ⚠️ 与 read_text 的取舍**故意相反**，不要混用：
      · read_text（交付链）：解码失败就抛错。容错解码会让"两边都坏所以一致"，
        把乱码当合格稿静默交付。
      · read_text_dirty（审查链）：只读不写，目的是"尽量多读一点脏数据来做核对"，
        损坏处会变成 U+FFFD，调用方**必须**把 replacement_ratio 报出来。

    为什么不能"严格全失败后只对最后一种编码容错"（初版就是这么写的，实测踩坑）：
      盗版聚合包的 txt 常见**混合编码**——页眉水印是 GBK、正文是 UTF-8。
      这类文件 utf-8 严格解码会在第 0 字节就失败，gb18030 严格解码也会在中段失败，
      于是初版落到 `gb18030 + replace`，把整篇 UTF-8 正文按 GBK 解成乱码
      （实测某部 70 万字节的书产生 2.3 万个替换符，引文核验全线误报）。
      正确做法是**每种编码各自容错解一次，按（是否乱码, 替换率）择优**。
    """
    with open(path, "rb") as fh:
        raw = fh.read()
    bom = _bom_encoding(raw)
    if bom:
        try:
            return raw.decode(bom)
        except UnicodeDecodeError:
            # 有 BOM 但尾部字节被截断（实测：UTF-16 文件末字节缺失）——
            # 审查链允许带替换符读出，交调用方用 replacement_ratio 判断可信度
            return raw.decode(bom, errors="replace")
    best = None
    for enc in ENCODINGS:
        try:
            text = raw.decode(enc)
        except UnicodeDecodeError:
            text = raw.decode(enc, errors="replace")
        # 严格解出来的那一档，仅 gb18030 需要体检；容错解的也要看是否已成乱码
        bad = 0 if _mojibake_reason(text) is None else 1
        key = (bad, replacement_ratio(text))
        if best is None or key < best[0]:
            best = (key, text)
        if key == (0, 0.0):
            break
    return best[1] if best else ""



def replacement_ratio(text):
    """替换字符（U+FFFD）占比 —— 脏源读取后必须报这个数，用来判断还能不能信任统计。"""
    return (text.count("\ufffd") / len(text)) if text else 0.0


def read_text_or_none(path):
    """读不了就返回 None 并打印文件名（**不打印绝对路径**，避免界面上泄露本机结构）。"""
    try:
        return read_text(path)
    except (OSError, ValueError) as e:
        print(f"  ✗ 跳过 {os.path.basename(path)}：{e}")
        return None

