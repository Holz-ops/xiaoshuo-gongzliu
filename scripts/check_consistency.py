#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""设定一致性扫描（一致性轨）。

判定两类结果：
  CONFLICT  真正的冲突，必须改（同一对象出现多种写法、纪年不统一、markdown 残留）
  LIST      待人工核对清单（日期、时间戳、编号），只汇总不判错

用法：
    python check_consistency.py <md目录> [--terms terms.md] [--strict] [--limit 15]

--terms 文件格式（每行一个词条，同义写法用 | 分隔，第一个为规范写法）：
    第七机甲维修站|第七维修站
    星历267年|星历 267 年
"""
import argparse
import glob
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import (  # noqa: E402  章号正则与"预期内"判定的唯一来源
    CHAPTER_HEAD_RE, is_non_chapter,
)
from textio import read_text  # noqa: E402

PATTERNS = {
    "纪年": re.compile(r"星历\s*([0-9一二三四五六七八九十百]+)\s*年"),
    "日期": re.compile(r"([0-9]{1,2})\s*月\s*([0-9]{1,2})\s*日"),
    "时间戳": re.compile(r"(?<![:0-9])([0-9]{1,2}):([0-9]{2})(?![:0-9])"),
    "编号": re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,4}-[0-9]{2,6})(?![A-Za-z0-9])"),
}

RESIDUE = {
    # 加粗必须按「成对标记」匹配：只写 \*\* 会把同一个加粗的左右两个
    # 标记各计一次（`**快跑**` 记成 2 处），凭空翻倍。
    "markdown残留": re.compile(r"\*\*[^*\n]+\*\*|^[ \t]*#{1,6}[ \t]|^[ \t]*\|[^\n]*\|[ \t]*$", re.M),
}

# 只放误报率低的；「粘了」这类两可用法不要放进来
TYPO = {
    "按装": "安装",
    "做为": "作为",
    "在说一次": "再说一次",
}
# 注：不含「板手」—— 它在汉语里是合法词（撬棍/杠杆），列入必然误报。

# 直引号是很多中文稿的正常排版，默认不查，--strict 才启用
STRICT_ONLY = {
    "半角引号": re.compile(r'"'),
}


def load_terms(path):
    """返回 (词条字典, 错误信息)。

    **绝不静默失败**：词典读不到就等于"专名检查没跑"，而下游仍然会打印
    「CONFLICT 无」—— 那是假阴性，比崩溃更危险（用户以为查过了）。
    所以路径不存在 / 无法解码 / 解出乱码，一律报错并让退出码反映失败。
    """
    groups = {}
    if not path:
        return groups, None
    if not os.path.exists(path):
        return groups, f"词典不存在：{path}"
    try:
        text = read_text(path)
    except (OSError, ValueError) as e:
        return groups, f"词典读取失败：{e}"
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|") if p.strip()]
        if parts:
            groups[parts[0]] = parts
    if not groups:
        return groups, f"词典里没有任何词条：{path}"
    return groups, None


def count_aliases(text, aliases):
    """统计各写法出现次数，**长词优先归属**。

    为什么不能各写各的 `re.findall(re.escape(a))`：
    互为子串的词条（张三 / 张三丰）会被**重复计数** —— 全文只写了「张三丰」，
    「张三」也会 +1，于是凭空出现"两种写法混用"的冲突。
    这里把所有写法按长度降序拼成一条正则，一次扫描，每次命中只归给**最长**的那个。
    """
    ordered = sorted(aliases, key=len, reverse=True)
    pat = re.compile("|".join(re.escape(a) for a in ordered))
    counts = {a: 0 for a in aliases}
    for m in pat.finditer(text):
        counts[m.group(0)] += 1
    return counts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--terms", default="")
    ap.add_argument("--era", default="", help="主纪年，如 267；其余纪年按历史纪年提示")
    ap.add_argument("--exclude", default="", help="文件名含这些词（逗号分隔）的跳过，如 报告,说明,清单,索引")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--limit", type=int, default=15)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*.md")))
    skip_words = [w.strip() for w in args.exclude.split(",") if w.strip()]
    skipped = [f for f in files if any(w in os.path.basename(f) for w in skip_words)]
    if skip_words:
        files = [f for f in files if not any(w in os.path.basename(f) for w in skip_words)]
    if not files:
        print("未找到 md 文件（无内容可扫描）：", args.dir)
        return 2

    groups, terms_err = load_terms(args.terms)
    if terms_err:
        print(f"✗ {terms_err}")
        print("  专名混用检查未执行 —— 不查就不该报「无冲突」。"
              "请修正 --terms 路径后重跑；确认不查专名则去掉该参数。")
        return 1
    conflicts = []           # (类型, 说明)
    notes = []               # 提示（不算错）
    listings = defaultdict(lambda: defaultdict(list))  # 类型 -> 值 -> [(文件,行)]

    pats = dict(PATTERNS)
    if args.strict:
        pats.update(STRICT_ONLY)

    per_file = []          # (文件名, 正文) —— 供跨文件判定使用

    for f in files:
        base = os.path.basename(f)
        try:
            text = read_text(f)
        except (OSError, ValueError) as e:
            print(f"  ✗ 跳过 {base}：{e}")
            continue
        per_file.append((base, text))

        for label, pat in pats.items():
            for m in pat.finditer(text):
                ln = text[: m.start()].count("\n") + 1
                listings[label][m.group(0).strip()].append((base, ln))

        for label, pat in RESIDUE.items():
            for m in pat.finditer(text):
                ln = text[: m.start()].count("\n") + 1
                line = text.split("\n")[ln - 1].strip()
                # 首行的标题（`# 第X章 …`）是规范写法，不算 markdown 残留。
                # 正则**复用 chapter_num**，不在这里另写一份字符类 ——
                # 此前自写的 `[0-9一二三四五六七八九十百]` 缺「零/两/千」，
                # 于是 `# 第一百零五章` 被判成 markdown 残留（误报）。
                # 序章/番外一类**本就没有章号**，首行标题同样合法，一并豁免 ——
                # 否则会和 md2docx / inventory 把它们定为「预期内」相反。
                if ln == 1 and (CHAPTER_HEAD_RE.match(line) or is_non_chapter(base)):
                    continue
                conflicts.append((label, f"{base} 第{ln}行：{line[:60]}"))

        for wrong, right in TYPO.items():
            if wrong in text:
                ln = text[: text.index(wrong)].count("\n") + 1
                conflicts.append(("疑似错别字", f"{base} 第{ln}行：「{wrong}」应为「{right}」"))

    # 专名混用：**跨文件汇总**判定。
    # 只在单文件内比对会漏掉最常见的形态 —— 各章各用一种写法，
    # 每章内部都"一致"，问题要到全书读完才暴露。
    for canon, aliases in groups.items():
        total = {a: 0 for a in aliases}
        where = {a: [] for a in aliases}
        for base, text in per_file:
            for a, c in count_aliases(text, aliases).items():
                if c:
                    total[a] += c
                    where[a].append(base)
        used = {a: c for a, c in total.items() if c}
        if len(used) > 1:
            detail = "、".join(f"{a}×{c}" for a, c in sorted(used.items()))
            conflicts.append(("专名混用", f"{detail} → 建议统一为「{canon}」"))

    # 纪年：给了 --era 就认它是主纪年，其余算历史纪年（只提示）；没给就视为冲突
    eras = listings.get("纪年", {})
    if args.era:
        # fullmatch 而非 `in`：否则 `--era 267` 会把「星历 2678 年」也认成主纪年
        era_re = re.compile(rf"星历\s*{re.escape(args.era)}\s*年")
        main_era = [v for v in eras if era_re.fullmatch(v)]
        others = {v: len(loc) for v, loc in eras.items() if v not in main_era}
        if not main_era:
            # 单章未写纪年属正常，降级为提示；全卷都缺才需检查
            notes.append(f"未发现主纪年「{args.era}」——单章未写纪年属正常；若整卷都缺，检查设定基准表")
        if others:
            conflicts.append(("历史纪年（确认是否有意设定）", f"{others}"))
    elif len(eras) > 1:
        conflicts.append(("纪年不统一", f"{ {v: len(loc) for v, loc in eras.items()} }"))

    print("=" * 60)
    print(f"扫描 {len(files)} 个文件" + (f"，跳过 {len(skipped)} 个非正文文件" if skipped else ""))
    print("=" * 60)

    if conflicts:
        print(f"\n【CONFLICT】必须处理，共 {len(conflicts)} 处\n")
        for label, msg in conflicts:
            print(f"  [{label}] {msg}")
    else:
        print("\n【CONFLICT】无\n")

    if notes:
        print("【提示】\n")
        for n in notes:
            print(f"  · {n}")
        print()

    print("【LIST】待人工核对（只汇总，不判错）\n")
    for label in ["纪年", "日期", "时间戳", "编号"] + (["半角引号"] if args.strict else []):
        items = listings.get(label)
        if not items:
            continue
        print(f"  {label}：{len(items)} 种")
        for val, loc in sorted(items.items(), key=lambda kv: -len(kv[1]))[: args.limit]:
            files_hit = sorted({f for f, _ in loc})
            where = "、".join(files_hit[:3]) + ("…" if len(files_hit) > 3 else "")
            print(f"    {val:<16} 共 {len(loc):>3} 次   {where}")
    print("\n提示：日期与时间戳需对照设定基准表的时间轴；编号需对照编号总表查重。")
    return 2 if conflicts else 0


if __name__ == "__main__":
    sys.exit(main())
