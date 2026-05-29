#!/usr/bin/env bash
# List a YouTube channel's latest uploads (id, duration, title) — newest first, no Shorts noise.
# yt-dlp --flat-playlist is cleaner than opencli and can reach further back than the API.
#
# Usage:
#   scripts/list_videos.sh "https://www.youtube.com/@handle/videos" 50
#   scripts/list_videos.sh "https://www.youtube.com/channel/UCxxxx/videos" 100
#
# Pipe the first column into transcribe.py via --ids-file, e.g.:
#   scripts/list_videos.sh "<url>" 50 | cut -f1 > ids.txt
#   uv run scripts/transcribe.py --ids-file ids.txt --out ./transcripts
set -euo pipefail

URL="${1:?usage: list_videos.sh <channel-videos-url> [limit]}"
LIMIT="${2:-50}"

# YouTube wants a direct connection in this environment.
unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy 2>/dev/null || true

# Note: some yt-dlp builds print the template's "\t" literally instead of a real tab.
# Pipe through sed so the columns are separated by an ACTUAL tab — then `cut -f1` works.
uvx yt-dlp --flat-playlist --playlist-end "$LIMIT" \
  --print "%(id)s\t%(duration)s\t%(title)s" \
  "$URL" | sed 's/\\t/\t/g'
