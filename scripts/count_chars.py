#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计每章净字数并标记越界。

口径：去空白后的字符数（含标点），标题行（首行）不计。
用法：
    python count_chars.py <md目录> [--min 2000] [--max 3000] [--csv out.csv]
"""
import argparse
import csv
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import (  # noqa: E402
    CHAPTER_HEAD_RE, ChapterError, parse_chapter, strip_chapter_prefix,
)
from textio import read_text  # noqa: E402

# 章号缺失的文件**单列一组**，不参与章序排序。
# 此前用 9999 兜底，会让「缺章」伪装成「第 9999 章」，掩盖真问题。
# 正则复用 chapter_num（含 章/回/节/段 四种单位），不再自写一份 —— 自写的那份
# 只认「章」，于是 txt_split 切出的「第001回：开端」标题没被剥离，
# 章名会连前缀一起显示成"第001回：开端"。
TITLE_RE = CHAPTER_HEAD_RE


def chapter_no(path):
    """章号；无法解析返回 None（调用方单列，**不静默塞 9999 假值**）。"""
    try:
        return parse_chapter(path)[0]
    except ChapterError:
        return None



def split_title(lines):
    """首行若为章节标题则拆出（标题不计字数）。"""
    if lines and TITLE_RE.match(lines[0].strip()):
        return lines[0].lstrip("# ").strip(), lines[1:]
    return "", lines


def net_chars(lines):
    _, body = split_title(lines)
    return len(re.sub(r"\s", "", "\n".join(body)))


def disp_width(s):
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def pad(s, width):
    return s + " " * max(0, width - disp_width(s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--min", type=int, default=2000)
    ap.add_argument("--max", type=int, default=3000)
    ap.add_argument("--csv", default="")
    ap.add_argument("--force", action="store_true",
                    help="允许覆盖已存在的 --csv 文件")
    args = ap.parse_args()

    # **递归扫子目录**：项目普遍把章放在 `第一卷/` 下（分卷是常态），
    # 非递归的 `*.md` 会把 49 章的书报成「未找到 md 文件」，然后 exit=2。
    # 排除 `_` 开头的文件与目录（_说明.md / _目录.md / 其他非正文产物）。
    files = sorted(
        (f for f in glob.glob(os.path.join(args.dir, "**", "*.md"), recursive=True)
         if not os.path.basename(f).startswith("_")
         and not any(part.startswith(("_", "."))
                     for part in os.path.relpath(f, args.dir).split(os.sep)[:-1])),
        key=lambda p: (chapter_no(p) is None, chapter_no(p) or 0, p))
    unnumbered = [f for f in files if chapter_no(f) is None]
    if not files:
        print("未找到 md 文件（无内容可统计）：", args.dir)
        return 2

    rows, total = [], 0
    unreadable = []
    for f in files:
        try:
            lines_f = read_text(f).split("\n")
        except (OSError, ValueError) as e:
            print(f"  ✗ 跳过 {os.path.basename(f)}：{e}")
            unreadable.append(os.path.basename(f))
            continue
        title, _ = split_title(lines_f)
        title = strip_chapter_prefix(title)
        if not title:
            title = os.path.basename(f).replace(".md", "")
        n = net_chars(lines_f)
        total += n
        flag = "OK"
        if n < args.min:
            flag = f"低于下限(-{args.min - n})"
        elif n > args.max:
            flag = f"超出上限(+{n - args.max})"
        rows.append((chapter_no(f), title, n, flag))

    # rows 可能为空：md 文件存在但**全部读不出**（如整批非 UTF-8/GB18030 或疑似乱码）。
    # 此前直接 `max(...)` → "max() iterable argument is empty" 崩栈，
    # 用户看不到"一个字都没统计到"这件要紧事，只看到 traceback。
    if not rows:
        print(f"✗ 没有任何文件统计成功（共 {len(files)} 个 md，"
              f"{len(unreadable)} 个读不出）：{args.dir}")
        for name in unreadable:
            print(f"    · {name}")
        return 2

    w = max(disp_width(r[1]) for r in rows) + 2
    print(pad("章", 8) + pad("标题", w) + f"{'字数':>6}  状态")
    print("-" * (w + 26))
    dup = {}
    for no, title, n, flag in rows:
        label = f"第{no}章" if no is not None else "未编号"
        print(pad(label, 8) + pad(title, w) + f"{n:>6}  {flag}")
        if no is not None:
            dup.setdefault(no, []).append(title)
    print("-" * (w + 24))
    print(f"共 {len(rows)} 章，合计 {total} 字，均 {total / len(rows):.1f} 字"
          if rows else "共 0 章")
    if unnumbered:
        print(f"\n⚠ {len(unnumbered)} 个文件无「第N章」编号（未参与章序排序）："
              + "、".join(os.path.basename(f) for f in unnumbered[:6])
              + ("…" if len(unnumbered) > 6 else ""))
    conflicts = {k: v for k, v in dup.items() if len(v) > 1}

    if conflicts:
        print("\n⚠ 章号重复（同章多份文件，交付时会互相覆盖）：")
        for k, v in sorted(conflicts.items()):
            print(f"    第{k}章：{'、'.join(v)}")

    bad = [r for r in rows if r[3] != "OK"]
    if bad:
        print(f"越界 {len(bad)} 章：" + "、".join(r[1] for r in bad))
    else:
        print("全部落在区间内。")

    if args.csv:
        if os.path.exists(args.csv) and not args.force:
            print(f"✗ {args.csv} 已存在；会被覆盖。确认要覆盖请加 --force。")
            return 1
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            wcsv = csv.writer(fh)
            wcsv.writerow(["文件", "标题", "字数", "状态"])
            wcsv.writerows(rows)
        print("已写出：", args.csv)
    if unreadable:
        print(f"\n⚠ {len(unreadable)} 个文件读不出、未计入总字数："
              + "、".join(unreadable[:6]) + ("…" if len(unreadable) > 6 else ""))
    # 退出码：0 通过 / 1 章号重号 / 2 无可统计内容 / 3 部分文件读不出（总字数不全）。
    # 重号优先于「读不出」：两者同现时若返回 3，重号这条会被门禁忽略。
    if conflicts:
        return 1
    if unreadable:
        return 3          # 部分未完成：总字数不全，别拿它去对平台规格
    return 0


if __name__ == "__main__":
    sys.exit(main())
