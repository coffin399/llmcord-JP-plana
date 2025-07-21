import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import logging
import yaml
import random
from typing import Dict, Optional, List, Union, Any
from enum import Enum, auto
import math
from pathlib import Path

try:
    from services.ytdlp_wrapper import Track, extract as extract_audio_data, ensure_stream
except ImportError as e:
    print(
        f"[CRITICAL] MusicCog: ytdlp_wrapperのインポートに失敗しました。servicesディレクトリに配置され、__init__.pyが存在するか確認してください。エラー: {e}")
    Track = None
    extract_audio_data = None
    ensure_stream = None

logger = logging.getLogger(__name__)


# --- Helper & Enumクラス ---
def format_duration(duration_seconds: int) -> str:
    if duration_seconds < 0: return "N/A"
    hours = duration_seconds // 3600
    minutes = (duration_seconds % 3600) // 60
    seconds = duration_seconds % 60
    return f"{hours:02}:{minutes:02}:{seconds:02}" if hours > 0 else f"{minutes:02}:{seconds:02}"


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
        self.volume: float = cog_config.get('music', {}).get('default_volume', 50) / 100.0
        self.loop_mode: LoopMode = LoopMode.OFF
        self.is_playing: bool = False
        self.is_paused: bool = False
        self.auto_leave_task: Optional[asyncio.Task] = None
        self.last_text_channel_id: Optional[int] = None
        self.now_playing_message: Optional[discord.Message] = None

    def update_last_text_channel(self, channel_id: int):
        self.last_text_channel_id = channel_id

    async def clear_queue(self):
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break


# --- MusicCog本体 ---

