#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""书籍文件管理体检：打印结构树 + 检查有没有文件放错层。

用法：
    python inventory.py <书目录>            # 结构 + 体检
    python inventory.py <书目录> --tree     # 只打印结构树
    python inventory.py <书目录> --check    # 只体检

检查项：
  1. 根目录有没有散落文件（白名单：`_` 开头 + `.zip`，其余都该归层）
  2. 该有的层是否齐全（9 个层目录全齐）
  3. 交付 docx 命名是否规范（中文数字 + 全角冒号）
  4. 009 其他\\版本\\快照\\ 有没有批前快照（没有快照＝改稿无从对照）
  5. 009 其他\\批次成品\\ 有没有成品包（没有＝改完没交付到人手上）
  6. _原稿 是否被改动（与只读约定对照，仅提示）

返回码：0 正常；2 有需要处理的问题。
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import ChapterError, num2cn, parse_chapter  # noqa: E402
from chapter_num import is_non_chapter  # noqa: E402  序章/番外的"预期内"判定

LAYERS = {
    "00 skill的内容": "流程说明 + 流程图（skill 本体在书外）",
    "001 世界观": "设定 + 报告（题材/基调/资料）",
    "003 梗概": "梗概",
    "004 目录": "章节表 / 先行章纲（写前定，冻结）",
    "005 章纲": "总结章纲（以此为准） / 章纲回填 / 编号对照表",
    "006 文章": "稿子的三个位置 + 错题本：扩展文章 / ai复查文章 / 人工复查文章",
    "007 最终文章": "定稿（交付用）",
    "008 进度": "新会话只读：上下文压缩包 / 续写档案 / 进度卡 / 未解决清单 / 文件地图",
    "009 其他": "版本（数据不流失）/ 问题本（教训不丢）/ 批次成品（给人读）/ 归档",
}

# 文章的四个位置：写出来 → AI 排查改完 → 人工排查改完 → 定稿
STAGES = [
    ("006 文章/扩展文章", "① 初稿"),
    ("006 文章/ai复查文章", "② AI 排查改完"),
    ("006 文章/人工复查文章", "③ 人工排查改完"),
    ("007 最终文章", "④ 定稿"),
]

ROOT_ALLOW = set()                    # 根目录白名单（文件地图等在 008 进度 里）
ROOT_ALLOW_PREFIX = ("_",)            # 下划线开头的结构记录文件允许
ROOT_ALLOW_SUFFIX = (".zip",)

# 交付 docx 命名：**逆推比对**，不维护第二份正则。
# 判据：解析文件名章号 -> 用 num2cn 生成规范形态 -> 比对实际名。
# 章号写法只有一个来源，inventory 永远不会和 md2docx 判定不一致。
# （此前 GOOD_DOCX 是死代码，且字符类缺「零/两/千」，第 100 章会被误判违规。）
CANONICAL_ONLY = re.compile(
    r"^第[零一二两三四五六七八九十百千]+章：.+\.docx$"
)


def docx_name_issue(fname):
    """返回问题描述；完全规范则返回 None。"""
    # 序章 / 番外 / 前言 / 后记 / 楔子**本就没有章号**，是正常内容。
    # md2docx 遇到它们走"预期内跳过"，这里必须给同样的定性 ——
    # 此前两边相反：转换脚本说正常、体检脚本判违规，用户无所适从。
    if is_non_chapter(fname):
        return None
    stem = fname[:-len(".docx")]
    try:
        n, title = parse_chapter(stem + ".md")
    except ChapterError as e:
        return f"文件名不是「第N章：标题.docx」格式（{e}）"
    if not title:
        return "缺少章标题"
    canonical = f"第{num2cn(n)}章：{title}.docx"
    if fname == canonical:
        return None
    if CANONICAL_ONLY.match(fname):
        return f"章号写法不规范：{fname} → 建议 {canonical}"
    return f"命名不规范：{fname} → 建议 {canonical}"


