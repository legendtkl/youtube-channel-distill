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
   └─> 用可配置 ASR 后端转写（不需要字幕）
         └─> 把转写稿分批派给并行 subagent 提炼
               └─> 按类别合并 / 去重
                     └─> 组装输出 skill（SKILL.md + references + sources.md）
```

关键设计：

- **不需要字幕。** 下载音频走 ASR 转写，所以即使频道关了自动字幕、或语言的 YT 字幕质量差也能做。
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
| **0 · 配置 ASR** | 用环境变量选后端 + 模型 | `references/asr-pipeline.md` |
| **1 · 列视频** | 拉频道上传列表（id、时长、标题） | `scripts/list_videos.sh` |
| **2 · 转写** | 下音频 → 解码 → 切片 → ASR → 文本 | `scripts/transcribe.py` |
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

### 2. 转写

用环境变量把脚本指向你的 ASR 服务端点（代码里什么都不写死），然后运行：

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy   # 端点走直连

export ASR_BACKEND=ark-omni                            # ark-omni | whisper-api
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # 你的 OpenAI 兼容端点
export ASR_API_KEY=<你的 key>
export ASR_MODEL=<你的 endpoint/model id>

uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts
```

产出：每条视频一个 `channel_transcripts/<vid>.txt`（带元信息头部）。**断点续跑**——
已转写的视频会跳过。

### 3–5. 蒸馏成 skill

在装了本 skill 的 Claude 里，把转写稿交给它，让它蒸馏这个频道。它会按
`references/distillation-method.md`：定 5–8 个类别 → 并行 subagent 扇出提炼 →
按类合并 → 组装 `SKILL.md` + `references/` + `sources.md`。

---

## ASR 后端

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
