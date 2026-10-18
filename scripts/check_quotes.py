#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""引文回源核验：把分析稿里的「原文引用」拿回源文件比对，核对不上就报错。

为什么需要这个脚本：
  实测一次 48 篇的分析任务，首轮引文回源命中率只有 33.7% —— 三分之二的"原文引用"
  在源文件里根本不存在（模型凭记忆写、或把自己的改写建议误加了引号）。
  凡"必须引用原文作证据"的任务，**不能靠模型自觉**，必须有一道机器核验当卡点。

判定规则（宁可漏判，不可误杀）：
  · 单行内成对抽取引文：` 「」『』“”"" ，长度 10–120
  · 引文中的省略号（……/.../——/／）按"省略"处理：拆成片段，每段 ≥8 字分别核对
  · 比对前两侧都做归一化：剔除所有空白；源侧另剔除盗版站水印块
  · 全部片段命中才算通过；任一片段缺失即 FAIL

退出码：
  0 全部通过        1 有 FAIL（禁止放行）
  2 没有可核验内容（没抽到引文 / 一篇源文件都没匹配上）——**这不是通过，是没查成**
  3 部分篇目未匹配到源文件（其余已核验）

用法：
    python check_quotes.py 分析稿.md --src <源文件根目录>
    python check_quotes.py a.md b.md --src ./书/ --map 篇名映射.json --json out.json
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textio import read_text, read_text_dirty  # noqa: E402

TEXT_EXT = (".txt", ".md")

# 盗版站水印块：既可能是独立行，也可能插在句子中间。核验前从**源侧**剔除，
# 否则"含水印的原文"与"去水印的引文"永远对不上。
WM_BLOCK = re.compile(
    r"(?:资源熊出品|百万级打包资源提供者|zybear\.taobao\.com|糯米社区|TXT论坛|"
    r"本作品来自互联网|内容版权归作者所有|更多txt好书|本站所有资源)[^\n]{0,90}")
WS = re.compile(r"\s+")
HEAD = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
# 成对引号；内容里不允许出现任何引号字符，避免跨句误抓
QUOTE = re.compile(r"([`『「“\"])([^`』」”\"\n]{10,120})([`』」”\"])")
ELLIPSIS = re.compile(r"…{1,4}|\.{2,4}|／|——")


def norm(s):
    return WS.sub("", s)


def strip_wm(s):
    return WM_BLOCK.sub("", s)


def title_key(s):
    """书名/篇名归一：去掉书名号、括号注、作者标注、空白与标点。"""
    s = re.sub(r"《([^》]*)》", r"\1", s)
    s = re.sub(r"（[^）]*）|\([^)]*\)", "", s)
    s = re.sub(r"作者[:：].*$", "", s)
    s = re.sub(r"[^\w\u4e00-\u9fa5]", "", s)
    return s.strip()


def build_index(src_root):
    """只索引文件名（不读内容），真正核验时再按需读取。
    同一本书可能被拆成 (1)(2)(3) 多片，或跨目录重复收录 —— 因此一个 key 对多路径。
    """
    idx = {}
    for path in glob.iglob(os.path.join(src_root, "**", "*"), recursive=True):
        if not os.path.isfile(path) or not path.lower().endswith(TEXT_EXT):
            continue
        stem = os.path.splitext(os.path.basename(path))[0]
        key = title_key(stem)
        if key:
            idx.setdefault(key, []).append(path)
    return idx


def resolve(title, idx, title_map):
    """篇名 → 候选源文件列表。先查显式映射，再做包含式匹配。
    **返回列表**而不是单个路径：分卷拆包时单值匹配会挑中错误的分片。
    """
    if title_map:
        for k, v in title_map.items():
            if title_key(k) == title_key(title):
                return [v]
    key = title_key(title)
    if not key:
        return []
    if key in idx:
        return idx[key]
    hits = []
    for k, paths in idx.items():
        if key in k or k in key:
            hits.append((len(k), paths))
    if not hits:
        return []
    # 取"最贴近"的那一档（最长 key），再一并返回它的所有分片
    hits.sort(key=lambda x: -x[0])
    return hits[0][1][:6]


_cache = {}


def source_norm(path):
    """返回（原文归一、去水印归一）。脏源用容错读取并**显式告知**，不静默。"""
    if path in _cache:
        return _cache[path]
    try:
        raw = read_text(path)
    except (OSError, ValueError) as e:
        print(f"  ! 源文件严格解码失败，改用容错读取（仅核验用，不可用于交付）：{e}")
        raw = read_text_dirty(path)
    raw = re.sub(r"\r\n|\r", "\n", raw)
    pair = (norm(raw), norm(strip_wm(raw)))
    _cache[path] = pair
    return pair


