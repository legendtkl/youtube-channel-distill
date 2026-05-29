# Installation & Verification Guide

> [English](#english) · [简体中文](#简体中文)

---

## English

### 1. Prerequisites

| Tool | Why | Install |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | runs `transcribe.py` and manages its deps | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `yt-dlp` | list + download YouTube audio | pulled on demand via `uvx yt-dlp` (no manual install) |
| An ASR backend | speech-to-text | see [README → ASR backends](../README.md#asr-backends) |

You do **not** need a system `ffmpeg` — `transcribe.py` decodes audio with PyAV,
which bundles its own codecs.

### 2. Install the skill into Claude

```bash
git clone https://github.com/legendtkl/youtube-channel-distill.git \
  ~/.claude/skills/youtube-channel-distill
```

Claude Code discovers skills under `~/.claude/skills/`. After cloning, start (or
restart) Claude Code; the skill registers as `youtube-channel-distill`.

To verify it's picked up, start a fresh session and ask Claude to "distill a
YouTube channel into a skill" — it should route to this skill.

### 3. Verify the scripts work (no skill needed)

```bash
cd ~/.claude/skills/youtube-channel-distill   # or wherever you cloned it

# YouTube wants a direct connection in most environments:
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

# (a) list a channel — should print id<TAB>duration<TAB>title rows
scripts/list_videos.sh "https://www.youtube.com/@<handle>/videos" 5

# (b) the transcriber's help — confirms uv resolves deps (openai, av, numpy, yt-dlp)
uv run scripts/transcribe.py --help
```

### 4. End-to-end test against your ASR endpoint

Configure your service endpoint via env vars (never commit keys — export them in
your shell), then transcribe one short video:

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

export ASR_BACKEND=ark-omni                            # ark-omni | whisper-api
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # your OpenAI-compatible endpoint
export ASR_API_KEY=<your key>
export ASR_MODEL=<your endpoint/model id>

# transcribe a single short video into ./_smoke
uv run scripts/transcribe.py --ids <SHORT_VIDEO_ID> --out ./_smoke

cat ./_smoke/<SHORT_VIDEO_ID>.txt
```

You should get a `.txt` with a metadata header followed by the transcript. If you
hit `unknown field input_audio`, the endpoint/model doesn't accept audio via
`chat.completions` — use a true omni model, or switch to a `whisper-api` endpoint.
See [`references/asr-pipeline.md`](../references/asr-pipeline.md) for tuning and
troubleshooting.

---

## 简体中文

### 1. 先决条件

| 工具 | 用途 | 安装 |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) | 运行 `transcribe.py`、管理依赖 | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `yt-dlp` | 列出 + 下载 YouTube 音频 | 由 `uvx yt-dlp` 按需拉取（无需手动安装） |
| 一个 ASR 后端 | 语音转文字 | 见 [README → ASR 后端](../README.zh-CN.md#asr-后端) |

**不需要**系统 `ffmpeg`——`transcribe.py` 用 PyAV 解码，自带编解码器。

### 2. 把 skill 安装进 Claude

```bash
git clone https://github.com/legendtkl/youtube-channel-distill.git \
  ~/.claude/skills/youtube-channel-distill
```

Claude Code 会发现 `~/.claude/skills/` 下的 skill。clone 完后启动（或重启）
Claude Code，skill 会注册为 `youtube-channel-distill`。

验证是否被识别：开一个全新会话，让 Claude "把某个 YouTube 频道蒸馏成 skill"，
它应当路由到本 skill。

### 3. 验证脚本可用（无需 skill）

```bash
cd ~/.claude/skills/youtube-channel-distill   # 或你 clone 的位置

# 多数环境下 YouTube 需要直连：
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

# (a) 列频道——应打印 id<TAB>时长<TAB>标题
scripts/list_videos.sh "https://www.youtube.com/@<handle>/videos" 5

# (b) 转写器 help——确认 uv 能解析依赖（openai、av、numpy、yt-dlp）
uv run scripts/transcribe.py --help
```

### 4. 对你的 ASR 端点做端到端测试

用环境变量配置服务端点（永远不要提交 key，在 shell 里 export），然后转写单条短视频：

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

export ASR_BACKEND=ark-omni                            # ark-omni | whisper-api
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # 你的 OpenAI 兼容端点
export ASR_API_KEY=<你的 key>
export ASR_MODEL=<你的 endpoint/model id>

# 转写单条短视频到 ./_smoke
uv run scripts/transcribe.py --ids <短视频ID> --out ./_smoke

cat ./_smoke/<短视频ID>.txt
```

你会得到一个带元信息头部 + 正文的 `.txt`。若报 `unknown field input_audio`，说明该
端点/模型不通过 `chat.completions` 收音频——换一个真正的 omni 模型，或改用 `whisper-api`
端点。调参与排错见 [`references/asr-pipeline.md`](../references/asr-pipeline.md)。
