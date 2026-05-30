#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["openai>=1.40", "av>=12", "numpy", "yt-dlp"]
# ///
"""
Turn YouTube videos into text transcripts, captions-first.

This is the transcript engine for the `youtube-channel-distill` skill. Policy per
video:
  1. If the video has subtitles, use them (no ASR). Uploaded subtitles are
     preferred; YouTube auto-generated captions are used only with --auto-subs.
  2. If there are no usable captions, transcribe with ASR — but ONLY if ASR is
     configured (ASR_API_KEY + ASR_MODEL present).
  3. If there are no captions and ASR is not configured, the video is skipped.

ASR (when used) needs NO system ffmpeg — PyAV bundles the codecs and we
resample/chunk in-process.

Network (counterintuitive, see references/asr-pipeline.md):
  - YouTube (yt-dlp) and the Ark endpoint both want a DIRECT connection.
    Run with proxies unset:  unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy

ASR backend is chosen by the ASR_BACKEND env var (or --backend). Both backends are
service endpoints; config is via env so the same script works for any provider:

  ark-omni        (default) OpenAI-compatible *omni* chat model that accepts audio.
                  Calls chat.completions with an `input_audio` content part.
                  Env: ASR_BASE_URL, ASR_API_KEY, ASR_MODEL
                  Proven on Volcano Ark omni:
                    ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
                    ASR_MODEL=ep-xxxxxxxx           (your endpoint id)
                  NOTE: audio goes through chat.completions, NOT the `responses` API
                  (responses rejects input_audio). The `responses`+input_image sample
                  in Ark docs is for vision, not transcription.

  whisper-api     OpenAI-compatible /audio/transcriptions endpoint (Whisper-style).
                  Env: ASR_BASE_URL, ASR_API_KEY, ASR_MODEL (e.g. whisper-1)

Usage:
  unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy
  export ASR_BACKEND=ark-omni
  export ASR_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
  export ASR_API_KEY=sk-....
  export ASR_MODEL=ep-xxxxxxxxxxxxx-xxxxx

  # transcribe latest 50 of a channel into ./transcripts/
  uv run transcribe.py --channel "https://www.youtube.com/@handle/videos" --limit 50 --out ./transcripts

  # or specific video ids
  uv run transcribe.py --ids B8OxtGSEfoo,mCI0-03LcvM --out ./transcripts

Output: one <out>/<vid>.txt per video, with a metadata header. Resumable — existing
non-empty outputs are skipped.
"""
import argparse, base64, io, json, os, re, subprocess, sys, tempfile, wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

DEFAULT_PROMPT = (
    "请将这段音频完整、逐字地转写成简体中文文本。"
    "保留所有数字、英文股票代码（如 NVDA、TSLA、PE、CPI）和专业术语。"
    "只输出转写出来的正文，不要做任何总结、解释或额外说明。"
)

# ----------------------------- video listing ------------------------------

