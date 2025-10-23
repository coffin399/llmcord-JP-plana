# PLANA/music/music_cog.py
import asyncio
import gc
import logging
import math
import random
from datetime import datetime, timedelta
from enum import Enum, auto
from pathlib import Path
from typing import Dict, Optional
import time
import subprocess
import discord
import yaml
from discord import app_commands
from discord.ext import commands, tasks

try:
    from .ytdlp_wrapper import Track, extract as extract_audio_data, ensure_stream
    from .error.errors import MusicCogExceptionHandler
except ImportError as e:
    print(f"[CRITICAL] MusicCog: 必須コンポーネントのインポートに失敗しました。エラー: {e}")
    Track = None
    extract_audio_data = None
    ensure_stream = None
    MusicCogExceptionHandler = None

logger = logging.getLogger(__name__)


def format_duration(duration_seconds: int) -> str:
    if duration_seconds is None or duration_seconds < 0:
        return "N/A"
    hours, remainder = divmod(duration_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}" if hours > 0 else f"{int(minutes):02}:{int(seconds):02}"


def parse_time_to_seconds(time_str: str) -> Optional[int]:
    try:
        time_str = time_str.strip()

        if ':' not in time_str:
            return max(0, int(time_str))

        time_str = time_str.rstrip(':')
        parts = [int(p) for p in time_str.split(':')]

        if not parts or any(p < 0 for p in parts):
            return None

        if len(parts) == 2:
            return max(0, parts[0] * 60 + parts[1])
        elif len(parts) == 3:
            return max(0, parts[0] * 3600 + parts[1] * 60 + parts[2])
        else:
            return None
    except (ValueError, AttributeError):
        pass
    return None


class LoopMode(Enum):
    OFF = auto()
    ONE = auto()
    ALL = auto()


