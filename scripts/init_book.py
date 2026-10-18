#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为一本新书初始化「一条完整流水线」的目录与起始文件。

核心约定：**一本书 = 一条流水线（单书隔离）**。
每本书独立一套目录、独立的进度卡、独立的章纲两条、独立的三层检查。
A 书的任何文件都不得用到 B 书上；同一作者同时写两本，也是两条独立流水线。

用法：
    python init_book.py "书名" <父目录> [--dry-run]

例：
    python init_book.py "星门纪元" "D:/小说"
    -> 生成 D:/小说/星门纪元/（00 skill的内容 … 009 其他），并从模板铺好起始文件
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from textio import read_text, write_text  # noqa: E402  统一的严格解码回退

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(SKILL_DIR, "assets", "templates")

# 书名必须安全：不含路径分隔符、不含 Windows 保留字符、不是 . 或 ..
_BAD_CHARS = re.compile(r'[\\/:*?"<>|]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} \
    | {f"LPT{i}" for i in range(1, 10)}


def validate_book(name):
    """校验书名，返回错误信息（空串= 合法）。"""
    if not name or not name.strip():
        return "书名不能为空"
    if name in (".", ".."):
        return f"书名不能是 {name}（否则会写进父目录本身）"
    if _BAD_CHARS.search(name):
        bad = _BAD_CHARS.findall(name)
        return f"书名含非法字符 {' '.join(bad)}（不能用路径分隔符或 : * ? \" < > |）"
    if name.split(".")[0].upper() in _RESERVED:
        return f"书名 {name} 是系统保留名，换一个"
    if len(name) > 64:
        return "书名过长（>64 字符）"
    return ""

DIRS = [
    "00 skill的内容",
    "001 世界观/报告",
    "003 梗概",
    "004 目录",
    "005 章纲",
    "006 文章/扩展文章",
    "006 文章/ai复查文章",
    "006 文章/人工复查文章",
    "006 文章/错题本/ai查出的",
    "006 文章/错题本/人查出的",
    "007 最终文章",
    "008 进度",
    "009 其他/版本/快照",
    "009 其他/版本/_原稿",
    "009 其他/问题本/ai查出的",
    "009 其他/问题本/人查出的",
    "009 其他/批次成品",
    "009 其他/归档",
]

# 目录 -> 说明（打印用）
# **键必须与 DIRS 里的字符串完全一致** —— 此前这里只写了"层目录名"
# （`001 世界观`），而 DIRS 里是带子层的完整路径（`001 世界观/报告`），
# 于是 18 个目录里有 10 个查不到说明、打印出一片空白。
DIR_NOTE = {
    "00 skill的内容": "流程说明 + 流程图（skill 本体留在书外）",
    "001 世界观": "设定资料（人物 / 时间线 / 体系…按书需要）/ 设定基准表 / 校验词典",
    "001 世界观/报告": "① 报告的落点（题材 · 基调 · 资料）",
    "003 梗概": "梗概（报告可放 001 世界观\\报告\\）",
    "004 目录": "章节表 / 先行章纲（写前定，冻结）",
    "005 章纲": "总结章纲（以此为准） / 章纲回填 / 编号对照表",
    "006 文章": "稿子的三个位置 + 错题本：扩展文章 → ai复查文章 → 人工复查文章",
    "006 文章/扩展文章": "① 初稿（刚扩写完，还没查）",
    "006 文章/ai复查文章": "② AI 排查改完，等人确定",
    "006 文章/人工复查文章": "③ 人工排查改完，通过",
    "006 文章/错题本/ai查出的": "机器查出的问题（工作副本，主本在 009 其他\\问题本）",
    "006 文章/错题本/人查出的": "人提的意见（工作副本，主本在 009 其他\\问题本）",
    "007 最终文章": "定稿（交付用）",
    "008 进度": "新会话只读：上下文压缩包 / 续写档案 / 进度卡 / 未解决清单 / 文件地图",
    "009 其他": "三样支援：版本（数据不流失）/ 问题本（教训不丢）/ 批次成品（给人读）",
    "009 其他/版本/快照": "改稿前的备份（数据不流失的第一道保险）",
    "009 其他/版本/_原稿": "原始稿件（**只读**）",
    "009 其他/问题本/ai查出的": "机器查出的问题（主本，长期台账）",
    "009 其他/问题本/人查出的": "人提的意见（主本，长期台账）",
    "009 其他/批次成品": "每批一包（给人读的唯一入口）",
    "009 其他/归档": "废弃流程与旧文档",
}


def note_for(d):
    """取目录说明；精确匹配优先，退回到它所属的层。"""
    return DIR_NOTE.get(d) or DIR_NOTE.get(d.split("/")[0], "")

# 模板 -> 目标（相对书目录）
COPY = [
    ("进度卡模板.md", "008 进度/进度卡.md"),
    ("续写档案模板.md", "008 进度/续写档案.md"),
    ("上下文压缩包模板.md", "008 进度/上下文压缩包.md"),
    ("报告模板.md", "001 世界观/报告/报告.md"),
    ("目录与章纲模板.md", "004 目录/先行章纲.md"),
    ("章纲总结模板.md", "005 章纲/总结章纲.md"),
    ("自查报告模板.md", "009 其他/问题本/ai查出的/自查报告.md"),
    ("改进记录模板.md", "009 其他/问题本/人查出的/改进记录.md"),
    ("批次导读模板.md", "009 其他/批次成品/_批次导读模板.md"),
    ("流程说明模板.md", "00 skill的内容/流程说明.md"),
]

VERSION_INDEX = """# 正文版本索引

> **一句话规则：看路径，不看文件名。**
>
> | 路径 | 身份 |
> |---|---|
> | `007 最终文章\\第X章-xxx.md` | **当前有效版**（md 源，一切后续工作以它为准） |
> | `009 其他\\版本\\快照\\<日期>-<批次>前\\第X章-xxx.md` | **该批改动前的版本**（存档，只读） |
> | `007 最终文章\\第X章-xxx.docx` | 当前交付版（由当前 md 生成） |
> | `009 其他\\版本\\快照\\<日期>-<批次>前\\第X章-xxx.docx` | 该批改前的交付版快照 |
>
> 文件名两边一样，**区分只看有没有「快照\\<日期>-<批次>前」这一层**。

## 一、快照一览

| 快照目录 | 日期 | 涵盖章节 | 性质 | 状态 |
|---|---|---|---|---|
| （还没有快照） | | | | |

## 二、各章改动状态

| 章 | 最近改动 | 所属批次 | 快照可查 |
|---|---|---|---|
| | | | |

## 三、每批改稿的标准动作

1. **改前**：把这批要动的 md 复制到 `009 其他\\版本\\快照\\<日期>-<批次>前\\`
2. **改前（可选）**：把这批要动的 docx 复制到同目录
3. **改**：按「文章怎么走」的格子走（扩写 → 复查 → 定稿），一份文章只有一个位置
4. **改后**：重新生成 docx（**中文数字命名 + 全角冒号**）；在本索引登记一行
5. **不需要"批后快照"**——下一批的"批前"自然包含这一批的"改后"

## 四、为什么不做「批后快照」

因为冗余。真正会丢版本的场景只有一个：**改之前没快照**。所以只需守住"批前"这一道。
"""


COPY_NOTE = """# 这是错题本的「工作副本」

> 改稿时人就在 `006 文章\\` 里干活，错题本放旁边**不用来回跳目录**。

- **主本（唯一）**：`009 其他\\问题本\\` —— 长期台账，以它为准
- **工作副本**：本目录 —— 改稿时就近看

**同步规则**：主本一更新，本副本就跟一遍（重新复制过去）。两份不一致时，**以主本为准**。

## 两份内容

| 目录 | 装什么 | 谁来填 |
|---|---|---|
| `ai查出的\\` | 机器查出来的：字数越界、一致性冲突、跨章穿帮、markdown 残留 | AI |
| `人查出的\\` | 人提的意见：这段没意思、这个人太木、想跳过 | 人 |
"""


# 只有这些"噪音"时不算"目录里有内容"：它们是系统/编辑器自动生成的，
# 用户往往根本不知道它们在。判空时若把它们算进去，会永久拒绝一个空目录。
NOISE_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini", "ehthumbs.db"}
NOISE_EXTS = (".tmp", ".swp")


def scan_existing(root):
    """返回 (真实内容列表, 噪音列表, 空子目录列表)。"""
    real, noise, empty_dirs = [], [], []
    if not os.path.isdir(root):
        return real, noise, empty_dirs
    for e in sorted(os.listdir(root)):
        p = os.path.join(root, e)
        if os.path.isdir(p):
            # 只含噪音/空的子目录不算"有内容"
            try:
                inner = os.listdir(p)
            except OSError:
                inner = [""]
            if not [x for x in inner if x not in NOISE_NAMES
                    and not x.startswith(".") and not x.endswith(NOISE_EXTS)]:
                empty_dirs.append(e + "/")
            else:
                real.append(e + "/")
            continue
        if e in NOISE_NAMES or e.startswith(".") or e.endswith(NOISE_EXTS):
            noise.append(e)
        else:
            real.append(e)
    return real, noise, empty_dirs


def precheck_templates():
    """落盘**之前**先确认模板齐全。

    顺序很要紧：此前是先建目录、最后才报"模板缺失"并 return 3 ——
    于是目录已经非空，用户补好模板重跑会被"目标已存在且非空"拒绝，
    **进退两难**。所以模板检查必须排在 makedirs 前面。
    """
    missing = []
    for src, _dst in COPY:
        if not os.path.exists(os.path.join(TPL, src)):
            missing.append(src)
    return missing


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book", help="书名")
    ap.add_argument("parent", help="父目录（会在其下创建以书名命名的文件夹）")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不落盘")
    args = ap.parse_args()

    bad = validate_book(args.book)
    if bad:
        print(f"✗ 书名不合法：{bad}")
        return 1

    parent = os.path.abspath(args.parent)
    root = os.path.join(parent, args.book)
    # 双保险：拼接后再确认仍位于 parent 内（防将来规则改动后回退）
    if os.path.commonpath([root, parent]) != parent:
        print("✗ 拒绝：目标路径会逃出父目录")
        return 1
    real, noise, empty_dirs = scan_existing(root)
    if real:
        print(f"⚠  目标已存在且非空：{root}")
        print("    单书隔离原则：一本书一条流水线，不要往已有书里塞新流程。")
        # **说清楚到底看见了什么**：此前只说"非空"，用户不知道里面是什么，
        # 更不知道那些 .DS_Store 之类的东西该不该管。
        print(f"    里面已有 {len(real)} 项："
              + "、".join(real[:10]) + ("…" if len(real) > 10 else ""))
        print("    如确认要在此目录初始化，请先清空或换一个父目录。")
        return 1
    if noise or empty_dirs:
        print(f"（目标已存在，但只有系统噪音/空目录，视为空："
              f"{'、'.join(noise + empty_dirs) if noise or empty_dirs else ''}）")

    # 模板齐全才落盘 —— 否则会出现"目录建了一半、还不能再建"的死锁
    pre = precheck_templates()
    if pre:
        print(f"✗ skill 安装不完整：assets/templates/ 缺少 {len(pre)} 个模板 —— "
              + "、".join(pre))
        print("  **未创建任何目录**（模板不全时先落盘会留下半个骨架，"
              "补好模板再跑反而会被『非空』判定拒住，进退两难）。")
        print("  请修复 skill 安装后重试。")
        return 3

    print(f"📕 新书：{args.book}")
    print(f"📂 位置：{root}")
    print("=" * 56)

    if args.dry_run:
        for d in DIRS:
            print(f"  建目录  {d:<24} {note_for(d)}")
        print("  铺模板：")
        for src, dst in COPY:
            print(f"    {src}  ->  {dst}")
        print("    ✓ 生成 006 文章/错题本/_说明.md")
        print("    ✓ 生成 009 其他/版本/版本索引.md")
        return 0

    os.makedirs(root, exist_ok=True)
    for d in DIRS:
        os.makedirs(os.path.join(root, d), exist_ok=True)
        print(f"  建目录  {d:<24} {note_for(d)}")

    print("  铺模板：")
    missing = []
    today = __import__("datetime").date.today().isoformat()
    for src, dst in COPY:
        s = os.path.join(TPL, src)
        if not os.path.exists(s):
            missing.append(src)
            print(f"    ✗ 模板缺失 {src}")
            continue
        try:
            text = read_text(s)
        except ValueError as e:
            missing.append(src)
            print(f"    ✗ 模板无法解码 {src}：{e}")
            continue
        # 替换占位符 —— 否则新会话读到的是占位符，认不出是哪本书。
        # 用 {{BOOK_TITLE}}（含书名号）整串替换，避免出现孤立的右括号。
        title = f"《{args.book}》"
        text = text.replace("{{BOOK_TITLE}}", title)
        text = text.replace("《书名》", title)
        text = text.replace("YYYY-MM-DD", today)
        d = os.path.join(root, dst)
        write_text(d, text)
        print(f"    ✓ {src}  ->  {dst}")

    # 错题本说明（副本目录里，防止误当主本改）
    cuoti = os.path.join(root, "006 文章", "错题本", "_说明.md")
    with open(cuoti, "w", encoding="utf-8") as fh:
        fh.write(COPY_NOTE)
    print("    ✓ 生成 006 文章/错题本/_说明.md")

    # 版本索引骨架
    vi_dir = os.path.join(root, "009 其他", "版本")
    os.makedirs(vi_dir, exist_ok=True)
    with open(os.path.join(vi_dir, "版本索引.md"), "w", encoding="utf-8") as fh:
        fh.write(VERSION_INDEX)
    print("    ✓ 生成 009 其他/版本/版本索引.md")

    print("=" * 56)
    print("下一步：从【入口】进——")
    print("  · 新书（从零）  ：头脑风暴 → ① 报告 → ② 梗概 → ③ 目录 → ④ 章纲 → ⑤ 正文（含章纲总结）→ ⑥ 复查 → 交付")
    print("  · 续写（素材已有）：素材体检 + 定基准 → ① 报告 → 同上")
    print("每一步都要过人工确认门；进度记在 008 进度/进度卡.md。")
    if missing:
        print(f"✗ 有 {len(missing)} 个模板没铺上（{', '.join(missing)}），"
              f"skill 安装不完整，请检查 assets/templates/ 是否齐全。")
        return 3        # 模板不全 = 安装损坏，必须让调用方知道
    print("    （已把《书名》与 YYYY-MM-DD 替换为真实值）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
