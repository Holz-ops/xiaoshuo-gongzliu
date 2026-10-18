#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文本质检 + lint：环境/动作/神态密度 ＋ 去 AI 味六门（脚本可测部分）。

用法：
    python lint_prose.py <md目录> [--top 15] [--json out.json] [--quiet]

退出码（**崩溃不再混进 1**：此前 GB18030 稿会抛 UnicodeDecodeError，
解释器同样返回 1，调用方分不清"有 error"和"脚本挂了"）：
    0 = 无 error（可放行）
    1 = 存在 error（禁止放行）
    2 = 没有任何可分析的章节（目录空 / 全是序章 / 全部读不出）
    3 = 有文件被跳过，其余已分析（部分完成，需人工看跳过清单）

字数口径说明：本脚本的「字数」是**质检口径**（剔除所有标题行与独立成行的
场景分隔符 `——`），用作密度分母；与 `count_chars.py` 的**交付口径**
（只剔首行标题）不是同一个数。两者不必相等，也不要拿它们互相对账。

口径来源：
    - 密度三指标：本项目第一卷基线（文本质检报告-第一卷（基线）.md）
    - 六门与三级禁用表：吸收自「文枢创作院」05-anti-ai-rules.md
    - 门槛校准：报"报警线"不报"删除线"，命中须人工过一眼（误杀防护）
