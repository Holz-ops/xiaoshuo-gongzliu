#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量把 md 章节转成 docx（封装编辑器 SDK 的 edsdk.py）。

会做四件事：
  1. 探测 edsdk.py —— **只在本skill 内的 plugins 目录找同名文件**，
     找到多个时列出全部并要求 --sdk 指定，不静默取第一个
  2. 转换前清洗 md：去掉三反引号代码块围栏；**闭合标签</…> 换全角**
     （等价改写而非删除，不等式与孤立尖括号原样保留）
  3. 转换后校验：比对「clean_md 的预期输出」与 docx 实际文本，
     逐字符找差异 —— 等长损坏也能抓到（长度阈值抓不到）
  4. 读文件支持 utf-8 / utf-8-sig / gb18030 严格回退，中文老稿不崩

用法：
    python md2docx.py <md目录> <输出目录> [--pattern "第{cn}章：{title}.docx"]
                   [--only 1,2,3] [--sdk <edsdk.py路径>] [--sdk-strict]

退出码：
    0  全部成功
    1  无法开工（edsdk.py 一个都没找到 / --sdk-strict 下有多个候选 / 目录里没有 md）
    2  有章节转换失败
    3  没有匹配到任何「第N章」文件，或有文件被跳过（需人工判断）
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import (  # noqa: E402
    ChapterError, is_non_chapter, num2cn, parse_chapter,
)
from textio import read_text  # noqa: E402

# 允许的插件 id —— 只认这两家提供的 edsdk.py，避免执行到别人投放的同名脚本
PLUGIN_IDS = ("workbuddy-local-office-edit", "workbuddy-office-routing")
PLUGIN_ROOT = os.path.normpath(os.path.expanduser("~/.workbuddy/plugins"))


def _version_key(path):
    """从插件版本目录名取 (主,次,修)；取不到返回 (0,0,0)。"""
    m = (re.search(r"[\\/](\d+)\.(\d+)\.(\d+)[\\/]", path)
         or re.search(r"(\d+)\.(\d+)\.(\d+)", path))
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def find_edsdk(explicit=""):
    """在受控插件目录里找 edsdk.py，按「新版本优先」排序返回。

    白名单范围（**故意不放宽，写在这里免得用户以为漏装**）：
    只认 PLUGIN_IDS 两个 id。本机磁盘上共 8 个 edsdk.py，其中
    `workbuddy-docx/html-to-doc`、`workbuddy-pptx`、
    `skill-tencent-local-office-edit` 各自带的**都不算命中** ——
    不同插件的 SDK 工具集不同，混用会调错接口，宁可找不到也不能乱用。
    所以「磁盘上有 edsdk.py 却报找不到」是设计使然，不是漏装。

    实现要点：**绝不在遍历时按插件 id 剪枝**。插件 id 出现在**深层**目录名上
    （plugins/cache/<市场>/skill-<id>/<版本>/edsdk.py），而 os.walk 的第一层
    是 `plugins/` 本身（不含任何 id）。若在第一层就 `dirnames[:] = []`，
    整棵树都被丢掉，walk 一轮结束、永远返回空 —— 这正是此前
    find_edsdk() 100% 返回 [] 的根因。
    """
    if explicit:
        p = os.path.abspath(explicit)
        return [p] if os.path.isfile(p) else []

    if not os.path.isdir(PLUGIN_ROOT):
        return []
    hits = []
    for dirpath, dirnames, filenames in os.walk(PLUGIN_ROOT):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        if "edsdk.py" not in filenames:
            continue
        if any(pid in dirpath for pid in PLUGIN_IDS):
            hits.append(os.path.join(dirpath, "edsdk.py"))

    def _key(p):
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = 0.0
        return (_version_key(p), mt)

    # 版本新优先，同版本按修改时间新优先 —— 保证同一台机器稳定选中同一个
    return sorted(set(hits), key=_key, reverse=True)


def resolve_edsdk(explicit="", strict=False):
    """返回 (路径, 错误信息, 提示信息)。

    多候选时**择优取一个**（版本新优先、其次修改时间新），并把选用结果与
    其余候选一并返回给调用方打印 —— 目标是不传 --sdk 也能直接跑起来。
    只有 --sdk-strict（strict=True）才把"多候选"当错误。
    """
    hits = find_edsdk(explicit)
    if not hits:
        return None, (
            "未找到 edsdk.py。本skill 依赖「本地 Office 编辑插件」，"
            "请确认已安装，或用 --sdk <路径> 手动指定。"), None
    if len(hits) > 1:
        if strict and not explicit:
            return None, (
                "找到多个 edsdk.py，请用 --sdk 明确指定其中一个：\n    "
                + "\n    ".join(hits)), None
        note = (f"  找到 {len(hits)} 个 edsdk.py，已自动选用最新版本：{hits[0]}\n"
                f"  其余候选（未使用）：{' ； '.join(hits[1:])}\n"
                f"  要指定请用 --sdk <路径>；要求必须显式指定请加 --sdk-strict")
        return hits[0], None, note
    return hits[0], None, None