def verify_quote(q, pair):
    frags = [norm(x) for x in ELLIPSIS.split(q) if len(x.strip()) >= 8]
    if not frags:
        return None  # 太短，不计入（避免"嗯？""走吧。"这类噪声）
    plain, clean = pair
    return all((f in plain) or (f in clean) for f in frags)


def check_file(md_path, idx, title_map, verbose):
    text = read_text_dirty(md_path)
    sections = []
    cur = None
    for line in text.splitlines():
        m = HEAD.match(line)
        if m:
            cur = m.group(2).strip()
            continue
        if cur:
            sections.append((cur, line))
    total = fail = 0
    fails = []
    unresolved = set()
    ambiguous = set()
    for title, line in sections:
        quotes = [m.group(2) for m in QUOTE.finditer(line)]
        if not quotes:
            continue
        srcs = resolve(title, idx, title_map)
        if not srcs:
            unresolved.add(title)
            continue
        if len(srcs) > 1:
            ambiguous.add(title)
        pairs = [source_norm(p) for p in srcs]
        for q in quotes:
            # 多候选取"任一命中即通过"：分卷拆包时引文可能落在任一分片里
            ok = None
            for pair in pairs:
                r = verify_quote(q, pair)
                if r is None:
                    continue
                if r:
                    ok = True
                    break
                ok = False
            if ok is None:
                continue
            total += 1
            if not ok:
                fail += 1
                fails.append((title, q))
    return total, fail, fails, unresolved, ambiguous


def main():
    ap = argparse.ArgumentParser(description="引文回源核验")
    ap.add_argument("targets", nargs="+", help="要核验的分析稿（.md）")
    ap.add_argument("--src", required=True, help="源文件根目录（递归索引 .txt/.md）")
    ap.add_argument("--map", default="", help="篇名→源文件 的 JSON 映射（重名/特殊命名时用）")
    ap.add_argument("--json", default="", help="把结果写成 JSON")
    ap.add_argument("--limit", type=int, default=20, help="最多打印多少条 FAIL（默认 20）")
    a = ap.parse_args()

    idx = build_index(a.src)
    if not idx:
        print(f"未在 {a.src} 下找到任何 .txt/.md，无法核验")
        return 2
    title_map = {}
    if a.map and os.path.exists(a.map):
        title_map = json.load(open(a.map, encoding="utf-8"))

    report = []
    grand_total = grand_fail = 0
    any_unresolved = False
    for md in a.targets:
        total, fail, fails, unresolved, ambiguous = check_file(md, idx, title_map, a.limit)
        grand_total += total
        grand_fail += fail
        any_unresolved = any_unresolved or bool(unresolved)
        rate = (total - fail) / total if total else 0
        print(f"\n=== {os.path.basename(md)}")
        print(f"    引文 {total} 条，通过 {total - fail} 条，失败 {fail} 条"
              f"（命中率 {rate:.1%}）")
        if ambiguous:
            print(f"    多候选源文件（分卷拆包/跨目录重复）{len(ambiguous)} 个，已按「任一命中」核验："
                  f"{'、'.join(sorted(ambiguous)[:4])}{' …' if len(ambiguous) > 4 else ''}")
        if unresolved:
            print(f"    未匹配到源文件的篇目 {len(unresolved)} 个："
                  f"{'、'.join(sorted(unresolved)[:6])}{' …' if len(unresolved) > 6 else ''}")
            print("    → 用 --map 指定映射，否则这些篇目等于没查")
        for title, q in fails[:a.limit]:
            print(f"    [FAIL] {title} :: {q[:70]}")
        if len(fails) > a.limit:
            print(f"    …… 另有 {len(fails) - a.limit} 条未列出")
        report.append({"file": md, "total": total, "fail": fail,
                       "unresolved": sorted(unresolved),
                       "fails": [{"section": t, "quote": q} for t, q in fails]})

    if a.json:
        json.dump(report, open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"\n合计：引文 {grand_total} 条，失败 {grand_fail} 条")
    if grand_total == 0:
        print("没有抽到任何可核验引文 —— 这不是通过，是没查成")
        return 2
    if grand_fail:
        print("有引文对不上源文：改为原文逐字片段，或去掉引号（宁可不引，不可编造）")
        return 1
    if any_unresolved:
        return 3
    print("全部引文均可回源核对")
    return 0


if __name__ == "__main__":
    sys.exit(main())
