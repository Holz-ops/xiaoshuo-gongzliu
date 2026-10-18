#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""切章后体检：查重复段、截断嫌疑、过短章、章末断句。

用法：
    python chapter_health.py <章节目录> [--min-chars 1000] [--json out.json]

为什么要有它（真实踩过的坑）：
    《红楼梦》源文件第 55 回在「我有一件大事」处**截断**，后面直接接了续书末回，
    切章脚本会把整段当成第 55 回，字数看起来正常，只有读到一半才发现少了半回。
    本脚本在分析开始前把这类问题挡下来：章末不是句末标点 = 疑截断。

检查项：
    1. 章数、字数区间
    2. 重复段（>40 字的段落出现 ≥2 次）——源文件重复块
    3. 截断嫌疑——末行末尾不是 。！？…”』」）】 等收束标点
    4. 过短章——低于 --min-chars
    5. 空章 / 只有标题

退出码：0 无问题 / 1 有需处理项 / 2 无可体检内容或参数错误
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textio import read_text_or_none  # noqa: E402

END_OK = '。！？…”』」）】》!?."\')'
# 章回体的正常收尾：套语、省略号、下回预告（实测误报 19 章，全是这些）
END_FORMULA = re.compile(
    r'(要知|且听|下回|后事|下文|后回|正是|-{2,}|_{2,}|—{2,}|…+)'
)


def load(path):
    # 走 textio 的回退链（utf-8-sig → utf-8 → gb18030）+ 乱码体检。
    # 原先硬编码 encoding='utf-8', errors='replace'：GBK 稿会**静默丢字**
    # （实测丢 54%~65%），随后「重复段 / 疑截断 / 过短」三项判定全建立在
    # 坏文本上，而且一句错都不报。
    return read_text_or_none(path)


def body_of(text):
    lines = [l.strip() for l in text.split('\n')]
    return [l for l in lines if l and not l.startswith('#')]


def check(path, min_chars):
    text = load(path)
    if text is None:
        return {'file': os.path.basename(path), 'chars': 0, 'paras': 0,
                'issues': ['读不出正文（编码不支持或疑似乱码），本项未体检'],
                'unreadable': True}
    paras = body_of(text)
    body = '\n'.join(paras)
    chars = len(re.sub(r'\s', '', body))
    issues = []

    # 重复段
    seen = {}
    for p in paras:
        if len(p) >= 40:
            seen[p] = seen.get(p, 0) + 1
    dups = [(p, n) for p, n in seen.items() if n > 1]
    if dups:
        extra = sum(len(p) * (n - 1) for p, n in dups)
        issues.append(f'重复段 {len(dups)} 处（多出约 {extra} 字）')

    # 截断嫌疑：末行末尾不是收束标点，且不是章回体收尾套语
    tail = paras[-1] if paras else ''
    if not tail:
        issues.append('空章（只有标题）')
    elif not END_FORMULA.search(tail[-40:]) and tail[-1] not in END_OK:
        issues.append(f'疑截断：末行以「{tail[-12:]}」结尾')

    # 注：**不做引号配对检查**——本书 ASCII 引号跨段使用极多，实测 38 章误报。
    # 语义错接（如第 55 回截断后接续书）脚本抓不到，靠 `_源文件缺陷.md` 登记 + 抽读首尾。

    # 过短
    if 0 < chars < min_chars:
        issues.append(f'过短：{chars} 字 < {min_chars}')

    return {'file': os.path.basename(path), 'chars': chars,
            'paras': len(paras), 'issues': issues}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('dir')
    ap.add_argument('--min-chars', type=int, default=1000)
    ap.add_argument('--json', dest='json_out')
    args = ap.parse_args()

    if not os.path.isdir(args.dir):
        print(f'✗ 目录不存在：{args.dir}', file=sys.stderr)
        return 1

    # 递归扫子目录：分卷（`第一卷/`）是常态，非递归会把整卷报成「没内容」。
    files = sorted(f for f in glob.glob(os.path.join(args.dir, '**', '*.md'), recursive=True)
                   if not os.path.basename(f).startswith('_')
                   and not any(part.startswith(('_', '.'))
                               for part in os.path.relpath(f, args.dir).split(os.sep)[:-1]))
    if not files:
        print(f'✗ 目录里没有 md（无内容可体检）：{args.dir}', file=sys.stderr)
        return 2

    rows = [check(f, args.min_chars) for f in files]
    bad = [r for r in rows if r['issues']]
    unreadable = [r for r in rows if r.get('unreadable')]
    good = [r for r in rows if not r.get('unreadable')]
    if not good:
        print(f'✗ {len(unreadable)} 个文件全部读不出，体检无效。'
              f'请确认编码或文件未被占用。', file=sys.stderr)
        return 2
    chars = [r['chars'] for r in good]

    print(f'章数 {len(rows)}｜字数 {min(chars)}~{max(chars)}｜均 {sum(chars)//len(chars)}')
    if unreadable:
        print(f'⚠ {len(unreadable)} 个文件读不出，已跳过（未计入上面的字数区间）')
    if bad:
        print(f'\n需处理 {len(bad)} 章：')
        for r in bad:
            print(f"  · {r['file'][:34]:<34} {r['chars']:>6} 字  {'；'.join(r['issues'])}")
    else:
        print('\n✓ 未发现重复段/截断/过短章')

    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as f:
            json.dump({'chapters': len(rows), 'unreadable': len(unreadable),
                       'issues': bad}, f, ensure_ascii=False, indent=1)

    # 1 = 有需处理项（门禁禁放行）；2 = 无可体检内容。
    # 此前用 2 表示"有问题"，而 2 在《工程纪律》里是"输入没内容"，两义混用会
    # 让门禁把真实缺陷当成输入问题放过。
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
