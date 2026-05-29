#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["openai>=1.40", "av>=12", "numpy", "yt-dlp"]
# ///
"""
Download YouTube audio and transcribe it to text via a configurable ASR backend.

This is the ASR engine for the `youtube-channel-distill` skill. It does NOT need a
system ffmpeg — PyAV bundles the codecs and we resample/chunk in-process.

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


def fetch_meta(vid: str) -> dict:
    url = f"https://www.youtube.com/watch?v={vid}"
    out = subprocess.run(
        ["uvx", "yt-dlp", "--skip-download",
         "--print", "%(title)s\t%(upload_date)s\t%(duration)s", url],
        capture_output=True, text=True,
    )
    title, date, dur = "", "", ""
    if out.returncode == 0 and out.stdout.strip():
        parts = out.stdout.strip().split("\t")
        title = parts[0] if len(parts) > 0 else ""
        date = parts[1] if len(parts) > 1 else ""
        dur = parts[2] if len(parts) > 2 else ""
    return {"title": title, "date": date, "duration": dur}


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

def transcribe_video(vid, backend, model, client, prompt, chunk_seconds, concurrency, out_dir):
    out_path = out_dir / f"{vid}.txt"
    if out_path.exists() and out_path.stat().st_size > 200:
        print(f"[skip] {vid} (already transcribed)")
        return out_path

    meta = fetch_meta(vid)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        audio = download_audio(vid, tmp)
        pcm = decode_pcm16_mono_16k(audio)

    chunks = list(chunk_pcm(pcm, chunk_seconds))
    print(f"[work] {vid}  {meta['title'][:40]!r}  {len(pcm)//16000}s -> {len(chunks)} chunks")

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
    header = (
        f"# {meta['title']} | {meta['date']} | dur={meta['duration']}s | vid={vid}\n"
        f"# https://www.youtube.com/watch?v={vid}\n\n"
    )
    out_path.write_text(header + body, encoding="utf-8")
    print(f"[done] {vid} -> {out_path} ({len(body)} chars)")
    return out_path


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
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

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
    print(f"{len(vids)} videos -> {out_dir}  (backend={args.backend}, model={args.model})")

    client = make_client()
    if not args.model:
        sys.exit("ASR_MODEL / --model is required for ark-omni and whisper-api backends.")

    ok, fail = 0, []
    for vid in vids:
        try:
            transcribe_video(vid, args.backend, args.model, client, args.prompt,
                             args.chunk_seconds, args.concurrency, out_dir)
            ok += 1
        except Exception as e:  # noqa
            print(f"[FAIL] {vid}: {e}", file=sys.stderr)
            fail.append(vid)

    print(f"\nDone. {ok} ok, {len(fail)} failed.")
    if fail:
        print("Failed ids:", ",".join(fail))
        (out_dir / "_failed.txt").write_text("\n".join(fail))


if __name__ == "__main__":
    main()
