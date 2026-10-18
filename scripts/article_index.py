#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文章位置索引：一眼看清每一章在哪一格、有没有缺、有没有"两个当前稿"。

用法：
    python article_index.py <书目录> [--write] [--json out.json]

背景（真实问题）：
    技能里曾同时存在两条互相打架的规则——
      A. "一份文章只有一个位置，旧版一律进 009 其他\\版本\\"
      B. "改前版本留在扩展文章，改后稿进 ai复查文章"
    两条都"合规"，于是不同批次/不同执行者做法不一：
    有时扩展文章有稿、有时没有；有时同一章在 ai复查与人工复查各有一份。
    本脚本把"当前稿唯一"这件事变成可检查的表。

口径（v1.2.5 起）：
    - **当前稿唯一**：同一章在 `ai复查文章` / `人工复查文章` / `最终文章` 中只能有一个。
    - `扩展文章` 是**初稿归档（只读）**，不算当前稿，⑥ 后允许保留（用于对照/回滚）。
    - `009 其他\\版本\\` 存快照，可多份；旧稿进了版本库就不该再占当前格。

退出码：0 无问题 / 1 参数错误 / 2 有需处理项
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import ChapterError, parse_chapter  # noqa: E402
from textio import read_text_or_none  # noqa: E402

STAGES = [
    ('A', os.path.join('006 文章', '扩展文章'), '初稿归档'),
    ('B', os.path.join('006 文章', 'ai复查文章'), 'AI改后'),
    ('C', os.path.join('006 文章', '人工复查文章'), '人工改后'),
    ('D', os.path.join('007 最终文章'), '定稿'),
]
CURRENT = ('B', 'C', 'D')


def scan(root):
    """返回 {章号: {stage: (path, chars)}}，跳过 _ 开头与非章节文件。"""
    books = {}
    for stage, rel, _label in STAGES:
        base = os.path.join(root, rel)
        if not os.path.isdir(base):
            continue
        for p in glob.glob(os.path.join(base, '**', '*.md'), recursive=True):
            name = os.path.basename(p)
            if name.startswith('_'):
                continue
            try:
                n, _title = parse_chapter(name)
            except ChapterError:
                continue
            text = read_text_or_none(p)
            chars = 0 if text is None else len(re.sub(r'\s', '', '\n'.join(
                l for l in text.split('\n') if not l.startswith('#'))))
            books.setdefault(n, {})[stage] = (p, chars)
    return books


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('book_dir')
    ap.add_argument('--write', action='store_true', help='写 008 进度/文章索引.md')
    ap.add_argument('--json', dest='json_out')
    args = ap.parse_args()

    if not os.path.isdir(args.book_dir):
        print(f'✗ 目录不存在：{args.book_dir}', file=sys.stderr)
        return 1

    data = scan(args.book_dir)
    if not data:
        print('✗ 四个文章格都没有可解析的章节文件（第N章*.md）', file=sys.stderr)
        return 2

    rows, issues = [], 0
    nums = sorted(data)
    # 断号：1..max 之间四格都没有的章（下限取最小已存在章，避免把序章算缺）
    for n in range(nums[0], nums[-1] + 1):
        if n not in data:
            rows.append({'n': n, 'cells': {s: None for s in 'ABCD'},
                         'current': [], 'issues': ['缺章（四格都没有）']})
            issues += 1
    for n in nums:
        cells = data[n]
        cur = [s for s in CURRENT if s in cells]
        probs = []
        if len(cur) == 0:
            probs.append('缺当前稿（只剩初稿）')
        elif len(cur) > 1:
            probs.append('多处当前稿：' + '/'.join(cur))
        for s, (p, c) in cells.items():
            if c == 0:
                probs.append(f'{s} 空文件')
        if probs:
            issues += 1
        rows.append({'n': n, 'cells': {s: cells.get(s) for s in 'ABCD'},
                     'current': cur, 'issues': probs})

    rows.sort(key=lambda r: r['n'])

    # 表格
    print(f'书目录：{args.book_dir}')
    print(f'章节数：{len(rows)}｜问题章：{issues}\n')
    head = f"{'章':>4}  {'扩':^4}{'AI':^4}{'人':^4}{'终':^4}  当前稿        问题"
    print(head)
    print('-' * len(head))
    for r in rows:
        mark = ''.join(' ✓ ' if r['cells'].get(s) else ' · ' for s in 'ABCD')
        cur = '/'.join(r['current']) or '—'
        print(f"{r['n']:>4} {mark}  {cur:<12} {'；'.join(r['issues']) or ''}")

    # 版本库计数
    ver = os.path.join(args.book_dir, '009 其他', '版本')
    vcount = len(glob.glob(os.path.join(ver, '**', '*.md'), recursive=True)) if os.path.isdir(ver) else 0
    print(f'\n版本库快照：{vcount} 个 md')
    print('口径：当前稿唯一（AI/人/最终 三选一）；扩展文章是初稿归档，不算当前稿。')

    if args.write:
        out = os.path.join(args.book_dir, '008 进度', '文章索引.md')
        os.makedirs(os.path.dirname(out), exist_ok=True)
        lines = ['# 文章索引（自动生成，勿手改）\n',
                 f'- 章节数：{len(rows)}｜问题章：{issues}｜版本库快照：{vcount}\n',
                 '| 章 | 扩展 | AI | 人 | 终 | 当前稿 | 问题 |', '|---|---|---|---|---|---|---|']
        for r in rows:
            cells = ['✓' if s in r['cells'] else '' for s in 'ABCD']
            lines.append(f"| {r['n']} | {' | '.join(cells)} | {'/'.join(r['current']) or '—'} | {'；'.join(r['issues'])} |")
        with open(out, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
        print(f'已写入 {out}')

    if args.json_out:
        with open(args.json_out, 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)

    return 2 if issues else 0


if __name__ == '__main__':
    sys.exit(main())
