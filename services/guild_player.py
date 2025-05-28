# guild_player.py
import asyncio
import contextlib
import logging
import random
from collections import deque
from pathlib import Path
from typing import Deque, Optional

import discord

from domain.entity.track import Track
from services import ytdlp_wrapper as ytdl

logger = logging.getLogger("arona.music.player")


class GuildPlayer:

    def __init__(self, guild: discord.Guild, voice_client: discord.VoiceClient):
        self.guild = guild
        self.voice_client = voice_client

        self._queue: Deque[Track] = deque()
        self._queue_not_empty = asyncio.Condition()

        self.current_track: Optional[Track] = None
        self.loop_current: bool = False           # /loop でトグル

        self._player_task: Optional[asyncio.Task] = None
        self._closed: bool = False

    async def enqueue(self, track: Track | list[Track]) -> None:
        if isinstance(track, list):
            async with self._queue_not_empty:
                self._queue.extend(track)
                self._queue_not_empty.notify()
        else:
            async with self._queue_not_empty:
                self._queue.append(track)
                self._queue_not_empty.notify()

    async def dequeue(self) -> Track:
        async with self._queue_not_empty:
            while not self._queue and not self._closed:
                await self._queue_not_empty.wait()
            if self._closed:
                raise asyncio.CancelledError
            return self._queue.popleft()

    def upcoming(self) -> list[Track]:
        return list(self._queue)

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        tmp = list(self._queue)
        random.shuffle(tmp)
        self._queue = deque(tmp)

    def remove(self, position: int) -> Track:
        """0-based index の曲を除去して返す。"""
        if position < 0 or position >= len(self._queue):
            raise IndexError
        tmp = list(self._queue)
        track = tmp.pop(position)
        self._queue = deque(tmp)
        return track

    def is_playing(self) -> bool:
        return self.voice_client.is_playing()

    def is_paused(self) -> bool:
        return self.voice_client.is_paused()

    def start(self):
        if not self._player_task or self._player_task.done():
            self._player_task = asyncio.create_task(self._player_loop(), name=f"player:{self.guild.id}")

    async def stop(self):
        self._closed = True
        if self._player_task and not self._player_task.done():
            self._player_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._player_task
        if self.voice_client.is_connected():
            await self.voice_client.disconnect()

    def pause(self):
        if self.voice_client.is_playing():
            self.voice_client.pause()

    def resume(self):
        if self.voice_client.is_paused():
            self.voice_client.resume()

    def skip(self):
        if self.voice_client.is_playing() or self.voice_client.is_paused():
            self.voice_client.stop()

    # Internal
    async def _player_loop(self):
        try:
            while not self._closed:
                if not self.loop_current or not self.current_track:
                    self.current_track = await self.dequeue()

                if not self.current_track.stream_url or "youtube.com" in self.current_track.stream_url:
                    try:
                        self.current_track = await ytdl.ensure_stream(self.current_track)
                        await asyncio.sleep(random.uniform(1, 2))
                    except Exception as e:
                        logger.error("stream 解決失敗", exc_info=True)
                        continue

                if Path(self.current_track.stream_url).is_file():
                    src = await discord.FFmpegOpusAudio.from_probe(
                        str(self.current_track.stream_url),
                        before_options="-nostdin",
                        options="-vn -acodec copy -b:a 192k",
                    )
                else:
                    ffmpeg_opts = {
                        "before_options": "-nostdin -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
                        "options": "-vn -acodec copy -b:a 192k",
                    }
                    src = await discord.FFmpegOpusAudio.from_probe(self.current_track.stream_url, **ffmpeg_opts)

                done = asyncio.Event()

                def _after(err: Optional[Exception]):
                    try:
                        if err:
                            logger.error(f"Player error: {err}", exc_info=True)

                        try:
                            p = Path(self.current_track.stream_url)
                            if p.is_file():
                                p.unlink(missing_ok=True)
                        except Exception as e:
                            logger.warning(f"Failed to delete cache: {e}", exc_info=True)
                    finally:
                        done.set()

                self.voice_client.play(src, after=_after)
                await done.wait()

                if not self.loop_current:
                    self.current_track = None
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Fatal error in player loop", exc_info=True)
            await asyncio.sleep(5)
            if not self._closed:
                self._player_task = asyncio.create_task(self._player_loop())
