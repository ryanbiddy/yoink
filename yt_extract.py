#!/usr/bin/env python3
"""
yt_extract.py — pull transcript + screenshots from any YouTube video.

Usage:
    python yt_extract.py <url>
    python yt_extract.py <url> --interval 30 --out ./yt_extracts

Outputs (per video, in a folder named after the video title):
    transcript.txt   — plain text transcript
    combined.md      — transcript + screenshots interleaved by timestamp
    screenshots/     — frames extracted every <interval> seconds
    video.<ext>      — low-res copy used for frame extraction (delete if you want)

Requirements:
    pip install yt-dlp
    ffmpeg installed and on PATH  (winget install ffmpeg, or brew install ffmpeg)
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path


# Windows reserved device names. As path segments these fail to open even
# when extensions are appended ("CON.txt" still resolves to the console
# device). slugify() prepends an underscore when it would otherwise emit
# one of these. Match is case-insensitive.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def slugify(text: str) -> str:
    # ASCII-only so the resulting slug is safe as a filesystem path segment
    # AND satisfies the server's strict session_id regex
    # ([A-Za-z0-9_-]{1,64}). Without re.ASCII, \w matches Unicode word
    # chars and a session named "cafe" with non-ASCII letters would slug to
    # those same letters -- which the API would later reject when the user
    # tried to add to it.
    slug = re.sub(r"[^\w\-]+", "_", text.strip(), flags=re.ASCII)[:80].strip("_")
    # Windows reserved-name guard. A video titled "CON" or "AUX" would
    # otherwise produce a folder Windows refuses to open.
    if slug.upper() in _WINDOWS_RESERVED_NAMES:
        slug = "_" + slug
    return slug


def fmt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def parse_srt(srt_path: Path):
    """Yield (start_sec, end_sec, text) from an SRT file."""
    content = srt_path.read_text(encoding="utf-8", errors="ignore")
    pattern = re.compile(
        r"(\d+):(\d+):(\d+)[,.](\d+)\s+-->\s+(\d+):(\d+):(\d+)[,.](\d+)"
    )
    blocks = re.split(r"\n\s*\n", content.strip())
    seen = set()
    for block in blocks:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        m = pattern.search(lines[1] if pattern.search(lines[1] or "") else " ".join(lines[:2]))
        if not m:
            for ln in lines:
                m = pattern.search(ln)
                if m:
                    break
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = (int(x) for x in m.groups())
        start = h1 * 3600 + m1 * 60 + s1 + ms1 / 1000
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
        text_lines = [ln for ln in lines if not pattern.search(ln) and not ln.isdigit()]
        text = " ".join(text_lines)
        text = re.sub(r"<[^>]+>", "", text).strip()
        # YouTube auto-captions repeat heavily; dedupe consecutive identical lines
        if text and text not in seen:
            seen.add(text)
            yield start, end, text


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--interval", type=int, default=30, help="screenshot interval in seconds (default: 30)")
    ap.add_argument("--out", default="./yt_extracts", help="output root folder")
    ap.add_argument("--keep-video", action="store_true", help="keep the downloaded video file")
    args = ap.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    print("Fetching title...")
    title = subprocess.check_output(["yt-dlp", "--get-title", args.url], text=True).strip()
    folder = out_root / slugify(title)
    folder.mkdir(exist_ok=True)
    print(f"-> {folder}")

    print("Downloading video + subtitles...")
    subprocess.run(
        [
            "yt-dlp",
            "--write-auto-subs",
            "--write-subs",
            "--sub-lang", "en.*,en",
            "--convert-subs", "srt",
            "-f", "worst[height>=360]/worst",
            "-o", str(folder / "video.%(ext)s"),
            args.url,
        ],
        check=True,
    )

    video_files = [f for f in folder.glob("video.*") if f.suffix in (".mp4", ".webm", ".mkv")]
    srt_files = list(folder.glob("video*.srt"))
    if not video_files:
        sys.exit("No video file found.")
    video_file = video_files[0]

    print(f"Extracting screenshots every {args.interval}s...")
    shots_dir = folder / "screenshots"
    shots_dir.mkdir(exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-loglevel", "error", "-y",
            "-i", str(video_file),
            "-vf", f"fps=1/{args.interval}",
            "-q:v", "2",
            str(shots_dir / "shot_%04d.jpg"),
        ],
        check=True,
    )
    shots = sorted(shots_dir.glob("shot_*.jpg"))

    entries = list(parse_srt(srt_files[0])) if srt_files else []

    print("Writing transcript.txt and combined.md...")
    if entries:
        plain = "\n".join(text for _, _, text in entries)
        (folder / "transcript.txt").write_text(plain, encoding="utf-8")

    md = [f"# {title}", "", f"Source: {args.url}", ""]
    for i, shot in enumerate(shots):
        start = i * args.interval
        end = (i + 1) * args.interval
        chunk = " ".join(t for s, _, t in entries if start <= s < end)
        md.append(f"## [{fmt_time(start)}]")
        md.append("")
        md.append(f"![shot {i+1}](screenshots/{shot.name})")
        md.append("")
        if chunk:
            md.append(chunk)
            md.append("")
    (folder / "combined.md").write_text("\n".join(md), encoding="utf-8")

    if not args.keep_video:
        video_file.unlink(missing_ok=True)

    print()
    print(f"Done. {len(shots)} screenshots, {len(entries)} caption lines.")
    print(f"Folder: {folder}")
