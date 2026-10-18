#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 skill 打包成可分发 zip（维护用）。

用法：
    python package_skill.py <输出zip路径> [--force]

做四件事：
  1. **白名单**校验必需文件（含全部模板与新增脚本）
  2. 只打包白名单内的目录与后缀 —— 目录里若被误放书稿/笔记，不会流出去
  3. 秘密扫描：疑似密钥、绝对路径、用户名的文件**中止打包**
  4. 输出路径防护：不能落在 skill 目录内（否则 zip 会把自己打进去）

例：
    python package_skill.py "C:/Users/xxx/Desktop/xiaoshuo-gongzliu.zip"
"""
import os
import re
import sys
import zipfile

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DIR_NAME = os.path.basename(SKILL_DIR)


def read_skill_name():
    """从 SKILL.md frontmatter 读 name。返回 (name, 错误信息)。

    **为什么不能用 basename(SKILL_DIR)**：zip 里的根目录名就是这个值。
    用本地目录名的后果是——换台机器、或副本目录叫 `skillcopy` 时，
    打出来的包根目录变成 `skillcopy/`，与 SKILL.md 的 name 不符，装回去即失效。
    """
    p = os.path.join(SKILL_DIR, "SKILL.md")
    if not os.path.exists(p):
        return "", "SKILL.md 不存在，无法确定 skill 名"
    try:
        with open(p, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8-sig", "replace")
    except OSError as e:
        return "", f"SKILL.md 读取失败：{e}"
    m = re.search(r"^name:\s*([^\s#]+)", text, re.M)
    if not m:
        return "", "SKILL.md frontmatter 里没有 name 字段"
    return m.group(1).strip(), ""


SKILL_NAME, _NAME_ERR = read_skill_name()

# --- 白名单：只有这些目录/文件可进包 ---
ALLOW_ROOTS = {"SKILL.md", "LICENSE", "CHANGELOG.md", "README.md",
               "scripts", "references", "assets"}
ALLOW_EXT = {".md", ".py", ".svg", ".txt", ".json"}
ALLOW_EXACT = {"LICENSE"}          # 无后缀的白名单文件（后缀过滤会漏掉它们）

# --- 必需文件：**显式名清单**，一个都不能少 ---
# 此前漏了 lint_prose.py / analyze_arc.py 与 4 个 references —— 删掉它们
# 照样打印「必需文件齐全」并打出残缺包（实测 exit 0）。
# 教训：collect_optional() 那种「运行时读目录枚举」天生查不出缺失
#（文件被删了自然不在目录里），所以这里必须写死名字，另外再用数量下限兜底。
REQUIRED = [
    "SKILL.md",
    "CHANGELOG.md",
    "LICENSE",
    # scripts（15 个）
    "scripts/analyze_arc.py",
    "scripts/calibrate.py",
    "scripts/epub_extract.py",
    "scripts/txt_split.py",
    "scripts/chapter_health.py",
    "scripts/article_index.py",
    "scripts/chapter_num.py",
    "scripts/check_consistency.py",
    "scripts/count_chars.py",
    "scripts/init_book.py",
    "scripts/inventory.py",
    "scripts/lint_prose.py",        # 文本轨闸门脚本，缺了等于没有质检
    "scripts/md2docx.py",
    "scripts/package_skill.py",
    "scripts/textio.py",
    # references（14 个，**以目录实际内容为准**，不照抄 SKILL.md 索引 ——
    # 索引漏了 verification-methodology.md，但它会被打进包）
    "references/anti-ai-rules.md",
    "references/craft-toolkit.md",
    "references/pipeline-stages.md",
    "references/工程纪律.md",        # v1.3.0 从 SKILL.md 本体搬出
    "references/问题回流闭环.md",
    "references/platform-specs.md",
    "references/rules-from-mistakes.md",
    "references/style-rules.md",
    "references/verification-methodology.md",
    "references/开书投喂标准.md",
    "references/三幕十五节拍表.md",
    "references/好小说拆解库.md",
    "references/好小说评估标准.md",
    "references/逐章分析工作流.md",
    # assets：模板 11 个 + 流程图 4 个。
    # 同样必须写死名字 —— 只靠 collect_optional() 枚举 + TPL_MIN 下限的话，
    # 从 11 个删到 10 个仍然"齐全"（实测漏检），因为枚举看不见被删的文件。
    "assets/templates/上下文压缩包模板.md",
    "assets/templates/升级材料模板.md",
    "assets/templates/批次导读模板.md",
    "assets/templates/报告模板.md",
    "assets/templates/改进记录模板.md",
    "assets/templates/流程说明模板.md",
    "assets/templates/目录与章纲模板.md",
    "assets/templates/章纲总结模板.md",
    "assets/templates/续写档案模板.md",
    "assets/templates/自查报告模板.md",
    "assets/templates/修改报告模板.md",
    "assets/templates/逐章为什么分析模板.md",
    "assets/templates/进度卡模板.md",
    "assets/流程图-三层分工.svg",
    "assets/流程图-总图.svg",
    "assets/流程图-章纲两条.svg",
    "assets/流程图-章纲回填.svg",
]
# templates / svg **运行时**枚举 —— 若在导入时固定，模板被删后清单里
# 就已经没有它们，会漏过校验、打出残缺包（实测：删光模板仍报"齐全"）。
# 下限必须**等于实际数量**，不能留容错窗口：
# 下限设 10 而实际 11 时，删掉 1 个模板仍然"齐全"（实测漏检），残缺包照样出门。
TPL_MIN = 13          # 模板数量下限（低于此视为安装损坏）
SVG_EXPECT = 4        # 流程图数量
SCRIPT_MIN = 15       # scripts/*.py 数量下限
REF_MIN = 14          # references/*.md 数量下限


def collect_optional():
    """运行时枚举模板与流程图，凑进校验清单。"""
    out = []
    tpl = os.path.join(SKILL_DIR, "assets", "templates")
    if os.path.isdir(tpl):
        out += [f"assets/templates/{n}" for n in sorted(os.listdir(tpl))
                if n.endswith(".md")]
    assets = os.path.join(SKILL_DIR, "assets")
    if os.path.isdir(assets):
        out += [f"assets/{n}" for n in sorted(os.listdir(assets))
                if n.endswith(".svg")]
    return out


def diff_required(required):
    """双向 diff。

    返回 (缺失, 未登记)：
      · 缺失   = 清单里有、磁盘上没有 —— **致命**，拒绝打包
      · 未登记 = 磁盘上有、清单里没有 —— 提示（新增文件需补进 REQUIRED），不致命
    双向都要做：只做单向会漏掉"清单写少了"这一类漂移。
    """
    missing = [r for r in required
               if not os.path.exists(os.path.join(SKILL_DIR, r))]
    listed = set(required)
    unregistered = []
    for sub, ext in (("scripts", ".py"), ("references", ".md")):
        d = os.path.join(SKILL_DIR, sub)
        if not os.path.isdir(d):
            continue
        for n in sorted(os.listdir(d)):
            if n.endswith(ext) and f"{sub}/{n}" not in listed:
                unregistered.append(f"{sub}/{n}")
    return missing, unregistered


def count_dir(sub, ext):
    d = os.path.join(SKILL_DIR, sub)
    if not os.path.isdir(d):
        return 0
    return len([x for x in os.listdir(d) if x.endswith(ext)])

EXCLUDE_DIRS = {"__pycache__", ".git", ".svn", ".idea"}
EXCLUDE_FILES = {".DS_Store", "Thumbs.db"}

# --- 秘密/隐私扫描：命中即中止打包（只报文件名，绝不打印命中内容）---
SECRET_PAT = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|Bearer\s|sk-[A-Za-z0-9]{16,}"
    r"|AKIA[0-9A-Z]{12,}|ghp_[A-Za-z0-9]{20,})", re.I)
PRIVACY_PAT = re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+")


def allowed(rel):
    """rel 是相对 SKILL_DIR 的路径（用 / 分隔）。"""
    parts = rel.split("/")
    if parts[0] not in ALLOW_ROOTS:
        return False
    base = parts[-1]
    if base in EXCLUDE_FILES or base.startswith("~$"):
        return False
    if base in ALLOW_EXACT:          # 无后缀白名单文件（LICENSE）
        return True
    return os.path.splitext(base)[1].lower() in ALLOW_EXT


# 扫描器自身含密钥模式串；CHANGELOG 会有意举例说明「什么会被拦」——
# 这两处必须豁免，否则扫描器自己告自己。
SELF = os.path.basename(os.path.abspath(__file__))
SELF_EXEMPT = {SELF, "CHANGELOG.md"}


def scan(rel, full):
    """返回问题描述；干净则返回 None。**只报文件名与类型，不回显内容。**"""
    if os.path.basename(full) in SELF_EXEMPT:
        return None
    try:
        with open(full, "rb") as fh:
            raw = fh.read()
        text = raw.decode("utf-8", "ignore")
    except OSError:
        return None
    if SECRET_PAT.search(text):
        return "疑似密钥/令牌"
    if PRIVACY_PAT.search(text):
        return "含本机绝对路径（泄露用户名）"
    if rel.endswith((".md", ".py", ".txt", ".json")) and len(raw) > 2_000_000:
        return "体积异常（>2MB），疑似误放大文件"
    return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    if not args:
        print("用法：python package_skill.py <输出zip路径> [--force]")
        return 1

    out = os.path.abspath(args[0])
    if not out.lower().endswith(".zip"):
        print(f"✗ 输出必须是 .zip 结尾：{out}")
        return 1
    # 输出不能落在 skill 目录内 —— 否则 zip 会把自己打进去（实测体积翻倍）
    try:
        if os.path.commonpath([out, SKILL_DIR]) == SKILL_DIR:
            print("✗ 输出路径不能位于 skill 目录内（会自包含）。请指定 skill 之外的路径。")
            return 1
    except ValueError:
        pass                      # 跨盘符，commonpath 抛错= 不在同棵树，安全
    if os.path.exists(out) and not force:
        print(f"✗ {out} 已存在。加 --force 覆盖，或换个路径。")
        return 1

    # 0. zip 根目录名必须来自 SKILL.md 的 name，不能来自本地目录名
    if _NAME_ERR:
        print("✗ " + _NAME_ERR + "（zip 根目录名以它为准）")
        return 1
    if SKILL_NAME != _DIR_NAME:
        print(f"✗ SKILL.md 的 name（{SKILL_NAME}）与本目录名（{_DIR_NAME}）不一致。")
        print(f"    请把目录改名为「{SKILL_NAME}」，或修正 SKILL.md 的 name 字段 ——")
        print("    zip 内的根目录名取自 name，不能依赖本地目录名。")
        return 1

    # 1. 校验必需文件（显式清单 + 运行时枚举的模板/流程图 + 数量下限）
    # 模板/流程图已写进 REQUIRED，collect_optional() 只用来兜住"新增的未登记文件"，
    # 因此要去重，否则计数会把 11 模板 + 4 图算两遍。
    required = list(dict.fromkeys(REQUIRED + collect_optional()))
    missing, unregistered = diff_required(required)
    n_tpl = count_dir(os.path.join("assets", "templates"), ".md")
    n_svg = count_dir("assets", ".svg")
    n_py = count_dir("scripts", ".py")
    n_ref = count_dir("references", ".md")
    if n_tpl < TPL_MIN:
        missing.append(f"assets/templates/ 只有 {n_tpl} 个模板（应 >= {TPL_MIN}）")
    if n_svg < SVG_EXPECT:
        missing.append(f"assets/ 只有 {n_svg} 个流程图（应 >= {SVG_EXPECT}）")
    if n_py < SCRIPT_MIN:
        missing.append(f"scripts/ 只有 {n_py} 个脚本（应 >= {SCRIPT_MIN}）")
    if n_ref < REF_MIN:
        missing.append(f"references/ 只有 {n_ref} 个分册（应 >= {REF_MIN}）")
    if missing:
        print("✗ 缺少必需文件（skill 安装不完整，拒绝打出残缺包）：")
        for m in missing:
            print("   ", m)
        return 1
    print(f"✓ 必需文件齐全（{len(required)} 项：{len(REQUIRED)} 显式登记 + "
          f"实测 {n_py} 脚本 / {n_ref} 分册 / {n_tpl} 模板 / {n_svg} 图）")
    if unregistered:
        print("  ⚠ 目录里有未登记进 REQUIRED 的文件（请补进清单，否则将来被删也查不出）：")
        for u in unregistered:
            print("    ", u)

    # 2. 收集 + 扫描
    picked, problems, skipped = [], [], []
    for base, dirs, files in os.walk(SKILL_DIR):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDE_DIRS)
        for f in sorted(files):
            full = os.path.join(base, f)
            if os.path.islink(full):
                skipped.append((os.path.relpath(full, SKILL_DIR), "符号链接，跳过"))
                continue
            rel = os.path.relpath(full, SKILL_DIR).replace("\\", "/")
            if not allowed(rel):
                skipped.append((rel, "不在白名单内，跳过"))
                continue
            issue = scan(rel, full)
            if issue:
                problems.append((rel, issue))
                continue
            picked.append((rel, full))

    if problems:
        print("✗ 下列文件未通过秘密/隐私扫描，**已中止打包**：")
        for rel, why in problems:
            print(f"   {rel}  —— {why}")
        print("  请清理后再打包。")
        return 1

    # 3. 打包
    out_dir = os.path.dirname(out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, full in picked:
            zf.write(full, posixpath_join(SKILL_NAME, rel))

    size = os.path.getsize(out)
    print("\n打包清单（相对 skill 目录）：")
    for rel, _ in picked:
        print("  +", rel)
    if skipped:
        print("\n跳过（白名单外/ 符号链接）：")
        for rel, why in skipped:
            print(f"  - {rel}  ({why})")
    print(f"\n✓ 打包完成：{out}")
    print(f"  {len(picked)} 个文件，{size} 字节")
    return 0


def posixpath_join(a, b):
    return a + "/" + b


if __name__ == "__main__":
    sys.exit(main())