def list_channel_video_ids(channel_url: str, limit: int) -> list[str]:
    out = subprocess.run(
        ["uvx", "yt-dlp", "--flat-playlist", "--playlist-end", str(limit),
         "--print", "%(id)s", channel_url],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        sys.exit(f"yt-dlp listing failed:\n{out.stderr}")
    return [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]


def fetch_info(vid: str) -> dict:
    """One yt-dlp -J call: returns the video's full metadata JSON, which also
    carries `subtitles` (uploaded) and `automatic_captions` (auto-generated)."""
    url = f"https://www.youtube.com/watch?v={vid}"
    out = subprocess.run(
        ["uvx", "yt-dlp", "--skip-download", "-J", url],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"yt-dlp info fetch failed for {vid}:\n{out.stderr[-800:]}")
    return json.loads(out.stdout)


def meta_from_info(info: dict) -> dict:
    return {
        "title": info.get("title", "") or "",
        "date": info.get("upload_date", "") or "",
        "duration": info.get("duration", "") or "",
    }


# ----------------------------- subtitles (captions-first) ------------------

# Subtitle formats we can parse, best first. json3 is cleanest; vtt/srt parse via
# the same timestamp-stripping path; ttml/srv* are XML-ish and handled best-effort.
_SUB_EXT_PREFERENCE = ["json3", "vtt", "srt", "srv3", "srv2", "srv1", "ttml"]


def pick_subtitle(info: dict, langs: list[str], allow_auto: bool):
    """Choose the best available subtitle track. Uploaded subtitles ('自带字幕')
    are preferred; auto-generated captions are used only if allow_auto is set.
    Language preference is honored in order, then any remaining track. Returns
    (lang, kind, fmt_dict) or None."""
    manual = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}

    def choose_lang(table):
        if not table:
            return None
        for want in langs:
            for have in table:
                if have == want or have.split("-")[0] == want.split("-")[0]:
                    return have
        return next(iter(table))  # any available track

    for kind, table in (("manual", manual),) + ((("auto", autos),) if allow_auto else ()):
        lang = choose_lang(table)
        if lang:
            fmts = table[lang]
            fmt = min(
                fmts,
                key=lambda f: _SUB_EXT_PREFERENCE.index(f.get("ext"))
                if f.get("ext") in _SUB_EXT_PREFERENCE else len(_SUB_EXT_PREFERENCE),
            )
            return lang, kind, fmt
    return None


def _strip_timestamps(raw: str) -> str:
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT":
            continue
        if line.startswith(("Kind:", "Language:", "NOTE")):
            continue
        if "-->" in line or line.isdigit():
            continue
        line = re.sub(r"<[^>]+>", "", line)   # inline timing/style tags
        line = re.sub(r"\{[^}]+\}", "", line)
        if line:
            out.append(line)
    deduped = []                              # auto-captions roll/repeat lines
    for l in out:
        if not deduped or deduped[-1] != l:
            deduped.append(l)
    return "\n".join(deduped)


def _parse_json3(raw: str) -> str:
    data = json.loads(raw)
    parts = [seg.get("utf8", "") for ev in data.get("events", [])
             for seg in (ev.get("segs") or [])]
    text = "".join(parts)
    return "\n".join(l.strip() for l in text.splitlines() if l.strip())


def fetch_subtitle_text(fmt: dict) -> str:
    import urllib.request
    url = fmt.get("url")
    if not url:
        return ""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read().decode("utf-8", "replace")
    if fmt.get("ext") == "json3":
        try:
            return _parse_json3(raw)
        except Exception:  # noqa
            return _strip_timestamps(raw)
    return _strip_timestamps(raw)


def download_audio(vid: str, tmpdir: Path) -> Path:
    url = f"https://www.youtube.com/watch?v={vid}"
    tmpl = str(tmpdir / f"{vid}.%(ext)s")
    out = subprocess.run(
        ["uvx", "yt-dlp", "-f", "bestaudio/best", "--no-playlist", "-o", tmpl, url],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"audio download failed for {vid}:\n{out.stderr[-800:]}")
    files = sorted(tmpdir.glob(f"{vid}.*"))
    if not files:
        raise RuntimeError(f"no audio file produced for {vid}")
    return files[0]


# ----------------------------- audio decode -------------------------------

def decode_pcm16_mono_16k(path: Path) -> "np.ndarray":
    import av  # noqa
    import numpy as np
    container = av.open(str(path))
    stream = container.streams.audio[0]
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    parts = []

    def collect(frames):
        if frames is None:
            return
        if not isinstance(frames, list):
            frames = [frames]
        for f in frames:
            if f is None:
                continue
            parts.append(f.to_ndarray().reshape(-1))

    for frame in container.decode(stream):
        collect(resampler.resample(frame))
    try:
        collect(resampler.resample(None))  # flush (newer PyAV)
    except Exception:
        pass
    container.close()
    if not parts:
        raise RuntimeError(f"decoded no audio from {path}")
    return np.concatenate(parts).astype(np.int16)


def pcm_to_wav_bytes(pcm16) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(pcm16.tobytes())
    return buf.getvalue()


