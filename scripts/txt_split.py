#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""txt 全书 → 逐章 md（供逐章分析用）。

用法：
    python txt_split.py <book.txt> <输出目录> [--dry-run] [--unit 回]

做什么：
    1. 自动解码：utf-8-sig → utf-8 → gb18030（与 textio 同序）
    2. 识别章标题：`第X回/章/节`、带卷前缀的 `第X卷(...)第X回`、简写 `十一　`
    3. 按出现顺序编号（不解析中文数字——「一一九/一二零」这类混写解析必错）
    4. 清洗：去 \\r、去广告行、合并空行、去行首全角空格
    5. 输出 `第NNN回：标题.md` + `_目录.md`

退出码：0 成功 / 1 参数或解码错误 / 2 找不到任何章标题
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textio import write_text  # noqa: E402

NUM = r'[0-9零〇一二三四五六七八九十百千两]'
# 1) 带卷前缀：第一卷(01--030章)第一回　标题 / 第二卷(31--60章)三十一　标题
RE_VOL = re.compile(rf'^\s*第{NUM}+卷[\(（][^\)）]*[\)）]\s*(.+)$')
# 2) 常规：第一回　标题 / 第12章 标题 / 第三节 标题
#    **必须**有分隔符（空白或标点）——否则正文「第四回中既将薛家…」会被当成回目（实测踩过）
RE_CHAP = re.compile(rf'^\s*第\s*{NUM}+\s*[回章节](?:[：:、.．\-—]|\s)+(.+)$')
# 3) 简写：十一　标题（必须带全角/连续空白；只用于卷前缀剥掉后的剩余部分）
RE_BARE = re.compile(rf'^{NUM}{{1,4}}\s*[　\u3000]+\s*(\S.{{0,40}})$')

AD_MARKS = ('txtsk.com', 'TXT书库', '更多免费txt', '本电子书仅供', '欢迎访问:', 'www.txt')


def decode(path):
    raw = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise ValueError('无法解码（非 UTF-8 / GB18030）')


def clean_lines(text):
    out = []
    for l in text.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        l = l.replace('\ufeff', '').lstrip('\u3000 ')
        if any(m in l for m in AD_MARKS):
            continue
        out.append(l.rstrip())
    return out


def find_headings(lines):
    """返回 [(行号, 标题)]，按出现顺序。"""
    hits = []
    for i, l in enumerate(lines):
        if not l.strip():
            continue
        m = RE_VOL.match(l)
        if m:
            rest = m.group(1).strip()
            m2 = RE_CHAP.match(rest)
            if m2:
                title = m2.group(1)
            else:
                m3 = RE_BARE.match(rest)   # 十一　标题（剥掉卷前缀后的简写）
                title = m3.group(1) if m3 else rest
                # 源文件偶有「十三　回秦可卿…」——简写序号后多排了一个「回」，
                # 它不是标题的一部分，剥掉（实测：红楼梦第13回）。
                title = re.sub(r'^[回章节]\s*', '', title)
            hits.append((i, title.strip('　 :：')))
            continue
        m = RE_CHAP.match(l)
        if m and m.group(1).strip():
            hits.append((i, m.group(1).strip('　 :：')))
            continue
        m = RE_BARE.match(l)
        if m:
            title = m.group(1).strip('　 :：')
            # **正文误判防线**：章回标题不含句读。
            # 实测正文「十一　他忽然停住，像是听见了什么。」会被 RE_BARE 当成回目，
            # 切出一个假章节并把后面的章号整体后移。
            if len(title) >= 2 and not re.search(r'[。！？，；：]', title):
                hits.append((i, title))
    # 去重（同一行只算一次）
    seen, uniq = set(), []
    for i, t in hits:
        if i not in seen:
            seen.add(i)
            uniq.append((i, t))
    return uniq


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('txt')
    ap.add_argument('out_dir')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--unit', default='回', help='文件名单位，默认「回」')
    args = ap.parse_args()

    if not os.path.isfile(args.txt):
        print(f'✗ 文件不存在：{args.txt}', file=sys.stderr)
        return 1
    try:
        text, enc = decode(args.txt)
    except ValueError as e:
        print(f'✗ {e}', file=sys.stderr)
        return 1
    lines = clean_lines(text)
    heads = find_headings(lines)
    if len(heads) < 2:
        print(f'✗ 只找到 {len(heads)} 个章标题，无法切章（可加 --unit 或检查格式）', file=sys.stderr)
        return 2

    print(f'编码 {enc}｜{len(lines)} 行｜识别 {len(heads)} 章')
    if args.dry_run:
        for k, (i, t) in enumerate(heads[:12], 1):
            print(f'  {k:>3}. {t[:40]}')
        if len(heads) > 12:
            print(f'  … 其余 {len(heads) - 12} 章')
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    index = [f'# 目录（txt 切章）\n', f'- 源文件：`{os.path.basename(args.txt)}`',
             f'- 编码：{enc}｜共 {len(heads)} 章\n']
    for k, (i, title) in enumerate(heads, 1):
        end = heads[k][0] if k < len(heads) else len(lines)
        body = '\n'.join(lines[i + 1:end]).strip()
        body = re.sub(r'\n{3,}', '\n\n', body)
        name = re.sub(r'[\\/:*?"<>|]', '_', f'第{k:03d}{args.unit}：{title[:30]}.md')
        write_text(os.path.join(args.out_dir, name),
                   f'# 第{k}{args.unit}　{title}\n\n{body}\n')
        index.append(f'- {name}（{len(re.sub(r"[　\s]", "", body))} 字）')
    write_text(os.path.join(args.out_dir, '_目录.md'), '\n'.join(index))
    print(f'✓ 已写出 {len(heads)} 章 → {args.out_dir}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