class MusicCog(commands.Cog, name="音楽"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not Track or not extract_audio_data or not ensure_stream:
            logger.critical("MusicCog: 必須コンポーネント(ytdlp_wrapper)インポート失敗。ロード中止。")
            raise commands.ExtensionFailed(self.qualified_name, "必須コンポーネントのインポート失敗")

        self.config = self._load_bot_config()
        self.music_config = self.config.get('music', {})
        self.guild_states: Dict[int, GuildState] = {}
        self.ffmpeg_path = self.music_config.get('ffmpeg_path', 'ffmpeg')
        self.ffmpeg_before_options = self.music_config.get('ffmpeg_before_options',
                                                           "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
        self.ffmpeg_options = self.music_config.get('ffmpeg_options', "-vn")
        self.auto_leave_timeout = self.music_config.get('auto_leave_timeout', 10)
        self.max_queue_size = self.music_config.get('max_queue_size', 9000)

    def _load_bot_config(self) -> dict:
        if hasattr(self.bot, 'config') and self.bot.config: return self.bot.config
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
                if not hasattr(self.bot, 'config') or not self.bot.config: self.bot.config = loaded_config
                return loaded_config
        except Exception:
            return {}

    def _get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState(self.bot, guild_id, self.config)
        return self.guild_states[guild_id]

    def _get_message(self, key: str, **kwargs) -> str:
        template = self.music_config.get('messages', {}).get(key, f"Message key '{key}' not found.")
        kwargs.setdefault('prefix', '/')
        try:
            return template.format(**kwargs)
        except KeyError as e:
            return f"メッセージ '{key}' の表示エラー: {e}"

    async def _send_response(self, interaction: discord.Interaction, message_key: str, ephemeral: bool = False,
                             **kwargs):
        content = self._get_message(message_key, **kwargs)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=ephemeral)
            else:
                await interaction.response.send_message(content, ephemeral=ephemeral)
        except discord.errors.InteractionResponded:
            try:
                await interaction.followup.send(content, ephemeral=ephemeral)
            except Exception as e:
                logger.error(f"Followup送信エラー: {e}", exc_info=True)
        except Exception as e:
            logger.error(f"応答送信エラー: {e}", exc_info=True)

    async def _send_background_message(self, channel_id: int, message_key: str, **kwargs):
        channel = self.bot.get_channel(channel_id)
        if channel:
            content = self._get_message(message_key, **kwargs)
            try:
                await channel.send(content)
            except discord.Forbidden:
                logger.warning(f"Ch:{channel_id} へのBGメッセージ送信権限なし。")

    async def _ensure_voice(self, interaction: discord.Interaction, connect_if_not_in: bool = True) -> Optional[
        discord.VoiceClient]:
        # --- ▼▼▼ デバッグログ① ▼▼▼ ---
        logger.info("--- [DEBUG] _ensure_voice called ---")
        logger.info(f"[DEBUG] User: {interaction.user.name} ({interaction.user.id})")
        logger.info(f"[DEBUG] interaction.user.voice: {interaction.user.voice}")
        if interaction.user.voice:
            logger.info(f"[DEBUG] interaction.user.voice.channel: {interaction.user.voice.channel}")
        logger.info("------------------------------------")
        # --- ▲▲▲ デバッグログ① ▲▲▲ ---

        state = self._get_guild_state(interaction.guild.id)
        state.update_last_text_channel(interaction.channel.id)

        user_voice = interaction.user.voice
        if not user_voice or not user_voice.channel:
            await self._send_response(interaction, "join_voice_channel_first", ephemeral=True)
            return None

        vc = state.voice_client
        if not vc or not vc.is_connected():
            if connect_if_not_in:
                try:
                    state.voice_client = await user_voice.channel.connect(timeout=15.0, reconnect=True)
                    await interaction.guild.me.edit(deafen=True)
                    return state.voice_client
                except Exception as e:
                    await self._send_response(interaction, "error_playing", ephemeral=True,
                                              error=f"VC接続失敗: {type(e).__name__}")
                    return None
            else:
                await self._send_response(interaction, "bot_not_in_voice_channel", ephemeral=True)
                return None

        if vc.channel != user_voice.channel:
            await self._send_response(interaction, "must_be_in_same_channel", ephemeral=True)
            return None

        return vc

    async def _play_next_song(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel()
        if state.is_paused or (state.voice_client and state.voice_client.is_playing()): return

        track_to_play: Optional[Track] = None
        if state.loop_mode == LoopMode.ONE and state.current_track:
            track_to_play = state.current_track
        elif not state.queue.empty():
            track_to_play = await state.queue.get()
            state.queue.task_done()

        if not track_to_play:
            state.current_track = None
            state.is_playing = False
            if state.last_text_channel_id:
                await self._send_background_message(state.last_text_channel_id, "queue_ended")
            self._schedule_auto_leave(guild_id)
            return

        state.current_track = track_to_play
        state.is_playing = True
        state.is_paused = False

        try:
            if not track_to_play.stream_url or not Path(track_to_play.stream_url).is_file():
                updated_track = await ensure_stream(track_to_play)
                if updated_track and updated_track.stream_url:
                    track_to_play.stream_url = updated_track.stream_url
                else:
                    raise RuntimeError("ストリームURLの取得/更新に失敗しました。")

            source = discord.FFmpegPCMAudio(track_to_play.stream_url, executable=self.ffmpeg_path,
                                            before_options=self.ffmpeg_before_options, options=self.ffmpeg_options)
            transformed_source = discord.PCMVolumeTransformer(source, volume=state.volume)
            state.voice_client.play(transformed_source, after=lambda e: self._song_finished_callback(e, guild_id))
            logger.info(f"ギルドID {guild_id}: 再生開始 - {track_to_play.title}")

            if state.last_text_channel_id:
                requester = self.bot.get_user(track_to_play.requester_id) or await self.bot.fetch_user(
                    track_to_play.requester_id)
                await self._send_background_message(
                    state.last_text_channel_id, "now_playing", title=track_to_play.title,
                    duration=format_duration(track_to_play.duration),
                    requester_display_name=requester.display_name if requester else "不明"
                )
        except Exception as e:
            logger.error(f"再生準備中エラー: {e}", exc_info=True)
            if state.last_text_channel_id: await self._send_background_message(state.last_text_channel_id,
                                                                               "error_playing", error=str(e))
            if state.loop_mode == LoopMode.ALL and track_to_play: await state.queue.put(track_to_play)
            state.current_track = None
            asyncio.create_task(self._play_next_song(guild_id))

    def _song_finished_callback(self, error: Optional[Exception], guild_id: int):
        state = self._get_guild_state(guild_id)
        finished_track = state.current_track
        state.is_playing = False
        state.current_track = None
        if error:
            logger.error(f"再生エラー (after): {error}")
            if state.last_text_channel_id:
                asyncio.run_coroutine_threadsafe(
                    self._send_background_message(state.last_text_channel_id, "error_playing", error=str(error)),
                    self.bot.loop)
        if finished_track and state.loop_mode == LoopMode.ALL:
            asyncio.run_coroutine_threadsafe(state.queue.put(finished_track), self.bot.loop)
        asyncio.run_coroutine_threadsafe(self._play_next_song(guild_id), self.bot.loop)

    def _schedule_auto_leave(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel()
        if state.voice_client and state.voice_client.is_connected():
            state.auto_leave_task = asyncio.create_task(self._auto_leave_coroutine(guild_id))

    async def _auto_leave_coroutine(self, guild_id: int):
        await asyncio.sleep(self.auto_leave_timeout)
        state = self._get_guild_state(guild_id)
        if state.voice_client and state.voice_client.is_connected():
            human_members = [m for m in state.voice_client.channel.members if not m.bot]
            if not human_members:
                if state.last_text_channel_id:
                    await self._send_background_message(state.last_text_channel_id, "auto_left_empty_channel")
                await state.voice_client.disconnect()

    async def _cleanup_guild_state(self, guild_id: int):
        if guild_id in self.guild_states:
            state = self.guild_states[guild_id]
            if state.voice_client and state.voice_client.is_connected(): state.voice_client.stop()
            if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel()
            del self.guild_states[guild_id]
            logger.info(f"ギルドID {guild_id}: GuildStateクリーンアップ完了。")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"{self.bot.user.name} の MusicCog が正常にロードされました。")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        logger.info(
            f"[DEBUG] on_voice_state_update triggered by '{member.name}'. Channel before: '{before.channel}', after: '{after.channel}'")

        if member.bot and member.id != self.bot.user.id: return
        guild_id = member.guild.id

        if member.id == self.bot.user.id and before.channel and not after.channel:
            await self._cleanup_guild_state(guild_id)
            return

        if guild_id not in self.guild_states: return
        state = self._get_guild_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected(): return

        current_vc_channel = state.voice_client.channel
        if before.channel != current_vc_channel and after.channel != current_vc_channel: return

        human_members_in_vc = [m for m in current_vc_channel.members if not m.bot]
        if not human_members_in_vc:
            if not state.auto_leave_task or state.auto_leave_task.done(): self._schedule_auto_leave(guild_id)
        else:
            if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel()

    @app_commands.command(name="join", description="ボットをあなたのいるボイスチャンネルに接続します。")
    async def join_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        vc = await self._ensure_voice(interaction, connect_if_not_in=True)
        if vc:
            await interaction.followup.send(self._get_message("already_connected"), ephemeral=True)

    @app_commands.command(name="leave", description="ボットをボイスチャンネルから切断します。")
    async def leave_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = self._get_guild_state(interaction.guild_id)
        if not state.voice_client or not state.voice_client.is_connected():
            await self._send_response(interaction, "bot_not_in_voice_channel", ephemeral=True)
            return

        await self._send_response(interaction, "leaving_voice_channel")
        await state.voice_client.disconnect()

    @app_commands.command(name="play", description="曲を再生またはキューに追加します。")
    @app_commands.describe(query="再生したい曲のタイトル、またはURL")
    async def play_slash(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()
        state = self._get_guild_state(interaction.guild.id)
        vc = await self._ensure_voice(interaction, connect_if_not_in=True)
        if not vc: return
        if state.queue.qsize() >= self.max_queue_size:
            await self._send_response(interaction, "max_queue_size_reached", ephemeral=True,
                                      max_size=self.max_queue_size)
            return

        try:
            extracted_media = await extract_audio_data(query, shuffle_playlist=False)
        except Exception as e:
            await self._send_response(interaction, "error_fetching_song", ephemeral=True, error=str(e));
            return

        if not extracted_media:
            await self._send_response(interaction, "search_no_results", ephemeral=True, query=query);
            return

        tracks = extracted_media if isinstance(extracted_media, list) else [extracted_media]
        added_count = 0
        first_track = None
        for track in tracks:
            if state.queue.qsize() < self.max_queue_size:
                track.requester_id = interaction.user.id
                await state.queue.put(track)
                if added_count == 0: first_track = track
                added_count += 1
            else:
                await self._send_response(interaction, "max_queue_size_reached", ephemeral=True,
                                          max_size=self.max_queue_size);
                break

        if added_count > 1:
            await self._send_response(interaction, "added_playlist_to_queue", count=added_count)
        elif added_count == 1 and first_track:
            await self._send_response(interaction, "added_to_queue", title=first_track.title,
                                      duration=format_duration(first_track.duration),
                                      requester_display_name=interaction.user.display_name)

        if not state.is_playing:
            await self._play_next_song(interaction.guild.id)

    @app_commands.command(name="skip", description="再生中の曲をスキップします。")
    async def skip_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = self._get_guild_state(interaction.guild.id)
        vc = await self._ensure_voice(interaction, connect_if_not_in=False)
        if not vc or not state.current_track:
            await self._send_response(interaction, "nothing_to_skip", ephemeral=True)
            return

        await self._send_response(interaction, "skipped_song", title=state.current_track.title)
        state.voice_client.stop()

    @app_commands.command(name="stop", description="再生を停止し、キューをクリアします。")
    async def stop_slash(self, interaction: discord.Interaction):
        await interaction.response.defer()
        state = self._get_guild_state(interaction.guild.id)
        vc = await self._ensure_voice(interaction, connect_if_not_in=False)
        if not vc: return

        state.loop_mode = LoopMode.OFF
        await state.clear_queue()
        if state.voice_client and state.voice_client.is_playing(): state.voice_client.stop()
        state.is_playing = False
        state.is_paused = False
        state.current_track = None
        await self._send_response(interaction, "stopped_playback")

    @app_commands.command(name="volume", description="音量を変更します (0-200)。")
    @app_commands.describe(level="設定したい音量レベル (0から200の間)")
    async def volume_slash(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]):
        state = self._get_guild_state(interaction.guild.id)
        state.volume = level / 100.0
        if state.voice_client and state.voice_client.source:
            state.voice_client.source.volume = state.volume
        await self._send_response(interaction, "volume_set", volume=level)

    @app_commands.command(name="loop", description="ループ再生モードを設定します。")
    @app_commands.describe(mode="ループのモードを選択してください。")
    @app_commands.choices(mode=[
        app_commands.Choice(name="オフ (Loop Off)", value="off"),
        app_commands.Choice(name="現在の曲をループ (Loop One)", value="one"),
        app_commands.Choice(name="キュー全体をループ (Loop All)", value="all"),
    ])
    async def loop_slash(self, interaction: discord.Interaction, mode: app_commands.Choice[str]):
        state = self._get_guild_state(interaction.guild.id)
        if mode.value == "off":
            state.loop_mode = LoopMode.OFF
            await self._send_response(interaction, "loop_off")
        elif mode.value == "one":
            state.loop_mode = LoopMode.ONE
            await self._send_response(interaction, "loop_one")
        elif mode.value == "all":
            state.loop_mode = LoopMode.ALL
            await self._send_response(interaction, "loop_all")


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config'):
        logger.warning("MusicCog: Botインスタンスに 'config' 属性がありません。")
    if not Track:
        raise commands.ExtensionFailed("MusicCog", "ytdlp_wrapper のコンポーネントが見つかりません。")
    try:
        await bot.add_cog(MusicCog(bot))
    except Exception as e:
        logger.error(f"MusicCogのセットアップ中にエラー: {e}", exc_info=True)
        raise