def chunk_pcm(pcm16, chunk_seconds: int):
    step = chunk_seconds * 16000
    for i in range(0, len(pcm16), step):
        yield pcm16[i:i + step]


# ----------------------------- ASR backends -------------------------------

def make_client():
    from openai import OpenAI
    base = os.environ.get("ASR_BASE_URL")
    key = os.environ.get("ASR_API_KEY")
    if not key:
        sys.exit("ASR_API_KEY is not set (needed for ark-omni / whisper-api backends).")
    return OpenAI(base_url=base, api_key=key)


def transcribe_chunk_ark(client, model, wav_bytes, prompt, retries=3):
    b64 = base64.b64encode(wav_bytes).decode()
    last = None
    for attempt in range(retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=0,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "input_audio",
                         "input_audio": {"data": b64, "format": "wav"}},
                    ],
                }],
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa
            last = e
    raise RuntimeError(f"ark transcription failed after {retries} tries: {last}")


def transcribe_chunk_whisper(client, model, wav_bytes, prompt, retries=3):
    last = None
    for attempt in range(retries):
        try:
            f = ("chunk.wav", io.BytesIO(wav_bytes), "audio/wav")
            resp = client.audio.transcriptions.create(model=model, file=f)
            return (getattr(resp, "text", "") or "").strip()
        except Exception as e:  # noqa
            last = e
    raise RuntimeError(f"whisper-api transcription failed after {retries} tries: {last}")


# ----------------------------- per-video flow ------------------------------

def _write_output(out_path, meta, vid, source, body):
    header = (
        f"# {meta['title']} | {meta['date']} | dur={meta['duration']}s | vid={vid} | source={source}\n"
        f"# https://www.youtube.com/watch?v={vid}\n\n"
    )
    out_path.write_text(header + body, encoding="utf-8")


def transcribe_video(vid, asr_enabled, backend, model, client, prompt,
                     chunk_seconds, concurrency, out_dir, sub_langs, allow_auto):
    """Captions-first policy:
      1. If the video has subtitles (uploaded; or auto-generated when --auto-subs)
         use them — no ASR call.
      2. Otherwise, transcribe with ASR — but only if ASR is configured.
      3. If there are no usable captions and ASR is not configured, skip the video.
    Returns one of: "captions", "asr", "skipped".
    """
    out_path = out_dir / f"{vid}.txt"
    if out_path.exists() and out_path.stat().st_size > 200:
        print(f"[skip] {vid} (already done)")
        return "captions"  # already have text; don't re-fetch

    info = fetch_info(vid)
    meta = meta_from_info(info)

    # 1) captions first
    sub = pick_subtitle(info, sub_langs, allow_auto)
    if sub:
        lang, kind, fmt = sub
        text = fetch_subtitle_text(fmt)
        if text.strip():
            src = f"captions:{kind}:{lang}"
            _write_output(out_path, meta, vid, src, text)
            print(f"[done] {vid}  {meta['title'][:40]!r}  via {src} ({len(text)} chars)")
            return "captions"
        print(f"[warn] {vid} had a {kind} {lang} track but it parsed empty; falling back")

    # 2) no usable captions -> ASR only if configured
    if not asr_enabled:
        print(f"[skip] {vid}  {meta['title'][:40]!r}  no captions and ASR not configured")
        return "skipped"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        audio = download_audio(vid, tmp)
        pcm = decode_pcm16_mono_16k(audio)

    chunks = list(chunk_pcm(pcm, chunk_seconds))
    print(f"[work] {vid}  {meta['title'][:40]!r}  {len(pcm)//16000}s -> {len(chunks)} chunks (ASR)")

    def do_chunk(idx_chunk):
        idx, pcm_chunk = idx_chunk
        wav = pcm_to_wav_bytes(pcm_chunk)
        if backend == "whisper-api":
            txt = transcribe_chunk_whisper(client, model, wav, prompt)
        else:  # ark-omni
            txt = transcribe_chunk_ark(client, model, wav, prompt)
        return idx, txt

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = {ex.submit(do_chunk, ic): ic[0] for ic in enumerate(chunks)}
        for fut in as_completed(futs):
            idx, txt = fut.result()
            results[idx] = txt
            print(f"    [{vid}] chunk {idx+1}/{len(chunks)} ok")

    body = "\n".join(results[i] for i in sorted(results))
    _write_output(out_path, meta, vid, "asr", body)
    print(f"[done] {vid} -> {out_path} via asr ({len(body)} chars)")
    return "asr"