"""
import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from chapter_num import ChapterError, parse_chapter  # noqa: E402  章号唯一来源
from chapter_num import is_non_chapter, is_title_line  # noqa: E402
from textio import read_text_or_none  # noqa: E402  严格解码回退

# ============ 一、密度指标（正向） ============
ENV_WORDS = (
    '光|灯|亮|暗|黑|影子|风|沙|雨|雪|雾|空气|味|气味|潮|干|冷|凉|热|烫|温|'
    '天|云|日头|太阳|月|星|地平线|顶棚|房顶|天花板|墙|地|地面|地板|沙地|泥|'
    '声音|响|安静|静|吵|嗡|咔|咕|轰|铁皮|金属|水泥|砖|灰|锈|油渍|湿'
)
ACT_WORDS = (
    '拧|摸|拽|扣|擦|数|握|按|推|拉|拖|提|塞|掏|掐|拍|敲|磕|刮|蹲|站|坐|躺|'
    '走|跑|退|转身|回头|伸手|抬手|低头|抬头|点头|摇头|弯|探|搬|扛|抱|夹|翻|'
    '撕|折|压|垫|划|写|记|看|盯|听|闻|咬|喝|吃|抽烟|点烟'
)
FACE_WORDS = (
    '脸色|脸|眉|眯|咬|咬牙|皱|撇嘴|嘴角|点头|摇头|僵|愣|沉默|不吭声|没说话|'
    '笑|哼|叹了口气|脖子|肩膀|后背|手指|手背|指节|喉结|下巴|眼皮|眼睛|出汗|'
    '抿|咧嘴|耸|缩|顿|停住|吸了口气'
)

# ============ 二、六门（负向·脚本可测） ============
# T1 = 出现即报（error）；T2 = 同段聚集才报（warn）；T3 = 全文密度控制（report）
T1_PATTERNS = {
    'T1面部套话': r'(嘴角(?:勾起|上扬|微扬|一勾)|笑意不达眼底|眼底闪过一[丝抹]|眼神(?:微暗|一沉|幽幽))',
    'T1手部套话': r'(指节(?:发白|泛白|捏得发白)|指甲(?:陷入|掐进|嵌进)|手不自觉地|拳头(?:紧握|攥得))',
    'T1呼吸套话': r'(心脏(?:漏跳|猛地一缩)|心跳漏了[一]?拍|脊背(?:发凉|一凉|发寒)|呼吸(?:一滞|一窒)|倒吸一口凉气)',
    # 「不知为何」原在 T1，但 anti-ai-rules.md 的三级表里根本没列它 ——
    # 脚本自作主张比文档更宽。按裁决降为 T2（见该文件 T2 行的同步登记）。
    'T1万能占位': r'(有什么东西(?:在|碎|动)|某种(?:说不清|难以名状|说不明白)|一种莫名的)',
    'T1升华结尾': r'(他终于明白[了]?|这就是.{0,8}的意义|从这一刻起|他懂了。|这一切.{0,4}都值了)',
    'T1上帝腔': r'(而他不知道的是|殊不知|然而他不知道|他不知道的是)',
    # 「很」原是可省的（`他很?`），于是「他紧张得手心出汗」「他愤怒地捶了一下墙」
    # 这类**情绪修饰动作的白描**被判 error（error = 禁止放行）。
    # anti-ai-rules.md 的规格示例是「他很害怕」——带程度副词才算"直述情感"。
    # 故改为**必须带程度副词**，并用 (?![地得]) 排除剩存的修饰用法。
    'T1情感直述': r'(他(?:很|非常|十分|特别|格外|异常|极其|挺|太)'
                  r'(?:害怕|紧张|恐惧|悲伤|愤怒)(?![地得])'
                  r'|心中(?:涌起|泛起)|内心(?:充满|无比))',
}
T2_PATTERNS = {
    # 「他知道」必须人工过一眼：中文小说里"他知道等着是什么意思"是正常判断，
    # 只有"他知道，从这一刻起一切都不同了"式的升华才要改。故降为 warn。
    'T2抽象总结': r'(他(?:知道|明白|意识到|清楚))',
    # 同上处置：AI 味确实有，但「不知为何，他心里一紧」也是中文网文的正常起句。
    # 降为 warn 要求人工过一眼，而不是直接判 error 阻断放行。
    'T2万能占位': r'(不知为何)',
    'T2依赖句式': r'(很[\u4e00-\u9fff]{1,2}很[\u4e00-\u9fff]{1,2}但|连他自己都(?:没|不))',
    'T2高频比喻': r'(像溺水的人|像抓住(?:了)?浮木|如同(?:被|被一)|仿佛被.{0,6}击中)',
    'T2程度副词': r'(非常|十分|极其|格外|异常|无比)(?:的)?',
}
T3_PATTERNS = {
    'T3水意象': r'(涟漪|波光|水面|潮湿的|水汽)',
    'T3景物同步': r'(风也(?:停了|静了)|天也(?:暗了|黑了)|像是感应到)',
}
# 五律之破折号：每章 ≤3 处（正文中的破折号，不含独立成行的场景分隔符）
DASH_RE = r'——'


def chapter_no(path):
    """章号；无法解析返回 None。

    **复用 chapter_num**：此前内联 `第(\\d+)章` 只认阿拉伯数字，
    而本技能交付规范就是中文数字（`第十七章：上报`）→ 那种命名下全部返回 0，
    被当成"没有章节"静默丢掉。中文章号与阿拉伯章号必须一起认。
    """
    try:
        return parse_chapter(path)[0]
    except ChapterError:
        return None


def clean_body(text):
    # 标题行判定走 chapter_num.is_title_line：裸写的「第10章：大抢修」也算标题，
    # 否则它会被当成正文第一段，环境/动作/神态密度与字数都被它污染。
    lines = [l.strip() for l in text.split('\n')]
    return [l for l in lines if l and not is_title_line(l) and l != '——']


def count(paras, pattern):
    return sum(len(re.findall(pattern, p)) for p in paras)


def analyse(path):
    # 走 textio 的 utf-8-sig → utf-8 → gb18030 严格回退；
    # 读不出（含解出乱码）时返回 None 由调用方登记跳过，**不再崩栈**。
    text = read_text_or_none(path)
    if text is None:
        return None
    paras = clean_body(text)
    body = '\n'.join(paras)
    chars = len(re.sub(r'\s', '', body))
    k = max(chars / 1000.0, 0.001)

    env_s = sum(1 for p in paras if re.search(ENV_WORDS, p))
    act_n = count(paras, ACT_WORDS)
    face_n = count(paras, FACE_WORDS)

    r = {
        'no': chapter_no(path),
        'chapter': os.path.basename(path)[:-3],
        'chars': chars,
        'paras': len(paras),
        'env_pct': round(env_s / max(len(paras), 1) * 100, 1),
        'act_k': round(act_n / k, 1),
        'face_k': round(face_n / k, 1),
        'errors': {},
        'warns': {},
        'reports': {},
    }

    for name, pat in T1_PATTERNS.items():
        hits = re.findall(pat, body)
        if hits:
            r['errors'][name] = len(hits)
    for name, pat in T2_PATTERNS.items():
        n = len(re.findall(pat, body))
        if n:
            r['warns'][name] = n
    for name, pat in T3_PATTERNS.items():
        n = len(re.findall(pat, body))
        if n:
            r['reports'][name] = n

    # 五律：正文破折号（独立成行的场景分隔符不计）
    r['dash'] = len(re.findall(DASH_RE, body))
    if r['dash'] > 3:
        r['warns']['五律破折号>3'] = r['dash']

    r['err_total'] = sum(r['errors'].values())
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('md_dir')
    ap.add_argument('--top', type=int, default=15)
    ap.add_argument('--json', dest='json_out')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    # 递归扫子目录：分卷（`第一卷/`）是常态，非递归会把整卷报成「没内容」。
    candidates = [f for f in glob.glob(os.path.join(args.md_dir, '**', '*.md'), recursive=True)
                  if not os.path.basename(f).startswith('_')
                  and not any(part.startswith(('_', '.'))
                              for part in os.path.relpath(f, args.md_dir).split(os.sep)[:-1])]

    # 分三类：能分析的 / 预期内跳过（序章番外）/ 非预期跳过（读不出、无章号）
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

    if not rows:
        print(f'没找到可分析的章节 md：{args.md_dir}', file=sys.stderr)
        for name, why, _ in skipped:
            print(f'  · {name} —— {why}', file=sys.stderr)
        return 2

    # rows 可能为空已被上面拦住，这里的 n 一定 >= 1（此前空表会炸 max()/除法）
    n = len(rows)

    # ---------- 密度总表 ----------
    if not args.quiet:
        print('（字数＝质检口径：不含标题行与独立成行的场景分隔符，'
              '与 count_chars 的交付口径不同）')
        print(f'{"章":<5}{"字数":>7}{"环境%":>7}{"动作/千":>8}{"神态/千":>8}{"破折号":>7}{"error":>7}')
        print('-' * 52)
        for r in rows:
            flag = ' ←' if r['err_total'] else ''
            print(f'{r["no"]:<5}{r["chars"]:>7}{r["env_pct"]:>7}{r["act_k"]:>8}'
                  f'{r["face_k"]:>8}{r["dash"]:>7}{r["err_total"]:>7}{flag}')
        print('-' * 52)
        print(f'{"均值":<5}{"":>7}'
              f'{sum(r["env_pct"] for r in rows)/n:>7.1f}'
              f'{sum(r["act_k"] for r in rows)/n:>8.1f}'
              f'{sum(r["face_k"] for r in rows)/n:>8.1f}')

    # ---------- 排行榜 ----------
    if not args.quiet:
        print()
        for key, label, asc in [
            ('env_pct', '【环境最薄】环境段占比', True),
            ('face_k', '【神态最少】神态密度', True),
            ('act_k', '【动作最少】动作密度', True),
        ]:
            # top 大于章节数时不能还写「的 15 章」——只有 3 章却印「15 章」，
            # 读者会以为漏了 12 章。取实际条数。
            k = min(args.top, len(rows))
            print(f'{label} 最低的 {k} 章')
            for r in sorted(rows, key=lambda x: x[key] if asc else -x[key])[:k]:
                print(f'  第{r["no"]:>2}章  {r[key]:>6.1f}   {r["chapter"]}')
            print()

    # ---------- 六门（error 汇总） ----------
    all_err = {}
    for r in rows:
        for k, v in r['errors'].items():
            all_err.setdefault(k, []).append((r['no'], v))
    print('=== 六门扫描（脚本可测部分）===')
    if not all_err:
        print('  ✅ T1 类（出现即报）全部为 0')
    else:
        for k, lst in sorted(all_err.items()):
            print(f'  ⚠ {k}：{len(lst)} 章命中')
            for no, v in lst[:6]:
                print(f'      第{no}章 ×{v}')
    warn_total = sum(sum(r['warns'].values()) for r in rows)
    print(f'  T2/T3 与五律（warn/report）：{warn_total} 处（逐条裁决，非必改）')

    if skipped:
        print('\n跳过清单（未参与质检）：')
        for name, why, expected in skipped:
            tag = why if expected else f'⚠ 非预期 —— {why}'
            print(f'  · {name}：{tag}')
        if any(not e for _, _, e in skipped):
            print('  ⚠ 有非预期跳过项：这些章节没有被质检，不要当成"已通过"。')

    if args.json_out:
        # 用 with 并补 ensure_ascii；目录不存在时由 write_text 兜底建目录
        from textio import write_text
        write_text(args.json_out, json.dumps(rows, ensure_ascii=False, indent=1))
        print(f'\n明细已写入 {args.json_out}')

    err = sum(r['err_total'] for r in rows)
    if err:
        print(f'\n退出码：1（存在 {err} 处 error，禁止放行）')
        return 1
    if any(not e for _, _, e in skipped):
        print('\n退出码：3（无 error，但有文件被跳过，质检未覆盖全部）')
        return 3
    print('\n退出码：0（无 error）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
