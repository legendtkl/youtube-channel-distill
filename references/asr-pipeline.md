# ASR 转写管线 · 配置与排错

`scripts/transcribe.py` 是转写引擎。它**不需要系统 ffmpeg**（PyAV 自带编解码），流程是：

```
yt-dlp -f bestaudio 下音频  →  PyAV 解码成 16k 单声道 s16 PCM  →  切 600s/片
     →  每片调 ASR 后端  →  按序拼接  →  写 <out>/<vid>.txt（带头部）
```

断点续跑：输出已存在且 >200 字节就跳过。失败的 id 落到 `<out>/_failed.txt`。

---

## ASR 后端（用户运行时配置，脚本不写死）

后端用 `ASR_BACKEND` 选，配置全走环境变量。两种**服务化端点**：

### 1. `ark-omni`（默认，推荐）

OpenAI 兼容的 **omni / 多模态 chat 模型**，能吃音频。脚本走 **`chat.completions`**，content 里放一个 `input_audio` part（base64 wav）。

```bash
export ASR_BACKEND=ark-omni
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # 公开火山方舟，或任意 OpenAI 兼容 omni 端点
export ASR_API_KEY=<key>
export ASR_MODEL=<endpoint id，如 ep-xxxxxxxxxxxxx-xxxxx>
```

> ⚠️ **关键坑**：音频必须走 `chat.completions` + `input_audio`，**不是 `responses` API**。Ark 文档里那个 `responses.create(... input_image ...)` 的示例是**视觉**用的；`responses` 不收 `input_audio`（会报 unknown field）。omni 模型在 chat.completions 下支持的 content type 是 `text` / `image_url` / `video_url` / `input_audio`。中文 + 财经术语质量远超本地 whisper，几乎无错字。实测 ~52s/片（600s 音频）。

### 2. `whisper-api`

任意 OpenAI 兼容的 `/audio/transcriptions`（Whisper 风格）端点。

```bash
export ASR_BACKEND=whisper-api
export ASR_BASE_URL=<你的 OpenAI 兼容端点>
export ASR_API_KEY=<key>
export ASR_MODEL=whisper-1     # 或服务商的等价模型名
```

> 两个后端都是**调远端服务**——不在本地起模型。选 `ark-omni` 还是 `whisper-api`，取决于你手上的端点支持哪种调用：能吃音频的 omni/chat 模型走 ark-omni，标准 Whisper 风格的转写端点走 whisper-api。

---

## 网络

- **YouTube（yt-dlp）和 ASR 端点都走直连** → 跑之前 `unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy`。某些环境下挂着代理反而连不上端点 / 卡住 YouTube。如果你的 ASR 端点必须走代理，按需单独设置即可。

---

## 常用参数

```bash
uv run scripts/transcribe.py \
  --channel "https://www.youtube.com/@handle/videos"  # 或 --ids a,b,c / --ids-file ids.txt
  --limit 50 \
  --out ./<channel>_transcripts \
  --chunk-seconds 600 \      # 切片长度；600s 实测稳，过长可能超 payload/超时
  --concurrency 4 \          # 单视频内分片并发
  --backend ark-omni \       # 覆盖 ASR_BACKEND
  --model ep-xxxx \          # 覆盖 ASR_MODEL
  --prompt "自定义转写提示词"
```

---

## 排错

| 症状 | 多半原因 / 处理 |
|---|---|
| 连不上端点 / 一直卡住不报错 | 多半是代理没 unset。`unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy` 重试 |
| `unknown field input_audio` | 用了 `responses` API 收音频。本脚本默认 `chat.completions`；若你改过代码，改回去 |
| `ASR_API_KEY is not set` | 两个后端都必须设 key |
| `ASR_MODEL / --model is required` | 两个后端都必须给 model/endpoint id |
| yt-dlp 提示无 JS runtime / 缺 ffmpeg 的 WARNING | 仅警告，flat 列表与 bestaudio 下载不受影响，可忽略 |
| 某条视频反复 FAIL | 可能是直播/会员/地区限制。先单独 `--ids <vid>` 复现；不行就从样本里剔除 |
| payload 过大 / 超时 | 调小 `--chunk-seconds`（如 300）|
| `grep -rl ... /` 扫满 CPU 数小时 | 别全盘 grep；所有检索限定到转写目录 |

> 上述 Ark omni 走 chat.completions + input_audio 的方案，是实测把 ~100 期视频稳定转写出来的同一条路径。