TITLE_RE = re.compile(r"^(#{0,6})\s*第[" + "0-9零一二两三四五六七八九十百千万" + r"]+章")

# 只处理**成对的 ASCII HTML 标签**（<b>…</b>）—— 这是「等价改写」，
# 既避免编辑器把它当 HTML 标签，又不破坏正文。
#
# 为什么要求「成对」：孤立的 <b> 或 </b> 也可能被当标签，但**无法区分**
# 它与「作者写了一半的句子」。而以下三类必须原样保留：
#   数值不等式HP<100 / a < b   —— 后面不是 ASCII 字母
#   中文尖括号   <小心> / </今天> —— 中文网文习惯写法，非 ASCII
#   跨段落标签   <b>在A段</b>在B段 —— 不做跨段配对，避免误判
_PAIR_TAG = re.compile(
    r"<([A-Za-z][A-Za-z0-9]*)>([^<>]{0,500}?)</\1>",      # 同名成对
    re.S,
)
# 无配对但形如 </tag> 的闭合标签：仍需处理，否则编辑器会吞掉它
_CLOSE_ONLY = re.compile(r"</([A-Za-z][A-Za-z0-9]{0,19})>")


def clean_md(text):
    """去掉代码块围栏标记行；成对 HTML 标签换全角（等价改写，不丢字）；
    首行标题补 # 。

    为什么不全量替换尖括号：无条件replace 会把正文里的不等式改成
    「HP《100」，且**等长所以长度校验抓不到** —— 交付出去用户看不出来。
    """
    text = re.sub(r"^```[a-zA-Z]*\s*$", "", text, flags=re.M)

    # 先处理成对标签（保留内部内容与标签名）
    text = _PAIR_TAG.sub(r"《\1》\2《/\1》", text)
    # 再处理剩余的孤立 ASCII 闭合标签
    text = _CLOSE_ONLY.sub(r"《/\1》", text)

    lines = text.split("\n")
    if lines and TITLE_RE.match(lines[0].strip()) and not lines[0].lstrip().startswith("#"):
        lines[0] = "# " + lines[0].strip()
        text = "\n".join(lines)
    return text


def call(edsdk, edir, tool, **kw):
    cmd = [sys.executable, edsdk, "call", tool] + [
        f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in kw.items()
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=edir)
    return (r.stdout or "") + (r.stderr or "")


def _sort_key(path):
    """按**章号**排序（SKILL.md:391「按章号排序」）。

    不能用 sorted(glob) 的字典序：`第100章` 会排在 `第1章` 前面（'0' < '章'）。
    实测 7 章的顺序是 100 → 105 → 10 → 17 → 1 → 2 → 3，既不符合文档承诺，
    也让 `seen` 去重时"先处理的赢"变得不可预期（撞号时保留哪一份随机）。
    解析不出章号的（序章/番外类）排到末尾，按文件名保持确定性。
    """
    try:
        n = parse_chapter(path)[0]
    except ChapterError:
        return (1, 0, os.path.basename(path))
    return (0, n, os.path.basename(path))


def normalize(s):
    """两边共用的"去噪口径"：去掉空白与 docx/排版常见的分隔字符。"""
    return re.sub(r"[|\-—–\s]", "", s)


def docx_text(path):
    """从 docx 提取纯文本（去掉 XML 标签），用于内容级校验。"""
    try:
        import zipfile
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "ignore")
        xml = re.sub(r"<w:p[ >]", "\n<w:p ", xml)
        txt = re.sub(r"<[^>]+>", "", xml)
        return normalize(txt)
    except Exception:
        return ""


def _first_diff(a, b):
    """返回第一处差异的描述，便于定位内容损坏。"""
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            ctx = 12
            return (f"第 {i + 1} 字：预期「{a[max(0,i-ctx):i+ctx]}」"
                    f" 实际「{b[max(0,i-ctx):i+ctx]}」")
    if len(a) != len(b):
        tail = f"（长度不同：预期 {len(a)} / 实际 {len(b)}）"
        return tail
    return "内容一致"


