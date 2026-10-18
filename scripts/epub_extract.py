#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""epub → 逐章 md（供逐章"为什么"分析用）。

用法：
    python epub_extract.py <book.epub> <输出目录> [--dry-run]

做什么：
    1. 读 epub（本质是 zip）：container.xml → OPF → spine 顺序
    2. 按 spine 抽 XHTML 正文，去标签、还原实体、保留段落
    3. 按「第X章 / 第X回 / Chapter N」切章；切不出就一篇一章
    4. 输出 `第N章：标题.md` + `_目录.md`（下划线开头，脚本不会把它当正文）

退出码：
    0 = 成功
    1 = 参数/文件错误（不是 epub、找不到 OPF、写盘失败）
    2 = 没有抽到任何正文（空书 / 全图片 / 加密）

边界：
    - 只处理**无 DRM** 的 epub；加密书会报错，不会产出空文件。
    - 不做 OCR：纯图片页抽不出文字，会在跳过清单里标出。
"""
import argparse
import os
import re
import sys
import zipfile
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textio import write_text  # noqa: E402

# 章节标题：第1章 / 第一章 / 第一回 / 第 12 节 / Chapter 3
CHAP_RE = re.compile(
    r'^\s*('
    r'第\s*[0-9零一二三四五六七八九十百千两]+\s*[章回节卷]'
    r'|Chapter\s+[0-9IVXLC]+'
    r')\s*[：:、.．\-—]?\s*(.{0,40})$',
    re.IGNORECASE,
)

BLOCK_TAGS = {'p', 'div', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
              'li', 'tr', 'blockquote', 'section', 'article'}


class TextExtractor(HTMLParser):
    """把 XHTML 抽成纯文本：块级标签换行，其余只取 data。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self._skip += 1
        elif tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self._skip:
            self._skip -= 1
        elif tag in BLOCK_TAGS:
            self.parts.append('\n')

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self):
        raw = ''.join(self.parts)
        raw = raw.replace('\r\n', '\n').replace('\r', '\n')
        lines = [re.sub(r'[ \t\u3000]+', ' ', l).strip() for l in raw.split('\n')]
        return '\n'.join(l for l in lines if l)


def read_opf(zf):
    """返回 (opf_path, opf_dir)。"""
    try:
        container = zf.read('META-INF/container.xml').decode('utf-8', 'ignore')
    except KeyError:
        raise ValueError('不是合法 epub：缺 META-INF/container.xml')
    m = re.search(r'full-path="([^"]+)"', container)
    if not m:
        raise ValueError('container.xml 里找不到 rootfile full-path')
    opf_path = m.group(1)
    return opf_path, os.path.dirname(opf_path)


def parse_spine(xml):
    """返回按 spine 顺序排好的 (href, media_type) 列表。"""
    manifest = {}
    for m in re.finditer(r'<item\b[^>]*>', xml):
        tag = m.group(0)
        idm = re.search(r'\bid="([^"]+)"', tag)
        href = re.search(r'\bhref="([^"]+)"', tag)
        mt = re.search(r'media-type="([^"]+)"', tag)
        if idm and href:
            manifest[idm.group(1)] = (href.group(1), mt.group(1) if mt else '')
    order = []
    for m in re.finditer(r'<itemref\b[^>]*idref="([^"]+)"', xml):
        ref = m.group(1)
        if ref in manifest:
            order.append(manifest[ref])
    if not order:  # 没有 spine 就退化成按 manifest 顺序
        order = list(manifest.values())
    return [x for x in order if 'html' in x[1].lower() or x[0].lower().endswith(('.html', '.xhtml', '.htm'))]


def split_chapters(text):
    """按章节标题切；没有标题就整篇当一章。返回 [(heading, body)]。"""
    lines = text.split('\n')
    idx = [i for i, l in enumerate(lines) if CHAP_RE.match(l)]
    if not idx:
        return [('', text)]
    out = []
    if idx[0] > 0:  # 第一章之前的版权页/前言
        head = '\n'.join(lines[:idx[0]]).strip()
        if head:
            out.append(('前言', head))
    for k, i in enumerate(idx):
        end = idx[k + 1] if k + 1 < len(idx) else len(lines)
        heading = lines[i].strip()
        body = '\n'.join(lines[i + 1:end]).strip()
        out.append((heading, body))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('epub')
    ap.add_argument('out_dir')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not os.path.isfile(args.epub):
        print(f'✗ 文件不存在：{args.epub}', file=sys.stderr)
        return 1
    try:
        zf = zipfile.ZipFile(args.epub)
    except zipfile.BadZipFile:
        print(f'✗ 不是 zip/epub 文件：{args.epub}', file=sys.stderr)
        return 1

    try:
        with zf:
            opf_path, opf_dir = read_opf(zf)
            xml = zf.read(opf_path).decode('utf-8', 'ignore')
            spine = parse_spine(xml)

            chapters, empty = [], []
            for href, _mt in spine:
                path = os.path.normpath(os.path.join(opf_dir, href)).replace('\\', '/')
                try:
                    raw = zf.read(path).decode('utf-8', 'ignore')
                except KeyError:
                    empty.append(path)
                    continue
                ex = TextExtractor()
                ex.feed(raw)
                txt = ex.text()
                if not txt:
                    empty.append(path)
                    continue
                chapters.extend(split_chapters(txt))
    except ValueError as e:
        print(f'✗ {e}', file=sys.stderr)
        return 1
    except (RuntimeError, NotImplementedError) as e:
        print(f'✗ 可能是加密/DRM 的 epub，无法解压正文：{e}', file=sys.stderr)
        return 2

    if not chapters:
        print('✗ 没抽到任何正文（空书 / 全图片 / 加密）', file=sys.stderr)
        return 2

    if args.dry_run:
        print(f'可切出 {len(chapters)} 章（dry-run 未写盘）：')
        for i, (h, b) in enumerate(chapters[:15], 1):
            print(f'  {i:>3}. {h or "(无标题)"}  {len(b)} 字')
        if len(chapters) > 15:
            print(f'  … 其余 {len(chapters) - 15} 章')
        return 0

    os.makedirs(args.out_dir, exist_ok=True)
    index = ['# 目录（epub 抽取）\n', f'- 源文件：`{os.path.basename(args.epub)}`', f'- 共 {len(chapters)} 章\n']
    written = 0
    for i, (heading, body) in enumerate(chapters, 1):
        m = CHAP_RE.match(heading) if heading else None
        if m:
            num_raw = re.sub(r'[^0-9零一二三四五六七八九十百千两]', '', m.group(1))
            title = m.group(2).strip()
            name = f'第{num_raw}章：{title}.md' if title else f'第{num_raw}章.md'
        elif heading == '前言':
            name = '_前言.md'
        else:
            name = f'第{i}章：{heading or "未命名"}.md'
        name = re.sub(r'[\\/:*?"<>|]', '_', name)
        text = f'# {heading}\n\n{body}\n' if heading else f'{body}\n'
        write_text(os.path.join(args.out_dir, name), text)
        index.append(f'- {name}（{len(re.sub(chr(92) + "s", "", body))} 字）')
        written += 1

    write_text(os.path.join(args.out_dir, '_目录.md'), '\n'.join(index))
    print(f'✓ 已抽取 {written} 章 → {args.out_dir}')
    if empty:
        print(f'  跳过 {len(empty)} 个无文本条目（可能是图片页）：')
        for p in empty[:10]:
            print(f'    · {p}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