def walk_tree(root, max_files_per_dir=40):
    lines = []
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        rel = os.path.relpath(base, root)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        indent = "  " * depth
        name = os.path.basename(root) if rel == "." else os.path.basename(base)
        lines.append(f"{indent}{name}/  ({len(files)} 个文件)")
        for f in sorted(files)[:max_files_per_dir]:
            lines.append(f"{indent}  {f}")
        if len(files) > max_files_per_dir:
            lines.append(f"{indent}  … 另有 {len(files) - max_files_per_dir} 个文件")
    return lines


def check(root):
    issues = []
    notes = []

    # 1. 根目录散落文件
    stray = []
    for e in sorted(os.listdir(root)):
        p = os.path.join(root, e)
        if not os.path.isfile(p):
            continue
        if e in ROOT_ALLOW or e.startswith(ROOT_ALLOW_PREFIX) or e.endswith(ROOT_ALLOW_SUFFIX):
            continue
        stray.append(e)
    if stray:
        issues.append(f"根目录有 {len(stray)} 个散落文件（应归入对应层）：" + "、".join(stray[:8])
                      + ("…" if len(stray) > 8 else ""))
    else:
        notes.append("根目录干净（不放散落文件）")

    # 2. 层是否齐全
    missing = [d for d in LAYERS if not os.path.isdir(os.path.join(root, d))]
    if missing:
        issues.append("缺少层目录：" + "、".join(missing))
    else:
        notes.append(f"{len(LAYERS)} 个层目录齐全")

    # 3. 文章在各格的分布（进度一目了然）
    # 必须递归：章节通常放在 `人工复查文章\第一卷\` 这类卷子目录下，
    # 用 os.listdir 只数第一层会把全部章节数成 0，于是"文章分布"恒为空——
    # 又是假阴性（不报错，只是数字错）。
    stage_line = []
    for d, note in STAGES:
        p = os.path.join(root, d)
        n = 0
        if os.path.isdir(p):
            for _b, _ds, fs in os.walk(p):
                n += len([f for f in fs if not f.startswith("~$")])
        stage_line.append(f"{os.path.basename(d)} {n}")

    # 新书判定：007 最终文章 为空 = 还没改稿，缺快照/成品包属正常
    body_has_content = False
    _body = os.path.join(root, "007 最终文章")
    if os.path.isdir(_body):
        body_has_content = any(
            f for _b, _d, fs in os.walk(_body) for f in fs
            if not f.startswith("~$"))
    notes.append("文章分布：" + " ｜ ".join(stage_line))

    # 3b. 交付 docx 命名（逆推比对；顺带检出章号重复）
    body = os.path.join(root, "007 最终文章")
    bad, dupmap, total = [], {}, 0
    if os.path.isdir(body):
        for base, dirs, files in os.walk(body):
            for f in files:
                if not f.endswith(".docx") or f.startswith("~$"):
                    continue
                total += 1
                if is_non_chapter(f):
                    continue          # 预期内：不判命名、也不参与章号查重
                issue = docx_name_issue(f)
                if issue:
                    bad.append(issue)
                try:
                    dupmap.setdefault(parse_chapter(f[:-5] + ".md")[0], []).append(f)
                except ChapterError:
                    pass
    if bad:
        issues.append(f"最终文章 docx 命名需整改（{len(bad)} 个）："
                      + "；".join(bad[:4]) + ("…" if len(bad) > 4 else ""))
    elif total:
        notes.append(f"最终文章 docx 命名规范（{total} 个）")

    dups = {k: v for k, v in dupmap.items() if len(v) > 1}
    if dups:
        issues.append("章号重复（同名章多份docx，会互相覆盖）："
                      + "；".join(f"第{k}章：{'、'.join(v)}" for k, v in sorted(dups.items())))

    # 4. 快照
    hist = os.path.join(root, "009 其他", "版本", "快照")
    snaps = []
    if os.path.isdir(hist):
        snaps = [d for d in os.listdir(hist)
                 if os.path.isdir(os.path.join(hist, d)) and d.endswith("前")]
    if snaps:
        notes.append(f"批前快照 {len(snaps)} 个（版本\\快照）：" + "、".join(sorted(snaps)))
    else:
        # 尚未改稿的新书本来就没有快照 —— 那是正常状态，不是问题
        (notes if not body_has_content else issues).append(
            "没有批前快照——改稿前必须先建（数据不流失的第一道保险）")

    # 5. 批次成品包
    pack = os.path.join(root, "009 其他", "批次成品")
    packs = []
    if os.path.isdir(pack):
        packs = [d for d in os.listdir(pack)
                 if os.path.isdir(os.path.join(pack, d)) and not d.startswith("_")]
    if packs:
        notes.append(f"批次成品包 {len(packs)} 个：" + "、".join(sorted(packs)))
    else:
        (notes if not body_has_content else issues).append(
            "没有批次成品包——每批改完都要建一个，否则等于没交付到人手上")

    # 6. _原稿 是否被改动 —— **真正参与判定**（此前只打印 mtime，永不进 issues、
    #    永不返 2，与 SKILL.md「原稿有没有被动过」的表述不符）。
    #
    #    判据为什么这样定：没有基线文件就无法证明"内容变了"，只能用 mtime 推断。
    #    原稿的语义是"最初的稿"，所以它**不该比这本书后来的产物还新**。
    #    据此判定：有正文内容可供对照时（006/007 里有文件），若 _原稿 的最新
    #    mtime 晚于正文的最新 mtime，说明是开始写作之后才被动过的 → 报问题。
    #
    #    **为什么必须先要求"存在正文内容"**：一本刚建的书往往只有原稿，
    #    此时原稿自然是全书最新的文件。若不设这个前提，每本新书都会误报。
    old = os.path.join(root, "009 其他", "版本", "_原稿")
    if os.path.isdir(old):
        latest, latest_f = 0, ""
        for base, dirs, files in os.walk(old):
            for f in files:
                if f.startswith("~$"):
                    continue
                t = os.path.getmtime(os.path.join(base, f))
                if t > latest:
                    latest, latest_f = t, f
        if latest:
            import datetime
            old_d = datetime.datetime.fromtimestamp(latest).strftime("%Y-%m-%d %H:%M")
            # 找正文档案的最新 mtime 作为对照基准
            ref, ref_f = 0, ""
            for d in ("006 文章", "007 最终文章"):
                p = os.path.join(root, d)
                if not os.path.isdir(p):
                    continue
                for base, dirs, files in os.walk(p):
                    for f in files:
                        if f.startswith("~$"):
                            continue
                        t = os.path.getmtime(os.path.join(base, f))
                        if t > ref:
                            ref, ref_f = t, f
            if ref and latest > ref + 1:
                ref_d = datetime.datetime.fromtimestamp(ref).strftime("%Y-%m-%d %H:%M")
                issues.append(
                    f"_原稿 疑似被动过：原稿最新 {old_d}（{latest_f[:20]}），"
                    f"却晚于正文最新 {ref_d}（{ref_f[:20]}）—— 原稿应只读，请确认是不是误改")
            elif ref:
                notes.append(f"_原稿 未被改动（{old_d}，早于正文最新改动）—— 只读约定成立")
            else:
                notes.append(f"_原稿 最近修改 {old_d}（{latest_f[:20]}）；"
                             "书内还没有正文可供对照，暂不判定是否被动过")

    return issues, notes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("--tree", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--files", type=int, default=40, help="每个目录最多列几个文件")
    args = ap.parse_args()

    root = os.path.abspath(args.book)
    if not os.path.isdir(root):
        print("目录不存在：", root)
        return 1

    print("=" * 64)
    print(f"书籍目录：{root}")
    print("=" * 64)

    do_tree = args.tree or not args.check
    do_check = args.check or not args.tree

    if do_tree:
        print("\n【结构】")
        for line in walk_tree(root, args.files):
            print("  " + line)

    if do_check:
        issues, notes = check(root)
        print("\n【体检】")
        for n in notes:
            print("  ✓ " + n)
        if issues:
            print()
            for i in issues:
                print("  ⚠ " + i)
            print(f"\n共 {len(issues)} 项需要处理。")
            return 2
        print("\n没有发现问题。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
