# youtube-channel-distill

> **English** · [简体中文](./README.zh-CN.md)

Distill an entire YouTube channel into a **reusable, progressive-disclosure
[Claude](https://www.anthropic.com/claude) skill** — a `SKILL.md` router +
categorized `references/` + a `sources.md` provenance file.

This is a **meta-skill**: a "skill that builds skills". It doesn't analyze stocks
or review products itself. Instead it turns *"listen to a creator's last ~100
episodes and crystallize their mental framework"* into a repeatable, re-runnable
pipeline. It works best on creators who **reuse a consistent framework across
episodes** — finance/investing/analysis YouTubers are the canonical case, but any
methodology-driven channel (tech reviews, fitness, cooking technique) works.

The output is the kind of skill that lets Claude *"analyze the way that creator
does"* — e.g. `talkjun-stock-analysis`, `laoli-stock-playbook`,
`nana-meigu-playbook` were all built with this exact pipeline.

---

## Why

A single creator's worth of insight is spread across dozens or hundreds of
videos. You can't paste 100 transcripts into one context window and get a clean
framework out. This project solves that with **fan-out distillation**:

```
list videos (yt-dlp)
   └─> transcribe with a configurable ASR backend (no captions needed)
         └─> fan-out distillation with parallel subagents (batch the transcripts)
               └─> per-category merge / dedupe
                     └─> assemble the output skill (SKILL.md + references + sources.md)
```

Key design choices:

- **No captions required.** Audio is downloaded and transcribed via ASR, so it
  works even on channels with auto-captions disabled or in languages where YT
  captions are poor.
- **Configurable ASR backend.** Pick the backend at run time via env vars — a
  service endpoint that is OpenAI-compatible: either an omni/chat model that
  accepts audio, or any Whisper-style `/audio/transcriptions` endpoint. No keys
  are hard-coded.
- **Progressive disclosure output.** The generated skill keeps `SKILL.md` lean
  (router + first principles) and pushes detail into `references/` that are read
  only on demand.
- **Re-runnable.** `sources.md` records the channel id, sample range, and the
  exact commands, so anyone can re-run the pipeline to incrementally update the
  skill. Transcription is resumable.

---

## The five-stage pipeline

| Stage | What happens | Tooling |
|---|---|---|
| **0 · Configure ASR** *(optional)* | Choose backend + model via env vars (only needed for caption-less videos) | `references/asr-pipeline.md` |
| **1 · List videos** | Pull the channel's uploads (id, duration, title) | `scripts/list_videos.sh` |
| **2 · Get text** | Captions-first: use subtitles if present, else ASR (if configured), else skip | `scripts/transcribe.py` |
| **3 · Fan-out distill** | Batch transcripts to parallel subagents → structured findings | `references/distillation-method.md` |
| **4 · Merge per category** | One subagent per category → deduped `references/<cat>.md` | `references/distillation-method.md` |
| **5 · Assemble skill** | Write `SKILL.md` + first principles + `sources.md` | `references/distillation-method.md` |

Stages 3–5 are model-driven (run by Claude using the prompt templates in
`references/distillation-method.md`). Stages 1–2 are plain scripts you can run
standalone.

---

## Quick start

### Prerequisites

- [`uv`](https://docs.astral.sh/uv/) (runs the Python script and manages deps)
- `uvx yt-dlp` (pulled automatically by `uv`)
- An ASR backend (see [ASR backends](#asr-backends))

No system `ffmpeg` is required — `transcribe.py` uses [PyAV](https://pyav.org/)
for decoding and resampling in-process.

### 1. List a channel's videos

```bash
scripts/list_videos.sh "https://www.youtube.com/@<handle>/videos" 50 | tee ids.txt
```

Skim the list, drop Shorts / pure ads / duplicate livestreams, and keep the ids
that actually carry the methodology.

### 2. Get the text (captions-first)

```bash
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy   # endpoints want a direct connection

# captions-only (no ASR): videos with subtitles are saved, the rest are skipped
uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts

# also accept YouTube auto-captions (lower quality, but covers more videos):
uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts --auto-subs
```

To transcribe caption-less videos, additionally point the script at an ASR
service endpoint via env vars (nothing is hard-coded):

```bash
export ASR_BACKEND=ark-omni                            # ark-omni | whisper-api
export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3   # your OpenAI-compatible endpoint
export ASR_API_KEY=<your key>
export ASR_MODEL=<your endpoint/model id>

uv run scripts/transcribe.py --ids-file ids.txt --out ./channel_transcripts
```

Output: one `channel_transcripts/<vid>.txt` per video, with a metadata header
that records the source (`source=captions:…` or `source=asr`). The run is
**resumable** — already-saved videos are skipped, and the final summary reports
how many came from captions, ASR, or were skipped.

### 3–5. Distill into a skill

Hand the transcripts to Claude with this skill installed and ask it to distill
the channel. It will follow `references/distillation-method.md`: define 5–8
categories, fan out distillation across parallel subagents, merge per category,
then assemble `SKILL.md` + `references/` + `sources.md`.

---

## ASR backends

ASR only runs for videos **without** captions, and only if configured (both
`ASR_API_KEY` and `ASR_MODEL` must be set). The backend is selected by
`ASR_BACKEND` (or `--backend`). Both are **service endpoints** — no model runs
locally. All config is via env vars, so no provider details are baked into the
code.

| Backend | When to use | Required env |
|---|---|---|
| `ark-omni` *(default)* | OpenAI-compatible **omni/chat** model that accepts audio. Best Chinese + finance-term quality. | `ASR_BASE_URL`, `ASR_API_KEY`, `ASR_MODEL` |
| `whisper-api` | Any OpenAI-compatible `/audio/transcriptions` (Whisper-style) endpoint. | `ASR_BASE_URL`, `ASR_API_KEY`, `ASR_MODEL` |

> ⚠️ For omni/chat backends, audio must go through **`chat.completions`** with an
> `input_audio` content part — **not** the `responses` API (it rejects
> `input_audio`). The `responses` + `input_image` sample you may see in vendor
> docs is for vision, not transcription.

See [`references/asr-pipeline.md`](./references/asr-pipeline.md) for full config,
network notes, tuning (`--chunk-seconds`, `--concurrency`), and troubleshooting.

---

## Dependencies

You only install **two** things yourself — `uv` and `git`. Everything else
(Python itself, `yt-dlp`, the Python libraries) is fetched and pinned
automatically by `uv` from the inline script metadata in `scripts/transcribe.py`.

### Tools you install

| Tool | Purpose | Install |
|---|---|---|
| [`uv`](https://docs.astral.sh/uv/) ≥ 0.4 | Runs the script and resolves/installs its Python deps (incl. `uvx yt-dlp`) | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `git` | Clone/install the skill | system package manager |
| A POSIX shell (`bash`/`zsh`) | Runs `scripts/list_videos.sh` | preinstalled on macOS/Linux |
| **Python ≥ 3.10** | Script runtime | **managed by `uv`** — no manual install |

> **No system `ffmpeg` needed.** Audio decoding/resampling is done in-process by
> PyAV, which bundles its own codecs.

### Python packages (auto-installed by `uv`, do **not** install manually)

Declared inline (PEP 723) in `scripts/transcribe.py` → `dependencies`:

| Package | Version | Used for |
|---|---|---|
| [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) | latest | list videos, fetch subtitles & audio |
| [`av`](https://pyav.org/) (PyAV) | ≥ 12 | decode audio → 16 kHz mono PCM, chunk in-process |
| [`numpy`](https://numpy.org/) | latest | PCM buffer handling |
| [`openai`](https://github.com/openai/openai-python) | ≥ 1.40 | OpenAI-compatible ASR client (**only when ASR runs**) |

Standard-library only (no install): `argparse`, `base64`, `io`, `json`, `os`,
`re`, `subprocess`, `sys`, `tempfile`, `wave`, `urllib.request`,
`concurrent.futures`, `pathlib`.

### External services / runtime

| Dependency | Required? | Notes |
|---|---|---|
| **YouTube** | yes | source of videos / subtitles / audio (wants a direct connection) |
| **ASR endpoint** | optional | only for caption-less videos; OpenAI-compatible omni/chat or Whisper endpoint |
| **Claude / Claude Code** | for stages 3–5 | runs the fan-out distillation that turns transcripts into a skill |

---

## Install as a Claude skill

Clone the repo straight into your Claude skills directory:

```bash
git clone https://github.com/legendtkl/youtube-channel-distill.git \
  ~/.claude/skills/youtube-channel-distill
```

Then in Claude Code, the skill is available as `youtube-channel-distill`. See
[`docs/INSTALL.md`](./docs/INSTALL.md) for details and verification steps.

---

## Repository layout

```
.
├── SKILL.md                       # the skill router (what Claude reads first)
├── references/
│   ├── asr-pipeline.md            # ASR config, network notes, troubleshooting
│   └── distillation-method.md     # category selection + subagent prompt templates
├── scripts/
│   ├── list_videos.sh             # list a channel's uploads via yt-dlp
│   └── transcribe.py              # configurable-backend ASR engine (uv script)
├── docs/
│   └── INSTALL.md                 # install & verification guide
├── README.md / README.zh-CN.md
└── LICENSE
```

---

## Contributing

Issues and PRs welcome. Good areas: more ASR backends, non-finance category
presets, distillation prompt improvements. Please **never commit credentials,
internal endpoints, or transcript corpora** (they're ignored by `.gitignore`).

## License

[MIT](./LICENSE) © 2026 legendtkl
