#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""报警线校准：把"人类好样本"算成可执行的阈值（人类上限 × 1.25）。

用法：
    python calibrate.py <样本目录> [--out 报警线.md] [--json 报警线.json]

为什么要有这个脚本：
    `anti-ai-rules.md` 的报警线 = 同类人类作品上限 × 1.25，但此前只能手填。
    手填的结果是**永远空着**——于是所有阈值都退化成"本项目基线 ×1.25"
    （目标线），防误杀能力为 0。本脚本把"读样本 → 取上限 → ×1.25"做成一步。

退出码（与其他脚本一致，别只看 0/1）：
    0 = 已产出校准表
    1 = 参数/写盘错误
    2 = 没有任何可分析的样本（目录空 / 全部读不出）
    3 = 有样本被跳过，其余已分析（校准值可能偏低，需看跳过清单）
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lint_prose import analyse  # noqa: E402  复用同一套口径，绝不另算一套
from textio import write_text  # noqa: E402

# 目标线（来自 anti-ai-rules.md，用于判定"报警线是否真的比目标线宽"）
TARGET = {
    'face_k': 4.5,     # 神态/千字
    'env_pct': 22.0,   # 环境段%
    'ai_k': 1.5,       # AI 味/千字（error+warn）
    'dash': 3.0,       # 破折号/章
}
CEIL = 1.25           # 人类上限 × 1.25 = 报警线


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sample_dir')
    ap.add_argument('--out', dest='report_out')
    ap.add_argument('--json', dest='json_out')
    args = ap.parse_args()

    files = [f for f in glob.glob(os.path.join(args.sample_dir, '*.md'))
             if not os.path.basename(f).startswith('_')]
    rows, skipped = [], []
    for f in sorted(files):
        r = analyse(f)
        if r is None:
            skipped.append((os.path.basename(f), '无法解码（非 UTF-8 / GB18030，或疑似乱码）'))
            continue
        if r['chars'] <= 0:
            skipped.append((os.path.basename(f), '正文为空（只有标题）'))
            continue
        ai_cnt = sum(r['errors'].values()) + sum(r['warns'].values())
        r['ai_k'] = round(ai_cnt / max(r['chars'] / 1000.0, 0.001), 2)
        rows.append(r)

    if not rows:
        print(f'没找到可校准的样本 md：{args.sample_dir}', file=sys.stderr)
        for name, why in skipped:
            print(f'  · {name} —— {why}', file=sys.stderr)
        return 2

    n = len(rows)
    total_chars = sum(r['chars'] for r in rows)

    def peak(key):
        return max(r[key] for r in rows)

    metrics = [
        ('神态/千字', 'face_k', peak('face_k'), TARGET['face_k'], 'high'),
        ('环境段%', 'env_pct', peak('env_pct'), TARGET['env_pct'], 'high'),
        ('动作/千字', 'act_k', peak('act_k'), None, 'high'),
        ('AI味/千字', 'ai_k', peak('ai_k'), TARGET['ai_k'], 'low'),
        ('破折号/章', 'dash', peak('dash'), TARGET['dash'], 'low'),
    ]

    out = []
    P = out.append
    P('# 报警线校准（人类样本上限 × 1.25）\n')
    P(f'**样本**：{n} 篇　**合计**：{total_chars} 字　**口径**：复用 `lint_prose.py` 的 analyse()（同一套，不另算）\n')
    P('| 指标 | 人类样本上限 | ×1.25 = 报警线 | 目标线 | 判定 |')
    P('|---|---|---|---|---|')

    calib = {}
    for label, key, mx, target, direction in metrics:
        if direction == 'high':
            # 越高越好（神态/环境/动作）：人样上限 ×1.25 即天花板
            line = round(mx * CEIL, 1)
        else:
            # 越低越好（AI味/破折号）：**不能直接 ×1.25**——
            # 人样 T1 命中为 0 时，0×1.25 仍是 0，比目标线还严，会把正常写法全报掉。
            # 故取三者最松：×1.25、+1 的加性余量、现有目标线。
            line = round(max(mx * CEIL, mx + 1.0, target or 0.0), 1)
        calib[key] = {'human_max': mx, 'alarm': line, 'target': target}
        if target is None:
            verdict = '—（无目标线）'
        elif direction == 'high':
            # 报警线必须**高于**目标线，否则"达标即报警"，规则自相矛盾
            verdict = '✅ 报警线高于目标线' if line > target else '⚠️ 报警线未高于目标线——目标线该下调'
        else:
            # 越低越好的指标：报警线不得比目标线更紧，否则误杀
            verdict = '✅ 不严于目标线' if line >= target else '⚠️ 比目标线更紧——会误杀，已按加性余量兜底'
        P(f'| {label} | {mx} | {line} | {target if target is not None else "—"} | {verdict} |')
    P('')
    P('> 「AI味/千字」与「破折号/章」是**越低越好**的指标。人样 T1 命中常为 0，'
      '0×1.25 仍是 0，会把正常写法全报掉——故这两项取 (上限×1.25 / 上限+1 / 目标线) 三者最松。')
    P('')

    P('## 怎么用\n')
    P('1. 把上表整段贴回 `references/anti-ai-rules.md` 的「两条线要分清」一节（替换"⚠️ 待补"）。')
    P('2. 报警线只在**超出**时提示，不是删除线；超线仍要人判断是硬伤还是风格。')
    P('3. 换题材/换平台要**重新取样本**再跑——不同题材的合理密度不同，不能一套阈值走天下。')
    P('')

    if skipped:
        P('## 未纳入的样本\n')
        for name, why in skipped:
            P(f'- {name}：{why}')
        P('')
        P('> ⚠ 有样本被跳过：校准值可能**偏低**（少了高分样本），不要直接当最终值。')

    report = '\n'.join(out)
    if args.report_out:
        write_text(args.report_out, report)
        print(f'校准表已写入 {args.report_out}')
    else:
        print(report)

    if args.json_out:
        write_text(args.json_out, json.dumps(
            {'samples': n, 'total_chars': total_chars, 'calibration': calib,
             'skipped': [s[0] for s in skipped]},
            ensure_ascii=False, indent=1))

    return 3 if skipped else 0


if __name__ == '__main__':
    sys.exit(main())
