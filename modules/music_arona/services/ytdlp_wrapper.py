# ytdlp_wrapper.py
from __future__ import annotations

import asyncio
import random
from pathlib import Path
from typing import List, Union

import yt_dlp
from yt_dlp.utils import ExtractorError

from modules.music_arona.domain.entity.track import Track
from modules.music_arona.config import config            


CACHE_DIR = Path("./cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)

NICO_COOKIE_PATH = Path("./nico_cookies.txt")
NICO_COOKIE_PATH.touch(exist_ok=True)

COMMON_OPTS: dict = {
    "format": "bestaudio[acodec=opus][asr=48000]/bestaudio/best",
    "sleep_requests": 1,
    "sleep_interval": 1,
    "max_sleep_interval": 3,
    "random_sleep": True,
    "quiet": True,
    "no_warnings": True,
    "concurrent_fragment_downloads": 4,
    "source_address": "0.0.0.0",
    "extract_flat": "in_playlist",
    "postprocessors": [{"key": "FFmpegMetadata"}],
}

def _is_nico(url: str) -> bool:
    return ("nicovideo.jp" in url) or ("nico.ms" in url)


def _build_nico_opts(login: bool) -> dict:
    opts = COMMON_OPTS | {
        "paths": {"home": str(CACHE_DIR)},
        "outtmpl": "%(id)s.%(ext)s",
        "cookiefile": str(NICO_COOKIE_PATH),
        "extract_flat": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {"key": "FFmpegMetadata"},
        ],
    }
    if login:
        opts |= {
            "username": config.get("niconico.email"),
            "password": config.get("niconico.password"),
        }
    return opts


def _inject_local_path(entry: dict, ytdl: yt_dlp.YoutubeDL):
    if not entry:
        return

    if entry.get("requested_downloads"):
        entry["local_path"] = entry["requested_downloads"][0]["filepath"]
    else:
        entry["local_path"] = Path(ytdl.prepare_filename(entry)).with_suffix(".mp3")


def _entry_to_track(entry: dict, *, downloaded: bool):
    return Track(
        url=entry["url"],
        title=entry.get("title", "No title"),
        duration=entry.get("duration") or 0,
        thumbnail=entry.get("thumbnail"),
        stream_url=entry.get("url") if downloaded else None,
        requester_id=0,
    )


async def ensure_stream(track: Track) -> Track:
    if track.stream_url:
        return track

    loop = asyncio.get_running_loop()
    def _run():
        ytdl = yt_dlp.YoutubeDL(COMMON_OPTS | {"noplaylist": False, "extract_flat": "in_playlist", "skip_download": True})
        info = ytdl.extract_info(track.url, download=False)
        return info["url"]

    try:
        track.stream_url = await loop.run_in_executor(None, _run)
    except ExtractorError as e:
        raise RuntimeError(f"stream 解決失敗: {e}") from e
    return track


async def extract(query: str, *, shuffle_playlist: bool = False) -> Union[Track, List[Track]]:
    loop = asyncio.get_running_loop()
    is_nico = _is_nico(query)

    if is_nico:
        opts = _build_nico_opts(login=not NICO_COOKIE_PATH.stat().st_size)
        download = True
    else:
        noplaylist = False
        opts = COMMON_OPTS | {"noplaylist": noplaylist, "extract_flat": "in_playlist", "skip_download": True}
        download = False

    def _run() -> tuple[dict, yt_dlp.YoutubeDL]:
        ytdl = yt_dlp.YoutubeDL(opts)
        info = ytdl.extract_info(query, download=download)

        if download:
            if info.get("entries"):
                for ent in info["entries"]:
                    _inject_local_path(ent, ytdl)
            else:
                _inject_local_path(info, ytdl)

        return info, ytdl

    try:
        info, ytdl = await loop.run_in_executor(None, _run)
    except ExtractorError as e:
        raise RuntimeError(f"yt-dlp 失敗: {e}") from e

    if is_nico:
        try:
            ytdl.cookiejar.save(str(NICO_COOKIE_PATH), ignore_discard=True, ignore_expires=True)
        except Exception as e:
            print(f"[warn] cookie 保存失敗: {e}")

    if info.get("entries"):
        tracks = [_entry_to_track(ent, downloaded=download) for ent in info["entries"] if ent]
        if shuffle_playlist:
            random.shuffle(tracks)
        return tracks

    return _entry_to_track(info, downloaded=download)