class GuildState:
    def __init__(self, bot: commands.Bot, guild_id: int, cog_config: dict):
        self.bot = bot
        self.guild_id = guild_id
        self.voice_client: Optional[discord.VoiceClient] = None
        self.current_track: Optional[Track] = None
        self.queue: asyncio.Queue[Track] = asyncio.Queue()
        self.volume: float = cog_config.get('music', {}).get('default_volume', 20) / 100.0
        self.loop_mode: LoopMode = LoopMode.OFF
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.auto_leave_task: Optional[asyncio.Task] = None
        self.last_text_channel_id: Optional[int] = None
        self.connection_lock = asyncio.Lock()
        self.last_activity = datetime.now()
        self.cleanup_in_progress = False
        self.playback_start_time: Optional[float] = None
        self.seek_position: int = 0
        self.paused_at: Optional[float] = None
        self.is_seeking: bool = False
        self.is_loading: bool = False

    def update_activity(self):
        self.last_activity = datetime.now()

    def update_last_text_channel(self, channel_id: int):
        self.last_text_channel_id = channel_id
        self.update_activity()

    def get_current_position(self) -> int:
        if not self.is_playing:
            return self.seek_position

        if self.is_paused and self.paused_at:
            elapsed = self.paused_at - self.playback_start_time
            return self.seek_position + int(elapsed)

        if self.playback_start_time:
            elapsed = time.time() - self.playback_start_time
            return self.seek_position + int(elapsed)

        return self.seek_position

    def reset_playback_tracking(self):
        self.playback_start_time = None
        self.seek_position = 0
        self.paused_at = None

    async def clear_queue(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break
        self.queue = asyncio.Queue()

    async def cleanup_voice_client(self):
        if self.cleanup_in_progress:
            return
        self.cleanup_in_progress = True
        try:
            if self.voice_client:
                try:
                    if self.voice_client.is_playing():
                        self.voice_client.stop()
                    if self.voice_client.is_connected():
                        await asyncio.wait_for(self.voice_client.disconnect(force=True), timeout=5.0)
                except Exception as e:
                    guild = self.bot.get_guild(self.guild_id)
                    logger.warning(f"Guild {self.guild_id} ({guild.name if guild else ''}): Voice cleanup error: {e}")
                finally:
                    self.voice_client = None
        finally:
            self.cleanup_in_progress = False


class MusicCog(commands.Cog, name="music_cog"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not all((Track, extract_audio_data, ensure_stream, MusicCogExceptionHandler)):
            raise commands.ExtensionFailed(self.qualified_name, "必須コンポーネントのインポート失敗")
        self.config = self._load_bot_config()
        self.music_config = self.config.get('music', {})
        self.guild_states: Dict[int, GuildState] = {}
        self.exception_handler = MusicCogExceptionHandler(self.music_config)
        self.ffmpeg_path = self.music_config.get('ffmpeg_path', 'ffmpeg')
        self.ffmpeg_before_options = self.music_config.get('ffmpeg_before_options',
                                                           "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
        self.ffmpeg_options = self.music_config.get('ffmpeg_options', "-vn")
        self.auto_leave_timeout = self.music_config.get('auto_leave_timeout', 10)
        self.max_queue_size = self.music_config.get('max_queue_size', 9000)
        self.max_guilds = self.music_config.get('max_guilds', 100000000)
        self.inactive_timeout_minutes = self.music_config.get('inactive_timeout_minutes', 30)
        self.global_connection_lock = asyncio.Lock()
        self.cleanup_task = None

    async def cog_load(self):
        if not self.cleanup_task or self.cleanup_task.done():
            self.cleanup_task = self.cleanup_task_loop.start()
        logger.info("MusicCog loaded and cleanup task started")

    def _load_bot_config(self) -> dict:
        if hasattr(self.bot, 'config') and self.bot.config:
            return self.bot.config
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
                self.bot.config = loaded_config
                return loaded_config
        except Exception:
            return {}

    def cog_unload(self):
        logger.info("Unloading MusicCog...")
        if hasattr(self, 'cleanup_task') and self.cleanup_task:
            self.cleanup_task.cancel()
        if hasattr(self, 'cleanup_task_loop') and self.cleanup_task_loop.is_running():
            self.cleanup_task_loop.cancel()
        for guild_id in list(self.guild_states.keys()):
            try:
                state = self.guild_states[guild_id]
                if state.voice_client and state.voice_client.is_connected():
                    asyncio.create_task(state.voice_client.disconnect(force=True))
                if state.auto_leave_task and not state.auto_leave_task.done():
                    state.auto_leave_task.cancel()
            except Exception as e:
                guild = self.bot.get_guild(guild_id)
                logger.warning(f"Guild {guild_id} ({guild.name if guild else ''}) unload cleanup error: {e}")
        self.guild_states.clear()
        logger.info("MusicCog unloaded.")

    @tasks.loop(minutes=5)
    async def cleanup_task_loop(self):
        try:
            current_time = datetime.now()
            inactive_threshold = timedelta(minutes=self.inactive_timeout_minutes)
            guilds_to_cleanup = [
                gid for gid, state in self.guild_states.items()
                if (current_time - state.last_activity > inactive_threshold and
                    not state.is_playing and
                    (not state.voice_client or not state.voice_client.is_connected()))
            ]
            for guild_id in guilds_to_cleanup:
                guild = self.bot.get_guild(guild_id)
                logger.info(f"Cleaning up inactive guild: {guild_id} ({guild.name if guild else ''})")
                await self._cleanup_guild_state(guild_id)
            if guilds_to_cleanup:
                gc.collect()
        except Exception as e:
            logger.error(f"Cleanup task error: {e}", exc_info=True)

    @cleanup_task_loop.before_loop
    async def before_cleanup_task(self):
        await self.bot.wait_until_ready()

    def _get_guild_state(self, guild_id: int) -> Optional[GuildState]:
        if guild_id not in self.guild_states:
            if len(self.guild_states) >= self.max_guilds:
                oldest_guild, oldest_time = None, datetime.now()
                for gid, state in self.guild_states.items():
                    if not state.is_playing and state.last_activity < oldest_time:
                        oldest_guild, oldest_time = gid, state.last_activity
                if oldest_guild:
                    asyncio.create_task(self._cleanup_guild_state(oldest_guild))
                    guild = self.bot.get_guild(oldest_guild)
                    logger.info(
                        f"Removed oldest inactive guild {oldest_guild} ({guild.name if guild else ''}) to make room")
            self.guild_states[guild_id] = GuildState(self.bot, guild_id, self.config)
        self.guild_states[guild_id].update_activity()
        return self.guild_states[guild_id]

    async def _send_response(self, interaction: discord.Interaction, message_key: str, ephemeral: bool = False,
                             **kwargs):
        content = self.exception_handler.get_message(message_key, **kwargs)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
        except discord.errors.InteractionResponded:
            try:
                await interaction.followup.send(content, ephemeral=ephemeral)
            except Exception as e:
                logger.error(f"Guild {interaction.guild.id} ({interaction.guild.name}): Followup error: {e}")
        except Exception as e:
            logger.error(f"Guild {interaction.guild.id} ({interaction.guild.name}): Response error: {e}")

    async def _send_background_message(self, channel_id: int, message_key: str, **kwargs):
        try:
            channel = self.bot.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                await channel.send(self.exception_handler.get_message(message_key, **kwargs))
        except discord.Forbidden:
            logger.debug(f"No permission to send to channel {channel_id}")
        except Exception as e:
            logger.error(f"Background message error: {e}")

    async def _handle_error(self, interaction: discord.Interaction, error: Exception):
        error_message = self.exception_handler.handle_error(error, interaction.guild)
        if interaction.response.is_done():
            await interaction.followup.send(error_message, ephemeral=True)
        else:
            await interaction.response.send_message(error_message, ephemeral=True)

    async def _ensure_voice(self, interaction: discord.Interaction, connect_if_not_in: bool = True) -> Optional[
        discord.VoiceClient]:
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.followup.send("サーバーの上限に達しています。", ephemeral=True)
            return None
        state.update_last_text_channel(interaction.channel.id)
        user_voice = interaction.user.voice
        if not user_voice or not user_voice.channel:
            await self._send_response(interaction, "join_voice_channel_first", ephemeral=True)
            return None

        async with state.connection_lock:
            async with self.global_connection_lock:
                active_connections = sum(
                    1 for s in self.guild_states.values() if s.voice_client and s.voice_client.is_connected())
                if active_connections >= self.max_guilds and not state.voice_client:
                    await self._send_response(interaction, "error_playing", ephemeral=True,
                                              error="現在接続数が上限に達しています。")
                    return None

            vc = state.voice_client
            if vc:
                if not vc.is_connected():
                    await state.cleanup_voice_client()
                    vc = None
                elif vc.channel == user_voice.channel:
                    return vc
                else:
                    await state.cleanup_voice_client()
                    await asyncio.sleep(0.5)
                    vc = None

            for voice_client in list(self.bot.voice_clients):
                if voice_client.guild.id == interaction.guild.id and voice_client != state.voice_client:
                    try:
                        await asyncio.wait_for(voice_client.disconnect(force=True), timeout=3.0)
                    except:
                        pass

            if not vc and connect_if_not_in:
                try:
                    await asyncio.sleep(0.3)
                    state.voice_client = await asyncio.wait_for(
                        user_voice.channel.connect(timeout=30.0, reconnect=True, self_deaf=True),
                        timeout=35.0
                    )
                    logger.info(
                        f"Guild {interaction.guild.id} ({interaction.guild.name}): Connected to {user_voice.channel.name}")
                    return state.voice_client
                except Exception as e:
                    await self._handle_error(interaction, e)
                    state.voice_client = None
                    return None
            elif not vc:
                await self._send_response(interaction, "bot_not_in_voice_channel", ephemeral=True)
                return None
            return vc

    async def _wait_for_playback_to_finish(self, vc: discord.VoiceClient):
        """ボイスクライアントの再生が終わるまで待機するヘルパー関数"""
        while vc.is_playing():
            await asyncio.sleep(0.2)

    async def _play_next_song(self, guild_id: int, seek_seconds: int = 0):
        state = self._get_guild_state(guild_id)
        if not state:
            return

        if state.auto_leave_task and not state.auto_leave_task.done():
            state.auto_leave_task.cancel()

        is_seek_operation = seek_seconds > 0

        # 1. MusicCogが「音楽再生中」と認識している場合は、シーク操作でない限り何もしない
        if state.is_playing and not is_seek_operation:
            return

        # 2. ボイスクライアントが何か（主にTTS）を再生中の場合、それが終わるまで待機する
        if state.voice_client and state.voice_client.is_playing():
            try:
                await asyncio.wait_for(self._wait_for_playback_to_finish(state.voice_client), timeout=30.0)
            except asyncio.TimeoutError:
                logger.warning(f"Guild {guild_id}: TTS等の再生終了待機がタイムアウトしました。再生を強制します。")
                if state.voice_client.is_connected():
                    state.voice_client.stop()

        track_to_play: Optional[Track] = None

        if is_seek_operation and state.current_track:
            track_to_play = state.current_track
        elif state.loop_mode == LoopMode.ONE and state.current_track and not is_seek_operation:
            track_to_play = state.current_track
        elif not state.is_playing and not state.queue.empty() and not is_seek_operation:
            try:
                track_to_play = await state.queue.get()
                state.queue.task_done()
            except:
                pass

        if not track_to_play:
            state.current_track = None
            state.is_playing = False
            state.is_seeking = False
            state.reset_playback_tracking()
            guild = self.bot.get_guild(guild_id)
            if state.voice_client and state.voice_client.is_connected():
                logger.info(f"Guild {guild_id} ({guild.name if guild else ''}): Queue has ended. Disconnecting.")
                if state.last_text_channel_id:
                    await self._send_background_message(state.last_text_channel_id, "queue_ended")
                await state.cleanup_voice_client()
            return

        if not is_seek_operation:
            state.current_track = track_to_play

        state.is_playing = True
        state.is_paused = False
        state.update_activity()

        state.seek_position = seek_seconds
        state.playback_start_time = time.time()
        state.paused_at = None

        try:
            # ===== デバッグログ開始 =====
            logger.info(f"[DEBUG] Guild {guild_id}: Track title: {track_to_play.title}")
            logger.info(f"[DEBUG] Guild {guild_id}: Track URL: {track_to_play.url}")
            logger.info(f"[DEBUG] Guild {guild_id}: Current stream_url: {track_to_play.stream_url}")

            # ★★★ ニコニコ動画のローカルファイルチェック ★★★
            is_local_file = False
            if track_to_play.stream_url:
                try:
                    is_local_file = Path(track_to_play.stream_url).is_file()
                    logger.info(f"[DEBUG] Guild {guild_id}: Is local file check: {is_local_file}")
                except Exception as path_error:
                    logger.warning(f"[DEBUG] Guild {guild_id}: Path check error: {path_error}")

            if not is_local_file:
                # ★★★ ローカルファイル以外は必ず ensure_stream で最新URLを取得 ★★★
                logger.info(f"Guild {guild_id}: Fetching fresh stream URL for '{track_to_play.title}'")

                try:
                    updated_track = await ensure_stream(track_to_play)

                    logger.info(f"[DEBUG] Guild {guild_id}: ensure_stream returned: {updated_track is not None}")
                    if updated_track:
                        logger.info(
                            f"[DEBUG] Guild {guild_id}: New stream_url length: {len(updated_track.stream_url) if updated_track.stream_url else 0}")

                    if not updated_track or not updated_track.stream_url:
                        raise RuntimeError(
                            f"'{track_to_play.title}' の有効なストリームURLを取得できませんでした。"
                        )

                    track_to_play.stream_url = updated_track.stream_url
                    logger.info(
                        f"Guild {guild_id}: Stream URL obtained successfully (length: {len(track_to_play.stream_url)})")

                except Exception as stream_error:
                    logger.error(
                        f"Guild {guild_id}: Stream URL fetch failed: {stream_error}",
                        exc_info=True
                    )
                    raise RuntimeError(
                        f"ストリームURL取得エラー: {stream_error}"
                    ) from stream_error
            else:
                logger.info(f"Guild {guild_id}: Using local file: {track_to_play.stream_url}")

            # FFmpegでの再生開始
            logger.info(
                f"[DEBUG] Guild {guild_id}: Starting FFmpeg with stream_url length: {len(track_to_play.stream_url)}")

            ffmpeg_before_opts = self.ffmpeg_before_options
            if seek_seconds > 0:
                ffmpeg_before_opts = f"-ss {seek_seconds} {ffmpeg_before_opts}"

            source = discord.PCMVolumeTransformer(
                discord.FFmpegPCMAudio(
                    track_to_play.stream_url,
                    executable=self.ffmpeg_path,
                    before_options=ffmpeg_before_opts,
                    options=self.ffmpeg_options,
                    stderr=subprocess.PIPE
                ),
                volume=state.volume
            )
            state.voice_client.play(source, after=lambda e: self._song_finished_callback(e, guild_id))

            if is_seek_operation:
                state.is_seeking = False

            guild = self.bot.get_guild(guild_id)
            seek_info = f" (seeking to {format_duration(seek_seconds)})" if seek_seconds > 0 else ""
            logger.info(
                f"Guild {guild_id} ({guild.name if guild else ''}): Now playing - {track_to_play.title}{seek_info}")

            if state.last_text_channel_id and track_to_play.requester_id and not is_seek_operation:
                try:
                    requester = self.bot.get_user(track_to_play.requester_id) or await self.bot.fetch_user(
                        track_to_play.requester_id)
                except:
                    requester = None
                await self._send_background_message(
                    state.last_text_channel_id,
                    "now_playing",
                    title=track_to_play.title,
                    duration=format_duration(track_to_play.duration),
                    requester_display_name=requester.display_name if requester else "不明"
                )
        except Exception as e:
            guild = self.bot.get_guild(guild_id)
            logger.error(f"Guild {guild_id} ({guild.name if guild else ''}): Playback error: {e}", exc_info=True)
            error_message = self.exception_handler.handle_error(e, guild)
            if state.last_text_channel_id:
                await self._send_background_message(state.last_text_channel_id, "error_message_wrapper",
                                                    error=error_message)
            if state.loop_mode == LoopMode.ALL and track_to_play and not is_seek_operation:
                await state.queue.put(track_to_play)
            state.current_track = None
            state.is_seeking = False
            state.is_playing = False
            state.reset_playback_tracking()
            asyncio.create_task(self._play_next_song(guild_id))

    def _song_finished_callback(self, error: Optional[Exception], guild_id: int):
        state = self._get_guild_state(guild_id)
        if not state:
            return

        if state.is_seeking:
            return

        finished_track = state.current_track
        state.is_playing = False
        state.current_track = None
        state.reset_playback_tracking()

        if error:
            guild = self.bot.get_guild(guild_id)
            error_message = self.exception_handler.handle_error(error, guild)
            if state.last_text_channel_id:
                asyncio.run_coroutine_threadsafe(
                    self._send_background_message(state.last_text_channel_id, "error_message_wrapper",
                                                  error=error_message),
                    self.bot.loop
                )

        if finished_track and state.loop_mode == LoopMode.ALL:
            asyncio.run_coroutine_threadsafe(state.queue.put(finished_track), self.bot.loop)

        asyncio.run_coroutine_threadsafe(self._play_next_song(guild_id), self.bot.loop)

    def _schedule_auto_leave(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        if not state:
            return
        if state.auto_leave_task and not state.auto_leave_task.done():
            state.auto_leave_task.cancel()
        if state.voice_client and state.voice_client.is_connected():
            state.auto_leave_task = asyncio.create_task(self._auto_leave_coroutine(guild_id))

    async def _auto_leave_coroutine(self, guild_id: int):
        await asyncio.sleep(self.auto_leave_timeout)
        state = self._get_guild_state(guild_id)
        if state and state.voice_client and state.voice_client.is_connected():
            if not [m for m in state.voice_client.channel.members if not m.bot]:
                if state.last_text_channel_id:
                    await self._send_background_message(state.last_text_channel_id, "auto_left_empty_channel")
                await state.voice_client.disconnect()

    async def _cleanup_guild_state(self, guild_id: int):
        if guild_id in self.guild_states:
            state = self.guild_states[guild_id]
            await state.cleanup_voice_client()
            if state.auto_leave_task and not state.auto_leave_task.done():
                state.auto_leave_task.cancel()
            await state.clear_queue()
            del self.guild_states[guild_id]
            guild = self.bot.get_guild(guild_id)
            logger.info(f"Guild {guild_id} ({guild.name if guild else ''}): State cleaned up")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"{self.bot.user.name} の MusicCog が正常にロードされました。")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.id == self.bot.user.id and before.channel and not after.channel:
            await self._cleanup_guild_state(member.guild.id)
            return

        guild_id = member.guild.id
        if guild_id not in self.guild_states:
            return

        state = self._get_guild_state(guild_id)
        if not state or not state.voice_client or not state.voice_client.is_connected():
            return

        current_vc_channel = state.voice_client.channel
        if before.channel != current_vc_channel and after.channel != current_vc_channel:
            return

        human_members_in_vc = [m for m in current_vc_channel.members if not m.bot]
        if not human_members_in_vc:
            if not state.auto_leave_task or state.auto_leave_task.done():
                self._schedule_auto_leave(guild_id)
        elif state.auto_leave_task and not state.auto_leave_task.done():
            state.auto_leave_task.cancel()

    @app_commands.command(name="play", description="曲を再生またはキューに追加します。")
    @app_commands.describe(query="再生したい曲のタイトル、またはURL")
    async def play_slash(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()

        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.followup.send("サーバーの上限に達しています。", ephemeral=True)
            return

        vc = await self._ensure_voice(interaction, connect_if_not_in=True)
        if not vc:
            return

        if state.queue.qsize() >= self.max_queue_size:
            await self._send_response(interaction, "max_queue_size_reached",
                                      max_size=self.max_queue_size)
            return

        was_playing = state.is_playing or state.is_loading
        state.is_loading = True

        try:
            await interaction.followup.send(
                self.exception_handler.get_message("searching_for_song", query=query)
            )

            # ★ 重要: ここでは stream_url は取得しない（extract のみ）
            extracted_media = await extract_audio_data(query, shuffle_playlist=False)

            if not extracted_media:
                await interaction.channel.send(
                    self.exception_handler.get_message("search_no_results", query=query)
                )
                return

            tracks = extracted_media if isinstance(extracted_media, list) else [extracted_media]
            added_count, first_track = 0, None

            for track in tracks:
                if state.queue.qsize() < self.max_queue_size:
                    track.requester_id = interaction.user.id
                    # ★ stream_url を明示的に None にして、再生時に取得させる
                    track.stream_url = None
                    await state.queue.put(track)
                    if added_count == 0:
                        first_track = track
                    added_count += 1
                else:
                    await interaction.channel.send(
                        self.exception_handler.get_message("max_queue_size_reached",
                                                           max_size=self.max_queue_size)
                    )
                    break

            if added_count > 1:
                await interaction.channel.send(
                    self.exception_handler.get_message("added_playlist_to_queue",
                                                       count=added_count)
                )
            elif added_count == 1 and first_track:
                await interaction.channel.send(
                    self.exception_handler.get_message("added_to_queue",
                                                       title=first_track.title,
                                                       duration=format_duration(first_track.duration),
                                                       requester_display_name=interaction.user.display_name)
                )

            if not was_playing:
                await self._play_next_song(interaction.guild.id)

        except Exception as e:
            error_message = self.exception_handler.handle_error(e, interaction.guild)
            await interaction.channel.send(
                self.exception_handler.get_message("error_message_wrapper", error=error_message)
            )
        finally:
            state.is_loading = False

    @app_commands.command(name="seek", description="再生位置を指定した時刻に移動します。")
    @app_commands.describe(time="移動先の時刻 (例: 1:30 または 90 秒)")
    async def seek_slash(self, interaction: discord.Interaction, time: str):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        await interaction.response.defer()

        if not await self._ensure_voice(interaction, connect_if_not_in=False):
            return

        if not state.current_track:
            await self._send_response(interaction, "nothing_to_skip", ephemeral=True)
            return

        seek_seconds = parse_time_to_seconds(time)
        if seek_seconds is None:
            await self._send_response(interaction, "invalid_time_format", ephemeral=True)
            return

        if seek_seconds >= state.current_track.duration:
            await self._send_response(interaction, "seek_beyond_duration", ephemeral=True,
                                      duration=format_duration(state.current_track.duration))
            return

        state.is_seeking = True

        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()

        await asyncio.sleep(0.5)

        try:
            updated_track = await ensure_stream(state.current_track)
            if updated_track and updated_track.stream_url:
                state.current_track.stream_url = updated_track.stream_url
        except Exception as e:
            logger.warning(f"Guild {interaction.guild.id}: Stream refresh failed during seek: {e}")

        await self._send_response(interaction, "seeked_to_position", position=format_duration(seek_seconds))
        await self._play_next_song(interaction.guild.id, seek_seconds=seek_seconds)

    @app_commands.command(name="pause", description="再生を一時停止します。")
    async def pause_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state or not await self._ensure_voice(interaction, connect_if_not_in=False):
            return

        if not state.is_playing:
            await self._send_response(interaction, "error_playing", ephemeral=True, error="再生中ではありません。")
            return

        if state.is_paused:
            await self._send_response(interaction, "error_playing", ephemeral=True, error="既に一時停止中です。")
            return

        state.voice_client.pause()
        state.is_paused = True
        state.paused_at = time.time()
        await self._send_response(interaction, "playback_paused")

    @app_commands.command(name="resume", description="一時停止中の再生を再開します。")
    async def resume_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state or not await self._ensure_voice(interaction, connect_if_not_in=False):
            return

        if not state.is_paused:
            await self._send_response(interaction, "error_playing", ephemeral=True, error="一時停止中ではありません。")
            return

        state.voice_client.resume()
        state.is_paused = False
        if state.paused_at and state.playback_start_time:
            pause_duration = time.time() - state.paused_at
            state.playback_start_time += pause_duration
        state.paused_at = None
        await self._send_response(interaction, "playback_resumed")

    @app_commands.command(name="skip", description="再生中の曲をスキップします。")
    async def skip_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        await interaction.response.defer()
        vc = await self._ensure_voice(interaction, connect_if_not_in=False)
        if not vc or not state.current_track:
            await self._send_response(interaction, "nothing_to_skip", ephemeral=True)
            return

        await self._send_response(interaction, "skipped_song", title=state.current_track.title)
        state.voice_client.stop()

    @app_commands.command(name="stop", description="再生を停止し、キューをクリアします。")
    async def stop_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        await interaction.response.defer()
        if not await self._ensure_voice(interaction, connect_if_not_in=False):
            return

        state.loop_mode = LoopMode.OFF
        await state.clear_queue()
        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        state.is_playing = False
        state.is_paused = False
        state.current_track = None
        state.reset_playback_tracking()
        await self._send_response(interaction, "stopped_playback")

    @app_commands.command(name="leave", description="ボットをボイスチャンネルから切断します。")
    async def leave_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        await interaction.response.defer()
        async with state.connection_lock:
            if not state.voice_client or not state.voice_client.is_connected():
                await self._send_response(interaction, "bot_not_in_voice_channel", ephemeral=True)
                return
            await self._send_response(interaction, "leaving_voice_channel")
            await state.cleanup_voice_client()

    @app_commands.command(name="queue", description="現在の再生キューを表示します。")
    async def queue_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        state.update_last_text_channel(interaction.channel.id)
        if state.queue.empty() and not state.current_track:
            await interaction.response.send_message(self.exception_handler.get_message("queue_empty"), ephemeral=True)
            return

        items_per_page = 10
        queue_list = list(state.queue._queue)
        total_items = len(queue_list)
        total_pages = math.ceil(len(queue_list) / items_per_page) if len(queue_list) > 0 else 1

        async def get_page_embed(page_num: int):
            embed = discord.Embed(
                title=self.exception_handler.get_message("queue_title",
                                                         count=total_items + (1 if state.current_track else 0)),
                color=discord.Color.blue()
            )
            lines = []
            if page_num == 1 and state.current_track:
                track = state.current_track
                try:
                    requester = interaction.guild.get_member(track.requester_id) or await self.bot.fetch_user(
                        track.requester_id)
                except:
                    requester = None
                status_icon = '▶️' if state.is_playing else '⏸️'
                current_pos = state.get_current_position()
                lines.append(
                    f"**{status_icon} {track.title}** (`{format_duration(current_pos)}/{format_duration(track.duration)}`) - Req: **{requester.display_name if requester else '不明'}**\n"
                )

            start = (page_num - 1) * items_per_page
            end = (page_num - 1) * items_per_page + items_per_page
            for i, track in enumerate(queue_list[start:end], start=start + 1):
                try:
                    requester = interaction.guild.get_member(track.requester_id) or await self.bot.fetch_user(
                        track.requester_id)
                except:
                    requester = None
                lines.append(
                    f"`{i}.` **{track.title}** (`{format_duration(track.duration)}`) - Req: **{requester.display_name if requester else '不明'}**"
                )

            embed.description = "\n".join(lines) if lines else "このページには曲がありません。"
            if total_pages > 1:
                embed.set_footer(text=f"ページ {page_num}/{total_pages}")
            return embed

        def get_queue_view(current_page: int, total_pages: int, user_id: int):
            view = discord.ui.View(timeout=60.0)

            first_button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                emoji="⏪",
                label="First",
                disabled=(current_page == 1)
            )

            async def first_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message("このボタンは使用できません。", ephemeral=True)
                    return
                nonlocal current_page
                current_page = 1
                await button_interaction.response.edit_message(
                    embed=await get_page_embed(current_page),
                    view=get_queue_view(current_page, total_pages, user_id)
                )

            first_button.callback = first_callback
            view.add_item(first_button)

            prev_button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                emoji="◀️",
                label="Previous",
                disabled=(current_page == 1)
            )

            async def prev_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message("このボタンは使用できません。", ephemeral=True)
                    return
                nonlocal current_page
                current_page = max(1, current_page - 1)
                await button_interaction.response.edit_message(
                    embed=await get_page_embed(current_page),
                    view=get_queue_view(current_page, total_pages, user_id)
                )

            prev_button.callback = prev_callback
            view.add_item(prev_button)

            stop_button = discord.ui.Button(
                style=discord.ButtonStyle.danger,
                emoji="⏹️",
                label="Close"
            )

            async def stop_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message("このボタンは使用できません。", ephemeral=True)
                    return
                view.stop()
                await button_interaction.response.edit_message(view=None)

            stop_button.callback = stop_callback
            view.add_item(stop_button)

            next_button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                emoji="▶️",
                label="Next",
                disabled=(current_page == total_pages)
            )

            async def next_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message("このボタンは使用できません。", ephemeral=True)
                    return
                nonlocal current_page
                current_page = min(total_pages, current_page + 1)
                await button_interaction.response.edit_message(
                    embed=await get_page_embed(current_page),
                    view=get_queue_view(current_page, total_pages, user_id)
                )

            next_button.callback = next_callback
            view.add_item(next_button)

            last_button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                emoji="⏩",
                label="Last",
                disabled=(current_page == total_pages)
            )

            async def last_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message("このボタンは使用できません。", ephemeral=True)
                    return
                nonlocal current_page
                current_page = total_pages
                await button_interaction.response.edit_message(
                    embed=await get_page_embed(current_page),
                    view=get_queue_view(current_page, total_pages, user_id)
                )

            last_button.callback = last_callback
            view.add_item(last_button)

            return view

        current_page = 1
        if total_pages <= 1:
            await interaction.response.send_message(embed=await get_page_embed(current_page))
        else:
            view = get_queue_view(current_page, total_pages, interaction.user.id)
            await interaction.response.send_message(embed=await get_page_embed(current_page), view=view)

    @app_commands.command(name="nowplaying", description="現在再生中の曲の情報を表示します。")
    async def nowplaying_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state or not state.current_track:
            await interaction.response.send_message(self.exception_handler.get_message("now_playing_nothing"),
                                                    ephemeral=True)
            return

        track = state.current_track
        status_icon = "▶️" if state.is_playing else ("⏸️" if state.is_paused else "⏹️")
        try:
            requester = interaction.guild.get_member(track.requester_id) or await self.bot.fetch_user(
                track.requester_id)
        except:
            requester = None

        current_pos = state.get_current_position()
        progress_bar = self._create_progress_bar(current_pos, track.duration)

        embed = discord.Embed(
            title=f"{status_icon} {track.title}",
            url=track.url,
            description=f"{progress_bar}\n`{format_duration(current_pos)}` / `{format_duration(track.duration)}`\n\nリクエスト: **{requester.display_name if requester else '不明'}**\nURL: {track.url}\nループモード: `{state.loop_mode.name.lower()}`",
            color=discord.Color.green() if state.is_playing else (
                discord.Color.orange() if state.is_paused else discord.Color.light_grey())
        )
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)
        await interaction.response.send_message(embed=embed)

    def _create_progress_bar(self, current: int, total: int, length: int = 20) -> str:
        if total <= 0:
            return "─" * length
        progress = min(current / total, 1.0)
        filled = int(length * progress)
        bar = "━" * filled + "○" + "─" * (length - filled - 1)
        return bar

    @app_commands.command(name="shuffle", description="再生キューをシャッフルします。")
    async def shuffle_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state or not await self._ensure_voice(interaction, connect_if_not_in=False):
            return

        if state.queue.qsize() < 2:
            await self._send_response(interaction, "error_playing", ephemeral=True,
                                      error="シャッフルするにはキューに2曲以上必要です。")
            return

        queue_list = list(state.queue._queue)
        random.shuffle(queue_list)
        state.queue = asyncio.Queue()
        for item in queue_list:
            await state.queue.put(item)
        await self._send_response(interaction, "queue_shuffled")

    @app_commands.command(name="clear", description="再生キューを空にします（再生中の曲は停止しません）。")
    async def clear_slash(self, interaction: discord.Interaction):
        state = self._get_guild_state(interaction.guild.id)
        if not state or not await self._ensure_voice(interaction, connect_if_not_in=False):
            return

        await state.clear_queue()
        await self._send_response(interaction, "queue_cleared")

    @app_commands.command(name="remove", description="キューから指定した番号の曲を削除します。")
    @app_commands.describe(index="削除したい曲のキュー番号")
    async def remove_slash(self, interaction: discord.Interaction, index: app_commands.Range[int, 1, None]):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        if state.queue.empty():
            await interaction.response.send_message(self.exception_handler.get_message("queue_empty"), ephemeral=True)
            return

        actual_index = index - 1
        if not (0 <= actual_index < state.queue.qsize()):
            await self._send_response(interaction, "invalid_queue_number", ephemeral=True)
            return

        queue_list = list(state.queue._queue)
        removed_track = queue_list.pop(actual_index)
        state.queue = asyncio.Queue()
        for item in queue_list:
            await state.queue.put(item)
        await self._send_response(interaction, "song_removed", title=removed_track.title)

    @app_commands.command(name="volume", description="音量を変更します (0-200)。")
    @app_commands.describe(level="設定したい音量レベル (0-200)")
    async def volume_slash(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        state.volume = level / 100.0
        state.update_activity()
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await self._send_response(interaction, "volume_set", volume=level)

    @app_commands.command(name="loop", description="ループ再生モードを設定します。")
    @app_commands.describe(mode="ループのモードを選択してください。")
    @app_commands.choices(mode=[
        app_commands.Choice(name="オフ (Loop Off)", value="off"),
        app_commands.Choice(name="現在の曲をループ (Loop One)", value="one"),
        app_commands.Choice(name="キュー全体をループ (Loop All)", value="all")
    ])
    async def loop_slash(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        state = self._get_guild_state(interaction.guild.id)
        if not state:
            await interaction.response.send_message("エラーが発生しました。", ephemeral=True)
            return

        await interaction.response.defer()
        mode_map = {"off": LoopMode.OFF, "one": LoopMode.ONE, "all": LoopMode.ALL}
        state.loop_mode = mode_map.get(mode.value, LoopMode.OFF)
        state.update_activity()
        await self._send_response(interaction, f"loop_{mode.value}")

    @app_commands.command(name="join", description="ボットをあなたのいるボイスチャンネルに接続します。")
    async def join_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if await self._ensure_voice(interaction, connect_if_not_in=True):
            await interaction.followup.send(self.exception_handler.get_message("already_connected"), ephemeral=True)

    @app_commands.command(name="music_help", description="音楽機能のコマンド一覧と使い方を表示します。")
    async def music_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        embed = discord.Embed(
            title="🎵 音楽機能 ヘルプ / Music Feature Help",
            description="音楽再生に関するコマンドの一覧です。\nAll commands start with a slash (`/`).",
            color=discord.Color.from_rgb(79, 194, 255)
        )
        command_info = {
            "▶️ 再生コントロール / Playback Control": [
                {"name": "play", "args": "<song name or URL>", "desc_ja": "曲を再生/キュー追加",
                 "desc_en": "Play/add a song"},
                {"name": "pause", "args": "", "desc_ja": "一時停止", "desc_en": "Pause"},
                {"name": "resume", "args": "", "desc_ja": "再生再開", "desc_en": "Resume"},
                {"name": "stop", "args": "", "desc_ja": "再生停止＆キュークリア", "desc_en": "Stop & clear queue"},
                {"name": "skip", "args": "", "desc_ja": "現在の曲をスキップ", "desc_en": "Skip song"},
                {"name": "seek", "args": "<time>", "desc_ja": "指定時刻に移動", "desc_en": "Seek to time"},
                {"name": "volume", "args": "<level 0-200>", "desc_ja": "音量変更", "desc_en": "Change volume"}
            ],
            "💿 キュー管理 / Queue Management": [
                {"name": "queue", "args": "", "desc_ja": "キュー表示", "desc_en": "Display queue"},
                {"name": "nowplaying", "args": "", "desc_ja": "現在再生中の曲", "desc_en": "Show current song"},
                {"name": "shuffle", "args": "", "desc_ja": "キューをシャッフル", "desc_en": "Shuffle queue"},
                {"name": "clear", "args": "", "desc_ja": "キューをクリア", "desc_en": "Clear queue"},
                {"name": "remove", "args": "<queue number>", "desc_ja": "指定番号の曲を削除", "desc_en": "Remove song"},
                {"name": "loop", "args": "<off|one|all>", "desc_ja": "ループモード設定", "desc_en": "Set loop mode"}
            ],
            "🔊 ボイスチャンネル / Voice Channel": [
                {"name": "join", "args": "", "desc_ja": "VCに接続", "desc_en": "Join VC"},
                {"name": "leave", "args": "", "desc_en": "Leave VC", "desc_ja": "VCから切断"}
            ]
        }
        cog_command_names = {cmd.name for cmd in self.__cog_app_commands__}
        for category, commands_in_category in command_info.items():
            field_value = "".join(
                f"`/{c['name']}{' ' + c['args'] if c['args'] else ''}`\n{c['desc_ja']} / {c['desc_en']}\n"
                for c in commands_in_category if c['name'] in cog_command_names
            )
            if field_value:
                embed.add_field(name=f"**{category}**", value=field_value, inline=False)

        active_guilds = len(self.guild_states)
        embed.set_footer(text=f"<> は引数を表します | Active: {active_guilds}/{self.max_guilds} servers")
        await interaction.followup.send(embed=embed)

    reload_group = app_commands.Group(name="reload",
                                      description="各種機能を再読み込みします。 / Reloads various features.")

    @reload_group.command(name="music_cog",
                          description="音楽Cogを再読み込みして、問題をリセットします。/ Reloads the music cog to fix issues.")
    async def reload_music_cog_subcommand(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        module_name = self.__module__
        logger.info(f"音楽Cog ({module_name}) の再読み込みを試みます。リクエスト者: {interaction.user}")

        try:
            await self.bot.reload_extension(module_name)
            logger.info(f"音楽Cog ({module_name}) の再読み込みに成功しました。")
            await interaction.followup.send(
                "🎵 音楽機能の再読み込みが完了しました。\n🎵 Music feature has been successfully reloaded.",
                ephemeral=True
            )
        except Exception as e:
            logger.error(f"音楽Cog ({module_name}) の再読み込み中にエラーが発生しました: {e}", exc_info=True)
            await interaction.followup.send(
                f"❌ 音楽機能の再読み込み中にエラーが発生しました。\n❌ An error occurred while reloading the music feature.\n```py\n{type(e).__name__}: {e}\n```",
                ephemeral=True
            )

    @reload_music_cog_subcommand.error
    async def reload_music_cog_subcommand_error(self, interaction: discord.Interaction,
                                                error: app_commands.AppCommandError):
        await self.exception_handler.handle_generic_command_error(interaction, error)


async def setup(bot: commands.Bot):
    try:
        await bot.add_cog(MusicCog(bot))
        logger.info("MusicCog successfully loaded")
    except Exception as e:
        logger.error(f"MusicCogのセットアップ中にエラー: {e}", exc_info=True)
        raise