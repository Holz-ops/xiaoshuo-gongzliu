#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合卷分析：把逐章产物合起来看，发现单章看不出的问题。

用法：
    python analyze_arc.py <md目录> [--json out.json] [--out 报告.md]

分析五项（前四项为脚本可测；第五项为可选，缺数据则跳过）：
    1. 节奏曲线   —— 每章的对话密度 / 场景切换数 / 平均段长
    2. 人物出场   —— 主要角色 × 章的出现矩阵（找长期缺席）
    3. 重复模式   —— 开场句与结尾句的相似度（找"一个模子"）
    4. 语言趋势   —— 神态/环境/AI味在全文的分布（复用 lint_prose 口径）
    5. 节奏与伏笔闭环（可选）—— 扫伏笔登记表列悬空/逾期；读章纲 [爆]/[缓] 标记查认知过山车
"""
import argparse
import glob
import json
import os
import re
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import ChapterError, parse_chapter  # noqa: E402  章号唯一来源
from chapter_num import is_non_chapter, is_title_line  # noqa: E402
from textio import read_text_or_none, write_text  # noqa: E402

# 角色分级：按"在故事里的功能"设缺席阈值，不搞一刀切
#   main  主角与核心配角——消失 8 章就该问
#   major 主要配角（女主、关键线索人物）——12 章
#   minor 次要角色——20 章
#   tool  功能角色（偶尔出现、本来就是工具人）——不报警
#
# ⚠️ **这里不再写死具体书名的人名**。此前硬编码了另一本书的 13 个角色
# （林北/老郑/艾米…），换一本书后「人物出场矩阵」永远是空表、永远输出
# 「无超过各自级别阈值的缺席」—— 不是"没缺席"，是**根本没在查**（恒真假阴性）。
# 现在改为从 `001 世界观/设定基准表.md` 的「三、人物」表读取，
# 或用 `--characters 周野,孟昭,宋萤` 直接指定；两者都没有就明确报「本项跳过」。
LEVEL_THRESHOLD = {'main': 8, 'major': 12, 'minor': 20, 'tool': 999}
CHARACTERS = []          # 运行时由 load_characters() 填充
CHARACTER_LEVEL = {}

ENV_WORDS = ('光|灯|亮|暗|黑|影子|风|沙|雨|雪|雾|空气|味|气味|潮|干|冷|凉|热|烫|温|'
             '天|云|日头|太阳|月|星|地平线|顶棚|房顶|天花板|墙|地|地面|地板|沙地|泥|'
             '声音|响|安静|静|吵|嗡|咔|咕|轰|铁皮|金属|水泥|砖|灰|锈|油渍|湿')
FACE_WORDS = ('脸色|脸|眉|眯|咬|咬牙|皱|撇嘴|嘴角|点头|摇头|僵|愣|沉默|不吭声|没说话|'
              '笑|哼|叹了口气|脖子|肩膀|后背|手指|手背|指节|喉结|下巴|眼皮|眼睛|出汗|'
              '抿|咧嘴|耸|缩|顿|停住|吸了口气')


def chapter_no(path):
    """章号；无法解析返回 None。

    **复用 chapter_num**：此前内联 `第(\\d+)章` 只认阿拉伯数字，
    中文章号（`第十七章：上报`）全部返回 0 被过滤掉 → 后面 `sum(...)/n`
    直接 ZeroDivisionError。中文章号与阿拉伯章号必须一起认。
    """
    try:
        return parse_chapter(path)[0]
    except ChapterError:
        return None


def find_character_table(md_dir):
    """从 md 目录向上找 `001 世界观/设定基准表.md`（最多 4 层）。"""
    cur = os.path.abspath(md_dir)
    for _ in range(4):
        cand = os.path.join(cur, '001 世界观', '设定基准表.md')
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def parse_character_table(text):
    """从设定基准表的「三、人物」表里取人名与级别。

    表格形如 `| **周野** | 主角，22 岁，第九层管道班维修工 |`：
    第一列是人名（去 ** 与空白），第二列含「主角」判 main，其余按 major。
    只收第一列不超过 8 个字的（排除说明性行）。
    """
    out = {}
    in_section = False
    for line in text.split('\n'):
        s = line.strip()
        if s.startswith('##'):
            in_section = ('人物' in s)
            continue
        if not in_section or not s.startswith('|'):
            continue
        cells = [c.strip() for c in s.strip('|').split('|')]
        if len(cells) < 2:
            continue
        name = cells[0].replace('*', '').replace('`', '').strip()
        if not name or name in ('角色', '人物', '名字') or set(name) <= set('-: '):
            continue
        if len(name) > 8 or ' ' in name:
            continue
        out[name] = 'main' if '主角' in cells[1] else 'major'
    return out


def load_characters(md_dir, spec):
    """确定本书要追踪哪些人物。返回 (人名列表, 级别表, 来源说明)。"""
    if spec:
        if os.path.isfile(spec):
            text = read_text_or_none(spec) or ''
            table = parse_character_table(text)
            if table:
                return list(table), table, f'--characters 文件：{os.path.basename(spec)}'
        names = [x.strip() for x in spec.replace('，', ',').split(',') if x.strip()]
        if names:
            return names, {n: 'major' for n in names}, '--characters 参数'
    path = find_character_table(md_dir)
    if path:
        text = read_text_or_none(path) or ''
        table = parse_character_table(text)
        if table:
            return list(table), table, f'设定基准表：{os.path.basename(os.path.dirname(os.path.dirname(path)))}/001 世界观/设定基准表.md'
    return [], {}, ''


def load(path):
    # 走 textio 严格回退；读不出（含乱码）返回 None，由调用方登记跳过。
    text = read_text_or_none(path)
    if text is None:
        return None, [], ''
    # 行尾归一化：Windows 写的 md 常是 CRLF，而 scenes 用 text.count('\n——\n')
    # 数字场景——CRLF 下实际序列是 '\n——\r\n'，永远数不到，且不报错（假阴性）。
    # paras 那一路有 l.strip() 兜底所以照常，只有数字场景这一条静默失效。
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    lines = [l.strip() for l in text.split('\n')]
    # 标题行判定走 chapter_num.is_title_line：裸写的「第10章：大抢修」也算标题。
    # 之前只认 `#` 开头，导致标题被当正文第一段，开场句比对全在比标题。
    title = next((l for l in lines if is_title_line(l)), '')
    paras = [l for l in lines if l and not is_title_line(l) and l != '——']
    return title, paras, text


def is_dialogue(p):
    return bool(re.search(r'[“”"]', p))


def analyse(path):
    title, paras, text = load(path)
    if text == '' and not paras:
        return None
    body = '\n'.join(paras)
    chars = len(re.sub(r'\s', '', body))

    dial = sum(1 for p in paras if is_dialogue(p))
    scenes = text.count('\n——\n') + (1 if text.strip().startswith('——') else 0)
    avg_par = round(chars / max(len(paras), 1), 1)

    face = sum(len(re.findall(FACE_WORDS, p)) for p in paras)
    env_p = sum(1 for p in paras if re.search(ENV_WORDS, p))

    # 人物出场
    casts = [c for c in CHARACTERS if c in body]

    return {
        'no': chapter_no(path),
        'title': title.lstrip('# ').strip(),
        'chars': chars,
        'paras': len(paras),
        'dial_pct': round(dial / max(len(paras), 1) * 100, 1),
        'scenes': scenes,
        'avg_par': avg_par,
        'face_k': round(face / max(chars / 1000, 0.001), 1),
        'env_pct': round(env_p / max(len(paras), 1) * 100, 1),
        'cast': casts,
        'first': paras[0][:40] if paras else '',
        # 结尾取末段的**最后一句**，不是末段开头 40 字。
        # 原先取 paras[-1][:40]，语义上根本不是「结尾」，下面的「结尾句式分布」
        # 也就变成了「末段开头句式分布」，两个检测都在看错的位置。
        'last': last_sentence(paras[-1]) if paras else '',
    }


def last_sentence(para, limit=40):
    """取段落最后一句（以。！？… 结尾），过长再截断。"""
    if not para:
        return ''
    parts = [s for s in re.split(r'(?<=[。！？…])', para) if s.strip()]
    tail = parts[-1].strip() if parts else para.strip()
    return tail[:limit]


def similar(a, b):
    # 空串守卫：SequenceMatcher(None, '', '').ratio() == 1.0，
    # 只有标题没有正文的章两两相邻会报"相似度 1.00 的雷同开场"，全是假报警。
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('md_dir')
    ap.add_argument('--json', dest='json_out')
    ap.add_argument('--out', dest='report_out')
    ap.add_argument('--zhanggang', dest='zhanggang',
                    help='可选：章纲文件路径，用于「认知过山车」检测（读每行 [爆]/[紧]/[缓]/[静] 张力标记）')
    ap.add_argument('--characters', dest='characters', default='',
                    help='可选：人物表。给文件路径（设定基准表）或逗号分隔人名；'
                         '不给则自动找 <书>/001 世界观/设定基准表.md')
    args = ap.parse_args()

    global CHARACTERS, CHARACTER_LEVEL
    CHARACTERS, CHARACTER_LEVEL, char_src = load_characters(args.md_dir, args.characters)

    # 递归扫子目录：分卷（`第一卷/`）是常态，非递归会把整卷报成「没内容」。
    candidates = [f for f in glob.glob(os.path.join(args.md_dir, '**', '*.md'), recursive=True)
                  if not os.path.basename(f).startswith('_')
                  and not any(part.startswith(('_', '.'))
                              for part in os.path.relpath(f, args.md_dir).split(os.sep)[:-1])]

    rows, skipped = [], []
    for f in sorted(candidates, key=lambda p: (chapter_no(p) is None, chapter_no(p) or 0, p)):
        if chapter_no(f) is None:
            why = '预期内（序章/番外类）' if is_non_chapter(f) else \
                  '文件名不含「第N章」，无法定位章号'
            skipped.append((os.path.basename(f), why, is_non_chapter(f)))
            continue
        r = analyse(f)
        if r is None:
            skipped.append((os.path.basename(f), '无法解码（非 UTF-8 / GB18030，或疑似乱码）', False))
            continue
        rows.append(r)

    # **空目录 / 全跳过必须提前返回**：此前 n==0 会让下面的 sum(...)/n
    # 抛 ZeroDivisionError，用户只看到一段 traceback。
    if not rows:
        print(f'没找到可分析的章节 md：{args.md_dir}', file=sys.stderr)
        for name, why, _ in skipped:
            print(f'  · {name} —— {why}', file=sys.stderr)
        return 2
    n = len(rows)

    out = []
    P = out.append

    P(f'# 合卷分析：{n} 章\n')

    # ---- 1 节奏曲线 ----
    P('## 一、节奏曲线\n')
    P('| 章 | 字数 | 对话% | 场景数 | 平均段长 | 神态/千 | 环境% |')
    P('|---|---|---|---|---|---|---|')
    for r in rows:
        P(f'| {r["no"]} | {r["chars"]} | {r["dial_pct"]} | {r["scenes"]} | '
          f'{r["avg_par"]} | {r["face_k"]} | {r["env_pct"]} |')
    P('')
    dial_avg = sum(r['dial_pct'] for r in rows) / n
    P(f'对话密度均值 {dial_avg:.1f}%　'
      f'最高 {max(r["dial_pct"] for r in rows):.1f}%（第 {max(rows, key=lambda x: x["dial_pct"])["no"]} 章）　'
      f'最低 {min(r["dial_pct"] for r in rows):.1f}%（第 {min(rows, key=lambda x: x["dial_pct"])["no"]} 章）')
    P('')

    # 连续同向检测：连续 ≥5 章对话密度都**显著**高于/低于均值 → 节奏平
    #
    # 为什么必须留容差带：连载小说里连续若干章是同类场景（同一批章戏、同一段
    # 视角）并不罕见，对话密度会**完全一样**。若按严格 `>` 判，"全部等于均值"
    # 会被整批塞进 else 的"低"分支 —— 高对话章节被报成「叙述过载」，方向反了。
    # （实测：8章均为 71.4% 的对话章节，报出"连续 8 章对话密度低于均值"。）
    # 所以用 ±5 个百分点的带把"与均值持平"单独判为 middle，不硬塞任何一侧。
    BAND = 5.0
    run_hi = run_lo = 0
    max_hi = max_lo = (0, None)
    for r in rows:
        if r['dial_pct'] > dial_avg + BAND:
            run_hi += 1; run_lo = 0
            if run_hi > max_hi[0]: max_hi = (run_hi, r['no'])
        elif r['dial_pct'] < dial_avg - BAND:
            run_lo += 1; run_hi = 0
            if run_lo > max_lo[0]: max_lo = (run_lo, r['no'])
        else:
            run_hi = run_lo = 0
    # 没有一段时不要打「0 章（到第 None 章）」—— None 泄漏到报告里很难看，
    # 而且读者会以为是 bug。分开两种情况说清楚。
    if max_hi[0]:
        P(f'- 最长「高对话连续段」：{max_hi[0]} 章（到第 {max_hi[1]} 章）'
          f'（阈值 > 均值+{BAND:g}%）')
    else:
        P(f'- 最长「高对话连续段」：无（无章超过均值+{BAND:g}%）')
    if max_lo[0]:
        P(f'- 最长「低对话连续段」：{max_lo[0]} 章（到第 {max_lo[1]} 章）'
          f'（阈值 < 均值-{BAND:g}%；与均值持平的章不计入任一侧）')
    else:
        P(f'- 最长「低对话连续段」：无（无章低于均值-{BAND:g}%；与均值持平的章不计入任一侧）')
    # 连续同向要**分两种含义**——它们指向的问题相反，不能一句"节奏偏平"糊过去。
    #
    # 依据（小说 ≠ 剧本）：电影能可视化的就别用对白，小说缺可视化手段，
    # **对白是交代情节的重要渠道**。所以：
    #   · 连续高对话 = 可能整卷靠嘴推进 → 要补动作/环境/神态，别让对白包办一切
    #   · 连续零对白 = 叙述过载，读者没有喘息 → 该让人物开口，用对话交出信息
    # 两者都是**该介入的信号，但介入方向相反**，所以分开写。
    if max_hi[0] >= 5:
        P(f'  ⚠️ 连续 {max_hi[0]} 章对话密度高于均值（到第 {max_hi[1]} 章）'
          f'——可能整段靠嘴推进。检查动作/环境/神态是否被对白挤掉了；'
          f'同时对白要过 **E 档质量**（太完整、标签过度、连续 ≥4 句纯对话），'
          f'每章 ≤2 处。')
    if max_lo[0] >= 5:
        P(f'  ⚠️ 连续 {max_lo[0]} 章对话密度低于均值（到第 {max_lo[1]} 章）'
          f'——可能是叙述过载。小说缺可视化手段，**对白是交代情节的重要渠道**，'
          f'低对白本身不是缺陷；但连续零对白会让读者失去"有人在说话"的锚点，'
          f'建议挑一处让两个人开口，用对话把信息交出去。')
    P('')

    # ---- 2 人物出场 ----
    P('## 二、人物出场矩阵\n')
    if not CHARACTERS:
        # 没有人物表就明说"本项没查"，不能打一张空表然后写「无超过阈值的缺席」
        # —— 那是在报告"查过了没问题"，而事实是根本没查。
        P('> ⚠️ 未提供人物表，本项**未检查**。'
          '请用 `--characters 周野,孟昭,宋萤` 指定，'
          '或在 `001 世界观\\设定基准表.md` 的「三、人物」表里列出角色后重跑。\n')
    else:
        P(f'> 人物来源：{char_src}（共 {len(CHARACTERS)} 人）\n')
    P('| 角色 | 级别 | 出场章数 | 首现 | 末现 | 最长缺席 | 缺席区间 |')
    P('|---|---|---|---|---|---|---|')
    missing_alerts = []
    for c in CHARACTERS:
        chs = [r['no'] for r in rows if c in r['cast']]
        if not chs:
            continue
        lv = CHARACTER_LEVEL.get(c, 'minor')
        th = LEVEL_THRESHOLD[lv]
        gaps = []
        prev = chs[0]
        for x in chs[1:]:
            gaps.append((x - prev - 1, prev, x))
            prev = x
        # 右端点用**最后一个已分析的章号**，不是章节总数 n：
        # n 是「有多少章」，与「最后一章是第几章」无关。缺号/跳号时用 n 会算出
        # 负的缺席（实测 1..10 缺第 3 章 → 算出 -1，输出「10→9」倒流区间）。
        last_no = rows[-1]['no']
        gaps.append((max(last_no - chs[-1], 0), chs[-1], last_no))
        mx = max(gaps) if gaps else (0, 0, 0)
        if mx[0]:
            P(f'| {c} | {lv} | {len(chs)} | {chs[0]} | {chs[-1]} | {mx[0]} 章 | {mx[1]}→{mx[2]} |')
        else:
            P(f'| {c} | {lv} | {len(chs)} | {chs[0]} | {chs[-1]} | — | — |')
        if mx[0] >= th:
            missing_alerts.append((c, lv, mx[0], mx[1], mx[2], th))
    P('')
    if missing_alerts:
        P('⚠️ **长期缺席**（按角色级别判定：主角核心 8 章／主要配角 12 章／次要 20 章）：')
        for c, lv, g, a, b, th in sorted(missing_alerts, key=lambda x: -x[2] / max(x[5], 1)):
            P(f'- **{c}**（{lv}，阈值 {th} 章）：第 {a} 章之后连续 **{g} 章**未出现')
    elif CHARACTERS:
        P('无超过各自级别阈值的缺席。')
    P('')

    # ---- 3 重复模式 ----
    P('## 三、重复模式检测\n')
    P('### 开场句（相邻章相似度 > 0.5 报警）')
    dup_first = []
    for i in range(1, n):
        s = similar(rows[i - 1]['first'], rows[i]['first'])
        if s > 0.5:
            dup_first.append((rows[i]['no'], s, rows[i]['first']))
    if dup_first:
        for no, s, txt in dup_first:
            P(f'- 第 {no} 章开场与上一章相似度 {s:.2f}：「{txt}」')
    else:
        P('- 无高相似开场')
    P('')
    P('### 结尾句（相邻章相似度 > 0.5 报警）')
    dup_last = []
    for i in range(1, n):
        s = similar(rows[i - 1]['last'], rows[i]['last'])
        if s > 0.5:
            dup_last.append((rows[i]['no'], s, rows[i]['last']))
    if dup_last:
        for no, s, txt in dup_last:
            P(f'- 第 {no} 章结尾与上一章相似度 {s:.2f}：「{txt}」')
    else:
        P('- 无高相似结尾')
    P('')

    # 结尾句式聚类（"他+动词"型）
    patterns = {'他+看/抬头': r'^他(抬头|看着|看了眼)',
                '他+走/离开': r'^他(走|转身|出门)',
                '他+说/答': r'^“',
                '他+决定/明白': r'^他(决定|知道|明白|想)'}
    P('### 结尾句式类型分布')
    cnt = {k: 0 for k in patterns}
    for r in rows:
        for k, pat in patterns.items():
            if re.search(pat, r['last']):
                cnt[k] += 1
    for k, v in cnt.items():
        if v:
            P(f'- {k}：{v} 章')
    if not any(cnt.values()):
        # 全 0 时原来只留一个空标题，读者不知道是"没数据"还是"脚本坏了"
        P('- 未匹配到上述任一句式（统计口径：每章末段最后一句）')
    else:
        top = max(cnt.items(), key=lambda x: x[1])
        if top[1] >= n * 0.3:
            P(f'  ⚠️ 「{top[0]}」占 {top[1]}/{n}（≥30%）→ 结尾句式偏同质')
    P('')

    # ---- 4 语言趋势 ----
    P('## 四、语言趋势\n')
    face_vals = [r['face_k'] for r in rows]
    env_vals = [r['env_pct'] for r in rows]
    P(f'- 神态/千字：均值 {sum(face_vals)/n:.1f}　'
      f'最低 {min(face_vals):.1f}（第 {min(rows, key=lambda x: x["face_k"])["no"]} 章）')
    P(f'- 环境段%：均值 {sum(env_vals)/n:.1f}　'
      f'最低 {min(env_vals):.1f}（第 {min(rows, key=lambda x: x["env_pct"])["no"]} 章）')
    P('')
    low_face = [r['no'] for r in rows if r['face_k'] < 2.0]
    if low_face:
        P(f'- ⚠️ 神态 <2.0/千字的章：{low_face}（对话章若在其中，优先补）')
    P('')

    # ---- 5 节奏与伏笔闭环（可选，缺数据则跳过） ----
    # 设计原则：依赖项目文件，找不到就跳过、绝不误报；不改任何现有指标与退出码。
    P('## 五、节奏与伏笔闭环（可选）\n')
    P('> 本节依赖项目文件（伏笔登记表 / 章纲张力标记）。缺则自动跳过，不影响其余结论与退出码。\n')

    # 5.1 伏笔闭环：扫描伏笔登记表，列出悬空 / 逾期未收
    P('### 5.1 伏笔闭环（扫描伏笔登记表）\n')
    foreshadow_path = None
    candidates_fx = [
        os.path.join(args.md_dir, '..', '005 章纲', '伏笔登记表.md'),
        os.path.join(args.md_dir, '..', '伏笔登记表.md'),
        os.path.join(args.md_dir, '伏笔登记表.md'),
    ]
    for c in candidates_fx:
        if os.path.isfile(c):
            foreshadow_path = c
            break
    if foreshadow_path:
        fx = read_text_or_none(foreshadow_path)
        if fx:
            # 解析表格行：| # | 伏笔内容 | 埋设章 | 计划回收章 | 状态 | 最近提及 |
            dangling = []
            last_no = rows[-1]['no']  # 当前已分析到的最高章号
            for line in fx.split('\n'):
                if not line.strip().startswith('|'):
                    continue
                cells = [cc.strip() for cc in line.strip().strip('|').split('|')]
                if len(cells) < 6:
                    continue
                if cells[0] in ('#', '') or set(cells[0]) <= set('-: '):
                    continue  # 表头 / 分隔行
                seq, content, buried, planned, status, recent = (
                    cells[0], cells[1], cells[2], cells[3], cells[4], cells[5])
                if '已收' in status or '弃' in status:
                    continue
                pb = int(re.sub(r'[^0-9]', '', planned)) if re.search(r'[0-9]', planned) else None
                recent_clean = recent if recent not in ('—', '-', '') else None
                rc = int(re.sub(r'[^0-9]', '', recent_clean)) if recent_clean and re.search(r'[0-9]', recent_clean) else None
                overdue = pb is not None and pb <= last_no
                stale = rc is not None and (last_no - rc) > 20
                if overdue or stale:
                    if overdue:
                        reason = '逾期未收（计划回收 %s，已写到第 %s 章）' % (planned, last_no)
                    else:
                        reason = '陈旧（最近提及 %s，已到第 %s 章，差 %d 章未提）' % (recent, last_no, last_no - rc)
                    dangling.append((seq, content, reason))
            if dangling:
                P('- 共扫描伏笔登记表：发现 **%d** 条悬空 / 逾期未收：' % len(dangling))
                for seq, content, reason in dangling:
                    P('  - 第 %s 条「%s」：%s' % (seq, content, reason))
            else:
                P('- 伏笔登记表已扫描：无悬空 / 逾期未收项（已收 / 已弃用项不计入）。')
        else:
            P('- 伏笔登记表读取失败（疑似编码问题），本项跳过。')
    else:
        P('- 未找到伏笔登记表（按 `craft-toolkit.md` 建议应在 `005 章纲\\伏笔登记表.md`）。'
          '本项跳过——可在该路径建表后重跑，悬空 / 逾期伏笔才会被列出。')
    P('')

    # 5.2 节奏交替（认知过山车）：读章纲张力标记，检测无缓冲连段
    P('### 5.2 节奏交替（认知过山车，依赖章纲标记）\n')
    zg_path = args.zhanggang
    if zg_path and os.path.isfile(zg_path):
        zg = read_text_or_none(zg_path)
        if zg:
            # 约定：章纲每行若带 [爆]/[紧]/[急] 表高强度，[缓]/[静]/[平] 表缓冲；无标记行忽略。
            tag_map = {'爆': 'hi', '紧': 'hi', '急': 'hi', '缓': 'lo', '静': 'lo', '平': 'lo'}
            seq_tags = []
            for line in zg.split('\n'):
                m = re.search(r'\[(爆|紧|急|缓|静|平)\]', line)
                if m:
                    seq_tags.append(tag_map[m.group(1)])
            if seq_tags:
                run_hi = 0
                max_hi = (0, 0)
                for i, t in enumerate(seq_tags):
                    if t == 'hi':
                        run_hi += 1
                        if run_hi > max_hi[0]:
                            max_hi = (run_hi, i + 1)
                    else:
                        run_hi = 0
                n_hi = sum(1 for t in seq_tags if t == 'hi')
                n_lo = len(seq_tags) - n_hi
                alerts = []
                if max_hi[0] >= 3:
                    alerts.append('连续 %d 个高强度章无任何缓冲（到第 %d 个标记）——认知过山车缺失，读者无喘息，'
                                  '建议其间插入 ≥1 个 [缓]/[静] 章。' % (max_hi[0], max_hi[1]))
                if n_lo == 0 and n_hi > 0:
                    alerts.append('所有被标记的章都是高强度，全程无缓冲——需要安排放缓章节。')
                if alerts:
                    for a in alerts:
                        P('  - ⚠️ %s' % a)
                else:
                    P('- 共 %d 个张力标记（高 %d / 缓 %d），无连续 ≥3 高强度无缓冲，节奏交替正常。'
                      % (len(seq_tags), n_hi, n_lo))
            else:
                P('- 章纲中未找到 `[爆]/[紧]/[缓]/[静]` 张力标记——本项跳过'
                  '（可在章纲每行末尾加标记后重跑，详见 `pipeline-stages.md` ④ 章纲）。')
        else:
            P('- 章纲读取失败，本项跳过。')
    else:
        P('- 未提供章纲（`--zhanggang` 未指定或文件不存在）。本项跳过——认知过山车检测依赖章纲张力标记，'
          '可在章纲每行末尾加 `[爆]/[紧]/[缓]/[静]` 标记后，用 '
          '`python analyze_arc.py <md目录> --zhanggang <章纲.md>` 重跑。')
    P('')

    P('---\n')
    P('> 本报告脚本可测部分如上。**钩子分类、节奏主观判断、伏笔陈旧度的"该不该收"仍需人工/AI 补**（见 `craft-toolkit.md`）。'
      '新增的「五、节奏与伏笔闭环」已能自动扫描伏笔登记表、并在提供章纲标记时检测认知过山车，但同样不替代人工裁决。')

    if skipped:
        P('')
        P('## 六、未纳入分析的章节\n')
        for name, why, expected in skipped:
            P(f'- {name}：{why if expected else "⚠ 非预期 —— " + why}')
        if any(not e for _, _, e in skipped):
            P('')
            P('> ⚠ 有非预期跳过项：这些章节没有进入合卷分析，'
              '缺席/节奏/重复模式的结论**对它们不成立**。')

    report = '\n'.join(out)

    if args.report_out:
        write_text(args.report_out, report)
        print(f'报告已写入 {args.report_out}')
    else:
        print(report)

    if args.json_out:
        write_text(args.json_out, json.dumps(rows, ensure_ascii=False, indent=1))
    # 3 = 有章节被跳过，报告不完整，需人工看第五节
    return 3 if any(not e for _, _, e in skipped) else 0


if __name__ == '__main__':
    sys.exit(main())