def main():
    ap = argparse.ArgumentParser(description="YouTube -> ASR transcripts (configurable backend)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--channel", help="channel /videos URL")
    src.add_argument("--ids", help="comma-separated video ids")
    src.add_argument("--ids-file", help="file with one video id per line")
    ap.add_argument("--limit", type=int, default=50, help="how many videos when using --channel")
    ap.add_argument("--out", default="./transcripts", help="output directory")
    ap.add_argument("--backend", default=os.environ.get("ASR_BACKEND", "ark-omni"),
                    choices=["ark-omni", "whisper-api"])
    ap.add_argument("--model", default=os.environ.get("ASR_MODEL"))
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--chunk-seconds", type=int, default=600)
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--sub-langs", default=os.environ.get("SUB_LANGS", "zh-Hans,zh,zh-Hant,en"),
                    help="comma-separated subtitle language preference (captions-first)")
    ap.add_argument("--auto-subs", action="store_true",
                    help="also accept YouTube auto-generated captions, not just uploaded ones")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sub_langs = [s.strip() for s in args.sub_langs.split(",") if s.strip()]

    if args.channel:
        vids = list_channel_video_ids(args.channel, args.limit)
    elif args.ids:
        vids = [v.strip() for v in args.ids.split(",") if v.strip()]
    else:
        vids = [ln.strip() for ln in Path(args.ids_file).read_text().splitlines() if ln.strip()]
    # Defensive: an id list may carry trailing "\t<dur>\t<title>" if it was built from a
    # 3-column listing whose tabs printed literally. Keep only the leading id token so the
    # output filename and watch?v= URL stay clean (titles can contain '/', which breaks paths).
    vids = [re.split(r"[\s\\]+", v)[0] for v in vids if v.strip()]

    # ASR is optional. It's "configured" only when both a key and a model are present.
    # Captions are always tried first; ASR only kicks in for videos without captions.
    key = os.environ.get("ASR_API_KEY")
    asr_enabled = bool(key) and bool(args.model)
    if (key or args.model) and not asr_enabled:
        print("[note] ASR is partially configured (need both ASR_API_KEY and ASR_MODEL) "
              "-> treating ASR as DISABLED; videos without captions will be skipped.")
    client = make_client() if asr_enabled else None
    print(f"{len(vids)} videos -> {out_dir}  (captions-first, langs={sub_langs}, "
          f"auto_subs={args.auto_subs}, asr={'on:'+args.backend if asr_enabled else 'off'})")

    counts = {"captions": 0, "asr": 0, "skipped": 0}
    fail = []
    for vid in vids:
        try:
            kind = transcribe_video(vid, asr_enabled, args.backend, args.model, client,
                                    args.prompt, args.chunk_seconds, args.concurrency,
                                    out_dir, sub_langs, args.auto_subs)
            counts[kind] = counts.get(kind, 0) + 1
        except Exception as e:  # noqa
            print(f"[FAIL] {vid}: {e}", file=sys.stderr)
            fail.append(vid)

    print(f"\nDone. {counts['captions']} via captions, {counts['asr']} via ASR, "
          f"{counts['skipped']} skipped (no captions, ASR off), {len(fail)} failed.")
    if counts["skipped"] and not asr_enabled:
        print("Tip: configure ASR (ASR_API_KEY + ASR_MODEL) to transcribe the skipped videos.")
    if fail:
        print("Failed ids:", ",".join(fail))
        (out_dir / "_failed.txt").write_text("\n".join(fail))


if __name__ == "__main__":
    main()
