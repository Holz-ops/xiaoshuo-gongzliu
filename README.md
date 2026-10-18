# xiaoshuo-gongzliu · 中文网文流水线 Skill

把「写一本中文网文」拆成**可确认、有脚本、有产物**的六步流水线：每步有门，不走就过不去。

这是一个 [Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills) 格式的技能包（`SKILL.md` + `references/` + `scripts/` + `assets/templates/`），用于让 AI 助手稳定地产出长篇中文网文，而不是写到一半跑偏、崩人设、断进度。

当前版本：**1.5.0** · 许可证：**MIT**

---

## 它解决什么问题

AI 写长篇最常见的四个翻车点：

| 问题 | 本技能的应对 |
|---|---|
| 一口气写十几章，方向错了才发现 | **人工确认门**：每步产出完整展示 + 自检打勾才能往下走 |
| 人设 / 纪年 / 编号前后穿帮 | `check_consistency.py` 扫一致性，`analyze_arc.py` 查人物长期缺席 |
| 写着写着「AI 味」重、四字格堆砌 | `lint_prose.py` 六门质检（T1/T2/T3 禁用词、环境/动作/神态密度），退出码 0 才放行 |
| 换会话就丢进度 | 进度卡 + 续写档案 + 上下文压缩包，用**档案**接管记忆，不靠对话 |

## 核心设计

### 主干六步 + 门

```
新书入口：头脑风暴 ─────────┐
                            ├→ ①报告 → ②梗概 → ③目录 → ④章纲一句话 → ⑤生成正文(含章纲回填)
续写入口：素材体检 + 定基准 ┘                                              │
                                                                        ↓ 自动衔接（不停顿）
      ⑥复查：AI 分析 → 出分析报告 → 据报告改稿 → 同时输出「修改报告 + 改后文章」
                                                        ↓
                                            ★ 门：暂停，等人工确认
```

| 步 | 名称 | 做什么 | 门 |
|---|---|---|---|
| ① | 报告 | 定题材、定基调、备齐梗概要用的资料 | ★ |
| ② | 梗概 | 人物、主线、势力、卷结构、结局方向 + 设定基准表 | ★ |
| ③ | 目录 | 分卷分章，每章标题 + 一句话，锁死结构 | ★ |
| ④ | 章纲一句话 | 每章 30~80 字「发生什么 / 结果 / 留什么钩子」+ P-xx 打法编号 | ★ |
| ⑤ | 生成正文 | 按章纲扩写 2000~3000 字 + 章纲回填 | 不停顿 → 自动进 ⑥ |
| ⑥ | 复查 | AI 分析 → 出报告 → 据报告改稿 → 输出「修改报告 + 改后文章」 | ★（在出口） |

- **没有确认，就没有下一步。** 每次过门必须：展示全文 → 逐条自检 → 给出「通过 / 修改 / 重来」三选项 → 停下等拍板。
- **⑤→⑥ 不算破门**：刚写完的初稿必然有字数不足、描写偏薄、残留词这类机械问题，交给机器改比交给人看更合理。门的位置变了，强度不变——**人只做裁判**。
- **一本书 = 一条独立流水线**，禁止把 A 书的设定用到 B 书。

### 三层分工

| 层 | 管什么 | 产出 |
|---|---|---|
| 第一层 文章层 | 故事、人物、节奏、文风 | 正文（每章 2000~3000 字） |
| 第二层 AI 检查层 | 字数、一致性、穿帮、markdown 残留（查「对不对」） | 自查报告 |
| 第三层 人的层 | 节奏、爽感、人物讨不讨喜（查「好不好」） | 人的意见 → AI 改 → 通过 |

## 安装

把它作为一个 skill 文件夹放进你的助手 skills 目录即可：

**WorkBuddy**

```bash
git clone https://github.com/Holz-ops/xiaoshuo-gongzliu.git ~/.workbuddy/skills/xiaoshuo-gongzliu
```

**Claude Code**

```bash
git clone https://github.com/Holz-ops/xiaoshuo-gongzliu.git ~/.claude/skills/xiaoshuo-gongzliu
```

只想要单文件包：`python scripts/package_skill.py ~/xiaoshuo-gongzliu.zip`（会先校验文件齐全，再打 zip）。

**运行环境**：Python 3.9+（纯标准库，无第三方依赖）。仅 `md2docx.py` 依赖 WorkBuddy 的 `edsdk`。

## 快速开始

起一本新书的第一个动作不是写，是**建目录**：

```bash
python scripts/init_book.py "书名" ~/我的小说        # 一次建好 00~99 分层 + 铺模板
```

然后把需求丢给助手。三种常见开场：

- 「我想写一本 XX 类型的小说，主角是 XX」 → 走新书入口，先出报告
- 「我有目录了，把第 3~8 章写出来」 → 从 ⑤ 开始，先补进度卡
- 「帮我看一下这几章有什么毛病」 → 从 ⑥ 开始