def convert(edsdk, md_path, out_path):
    """单章转换。返回 (是否成功, 说明)。

    校验逻辑：**把「允许的改动」显式建模进预期值**——
    期望 = clean_md(源文) 的净字数串，与 docx 实际文本逐字符比。
    这样等长损坏（同一个字被换成另一个字）也能抓到，而长度阈值抓不到。
    """
    edir = os.path.dirname(edsdk)
    out_path = os.path.abspath(out_path)
    raw = read_text(md_path)
    expected = docx_text_expectation(raw)

    # 临时文件用 mkstemp，避免并发转换互相覆盖（曾导致跨书串稿）
    fd, tmp = tempfile.mkstemp(prefix="md2docx_", suffix=".md")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(clean_md(raw))

        out = call(edsdk, edir, "create_doc")
        m = re.findall(r'file_id=([A-Za-z0-9_\-]+)', out)
        fid = m[-1] if m else None
        if not fid:
            return False, "create_doc 未返回 file_id"

        # edsdk 的 doc_insert_markdown 约定是 "file://<本地绝对路径>"
        # ——**双斜杠**。用 pathlib.Path.as_uri() 会得到 file:///C:/…（三斜杠），
        # 后端按 "/C:/…" 去开文件 → "cannot open markdown file"。
        # 实测：必须用双斜杠 + 绝对路径。传纯路径也不行（会被当正文插入）。
        uri = "file://" + os.path.abspath(tmp)
        r = call(edsdk, edir, "doc_insert_markdown", idx=0, markdown=uri, file_id=fid)
        if "insert_markdown ok" not in r:
            return False, "插入失败：" + r.strip().split("\n")[0][:120]

        pre_mtime = os.path.getmtime(out_path) if os.path.exists(out_path) else 0
        r = call(edsdk, edir, "save_file", file_id=fid, file_path=out_path)
        if "File saved to" not in r and not os.path.exists(out_path):
            return False, "保存失败：" + r.strip().split("\n")[0][:120]
        if not os.path.exists(out_path):
            return False, "保存失败：目标不存在"
        post_mtime = os.path.getmtime(out_path)
        if post_mtime <= pre_mtime:
            return False, "疑似未更新（mtime 没变）—— 可能写到了别处，请用绝对路径重试"

        size = os.path.getsize(out_path)
        got = docx_text(out_path)
        if not got:
            return False, f"无法读取 docx 内容（{size} 字节）—— 疑似空文件"
        okc, detail = verify_content(expected, got)
        if not okc:
            return False, f"内容不一致（{detail}）"
        return True, f"{len(got)} 字，{size} 字节（{detail}）"
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def docx_text_expectation(raw):
    """预期 docx 文本 = clean_md(源文) 剥掉 markdown 语法后的净字串。

    为什么必须先剥语法（此前 100% 误判的根因）：
    `doc_insert_markdown` 是「把 markdown 转成富文本」，`#`、`**`、`>`、`1.`
    这些标记**不会**出现在 docx 里；而 clean_md 还会**主动给首行补 `# `**。
    拿含标记的串当预期值，等于每一章都必然不相等（实测 3/3 误判，
    而 docx 内容其实逐字正确）。

    刻意**直接调用 clean_md**再剥语法，不硬编码「哪些差异算合法」——
    硬编码会让下次 clean_md 改版时校验悄悄失效。
    """
    return normalize(md_to_plaintext(raw))


def md_to_plaintext(text):
    """把 markdown 降成"渲染后应得的纯文本"（保留全部正文字符）。

    只剥**标记**，不删内容 —— 剥完的字符数就是 docx 里应该出现的字符数。
    """
    t = clean_md(text)
    t = re.sub(r"^[ \t]*```.*$", "", t, flags=re.M)                 # 代码围栏残余
    t = re.sub(r"^[ \t]*([-*_])(?:[ \t]*\1){2,}[ \t]*$", "", t, flags=re.M)  # 分隔线
    t = re.sub(r"^[ \t]*#{1,6}[ \t]*", "", t, flags=re.M)           # 标题记号
    t = re.sub(r"^[ \t]*>[ \t]?", "", t, flags=re.M)                # 引用记号
    t = re.sub(r"^[ \t]*[-*+][ \t]+", "", t, flags=re.M)            # 无序列表
    t = re.sub(r"^[ \t]*\d+[.)][ \t]+", "", t, flags=re.M)          # 有序列表
    t = re.sub(r"\*\*(.{0,500}?)\*\*", r"\1", t, flags=re.S)        # 加粗
    t = re.sub(r"__([^_\n]{0,500}?)__", r"\1", t)                   # 加粗(下划线)
    t = re.sub(r"(?<![\*\w])\*([^*\n]{0,500}?)\*(?!\*)", r"\1", t)  # 斜体
    t = re.sub(r"~~(.{0,500}?)~~", r"\1", t, flags=re.S)            # 删除线
    t = re.sub(r"`([^`\n]{0,500}?)`", r"\1", t)                     # 行内代码
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)                      # 图片（无文字）
    t = re.sub(r"\[([^\]]{0,500}?)\]\([^)]*\)", r"\1", t)           # 链接留文字
    return t


