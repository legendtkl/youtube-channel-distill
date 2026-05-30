# youtube-channel-distill

> [English](./README.md) · **简体中文**

把一整个 YouTube 频道**蒸馏成一个可复用、渐进式披露的
[Claude](https://www.anthropic.com/claude) skill**——结构为 `SKILL.md`（路由）+
分类 `references/` + `sources.md`（溯源 + 更新方法）。

这是一个 **meta-skill（造 skill 的 skill）**。它本身不分析股票、不测评产品，而是把
*"听完一个博主上百期视频、把他的思维模型固化下来"* 这件事，做成一条**可复用、可重跑**的
流水线。它最适合那些**反复使用同一套框架**的创作者——财经/投资/分析类 YouTuber 是典型场景，
但任何方法论驱动的频道（科技测评、健身、烹饪技法……）都适用。

产出的就是那种能让 Claude *"像这个博主一样分析"* 的 skill——例如
`talkjun-stock-analysis`、`laoli-stock-playbook`、`nana-meigu-playbook`
都是用这条同样的流水线做出来的。

---

## 为什么需要它

一个博主的精华散落在几十上百期视频里。你没法把 100 篇转写稿塞进一个 context 还指望得到
一份干净的框架。本项目用 **fan-out（扇出）蒸馏** 解决这个问题：

```
列视频清单（yt-dlp）
   └─> 逐条取文本 —— 字幕优先：
         ├─ 有字幕？  直接用字幕（不调 ASR）
         └─ 无字幕？  配了 ASR 就转写，没配就跳过该视频
               └─> 把文稿分批派给并行 subagent 提炼
                     └─> 按类别合并 / 去重
                           └─> 组装输出 skill（SKILL.md + references + sources.md）
```

关键设计：

- **字幕优先。** 视频本身有字幕就直接用，不调 ASR——更快更省。默认只用 UP 主上传的字幕；
  YouTube 自动字幕需显式开启（`--auto-subs`）。
- **ASR 可选，只为没字幕的视频兜底。** 配了后端就转写无字幕的视频；不配则这些视频**直接跳过**
  （适合已经全程配了字幕的频道）。
- **ASR 后端可配置。** 运行时用环境变量选**服务化端点**——能吃音频的 OpenAI 兼容 omni/chat
  模型，或任意 OpenAI 兼容的 Whisper 风格 `/audio/transcriptions` 端点。**代码里不写死任何 key。**
- **渐进式披露的产物。** 生成的 skill 让 `SKILL.md` 保持精简（路由 + 第一性原理），细节进
  `references/`，用到才读。
- **可重跑。** `sources.md` 记录 channelId、样本区间和确切命令，任何人都能重跑这条流水线做
  增量更新。转写本身**断点续跑**。

---

## 五阶段流水线

| 阶段 | 做什么 | 工具 |
|---|---|---|
| **0 · 配置 ASR**（可选） | 用环境变量选后端 + 模型（仅无字幕视频才需要） | `references/asr-pipeline.md` |
| **1 · 列视频** | 拉频道上传列表（id、时长、标题） | `scripts/list_videos.sh` |
| **2 · 取文本** | 字幕优先：有字幕用字幕，无字幕走 ASR（配了的话），否则跳过 | `scripts/transcribe.py` |
| **3 · 扇出蒸馏** | 转写稿分批派给并行 subagent → 结构化要点 | `references/distillation-method.md` |
| **4 · 按类合并** | 每类一个 subagent → 去重后的 `references/<类>.md` | `references/distillation-method.md` |
| **5 · 组装 skill** | 写 `SKILL.md` + 第一性原理 + `sources.md` | `references/distillation-method.md` |

阶段 3–5 由模型驱动（Claude 按 `references/distillation-method.md` 里的提示词模板执行）。
阶段 1–2 是可独立运行的纯脚本。

---

## 快速开始

### 先决条件

- [`uv`](https://docs.astral.sh/uv/)（运行 Python 脚本、管理依赖）
- `uvx yt-dlp`（由 `uv` 自动拉取）
- 一个 ASR 后端（见 [ASR 后端](#asr-后端)）

**不需要系统 `ffmpeg`**——`transcribe.py` 用 [PyAV](https://pyav.org/) 在进程内解码和重采样。

### 1. 列频道视频

```bash
scripts/list_videos.sh "https://www.youtube.com/@<handle>/videos" 50 | tee ids.txt
```

扫一眼清单，剔除 Shorts / 纯广告 / 重复直播，留下真正承载方法论的 id。

### 2. 取文本（字幕优先）

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy   # 端点走直连

# 仅字幕（不调 ASR）：有字幕的存下来，其余跳过
uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts

# 也接受 YouTube 自动字幕（质量较低，但覆盖更多视频）：
uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts --auto-subs
```

要把**没字幕**的视频也转出来，再额外用环境变量指向一个 ASR 服务端点（代码里什么都不写死）：

```bash
export ASR_BACKEND=ark-omni                            # ark-omni | whisper-api
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # 你的 OpenAI 兼容端点
export ASR_API_KEY=<你的 key>
export ASR_MODEL=<你的 endpoint/model id>

uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts
```

产出：每条视频一个 `channel_transcripts/<vid>.txt`（头部记录来源 `source=captions:…`
或 `source=asr`）。**断点续跑**——已有的会跳过；结尾汇总有多少来自字幕、多少来自 ASR、多少被跳过。

### 3–5. 蒸馏成 skill

在装了本 skill 的 Claude 里，把转写稿交给它，让它蒸馏这个频道。它会按
`references/distillation-method.md`：定 5–8 个类别 → 并行 subagent 扇出提炼 →
按类合并 → 组装 `SKILL.md` + `references/` + `sources.md`。

---

## ASR 后端

ASR 只对**没有字幕**的视频生效，且需配齐（`ASR_API_KEY` 和 `ASR_MODEL` 都要设）才会启用。
后端由 `ASR_BACKEND`（或 `--backend`）选择，两者都是**服务化端点**——不在本地起模型。
所有配置走环境变量，代码里不写死任何厂商信息。

| 后端 | 何时用 | 必需环境变量 |
|---|---|---|
| `ark-omni`（默认） | 能吃音频的 OpenAI 兼容 **omni/chat** 模型。中文 + 财经术语质量最佳。 | `ASR_BASE_URL`、`ASR_API_KEY`、`ASR_MODEL` |
| `whisper-api` | 任意 OpenAI 兼容的 `/audio/transcriptions`（Whisper 风格）端点。 | `ASR_BASE_URL`、`ASR_API_KEY`、`ASR_MODEL` |

> ⚠️ omni/chat 后端的音频必须走 **`chat.completions`** + `input_audio` content part，
> **不是** `responses` API（它不收 `input_audio`）。厂商文档里 `responses` + `input_image`
> 的示例是给**视觉**用的，不是转写。

完整配置、网络注意事项、调参（`--chunk-seconds`、`--concurrency`）、排错见
[`references/asr-pipeline.md`](./references/asr-pipeline.md)。

---

## 依赖清单

你只需要自己装**两样**——`uv` 和 `git`。其余（Python 本身、`yt-dlp`、所有 Python 库）
都由 `uv` 按 `scripts/transcribe.py` 里的内联声明**自动拉取并锁定版本**。

### 需要你安装的工具

| 工具 | 用途 | 安装 |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) ≥ 0.4 | 运行脚本、解析安装 Python 依赖（含 `uvx yt-dlp`） | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `git` | clone / 安装本 skill | 系统包管理器 |
| 一个 POSIX shell（`bash`/`zsh`） | 运行 `scripts/list_videos.sh` | macOS/Linux 自带 |
| **Python ≥ 3.10** | 脚本运行时 | **由 `uv` 托管**，无需手动装 |

> **不需要系统 `ffmpeg`。** 音频解码/重采样由 PyAV 在进程内完成，自带编解码器。

### Python 包（由 `uv` 自动安装，**请勿**手动装）

内联声明（PEP 723）在 `scripts/transcribe.py` 的 `dependencies` 里：

| 包 | 版本 | 用途 |
|---|---|---|
| [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) | 最新 | 列视频、拉字幕与音频 |
| [`av`](https://pyav.org/)（PyAV） | ≥ 12 | 解码音频 → 16 kHz 单声道 PCM、进程内切片 |
| [`numpy`](https://numpy.org/) | 最新 | PCM 缓冲处理 |
| [`openai`](https://github.com/openai/openai-python) | ≥ 1.40 | OpenAI 兼容的 ASR 客户端（**仅 ASR 启用时**） |

仅标准库（无需安装）：`argparse`、`base64`、`io`、`json`、`os`、`re`、`subprocess`、
`sys`、`tempfile`、`wave`、`urllib.request`、`concurrent.futures`、`pathlib`。

### 外部服务 / 运行环境

| 依赖 | 是否必需 | 说明 |
|---|---|---|
| **YouTube** | 必需 | 视频 / 字幕 / 音频来源（需直连） |
| **ASR 端点** | 可选 | 仅用于无字幕视频；OpenAI 兼容的 omni/chat 或 Whisper 端点 |
| **Claude / Claude Code** | 阶段 3–5 需要 | 运行扇出蒸馏，把转写稿变成一个 skill |

---

## 作为 Claude skill 安装

直接 clone 到你的 Claude skills 目录：

```bash
git clone https://github.com/legendtkl/youtube-channel-distill.git \
  ~/.claude/skills/youtube-channel-distill
```

之后在 Claude Code 里就能用 `youtube-channel-distill` 这个 skill。详情和验证步骤见
[`docs/INSTALL.md`](./docs/INSTALL.md)。

---

## 目录结构

```
.
├── SKILL.md                       # skill 路由（Claude 首先读这个）
├── references/
│   ├── asr-pipeline.md            # ASR 配置、网络注意、排错
│   └── distillation-method.md     # 选类指南 + subagent 提示词模板
├── scripts/
│   ├── list_videos.sh             # 用 yt-dlp 列频道上传
│   └── transcribe.py              # 后端可配置的 ASR 引擎（uv 脚本）
├── docs/
│   └── INSTALL.md                 # 安装与验证指南
├── README.md / README.zh-CN.md
└── LICENSE
```

---

## 贡献

欢迎提 Issue / PR。可发力的方向：更多 ASR 后端、非财经领域的类别预设、蒸馏提示词改进。
请**永远不要提交凭证、内网端点、转写语料**（这些已被 `.gitignore` 忽略）。

## 许可证

[MIT](./LICENSE) © 2026 legendtkl