手写某一环绕过的影响不大，但**①~④ 缺了会让 ⑤ 的正文没有地基**。

## 常用命令

```bash
python scripts/init_book.py "书名" <父目录>          # 起一本新书
python scripts/inventory.py <书目录>                 # 文件管理体检
python scripts/article_index.py <书目录> --write      # 文章位置索引（当前稿是谁 / 缺章断号）
python scripts/count_chars.py <md目录> --min 2000 --max 3000
python scripts/lint_prose.py <md目录>                 # 文本质检门禁：退出码 0 放行、1 禁放行
python scripts/check_consistency.py <md目录> --terms terms.md --era 267
python scripts/analyze_arc.py <md目录>                # 合卷分析：节奏曲线 / 人物缺席 / 语言趋势
python scripts/calibrate.py <人类好样本目录>            # 报警线校准：喂样本 → 出阈值
python scripts/md2docx.py <md目录> <输出目录>          # 批量 md → docx
python scripts/epub_extract.py <epub> <输出目录>       # epub → 逐章 md（需无 DRM）
python scripts/txt_split.py <txt> <输出目录>           # txt → 逐章 md
```

## 目录结构

```
xiaoshuo-gongzliu/
├── SKILL.md                 # 路由页：流程、门规则、决策路由（助手读这个）
├── CHANGELOG.md             # 用户可见的行为变更
├── LICENSE                  # MIT
├── references/              # 14 份细则，按需加载
│   ├── pipeline-stages.md   # 六步详解：输入/动作/产出/完成标准/易错点/确认话术
│   ├── 工程纪律.md           # 目录分层 / 文章四格 / 快照 / 成品包 / 命名 / 退出码
│   ├── style-rules.md       # 文风规范（短句白描 + 嘴碎内心戏）
│   ├── anti-ai-rules.md     # 去 AI 味：三条硬边界、六门、T1/T2/T3 禁用表
│   ├── craft-toolkit.md     # 人物档案卡、钩子五型、伏笔陈旧度、九维质量门槛
│   ├── platform-specs.md    # 番茄规格、前 10 章检查清单
│   ├── 问题回流闭环.md       # 反馈 → 问题本（I/F/T/O）→ 规则 → 升级材料
│   ├── 好小说拆解库.md / 评估标准.md / 逐章分析工作流.md
│   └── ...                  # 三幕十五节拍表、开书投喂标准、验证方法论、踩坑规则对照等
├── scripts/                 # 16 个 Python 脚本，纯标准库
└── assets/
    ├── 流程图-总图.svg / 三层分工.svg / 章纲两条.svg / 章纲回填.svg
    └── templates/           # 13 份模板：进度卡、续写档案、上下文压缩包、修改报告…
```

> 想只了解流程：读 `SKILL.md`。想了解某一步的全部细节：读 `references/pipeline-stages.md`。要对既有成稿做审查拆解：读 `references/逐章分析工作流.md`。

## 几条硬规矩

1. **章纲两条**：先行章纲（冻结，作对比基准）／总结章纲（按实际总结，后续以它为准）。计划与实际两列都留，一比就是偏差。
2. **改稿前先快照**：正文原地覆盖，不留快照永远拿不回改前版本。
3. **没过人那一关，绝不放进 `007 最终文章\`**。
4. **按批推进，每批 3~10 章**，每批改完必出批次成品包。
5. **字数用脚本算，不要目测**。交付口径看 `count_chars.py`，质检密度分母看 `lint_prose.py`，两者不必相等。

## 参与

Issue 与 PR 都欢迎。改 `references/` 里的规则时，请一并说明「踩坑 → 规则」的对应关系；改 `scripts/` 的阈值时，请跑一遍 `calibrate.py`（阈值必须是校准出来的，不是拍脑袋定的）。

## 来源与归属

本项目的**六步流水线**、**人工确认门**、**三层分工**、以及全部 `scripts/` 质检脚本、`anti-ai-rules.md`（去 AI 味六门）、`style-rules.md`、`craft-toolkit.md`、`工程纪律.md`、`问题回流闭环.md` 等规则文档，均为作者在实际写作过程中踩坑总结的原创内容。

`references/三幕十五节拍表.md` 使用的「三幕 / 十五节点」划分属于通用的剧作常识，本文件是按中文长篇网文节奏重写的**自述版本**，不引用任何第三方原文。

本仓库遵循「只借结构与方法、不借文本」的原则。如果你认为其中某处做法与某项公开成果高度重合，欢迎在 Issue 中指出，作者会核实后在本节补上致谢。

## 许可证

[MIT](./LICENSE) — 随便用、随便改、随便商用，保留版权声明即可。
