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

DEFAULT_PREFIX = "!!"
logger = logging.getLogger(__name__)


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


class MusicCog(commands.Cog, name="音楽"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not Track or not extract_audio_data or not ensure_stream:
            logger.critical(
                "MusicCog: 必須コンポーネント(Track/extract/ensure_stream)がytdlp_wrapperからインポートできませんでした。音楽機能は利用できません。")
            raise commands.ExtensionFailed(self.qualified_name, "必須コンポーネントのインポート失敗")

        self.config = self._load_bot_config()
        self.music_config = self.config.get('music', {})

        log_level_str = self.music_config.get('log_level', 'INFO').upper()
        numeric_level = getattr(logging, log_level_str, logging.INFO)
        logger.setLevel(numeric_level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s')
            handler.setFormatter(formatter)
            handler.setLevel(numeric_level)
            logger.addHandler(handler)
        else:
            for h in logger.handlers: h.setLevel(numeric_level)

        self.guild_states: Dict[int, GuildState] = {}
        self.ffmpeg_path = self.music_config.get('ffmpeg_path', 'ffmpeg')
        self.ffmpeg_before_options = self.music_config.get('ffmpeg_before_options',
                                                           "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
        self.ffmpeg_options = self.music_config.get('ffmpeg_options', "-vn")
        self.auto_leave_timeout = self.music_config.get('auto_leave_timeout', 60)
        self.max_queue_size = self.music_config.get('max_queue_size', 100)

    def _load_bot_config(self) -> dict:
        if hasattr(self.bot, 'config') and self.bot.config: return self.bot.config
        logger.warning("Botインスタンスに 'config' 属性が見つからないか空です。config.yamlから直接読み込みを試みます。")
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
                if not hasattr(self.bot, 'config') or not self.bot.config: self.bot.config = loaded_config
                return loaded_config
        except FileNotFoundError:
            logger.error("config.yamlが見つかりません。"); return {}
        except yaml.YAMLError as e:
            logger.error(f"config.yaml の解析エラー: {e}"); return {}

    def _get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState(self.bot, guild_id, self.config)
        return self.guild_states[guild_id]

    def _get_message(self, key: str, **kwargs) -> str:
        messages_dict = self.music_config.get('messages', {})
        template = messages_dict.get(key, f"メッセージキー '{key}' が見つかりません。")
        prefix_val = DEFAULT_PREFIX
        if hasattr(self.bot, 'command_prefix'):
            prefix = self.bot.command_prefix
            if isinstance(prefix, (list, tuple)):
                prefix_val = prefix[0]
            elif isinstance(prefix, str):
                prefix_val = prefix
        kwargs.setdefault('prefix', prefix_val)
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error(
                f"メッセージキー '{key}' のフォーマット中にエラー: 不足しているプレースホルダ {e}"); return f"メッセージ '{key}' の表示エラー (開発者向け: {e})"

    async def _send_msg(self, channel: discord.TextChannel, message_key: str, **kwargs):
        if not channel: return None
        content = self._get_message(message_key, **kwargs)
        try:
            return await channel.send(content)
        except discord.Forbidden:
            logger.warning(f"チャンネル {channel.name} ({channel.id}) へのメッセージ送信権限がありません。")
        except discord.HTTPException as e:
            logger.error(f"メッセージ送信中にHTTPエラー: {e.status} - {e.text}")
        return None

    async def _ensure_voice(self, ctx: commands.Context, connect_if_not_in: bool = True) -> Optional[
        discord.VoiceClient]:
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        if not ctx.author.voice or not ctx.author.voice.channel:
            await self._send_msg(ctx.channel, "join_voice_channel_first");
            return None
        if state.voice_client is None or not state.voice_client.is_connected():
            if connect_if_not_in:
                try:
                    state.voice_client = await ctx.author.voice.channel.connect(timeout=15.0,
                                                                                reconnect=True); logger.info(
                        f"ギルド {ctx.guild.name} のVC {state.voice_client.channel.name} に接続。")
                except asyncio.TimeoutError:
                    logger.error(f"ギルド {ctx.guild.name} VC接続タイムアウト。"); await self._send_msg(ctx.channel,
                                                                                                       "error_playing",
                                                                                                       error="VC接続タイムアウト。"); return None
                except Exception as e:
                    logger.error(f"VC接続エラー: {e}", exc_info=True); await self._send_msg(ctx.channel,
                                                                                            "error_playing",
                                                                                            error=f"VC接続失敗 ({type(e).__name__})"); return None
            else:
                await self._send_msg(ctx.channel, "bot_not_in_voice_channel"); return None
        if state.voice_client.channel != ctx.author.voice.channel:
            await self._send_msg(ctx.channel, "must_be_in_same_channel");
            return None
        return state.voice_client

    async def _play_next_song(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel(); state.auto_leave_task = None
        if state.is_paused: return
        if state.voice_client is None or not state.voice_client.is_connected():
            logger.info(f"ギルドID {guild_id}: VC未接続/切断済みのため再生中止。");
            await self._cleanup_guild_state(guild_id);
            return
        if state.voice_client.is_playing() or state.voice_client.is_paused():
            logger.debug(f"ギルドID {guild_id}: _play_next_song が呼ばれましたが、既に再生中または一時停止中です。");
            return

        track_to_play: Optional[Track] = None
        if state.current_track and state.loop_mode == LoopMode.ONE:
            track_to_play = state.current_track
        else:
            if state.queue.empty():
                old_current_track = state.current_track;
                state.current_track = None;
                state.is_playing = False
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    if channel: await self._send_msg(channel, "queue_ended")
                if old_current_track and state.loop_mode == LoopMode.ALL:
                    await state.queue.put(old_current_track)
                    if not state.queue.empty():
                        track_to_play = await state.queue.get(); state.queue.task_done()
                    else:
                        self._schedule_auto_leave(guild_id); return
                else:
                    self._schedule_auto_leave(guild_id); return
            else:
                track_to_play = await state.queue.get(); state.queue.task_done()

        if not track_to_play: logger.warning(f"ギルドID {guild_id}: 再生トラックなし。"); self._schedule_auto_leave(
            guild_id); return
        state.current_track = track_to_play;
        state.is_playing = True;
        state.is_paused = False
        text_channel = self.bot.get_channel(state.last_text_channel_id) if state.last_text_channel_id else None

        try:
            try:
                if track_to_play.stream_url and Path(track_to_play.stream_url).is_file():
                    logger.debug(f"ローカルファイルのためensure_streamスキップ: {track_to_play.stream_url}")
                elif track_to_play.url and not track_to_play.url.startswith("ytsearch"):
                    updated_track = await ensure_stream(track_to_play)
                    if updated_track.stream_url:
                        track_to_play.stream_url = updated_track.stream_url
                    else:
                        raise RuntimeError("ストリームURL取得/更新失敗（空）")
                elif not track_to_play.stream_url:
                    raise RuntimeError("再生可能ストリームURLなし。")
            except RuntimeError as e_stream:
                logger.error(f"ギルドID {guild_id}: ストリームURL処理エラー ({track_to_play.title}): {e_stream}")
                if text_channel: await self._send_msg(text_channel, "error_playing_stream", error=str(e_stream))
                if state.loop_mode == LoopMode.ALL and track_to_play: await state.queue.put(track_to_play)
                state.current_track = None;
                asyncio.create_task(self._play_next_song(guild_id));
                return

            source = discord.FFmpegPCMAudio(track_to_play.stream_url, executable=self.ffmpeg_path,
                                            before_options=self.ffmpeg_before_options, options=self.ffmpeg_options)
            transformed_source = discord.PCMVolumeTransformer(source, volume=state.volume)
            state.voice_client.play(transformed_source, after=lambda e: self._song_finished_callback(e, guild_id))
            logger.info(f"ギルドID {guild_id}: 再生開始 - {track_to_play.title}")
            if text_channel:
                if state.now_playing_message:
                    try:
                        await state.now_playing_message.delete()
                    except:
                        pass
                state.now_playing_message = await self._send_msg(text_channel, "now_playing", title=track_to_play.title,
                                                                 duration=format_duration(track_to_play.duration),
                                                                 requester_id=track_to_play.requester_id)
        except Exception as e:
            logger.error(
                f"ギルドID {guild_id}: 曲 '{track_to_play.title if track_to_play else 'N/A'}' 再生準備中エラー: {e}",
                exc_info=True)
            if text_channel: await self._send_msg(text_channel, "error_playing", error=str(e))
            if state.loop_mode == LoopMode.ALL and track_to_play: await state.queue.put(track_to_play)
            state.current_track = None;
            asyncio.create_task(self._play_next_song(guild_id))

    def _song_finished_callback(self, error: Optional[Exception], guild_id: int):
        state = self._get_guild_state(guild_id)
        finished_track = state.current_track;
        state.is_playing = False
        if error:
            logger.error(f"ギルドID {guild_id}: 再生エラー (after): {error}")
            if state.last_text_channel_id:
                text_channel = self.bot.get_channel(state.last_text_channel_id)
                if text_channel: asyncio.run_coroutine_threadsafe(
                    self._send_msg(text_channel, "error_playing", error=str(error)), self.bot.loop)

        if finished_track and state.loop_mode == LoopMode.ALL:
            async def _add_finished_to_queue():
                if finished_track: await state.queue.put(finished_track)

            asyncio.run_coroutine_threadsafe(_add_finished_to_queue(), self.bot.loop)

        coro = self._play_next_song(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    def _schedule_auto_leave(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel(); state.auto_leave_task = None
        if not state.is_playing and not state.is_paused and state.queue.empty() and not state.current_track:
            if state.voice_client and state.voice_client.is_connected():
                human_members = [m for m in state.voice_client.channel.members if not m.bot]
                if not human_members:
                    logger.info(
                        f"ギルドID {guild_id}: 自動退出タイマー開始 ({self.auto_leave_timeout}秒)。"); state.auto_leave_task = asyncio.create_task(
                        self._auto_leave_coroutine(guild_id))
                else:
                    logger.debug(f"ギルドID {guild_id}: ユーザーがいるため自動退出タイマー開始せず。")
            else:
                logger.debug(f"ギルドID {guild_id}: VC未接続のため自動退出タイマー開始せず。")

    async def _auto_leave_coroutine(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        await asyncio.sleep(self.auto_leave_timeout)
        if state.voice_client and state.voice_client.is_connected() and \
                not state.is_playing and not state.is_paused and state.queue.empty() and not state.current_track:
            human_members = [m for m in state.voice_client.channel.members if not m.bot]
            if not human_members:
                logger.info(f"ギルドID {guild_id}: タイムアウト。VCから自動退出。")
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    if channel: await self._send_msg(channel, "auto_left_empty_channel")
                await state.voice_client.disconnect()
            else:
                logger.info(f"ギルドID {guild_id}: 自動退出キャンセル (ユーザー再参加/再生開始)。")
        else:
            logger.info(f"ギルドID {guild_id}: 自動退出処理中に状態変化、退出中止。")

    async def _cleanup_guild_state(self, guild_id: int):
        logger.debug(f"ギルドID {guild_id}: ギルド状態クリーンアップ。")
        if guild_id in self.guild_states:
            state = self.guild_states[guild_id]
            if state.voice_client and state.voice_client.is_connected(): state.voice_client.stop()
            state.voice_client = None;
            state.current_track = None;
            await state.clear_queue()
            state.is_playing = False;
            state.is_paused = False;
            state.loop_mode = LoopMode.OFF
            if state.now_playing_message:
                try:
                    await state.now_playing_message.delete()
                except:
                    pass
                state.now_playing_message = None
            if state.auto_leave_task and not state.auto_leave_task.done(): state.auto_leave_task.cancel(); state.auto_leave_task = None
            del self.guild_states[guild_id];
            logger.info(f"ギルドID {guild_id}: GuildStateオブジェクト削除。")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"{self.bot.user.name} の MusicCog が正常にロードされました。")
        activity_template = self.music_config.get('bot_activity_playing', "音楽再生中 | {prefix}help")
        prefix = DEFAULT_PREFIX
        if hasattr(self.bot, 'command_prefix'):
            bot_prefix_attr = self.bot.command_prefix
            if isinstance(bot_prefix_attr, (list, tuple)):
                prefix = bot_prefix_attr[0]
            elif isinstance(bot_prefix_attr, str):
                prefix = bot_prefix_attr
        activity_text = activity_template.format(prefix=prefix)
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.listening, name=activity_text))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if member.bot and member.id != self.bot.user.id: return
        guild_id = member.guild.id
        if guild_id not in self.guild_states: return
        state = self._get_guild_state(guild_id)
        if not state.voice_client or not state.voice_client.is_connected(): return
        if member.id == self.bot.user.id and before.channel and not after.channel:
            logger.info(f"ギルドID {guild_id}: ボットがVC {before.channel.name} から切断。状態クリーンアップ。");
            await self._cleanup_guild_state(guild_id);
            return
        if state.voice_client.channel != before.channel and state.voice_client.channel != after.channel:
            if before.channel == state.voice_client.channel:
                human_members_in_old_channel = [m for m in before.channel.members if not m.bot]
                if not human_members_in_old_channel and not state.is_playing and not state.is_paused:
                    if not state.auto_leave_task or state.auto_leave_task.done(): self._schedule_auto_leave(guild_id)
            return
        current_vc_channel = state.voice_client.channel
        human_members_in_vc = [m for m in current_vc_channel.members if not m.bot]
        if not human_members_in_vc:
            if not state.is_playing and not state.is_paused:
                if not state.auto_leave_task or state.auto_leave_task.done(): self._schedule_auto_leave(guild_id)
        else:
            if state.auto_leave_task and not state.auto_leave_task.done():
                logger.info(f"ギルドID {guild_id}: ユーザーVC参加/残存のため自動退出タイマーキャンセル。");
                state.auto_leave_task.cancel();
                state.auto_leave_task = None

    # --- プレフィックスコマンド ---
    @commands.command(name="join", aliases=["connect", "j"], help="ボットを指定したVCに接続。")
    async def join_command(self, ctx: commands.Context, *, channel: Optional[discord.VoiceChannel] = None):
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        target_channel = channel or (ctx.author.voice.channel if ctx.author.voice else None)
        if not target_channel: await self._send_msg(ctx.channel, "join_voice_channel_first"); return
        if state.voice_client and state.voice_client.is_connected():
            if state.voice_client.channel == target_channel: await self._send_msg(ctx.channel,
                                                                                  "already_connected"); return
            try:
                await state.voice_client.move_to(target_channel); logger.info(
                    f"ギルド {ctx.guild.name}: VCを {target_channel.name} に移動。"); await ctx.message.add_reaction("✅")
            except Exception as e:
                logger.error(f"チャンネル移動エラー: {e}", exc_info=True); await self._send_msg(ctx.channel,
                                                                                                "error_playing",
                                                                                                error=f"チャンネル移動失敗 ({type(e).__name__})")
        else:
            try:
                state.voice_client = await target_channel.connect(timeout=15.0, reconnect=True); logger.info(
                    f"ギルド {ctx.guild.name}: VC {target_channel.name} に接続。"); await ctx.message.add_reaction("✅")
            except asyncio.TimeoutError:
                logger.error(f"ギルド {ctx.guild.name}: VC接続タイムアウト。"); await self._send_msg(ctx.channel,
                                                                                                    "error_playing",
                                                                                                    error="VC接続タイムアウト。")
            except Exception as e:
                logger.error(f"チャンネル接続エラー: {e}", exc_info=True); await self._send_msg(ctx.channel,
                                                                                                "error_playing",
                                                                                                error=f"チャンネル接続失敗 ({type(e).__name__})")

    @commands.command(name="leave", aliases=["disconnect", "dc", "bye"], help="ボットをVCから切断。")
    async def leave_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        if not state.voice_client or not state.voice_client.is_connected(): await self._send_msg(ctx.channel,
                                                                                                 "bot_not_in_voice_channel"); return
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} によりVCから切断。");
        await self._send_msg(ctx.channel, "leaving_voice_channel");
        await state.voice_client.disconnect()

    @commands.command(name="play", aliases=["p"], help="曲を再生/キュー追加。\nURLか検索語を指定。")
    async def play_command(self, ctx: commands.Context, *, query: str):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=True);
        if not vc: return
        if state.queue.qsize() >= self.max_queue_size: await self._send_msg(ctx.channel, "max_queue_size_reached",
                                                                            max_size=self.max_queue_size); return
        nico_email = self.music_config.get('niconico', {}).get('email');
        nico_password = self.music_config.get('niconico', {}).get('password');
        max_playlist_items = self.music_config.get('max_playlist_items', 50)
        async with ctx.typing():
            try:
                extracted_media = await extract_audio_data(query, shuffle_playlist=False, nico_email=nico_email,
                                                           nico_password=nico_password,
                                                           max_playlist_items=max_playlist_items)
            except RuntimeError as e:
                logger.error(f"音声データ抽出RuntimeError: {e} (Query: {query})"); await self._send_msg(ctx.channel,
                                                                                                        "error_fetching_song",
                                                                                                        error=str(
                                                                                                            e)); return
            except Exception as e:
                logger.error(f"音声データ抽出エラー: {e} (Query: {query})", exc_info=True); await self._send_msg(
                    ctx.channel, "error_fetching_song", error=type(e).__name__); return
        if not extracted_media: await self._send_msg(ctx.channel, "search_no_results", query=query); return
        tracks_to_add: List[Track] = []
        if isinstance(extracted_media, list):
            tracks_to_add.extend(extracted_media)
        else:
            tracks_to_add.append(extracted_media)
        if not tracks_to_add: await self._send_msg(ctx.channel, "search_no_results", query=query); return
        added_count = 0;
        first_added_track_info = None
        for track in tracks_to_add:
            if state.queue.qsize() < self.max_queue_size:
                track.requester_id = ctx.author.id;
                track.original_query = query;
                await state.queue.put(track)
                if added_count == 0: first_added_track_info = {"title": track.title,
                                                               "duration": format_duration(track.duration),
                                                               "requester_id": track.requester_id}
                added_count += 1
            else:
                await self._send_msg(ctx.channel, "max_queue_size_reached", max_size=self.max_queue_size); break
        if added_count == 0:
            pass
        elif len(tracks_to_add) > 1 and added_count > 0:
            await self._send_msg(ctx.channel, "added_playlist_to_queue", count=added_count)
        elif added_count == 1 and first_added_track_info:
            await self._send_msg(ctx.channel, "added_to_queue", **first_added_track_info)
        if not state.is_playing and not state.is_paused and added_count > 0: asyncio.create_task(
            self._play_next_song(ctx.guild.id))

    @commands.command(name="skip", aliases=["s", "next"], help="再生中の曲をスキップ。")
    async def skip_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if not state.current_track and state.queue.empty(): await self._send_msg(ctx.channel, "nothing_to_skip"); return
        if not state.is_playing and not state.is_paused and not state.current_track: await self._send_msg(ctx.channel,
                                                                                                          "nothing_to_skip"); return
        skipped_title = state.current_track.title if state.current_track else "キューの次の曲"
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} により {skipped_title} をスキップ。");
        await self._send_msg(ctx.channel, "skipped_song", title=skipped_title);
        state.voice_client.stop()

    @commands.command(name="stop", help="再生停止、キュークリア。")
    async def stop_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} により再生停止、キュークリア。");
        await self._send_msg(ctx.channel, "stopped_playback")
        state.loop_mode = LoopMode.OFF;
        await state.clear_queue();
        state.current_track = None
        if state.voice_client and state.voice_client.is_playing(): state.voice_client.stop()
        state.is_playing = False;
        state.is_paused = False
        if state.now_playing_message:
            try:
                await state.now_playing_message.delete()
            except:
                pass
            state.now_playing_message = None
        self._schedule_auto_leave(ctx.guild.id)

    @commands.command(name="pause", help="再生を一時停止。")
    async def pause_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if not state.is_playing: await ctx.send(
            self._get_message("error_playing", error="再生中ではありません。")); return
        if state.is_paused: await ctx.send(self._get_message("error_playing", error="既に一時停止中です。")); return
        state.voice_client.pause();
        state.is_paused = True;
        await self._send_msg(ctx.channel, "playback_paused")

    @commands.command(name="resume", aliases=["unpause"], help="一時停止中の再生を再開。")
    async def resume_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if not state.is_paused: await ctx.send(
            self._get_message("error_playing", error="一時停止中ではありません。")); return
        state.voice_client.resume();
        state.is_paused = False;
        await self._send_msg(ctx.channel, "playback_resumed")

    @commands.command(name="volume", aliases=["vol"], help="音量変更 (0-200)。引数なしで現在値表示。")
    async def volume_command(self, ctx: commands.Context, volume: Optional[int] = None):
        state = self._get_guild_state(ctx.guild.id)
        if volume is None: current_vol_percent = int(state.volume * 100); await ctx.send(
            self._get_message("volume_set", volume=current_vol_percent).replace("設定しました",
                                                                                f"です (現在値)")); return
        if not (0 <= volume <= 200): await self._send_msg(ctx.channel, "invalid_volume"); return
        state.volume = volume / 100.0
        if state.voice_client and state.voice_client.source and isinstance(state.voice_client.source,
                                                                           discord.PCMVolumeTransformer): state.voice_client.source.volume = state.volume
        await self._send_msg(ctx.channel, "volume_set", volume=volume)

    @commands.command(name="queue", aliases=["q", "list"], help="現在の再生キュー表示。")
    async def queue_command(self, ctx: commands.Context, page: int = 1):
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        if state.queue.empty() and not state.current_track: await self._send_msg(ctx.channel, "queue_empty"); return
        items_per_page = 10;
        embed = discord.Embed(
            title=self._get_message("queue_title", count=state.queue.qsize() + (1 if state.current_track else 0)),
            color=discord.Color.blue());
        description_lines = []
        current_queue_list = list(state.queue._queue);
        all_tracks_for_display = []
        if state.current_track: all_tracks_for_display.append(state.current_track)
        all_tracks_for_display.extend(current_queue_list)
        if not all_tracks_for_display: await self._send_msg(ctx.channel, "queue_empty"); return
        total_queued_items = len(current_queue_list);
        total_pages = math.ceil(total_queued_items / items_per_page) if total_queued_items > 0 else 1
        if page < 1 or (page > total_pages and total_pages > 0): await ctx.send(self._get_message("error_playing",
                                                                                                  error=f"無効なページ番号。最大ページ: {total_pages if total_pages > 0 else 1}")); return
        q_start_index = (page - 1) * items_per_page;
        q_end_index = q_start_index + items_per_page
        if page == 1 and state.current_track: track = state.current_track; prefix_char = ":arrow_forward:" if state.is_playing else (
            ":pause_button:" if state.is_paused else ":musical_note:"); description_lines.append(
            f"**{prefix_char} {track.title}** (`{format_duration(track.duration)}`) - リクエスト: <@{track.requester_id}>")
        for i, track in enumerate(current_queue_list[q_start_index:q_end_index],
                                  start=q_start_index + 1): description_lines.append(
            self._get_message("queue_entry", index=i, title=track.title, duration=format_duration(track.duration),
                              requester_id=track.requester_id))
        if not description_lines: await self._send_msg(ctx.channel, "queue_empty"); return
        embed.description = "\n".join(description_lines)
        if total_pages > 1: embed.set_footer(text=f"ページ {page}/{total_pages}")
        await ctx.send(embed=embed)

    @commands.command(name="shuffle", aliases=["sh"], help="再生キューをシャッフル。")
    async def shuffle_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if state.queue.qsize() < 2: await ctx.send(
            self._get_message("error_playing", error="シャッフルするにはキューに2曲以上必要です。")); return
        queue_list = list(state.queue._queue);
        random.shuffle(queue_list);
        new_q = asyncio.Queue()
        for item in queue_list: await new_q.put(item)
        state.queue = new_q;
        await self._send_msg(ctx.channel, "queue_shuffled")

    @commands.command(name="nowplaying", aliases=["np", "current"], help="現在再生中の曲情報表示。")
    async def nowplaying_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        if not state.current_track: await self._send_msg(ctx.channel, "now_playing_nothing"); return
        track = state.current_track;
        status_icon = ":arrow_forward:" if state.is_playing else (
            ":pause_button:" if state.is_paused else ":musical_note:")
        embed = discord.Embed(title=f"{status_icon} {track.title}", description=(
            f"長さ: `{format_duration(track.duration)}`\nリクエスト: <@{track.requester_id}>\nループモード: `{state.loop_mode.name.lower()}`"),
                              color=discord.Color.green() if state.is_playing else (
                                  discord.Color.orange() if state.is_paused else discord.Color.light_grey()))
        if track.thumbnail: embed.set_thumbnail(url=track.thumbnail)
        await ctx.send(embed=embed)

    @commands.command(name="clear", aliases=["clr"], help="再生キュークリア (再生中の曲は影響なし)。")
    async def clear_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        await self._ensure_voice(ctx, connect_if_not_in=False)
        await state.clear_queue();
        await self._send_msg(ctx.channel, "queue_cleared")

    @commands.command(name="loop", aliases=["repeat"], help="ループモード設定 (off, one, all)。引数なしで現在値表示。")
    async def loop_command(self, ctx: commands.Context, mode: Optional[str] = None):
        state = self._get_guild_state(ctx.guild.id)
        if mode is None: await ctx.send(self._get_message("loop_all").replace("キュー全体をループ再生します。",
                                                                              f"現在のループモード: {state.loop_mode.name.lower()}")); return
        mode_lower = mode.lower()
        if mode_lower in ["off", "none"]:
            state.loop_mode = LoopMode.OFF; await self._send_msg(ctx.channel, "loop_off")
        elif mode_lower in ["one", "song", "track"]:
            state.loop_mode = LoopMode.ONE; await self._send_msg(ctx.channel, "loop_one")
        elif mode_lower in ["all", "queue"]:
            state.loop_mode = LoopMode.ALL; await self._send_msg(ctx.channel, "loop_all")
        else:
            await self._send_msg(ctx.channel, "invalid_loop_option")

    @commands.command(name="remove", aliases=["rm"], help="キューから指定番号の曲削除。")
    async def remove_command(self, ctx: commands.Context, index: int):
        state = self._get_guild_state(ctx.guild.id)
        if state.queue.empty(): await ctx.send(self._get_message("queue_empty")); return
        actual_index = index - 1
        if not (0 <= actual_index < state.queue.qsize()): await self._send_msg(ctx.channel, "invalid_queue_number",
                                                                               prefix=self.bot.command_prefix); return
        queue_list = list(state.queue._queue);
        removed_track = queue_list.pop(actual_index);
        new_q = asyncio.Queue()
        for item in queue_list: await new_q.put(item)
        state.queue = new_q;
        await self._send_msg(ctx.channel, "song_removed", title=removed_track.title)

    async def get_music_prefix_from_config(self) -> str:
        prefix = DEFAULT_PREFIX
        if hasattr(self.bot, 'config') and self.bot.config:
            cfg_prefix = self.bot.config.get('prefix')
            if isinstance(cfg_prefix, str) and cfg_prefix:
                prefix = cfg_prefix
        return prefix

    # --- ここから日英併記の音楽ヘルプスラッシュコマンド ---
    @app_commands.command(name="music_help",description="音楽機能に関するヘルプを日英で表示します。/ Displays music help in JP & EN.")
    async def music_help_slash(self, interaction: discord.Interaction):
        """音楽機能のコマンド一覧と各コマンドの詳細な使い方を日本語と英語で併記して表示します。"""
        await interaction.response.defer(ephemeral=False)

        prefix = await self.get_music_prefix_from_config()

        embed = discord.Embed(
            title="🎵 音楽機能 ヘルプ詳細 / Music Feature - Detailed Help",
            description=(
                f"音楽再生に関するコマンドの詳細な説明です。\n"
                f"Here is a detailed explanation of commands related to music playback.\n\n"
                f"コマンドプレフィックス / Command Prefix: `{prefix}`"
            ),
            color=discord.Color.from_rgb(79, 194, 255)
        )
        # Optional: Set a thumbnail for the music help
        # embed.set_thumbnail(url="https://i.imgur.com/your-music-icon.png")

        # コマンドカテゴリと情報を日英で定義
        # (nameはBot内部のコマンド名と一致させる)
        command_info_bilingual = {
            "▶️ 再生コントロール / Playback Control": [
                {"name": "play", "args_ja": "<曲名またはURL>", "args_en": "<song name or URL>",
                 "desc_ja": "指定された曲を再生、またはキューに追加します。YouTube, SoundCloudなどのURLや検索語が使えます。",
                 "desc_en": "Plays the specified song or adds it to the queue. Supports URLs from YouTube, SoundCloud, etc., or search terms."},
                {"name": "pause", "args_ja": "", "args_en": "",
                 "desc_ja": "現在再生中の曲を一時停止します。",
                 "desc_en": "Pauses the currently playing song."},
                {"name": "resume", "args_ja": "", "args_en": "",
                 "desc_ja": "一時停止中の曲の再生を再開します。",
                 "desc_en": "Resumes playback of a paused song."},
                {"name": "stop", "args_ja": "", "args_en": "",
                 "desc_ja": "再生を完全に停止し、キューをクリアします。",
                 "desc_en": "Completely stops playback and clears the queue."},
                {"name": "skip", "args_ja": "", "args_en": "",
                 "desc_ja": "現在再生中の曲をスキップして次の曲を再生します。",
                 "desc_en": "Skips the currently playing song and plays the next one in the queue."},
                {"name": "volume", "args_ja": "[音量(0-200)]", "args_en": "[level (0-200)]",
                 "desc_ja": "再生音量を変更します。引数なしで現在の音量を表示。",
                 "desc_en": "Changes the playback volume. Shows current volume if no argument is given."},
            ],
            "💿 キュー管理 / Queue Management": [
                {"name": "queue", "args_ja": "[ページ番号]", "args_en": "[page number]",
                 "desc_ja": "現在の再生キュー（順番待ちリスト）を表示します。",
                 "desc_en": "Displays the current song queue."},
                {"name": "nowplaying", "args_ja": "", "args_en": "",
                 "desc_ja": "現在再生中の曲の情報を表示します。",
                 "desc_en": "Shows information about the currently playing song."},
                {"name": "shuffle", "args_ja": "", "args_en": "",
                 "desc_ja": "再生キューをシャッフル（ランダムな順番に並び替え）します。",
                 "desc_en": "Shuffles the song queue into a random order."},
                {"name": "clear", "args_ja": "", "args_en": "",
                 "desc_ja": "再生キューをクリアします（再生中の曲は停止しません）。",
                 "desc_en": "Clears the song queue (does not stop the current song)."},
                {"name": "remove", "args_ja": "<キューの番号>", "args_en": "<queue number>",
                 "desc_ja": "再生キューから指定した番号の曲を削除します。",
                 "desc_en": "Removes a song from the queue by its number."},
                {"name": "loop", "args_ja": "[off | one | all]", "args_en": "[off | one | all]",
                 "desc_ja": "ループ再生モードを設定します (off: ループなし, one: 現在の曲, all: キュー全体)。引数なしで現在のモードを表示。",
                 "desc_en": "Sets the loop mode (off: no loop, one: current song, all: entire queue). Shows current mode if no argument."},
            ],
            "🔊 ボイスチャンネル / Voice Channel": [
                {"name": "join", "args_ja": "[チャンネル名またはID]", "args_en": "[channel name or ID]",
                 "desc_ja": "Botをあなたのいるボイスチャンネル、または指定したチャンネルに接続します。",
                 "desc_en": "Connects the bot to your current voice channel or a specified channel."},
                {"name": "leave", "args_ja": "", "args_en": "",
                 "desc_ja": "Botをボイスチャンネルから切断します。",
                 "desc_en": "Disconnects the bot from the voice channel."},
            ]
        }

        cog_commands = self.get_commands()
        cog_commands_dict = {cmd.name: cmd for cmd in cog_commands}
        for cmd in cog_commands:  # エイリアスもコマンドオブジェクトにマッピング
            for alias in cmd.aliases:
                cog_commands_dict[alias] = cmd

        for category_title_bilingual, commands_in_category in command_info_bilingual.items():
            field_value = ""
            for cmd_info in commands_in_category:
                command = cog_commands_dict.get(cmd_info["name"])  # Cog内のコマンド辞書から取得

                if command and not command.hidden:
                    # 使い方 (日本語と英語の引数を結合するが、コマンド名は1つ)
                    usage_ja = f"`{prefix}{command.name}"
                    if cmd_info["args_ja"]: usage_ja += f" {cmd_info['args_ja']}"
                    usage_ja += "`"

                    usage_en = f"`{prefix}{command.name}"
                    if cmd_info["args_en"]: usage_en += f" {cmd_info['args_en']}"
                    usage_en += "`"

                    # 説明 (日本語と英語)
                    description_line_ja = f"{cmd_info['desc_ja']}"
                    description_line_en = f"{cmd_info['desc_en']}"

                    aliases_line_ja = ""
                    aliases_line_en = ""
                    if command.aliases:
                        aliases_str = f"`{', '.join(command.aliases)}`"
                        aliases_line_ja = f"\n   *別名: {aliases_str}*"
                        aliases_line_en = f"\n   *Aliases: {aliases_str}*"

                    # 日本語セクションと英語セクションを構成
                    entry_ja = f"**{usage_ja}**\n   {description_line_ja}{aliases_line_ja}"
                    entry_en = f"**{usage_en}**\n   {description_line_en}{aliases_line_en}"

                    field_value += f"{entry_ja}\n\n{entry_en}\n\n---\n\n"  # セパレータを追加

            if field_value:
                field_value = field_value.rsplit("\n\n---\n\n", 1)[0]  # 最後のセパレータを削除
                # フィールド値が長すぎる場合の対処
                if len(field_value) > 1024:
                    chunks = [field_value[i:i + 1020] for i in range(0, len(field_value), 1020)]
                    for i, chunk in enumerate(chunks):
                        title = f"**{category_title_bilingual} (続き / Cont. {i + 1})**" if i > 0 else f"**{category_title_bilingual}**"
                        embed.add_field(name=title, value=chunk.strip() + ("..." if len(chunk) == 1020 else ""),
                                        inline=False)
                else:
                    embed.add_field(name=f"**{category_title_bilingual}**", value=field_value.strip(), inline=False)

        if not embed.fields:
            desc_ja_no_cmd = "\n利用可能な音楽コマンドが見つかりませんでした。"
            desc_en_no_cmd = "\nNo available music commands found."
            embed.description += f"{desc_ja_no_cmd}\n{desc_en_no_cmd}"

        footer_ja = "<> は必須引数、[] は任意引数を表します。"
        footer_en = "<> denotes a required argument, [] denotes an optional argument."
        embed.set_footer(text=f"{footer_ja}\n{footer_en}")

        await interaction.followup.send(embed=embed, ephemeral=False)
        logger.info(
            f"/music_help_bilingual が実行されました。 (User: {interaction.user.id}, Guild: {interaction.guild_id})")


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config'):
        logger.warning("MusicCog: Botインスタンスに 'config' 属性がありません。設定依存機能に影響の可能性。")
    if not Track or not extract_audio_data or not ensure_stream:
        logger.critical("MusicCog: 必須コンポーネント(ytdlp_wrapper)インポート失敗。ロード中止。")
        raise commands.ExtensionFailed("MusicCog", "ytdlp_wrapper のコンポーネントが見つかりません。")

    try:
        cog_instance = MusicCog(bot)
        await bot.add_cog(cog_instance)
        logger.info("MusicCogが正常にロードされました。")
    except commands.ExtensionFailed as e:
        logger.error(f"MusicCogの初期化中にエラー (ExtensionFailed): {e}")
        raise
    except Exception as e:
        logger.error(f"MusicCogのセットアップ中に予期しないエラー: {e}", exc_info=True)
        raise