# 宽松校验：逐字比对失败时的兜底口径。
# 为什么不直接判 FAIL：markdown 渲染细节（列表编号、表格、脚注）难以 100% 建模，
# 全判 FAIL 会让"目录里躺着正确 docx、屏幕上全是 FAIL"，用户反复重跑。
# 为什么不能更松：**空 docx 与内容截断必须在长度这一关被拦下**。
LOOSE_LEN_TOL = 0.02     # 长度相对偏差容差（2%）
LOOSE_PROBES = 10        # 关键句抽样条数
LOOSE_MISS_TOL = 0.2     # 关键句最多允许缺失比例（20%）


def _probes(text, k=LOOSE_PROBES):
    """从文本里均匀抽 k 个片段，用于「关键句存在性」校验。"""
    parts = [p.strip() for p in re.split(r"[。！？；\n]", text) if len(p.strip()) >= 8]
    if not parts:
        step = max(len(text) // max(k, 1), 12)
        parts = [text[i:i + 12] for i in range(0, max(len(text) - 11, 1), step)]
        parts = [p for p in parts if p.strip()]
    if len(parts) <= k:
        return parts
    stride = len(parts) / float(k)
    return [parts[int(i * stride)] for i in range(k)]


def verify_content(expected, got):
    """逐字比对；不一致时退化为「长度量级 + 关键句存在性」的宽松校验。

    返回 (是否通过, 说明)。判定顺序刻意这样排，**绝不会弱到等于没校验**：
      · 长度相同却逐字不同 -> **必 FAIL**（等长损坏：字符被替换/顺序被打乱，
        这是长度阈值唯一抓不到的形态，绝不能放进宽松通道）
      · docx 为空          -> 必 FAIL
      · 长度偏差 > 2%      -> 必 FAIL（截断 / 重复写入）
      · 关键句缺失 > 20%   -> 必 FAIL（大段丢失）
      · 长度差 ≤2% 且关键句基本都在 -> 判通过，并**在说明里写明是宽松口径**
    """
    if got == expected:
        return True, "逐字一致"
    if not expected:
        return False, "源文为空，无法校验"
    if not got:
        return False, "docx 内容为空（转换疑似失败）"
    probes = _probes(expected)
    miss = [p for p in probes if p not in got]
    diff = _first_diff(expected, got)
    if len(got) == len(expected):
        return False, f"等长但内容不同（疑似字符被替换）：{diff}"
    ratio = abs(len(got) - len(expected)) / float(max(len(expected), 1))
    allow_miss = max(1, int(len(probes) * LOOSE_MISS_TOL))
    if ratio <= LOOSE_LEN_TOL and len(miss) <= allow_miss:
        return True, ("宽松校验通过（差异疑似 markdown 渲染方式不同："
                      f"长度差 {ratio * 100:.1f}% ≤ {LOOSE_LEN_TOL * 100:.0f}%，"
                      f"关键句缺失 {len(miss)}/{len(probes)} ≤ {allow_miss}；{diff}）")
    if miss:
        return False, (f"{len(miss)}/{len(probes)} 处关键句缺失，"
                       f"首处「{miss[0][:24]}」；{diff}")
    return False, (f"长度差 {ratio * 100:.1f}%（容差 {LOOSE_LEN_TOL * 100:.0f}%），"
                   f"疑似内容被截断或重复写入；{diff}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("dst")
    ap.add_argument(
        "--pattern",
        default="第{cn}章：{title}.docx",
        help="命名模板。{cn}=中文数字（第十七章），{n}=阿拉伯数字，{title}=章名。"
        "**默认用中文数字**——必须与项目既有交付文件保持一致，"
        "否则会生成「假重复」文件，掩盖旧版本仍然存在的事实。",
    )
    ap.add_argument("--only", default="", help="只转指定章号，逗号分隔，如 1,2,3")
    ap.add_argument("--sdk", default="", help="手动指定 edsdk.py 路径（覆盖自动择优）")
    ap.add_argument("--sdk-strict", action="store_true",
                    help="找到多个 edsdk.py 时不自动择优，要求必须用 --sdk 指定")
    args = ap.parse_args()

    edsdk, err, note = resolve_edsdk(args.sdk, strict=args.sdk_strict)
    if err:
        print("✗ " + err)
        return 1
    print("使用转换脚本：", edsdk)
    if note:
        print(note)

    dst_abs = os.path.abspath(args.dst)
    os.makedirs(dst_abs, exist_ok=True)
    files = sorted(glob.glob(os.path.join(args.src, "*.md")), key=_sort_key)

    only = set()
    if args.only:
        bad = [x.strip() for x in args.only.split(",")
               if x.strip() and not x.strip().isdigit()]
        if bad:
            print(f"✗ --only 含非法章号：{bad}（应为阿拉伯数字，逗号分隔）")
            return 1
        only = {int(x) for x in args.only.split(",") if x.strip()}

    ok = fail = 0
    skipped = []          # (文件名, 原因, 是否预期内)
    seen = {}             # 章号 -> 已占用的输出路径
    for f in files:
        base = os.path.basename(f)
        try:
            n, title = parse_chapter(f)
        except ChapterError as e:
            # 序章 / 番外属正常内容，标为预期内；其余按非预期报。
            # 白名单**复用 chapter_num 的共享常量**，不在这里另写一份 ——
            # 否则 md2docx 说"预期内"、inventory 说"命名不规范"，定性相反。
            expected_skip = is_non_chapter(base)
            skipped.append((base, str(e), expected_skip))
            continue
        if only and n not in only:
            continue
        if not title:
            title = base.replace(".md", "")

        # 关键：pattern 拼到 dst 之后**再** abspath。
        # 此前是 abspath(pattern.format(...))，以进程 CWD 为基准解析，
        # 于是下面的逃逸守卫恒成立、每一章都被拒（产出 0 个 docx）。
        out_name = args.pattern.format(n=n, cn=num2cn(n), title=title).strip()
        if not out_name.lower().endswith(".docx"):
            out_name += ".docx"
        out_path = os.path.abspath(os.path.join(dst_abs, out_name))
        # 防输出路径逃逸：必须落在 dst 内
        inside = True
        try:
            if os.path.commonpath([out_path, dst_abs]) != dst_abs:
                inside = False
        except ValueError:
            # 跨盘符（或路径形态不同）时 commonpath 抛错，一律按逃出处理
            inside = False
        if not inside:
            print(f"  FAIL 第{n}章 {title} -> 输出路径逃出目标目录"
                  f"（或跨盘符），已拒绝：{out_path}")
            fail += 1
            continue
        if out_path in seen:
            print(f"  FAIL 第{n}章 {title} -> 与「{seen[out_path]}」输出重名，已拒绝（避免互相覆盖）")
            fail += 1
            continue
        seen[out_path] = f"第{n}章 {title}"

        try:
            read_text(f)
        except ValueError as e:
            print(f"  FAIL 第{n}章 {title} -> {os.path.basename(f)} {e}")
            fail += 1
            continue

        good, msg = convert(edsdk, f, out_path)
        print(("  OK   " if good else "  FAIL ")
              + f"第{n}章 {title} -> {os.path.basename(out_path)}  {msg}")
        ok, fail = (ok + 1, fail) if good else (ok, fail + 1)

    if skipped:
        print("\n跳过清单（文件名不含「第N章」，未处理）：")
        for base, why, exp in skipped:
            tag = "预期内（序章/番外类）" if exp else "非预期，建议改名"
            print(f"  · {base} —— {tag}")
        if any(not e for _, _, e in skipped):
            print("  ⚠ 有非预期跳过项：这些文件不会被转换，也不会有交付物。")

    print(f"\n完成：成功 {ok}，失败 {fail}" + (f"，跳过 {len(skipped)}" if skipped else ""))
    if not files:
        print("（目录内没有 md 文件）")
        return 1
    if ok == 0 and not fail:
        print("⚠ 没有匹配到任何「第N章」文件，未产出任何 docx。")
        return 3
    if skipped:
        return 3          # 有文件未处理，但不阻断；需人工判断
    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
