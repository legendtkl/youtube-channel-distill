---
name: youtube-channel-distill
description: >
  Distill a YouTube channel — especially a finance / investing / analysis creator who
  reuses a consistent mental framework across episodes — into a reusable, progressive-disclosure
  Claude skill (a SKILL.md router + categorized references + a sources.md), the way
  `talkjun-stock-analysis` was built. Use this skill whenever the user wants to "蒸馏/distill a
  YouTube channel/blogger into a skill", "把某个 up 主/频道做成一个 skill", "学习某个博主的分析方法/框架",
  "提炼某个 YouTube 频道的方法论", clone a creator's analysis style, or build a methodology skill from
  video transcripts. It covers the full pipeline: list videos (yt-dlp) → get text (captions-first:
  use the video's own subtitles when present, fall back to ASR only for videos without captions) →
  fan-out distillation with parallel subagents → per-category merge/dedupe → assemble the output
  skill. ASR is optional; videos without captions are skipped unless ASR is configured by the user at
  run time via env vars (ASR_BACKEND / ASR_BASE_URL / ASR_API_KEY / ASR_MODEL) — a service endpoint:
  an OpenAI-compatible omni/chat model that accepts audio (e.g. Volcano Ark omni), or any
  OpenAI-compatible Whisper-style /audio/transcriptions endpoint.
metadata:
  type: skill
---

# YouTube 频道蒸馏 · 把一个博主做成一个 skill

把一个 YouTube 频道（尤其是反复使用同一套分析框架的财经/投资/分析类创作者）提炼成一个**渐进式披露的 Claude skill**——结构对标 `talkjun-stock-analysis`：`SKILL.md`（路由 + 第一性原理 + 流程骨架 + 输出风格）+ 分类 `references/` + `sources.md`（溯源 + 更新方法）。

**这是一个"造 skill 的 skill"（meta-skill）。** 它本身不分析股票，而是把"听完一个博主上百期视频、把他的思维模型固化下来"这件事做成一条可复用、可重跑的流水线。

---

## 何时用本 skill

- 用户说："把 `@某频道` 蒸馏 / 提炼成一个 skill"、"学习这个博主的方法做成 skill"、"clone 这个 up 主的分析风格"。
- 用户给一个 YouTube 频道/handle URL，想要一套能"像他那样分析"的可复用框架。
- 想更新一个已有的"博主框架"skill（增量补充更老/更新的期号）。

不适用：只是想看某一条视频的总结（直接用 opencli/transcript），或频道没有可提炼的稳定方法论（纯 vlog/搞笑/资讯流水账）。

---

## 五阶段流水线（骨架，细节按需读 reference）

> **先决条件**：`uv`、`uvx yt-dlp` 可用；ASR **服务端点**已配置（见下）。网络反直觉点：**YouTube 和 ASR 端点都走直连**（`unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy`）。详见 `references/asr-pipeline.md`。

### 阶段 0 · 配置 ASR（用户运行时决定）

ASR 后端 + 模型由用户用环境变量配置，脚本不写死任何 key/model。两个后端都是**服务化端点**（不在本地起模型）：

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy   # YouTube + ASR 端点直连
export ASR_BACKEND=ark-omni                            # ark-omni | whisper-api
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # 任意 OpenAI 兼容 omni 端点
export ASR_API_KEY=<你的 key>
export ASR_MODEL=<你的 endpoint/model id，如 ep-xxxxxxxxxxxxx-xxxxx>
```

**ASR 是可选的**：阶段 2 字幕优先，只有遇到**没有字幕**的视频才需要 ASR。若用户不配 ASR，无字幕的视频会被跳过（适合只蒸馏有字幕的频道）。要覆盖无字幕视频就让用户给出后端与 model。两个后端的差异、坑、`input_audio` 走 `chat.completions` 而非 `responses` 的关键点，见 `references/asr-pipeline.md`。

### 阶段 1 · 列视频清单

```bash
scripts/list_videos.sh "https://www.youtube.com/@<handle>/videos" 50
```

输出 `id\t时长\t标题`。先扫一眼：剔除 Shorts/纯广告/重复直播，确认这些视频确实承载方法论。把要转写的 id 存成 `ids.txt`。

### 阶段 2 · 拿文本（字幕优先，ASR 兜底）

```bash
uv run scripts/transcribe.py --ids-file ids.txt --out ./<channel>_transcripts
# 或一步到位：
uv run scripts/transcribe.py --channel "https://www.youtube.com/@<handle>/videos" --limit 50 --out ./<channel>_transcripts
```

**每条视频的处理策略**：

1. **有字幕 → 直接用字幕**，不调 ASR（默认只用 UP 主上传的字幕；加 `--auto-subs` 才用 YouTube 自动字幕）。
2. **无字幕 + 配了 ASR**（`ASR_API_KEY` + `ASR_MODEL`）→ 走 ASR：下音频 → PyAV 解码 16k 单声道 → 切片 → 调 ASR → 拼接。
3. **无字幕 + 没配 ASR** → **跳过该视频**（结尾会汇总跳过数，并提示配置 ASR）。

每条产出 `<out>/<vid>.txt`（头部含标题/日期/时长 + `source=captions:.../asr`），**断点续跑**（已有的跳过）。`--sub-langs` 配字幕语言优先级（默认 `zh-Hans,zh,zh-Hant,en`）。细节/排错见 `references/asr-pipeline.md`。

### 阶段 3 · 并行蒸馏（fan-out）

转写量大时**不要一个 context 硬读**。先确定 **5–8 个 reference 类别**（见 `references/distillation-method.md` 选类指南——财经博主可直接用 talkjun 的 7 类做起点，但要按本频道实际反复出现的主题增删）。然后把转写稿**分批**派给并行 subagent（用 Agent 工具或 Workflow），每个 subagent 把它那批稿子提炼成**结构化要点**（注明出处期号），按类别落盘到 `_findings/`。

### 阶段 4 · 按类合并去重（merge）

每个类别派一个 merge subagent：读该类别所有 findings → 合并、去重、升级表述、保留高频/高信息量观点、丢弃一次性闲聊 → 产出该类别的 `references/<category>.md`。

### 阶段 5 · 组装 skill

- 写 `SKILL.md`：YAML frontmatter（`name` + 富 `description`，塞满触发词供语义匹配）+ 第一性原理（从全部语料里提炼博主最核心、最反复强调的几条）+ 标准流程骨架 + 路由表 + 输出风格。
- 写 `sources.md`：频道信息、channelId、样本期号区间、提炼方法、**如何更新**（重跑本流水线）。
- 保留原始转写稿到一个 `*_transcripts/` 目录，供下次增量更新。

模板、subagent 提示词、选类指南、输出 skill 的目录约定，全部在 `references/distillation-method.md`。

---

## 路由表

| 你要做的事 | 读这个 |
|---|---|
| 配置/排查 ASR、换后端、网络坑、切片/并发参数 | `references/asr-pipeline.md` |
| 怎么选类别、怎么写 subagent 提炼/合并提示词、输出 skill 的 SKILL.md / sources.md 模板、第一性原理怎么提 | `references/distillation-method.md` |
| 列视频 | `scripts/list_videos.sh` |
| 转写引擎（后端可配置） | `scripts/transcribe.py` |

---

## 输出风格（造出来的 skill 应该长这样）

- **渐进式披露**：`SKILL.md` 只装路由 + 骨架 + 必须常驻的核心心法；细节进 `references/`，用到哪个读哪个。
- **description 富触发词**：把用户可能的说法（中英、口语、术语、人名/频道名）尽量塞进 frontmatter 的 `description`，语义路由才命中。
- **忠于博主、但标注时效**：观点用博主自己的语言和框架；强时效的个股/板块判断单独成文并标"仅参考、以当时为准"。
- **可重跑**：`sources.md` 写清 channelId、样本区间、更新命令，让下一次增量更新照着跑即可。
- **区分事实/框架/主观**：方法论是骨架；具体数字永远让使用方用数据类 skill 现取，不在 skill 里写死会过期的数字。
