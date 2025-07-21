import discord
from discord.ext import commands, tasks
# from discord import app_commands # app_commandsは不要なので削除またはコメントアウト
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
        self.auto_leave_timeout = self.music_config.get('auto_leave_timeout', 10)
        self.max_queue_size = self.music_config.get('max_queue_size', 9000)

    def _load_bot_config(self) -> dict:
        if hasattr(self.bot, 'config') and self.bot.config: return self.bot.config
        logger.warning("Botインスタンスに 'config' 属性が見つからないか空です。config.yamlから直接読み込みを試みます。")
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                loaded_config = yaml.safe_load(f)
                if not hasattr(self.bot, 'config') or not self.bot.config: self.bot.config = loaded_config
                return loaded_config
        except FileNotFoundError:
            logger.error("config.yamlが見つかりません。");
            return {}
        except yaml.YAMLError as e:
            logger.error(f"config.yaml の解析エラー: {e}");
            return {}

    def _get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState(self.bot, guild_id, self.config)
        return self.guild_states[guild_id]

    def _get_message(self, key: str, **kwargs) -> str:
        messages_dict = self.music_config.get('messages', {})
        template = messages_dict.get(key, f"Message key '{key}' not found.")
        if key == "leaving_channel_queue_empty" and template == f"Message key '{key}' not found.":
            template = "キューの再生が終了したため、ボイスチャンネルから退出します。"
        prefix_val = DEFAULT_PREFIX
        # ctxオブジェクトからプレフィックスを取得する方がより動的
        if 'ctx' in kwargs and hasattr(kwargs['ctx'], 'prefix'):
            prefix_val = kwargs['ctx'].prefix
        elif hasattr(self.bot, 'command_prefix'):
            # command_prefixがcallableの場合を考慮
            if callable(self.bot.command_prefix):
                # このコンテキストではメッセージオブジェクトがないため、デフォルト値にフォールバック
                prefix_val = DEFAULT_PREFIX
            elif isinstance(self.bot.command_prefix, (list, tuple)):
                prefix_val = self.bot.command_prefix[0]
            elif isinstance(self.bot.command_prefix, str):
                prefix_val = self.bot.command_prefix

        kwargs.setdefault('prefix', prefix_val)
        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error(
                f"メッセージキー '{key}' のフォーマット中にエラー: 不足しているプレースホルダ {e}");
            return f"メッセージ '{key}' の表示エラー (開発者向け: {e})"

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
            await self._send_msg(ctx.channel, "join_voice_channel_first", ctx=ctx);
            return None
        if state.voice_client is None or not state.voice_client.is_connected():
            if connect_if_not_in:
                try:
                    state.voice_client = await ctx.author.voice.channel.connect(timeout=15.0,
                                                                                reconnect=True)
                    await ctx.guild.me.edit(deafen=True)
                    logger.info(
                        f"ギルド {ctx.guild.name} のVC {state.voice_client.channel.name} に接続し、スピーカーミュートにしました。")
                except asyncio.TimeoutError:
                    logger.error(f"ギルド {ctx.guild.name} VC接続タイムアウト。");
                    await self._send_msg(ctx.channel,
                                         "error_playing",
                                         error="VC接続タイムアウト。", ctx=ctx);
                    return None
                except Exception as e:
                    logger.error(f"VC接続エラー: {e}", exc_info=True);
                    await self._send_msg(ctx.channel,
                                         "error_playing",
                                         error=f"VC接続失敗 ({type(e).__name__})", ctx=ctx);
                    return None
            else:
                await self._send_msg(ctx.channel, "bot_not_in_voice_channel", ctx=ctx);
                return None
        if state.voice_client.channel != ctx.author.voice.channel:
            await self._send_msg(ctx.channel, "must_be_in_same_channel", ctx=ctx);
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
                logger.info(f"ギルドID {guild_id}: キューが空になりました。VCから退出処理を開始します。")
                state.current_track = None;
                state.is_playing = False
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    if channel:
                        await self._send_msg(channel, "queue_ended")
                        await self._send_msg(channel, "leaving_channel_queue_empty")
                if state.voice_client and state.voice_client.is_connected():
                    await state.voice_client.disconnect()
                return

            else:
                track_to_play = await state.queue.get();
                state.queue.task_done()

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

            if text_channel and track_to_play.requester_id:
                guild = self.bot.get_guild(guild_id)
                requester_member = None
                if guild:
                    try:
                        requester_member = await guild.fetch_member(track_to_play.requester_id)
                    except discord.NotFound:
                        logger.warning(
                            f"NowPlaying: リクエスト者 (ID: {track_to_play.requester_id}) がサーバーに見つかりません。")
                    except discord.HTTPException:
                        logger.error(
                            f"NowPlaying: リクエスト者 (ID: {track_to_play.requester_id}) の取得中にHTTPエラー。")

                requester_display_name = "不明なユーザー (Unknown User)"
                if requester_member:
                    requester_display_name = requester_member.display_name
                else:
                    try:
                        user = await self.bot.fetch_user(track_to_play.requester_id)
                        if user:
                            requester_display_name = user.display_name
                    except discord.NotFound:
                        logger.warning(
                            f"NowPlaying: リクエストユーザー (ID: {track_to_play.requester_id}) が見つかりません。")
                    except discord.HTTPException:
                        logger.error(
                            f"NowPlaying: リクエストユーザー (ID: {track_to_play.requester_id}) の取得中にHTTPエラー。")

                if state.now_playing_message:
                    try:
                        await state.now_playing_message.delete()
                    except:
                        pass

                state.now_playing_message = await self._send_msg(
                    text_channel,
                    "now_playing",
                    title=track_to_play.title,
                    duration=format_duration(track_to_play.duration),
                    requester_display_name=requester_display_name
                )
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
        finished_track = state.current_track
        state.is_playing = False
        state.current_track = None

        if error:
            logger.error(f"ギルドID {guild_id}: 再生エラー (after): {error}")
            if state.last_text_channel_id:
                text_channel = self.bot.get_channel(state.last_text_channel_id)
                if text_channel:
                    asyncio.run_coroutine_threadsafe(
                        self._send_msg(text_channel, "error_playing", error=str(error)), self.bot.loop)

        if finished_track and state.loop_mode == LoopMode.ALL:
            asyncio.run_coroutine_threadsafe(state.queue.put(finished_track), self.bot.loop)

        coro = self._play_next_song(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    def _schedule_auto_leave(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        if state.auto_leave_task and not state.auto_leave_task.done():
            state.auto_leave_task.cancel()

        if state.voice_client and state.voice_client.is_connected():
            logger.info(
                f"ギルドID {guild_id}: 自動退出タイマー開始 ({self.auto_leave_timeout}秒)。")
            state.auto_leave_task = asyncio.create_task(
                self._auto_leave_coroutine(guild_id))
        else:
            logger.debug(f"ギルドID {guild_id}: VC未接続のため自動退出タイマー開始せず。")

    async def _auto_leave_coroutine(self, guild_id: int):
        state = self._get_guild_state(guild_id)
        await asyncio.sleep(self.auto_leave_timeout)

        if state.voice_client and state.voice_client.is_connected():
            human_members = [m for m in state.voice_client.channel.members if not m.bot]
            if not human_members:
                logger.info(f"ギルドID {guild_id}: タイムアウト。VCから自動退出。")
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    if channel: await self._send_msg(channel, "auto_left_empty_channel")
                await state.voice_client.disconnect()
            else:
                logger.info(f"ギルドID {guild_id}: 自動退出キャンセル (ユーザー再参加)。")
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

        current_vc_channel = state.voice_client.channel
        if before.channel != current_vc_channel and after.channel != current_vc_channel:
            return

        human_members_in_vc = [m for m in current_vc_channel.members if not m.bot]

        if not human_members_in_vc:
            if not state.auto_leave_task or state.auto_leave_task.done():
                self._schedule_auto_leave(guild_id)
        else:
            if state.auto_leave_task and not state.auto_leave_task.done():
                logger.info(f"ギルドID {guild_id}: ユーザーVC参加/残存のため自動退出タイマーキャンセル。");
                state.auto_leave_task.cancel();
                state.auto_leave_task = None

    @commands.command(name="join", aliases=["connect", "j"], help="ボットを指定したVCに接続。")
    async def join_command(self, ctx: commands.Context, *, channel: Optional[discord.VoiceChannel] = None):
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        target_channel = channel or (ctx.author.voice.channel if ctx.author.voice else None)
        if not target_channel: await self._send_msg(ctx.channel, "join_voice_channel_first", ctx=ctx); return
        if state.voice_client and state.voice_client.is_connected():
            if state.voice_client.channel == target_channel: await self._send_msg(ctx.channel,
                                                                                  "already_connected", ctx=ctx); return
            try:
                await state.voice_client.move_to(target_channel)
                await ctx.guild.me.edit(deafen=True)
                logger.info(
                    f"ギルド {ctx.guild.name}: VCを {target_channel.name} に移動し、スピーカーミュートにしました。");
                await ctx.message.add_reaction("✅")
            except Exception as e:
                logger.error(f"チャンネル移動エラー: {e}", exc_info=True);
                await self._send_msg(ctx.channel,
                                     "error_playing",
                                     error=f"チャンネル移動失敗 ({type(e).__name__})", ctx=ctx)
        else:
            try:
                state.voice_client = await target_channel.connect(timeout=15.0, reconnect=True)
                await ctx.guild.me.edit(deafen=True)
                logger.info(
                    f"ギルド {ctx.guild.name}: VC {target_channel.name} に接続し、スピーカーミュートにしました。");
                await ctx.message.add_reaction("✅")
            except asyncio.TimeoutError:
                logger.error(f"ギルド {ctx.guild.name}: VC接続タイムアウト。");
                await self._send_msg(ctx.channel,
                                     "error_playing",
                                     error="VC接続タイムアウト。", ctx=ctx)
            except Exception as e:
                logger.error(f"チャンネル接続エラー: {e}", exc_info=True);
                await self._send_msg(ctx.channel,
                                     "error_playing",
                                     error=f"チャンネル接続失敗 ({type(e).__name__})", ctx=ctx)

    @commands.command(name="leave", aliases=["disconnect", "dc", "bye"], help="ボットをVCから切断。")
    async def leave_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        state.update_last_text_channel(ctx.channel.id)
        if not state.voice_client or not state.voice_client.is_connected(): await self._send_msg(ctx.channel,
                                                                                                 "bot_not_in_voice_channel",
                                                                                                 ctx=ctx); return
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} によりVCから切断。");
        await self._send_msg(ctx.channel, "leaving_voice_channel", ctx=ctx);
        await state.voice_client.disconnect()

    @commands.command(name="play", aliases=["p"], help="曲を再生/キュー追加。\nURLか検索語を指定。")
    async def play_command(self, ctx: commands.Context, *, query: str):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=True)
        if not vc: return

        if state.queue.qsize() >= self.max_queue_size:
            await self._send_msg(ctx.channel, "max_queue_size_reached", max_size=self.max_queue_size, ctx=ctx)
            return

        nico_email = self.music_config.get('niconico', {}).get('email')
        nico_password = self.music_config.get('niconico', {}).get('password')
        max_playlist_items = self.music_config.get('max_playlist_items', 50)

        async with ctx.typing():
            try:
                extracted_media = await extract_audio_data(query, shuffle_playlist=False, nico_email=nico_email,
                                                           nico_password=nico_password,
                                                           max_playlist_items=max_playlist_items)
            except RuntimeError as e:
                logger.error(f"音声データ抽出RuntimeError: {e} (Query: {query})")
                await self._send_msg(ctx.channel, "error_fetching_song", error=str(e), ctx=ctx)
                return
            except Exception as e:
                logger.error(f"音声データ抽出エラー: {e} (Query: {query})", exc_info=True)
                await self._send_msg(ctx.channel, "error_fetching_song", error=type(e).__name__, ctx=ctx)
                return

        if not extracted_media:
            await self._send_msg(ctx.channel, "search_no_results", query=query, ctx=ctx)
            return

        tracks_to_add: List[Track] = []
        if isinstance(extracted_media, list):
            tracks_to_add.extend(extracted_media)
        else:
            tracks_to_add.append(extracted_media)

        if not tracks_to_add:
            await self._send_msg(ctx.channel, "search_no_results", query=query, ctx=ctx)
            return

        added_count = 0
        first_added_track_info = None
        requester_display_name = ctx.author.display_name

        for track_idx, track in enumerate(tracks_to_add):
            if state.queue.qsize() < self.max_queue_size:
                track.requester_id = ctx.author.id
                track.original_query = query
                await state.queue.put(track)
                if added_count == 0:
                    first_added_track_info = {
                        "title": track.title,
                        "duration": format_duration(track.duration),
                        "requester_display_name": requester_display_name
                    }
                added_count += 1
            else:
                await self._send_msg(ctx.channel, "max_queue_size_reached", max_size=self.max_queue_size, ctx=ctx)
                break

        if added_count == 0:
            pass
        elif len(tracks_to_add) > 1 and added_count > 0:
            await self._send_msg(ctx.channel, "added_playlist_to_queue", count=added_count, ctx=ctx)
        elif added_count == 1 and first_added_track_info:
            await self._send_msg(ctx.channel, "added_to_queue", **first_added_track_info, ctx=ctx)

        if not state.is_playing and not state.is_paused and added_count > 0:
            asyncio.create_task(self._play_next_song(ctx.guild.id))

    @commands.command(name="skip", aliases=["s", "next"], help="再生中の曲をスキップ。")
    async def skip_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if not state.current_track and state.queue.empty(): await self._send_msg(ctx.channel, "nothing_to_skip",
                                                                                 ctx=ctx); return
        if not state.is_playing and not state.is_paused and not state.current_track: await self._send_msg(ctx.channel,
                                                                                                          "nothing_to_skip",
                                                                                                          ctx=ctx); return
        skipped_title = state.current_track.title if state.current_track else "現在の曲"
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} により {skipped_title} をスキップ。");
        await self._send_msg(ctx.channel, "skipped_song", title=skipped_title, ctx=ctx);
        state.voice_client.stop()

    @commands.command(name="stop", help="再生停止、キュークリア。")
    async def stop_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} により再生停止、キュークリア。");
        await self._send_msg(ctx.channel, "stopped_playback", ctx=ctx)
        state.loop_mode = LoopMode.OFF;
        await state.clear_queue();

        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()
        else:
            # 停止コマンドは次の曲の再生をトリガーすべきではない
            state.current_track = None

        state.is_playing = False;
        state.is_paused = False
        if state.now_playing_message:
            try:
                await state.now_playing_message.delete()
            except:
                pass
            state.now_playing_message = None

    @commands.command(name="pause", help="再生を一時停止。")
    async def pause_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if not state.is_playing: await ctx.send(
            self._get_message("error_playing", error="再生中ではありません。", ctx=ctx)); return
        if state.is_paused: await ctx.send(
            self._get_message("error_playing", error="既に一時停止中です。", ctx=ctx)); return
        state.voice_client.pause();
        state.is_paused = True;
        await self._send_msg(ctx.channel, "playback_paused", ctx=ctx)

    @commands.command(name="resume", aliases=["unpause"], help="一時停止中の再生を再開。")
    async def resume_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if not state.is_paused: await ctx.send(
            self._get_message("error_playing", error="一時停止中ではありません。", ctx=ctx)); return
        state.voice_client.resume();
        state.is_paused = False;
        await self._send_msg(ctx.channel, "playback_resumed", ctx=ctx)

    @commands.command(name="volume", aliases=["vol"], help="音量変更 (0-200)。引数なしで現在値表示。")
    async def volume_command(self, ctx: commands.Context, volume: Optional[int] = None):
        state = self._get_guild_state(ctx.guild.id)
        if volume is None:
            current_vol_percent = int(state.volume * 100)
            await ctx.send(self._get_message("volume_set", volume=current_vol_percent, ctx=ctx).replace("設定しました",
                                                                                                        f"です (現在値)"))
            return
        if not (0 <= volume <= 200): await self._send_msg(ctx.channel, "invalid_volume", ctx=ctx); return
        state.volume = volume / 100.0
        if state.voice_client and state.voice_client.source and isinstance(state.voice_client.source,
                                                                           discord.PCMVolumeTransformer): state.voice_client.source.volume = state.volume
        await self._send_msg(ctx.channel, "volume_set", volume=volume, ctx=ctx)

    @commands.command(name="queue", aliases=["q", "list"], help="現在の再生キュー表示。矢印でページ操作可能。")
    async def queue_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        state.update_last_text_channel(ctx.channel.id)

        if state.queue.empty() and not state.current_track:
            await self._send_msg(ctx.channel, "queue_empty", ctx=ctx)
            return

        items_per_page = 10
        queue_list = list(state.queue._queue)
        total_items = len(queue_list)
        total_pages = math.ceil(total_items / items_per_page) if total_items > 0 else 1

        async def get_page_embed(page_num: int):
            embed = discord.Embed(
                title=self._get_message("queue_title", count=total_items + (1 if state.current_track else 0), ctx=ctx),
                color=discord.Color.blue()
            )
            lines = []

            if page_num == 1 and state.current_track:
                track = state.current_track
                prefix = "▶️" if state.is_playing else "⏸️"
                requester_name = "不明"
                if track.requester_id:
                    member = ctx.guild.get_member(track.requester_id)
                    if member:
                        requester_name = member.display_name
                    else:
                        try:
                            user = await self.bot.fetch_user(track.requester_id)
                            requester_name = user.display_name
                        except:
                            pass
                lines.append(
                    f"**{prefix} {track.title}** (`{format_duration(track.duration)}`) - Req: **{requester_name}**\n")

            start_index = (page_num - 1) * items_per_page
            end_index = start_index + items_per_page

            for i, track in enumerate(queue_list[start_index:end_index], start=start_index + 1):
                requester_name = "不明"
                if track.requester_id:
                    member = ctx.guild.get_member(track.requester_id)
                    if member:
                        requester_name = member.display_name
                    else:
                        try:
                            user = await self.bot.fetch_user(track.requester_id)
                            requester_name = user.display_name
                        except:
                            pass
                lines.append(
                    f"`{i}.` **{track.title}** (`{format_duration(track.duration)}`) - Req: **{requester_name}**")

            if not lines and not (page_num == 1 and state.current_track):
                embed.description = "このページには曲がありません。"
            elif not lines and (page_num == 1 and state.current_track):
                pass  # 再生中の曲だけ表示
            else:
                embed.description = "\n".join(lines)

            if total_pages > 1:
                embed.set_footer(text=f"ページ {page_num}/{total_pages}")

            return embed

        current_page = 1
        message = await ctx.send(embed=await get_page_embed(current_page))

        if total_pages <= 1 and not (total_items > items_per_page):
            return

        controls = ["⏪", "◀️", "▶️", "⏩", "⏹️"]
        for control in controls:
            await message.add_reaction(control)

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in controls and reaction.message.id == message.id

        while True:
            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)

                new_page = current_page
                if str(reaction.emoji) == "⏪":
                    new_page = 1
                elif str(reaction.emoji) == "◀️":
                    new_page = max(1, current_page - 1)
                elif str(reaction.emoji) == "▶️":
                    new_page = min(total_pages, current_page + 1)
                elif str(reaction.emoji) == "⏩":
                    new_page = total_pages
                elif str(reaction.emoji) == "⏹️":
                    await message.clear_reactions();
                    return

                if new_page != current_page:
                    current_page = new_page
                    await message.edit(embed=await get_page_embed(current_page))

                try:
                    await message.remove_reaction(reaction, user)
                except discord.Forbidden:
                    pass

            except asyncio.TimeoutError:
                try:
                    await message.clear_reactions()
                except (discord.Forbidden, discord.HTTPException):
                    pass
                break

    @commands.command(name="shuffle", aliases=["sh"], help="再生キューをシャッフル。")
    async def shuffle_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        vc = await self._ensure_voice(ctx, connect_if_not_in=False);
        if not vc: return
        if state.queue.qsize() < 2: await ctx.send(
            self._get_message("error_playing", error="シャッフルするにはキューに2曲以上必要です。", ctx=ctx)); return
        queue_list = list(state.queue._queue);
        random.shuffle(queue_list);
        new_q = asyncio.Queue()
        for item in queue_list: await new_q.put(item)
        state.queue = new_q;
        await self._send_msg(ctx.channel, "queue_shuffled", ctx=ctx)

    @commands.command(name="nowplaying", aliases=["np", "current"], help="現在再生中の曲情報表示。")
    async def nowplaying_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        state.update_last_text_channel(ctx.channel.id)
        if not state.current_track:
            await self._send_msg(ctx.channel, "now_playing_nothing", ctx=ctx)
            return

        track = state.current_track
        status_icon = "▶️" if state.is_playing else ("⏸️" if state.is_paused else "⏹️")

        requester_display_name = "不明なユーザー"
        if track.requester_id:
            try:
                member = ctx.guild.get_member(track.requester_id) or await self.bot.fetch_user(track.requester_id)
                requester_display_name = member.display_name
            except (discord.NotFound, discord.HTTPException):
                pass

        embed = discord.Embed(
            title=f"{status_icon} {track.title}",
            url=track.url,
            description=(
                f"長さ: `{format_duration(track.duration)}`\n"
                f"リクエスト: **{requester_display_name}**\n"
                f"URL: {track.url}\n"
                f"ループモード: `{state.loop_mode.name.lower()}`"
            ),
            color=discord.Color.green() if state.is_playing else (
                discord.Color.orange() if state.is_paused else discord.Color.light_grey())
        )
        if track.thumbnail: embed.set_thumbnail(url=track.thumbnail)
        await ctx.send(embed=embed)

    @commands.command(name="clear", aliases=["clr"], help="再生キュークリア (再生中の曲は影響なし)。")
    async def clear_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id);
        await self._ensure_voice(ctx, connect_if_not_in=False)
        await state.clear_queue();
        await self._send_msg(ctx.channel, "queue_cleared", ctx=ctx)

    @commands.command(name="loop", aliases=["repeat"], help="ループモード設定 (off, one, all)。引数なしで現在値表示。")
    async def loop_command(self, ctx: commands.Context, mode: Optional[str] = None):
        state = self._get_guild_state(ctx.guild.id)
        if mode is None:
            await ctx.send(self._get_message("loop_all", ctx=ctx).replace("キュー全体をループ再生します。",
                                                                          f"現在のループモード: {state.loop_mode.name.lower()}"))
            return
        mode_lower = mode.lower()
        if mode_lower in ["off", "none", "false", "0"]:
            state.loop_mode = LoopMode.OFF;
            await self._send_msg(ctx.channel, "loop_off", ctx=ctx)
        elif mode_lower in ["one", "song", "track", "1"]:
            state.loop_mode = LoopMode.ONE;
            await self._send_msg(ctx.channel, "loop_one", ctx=ctx)
        elif mode_lower in ["all", "queue", "true", "2"]:
            state.loop_mode = LoopMode.ALL;
            await self._send_msg(ctx.channel, "loop_all", ctx=ctx)
        else:
            await self._send_msg(ctx.channel, "invalid_loop_option", ctx=ctx)

    @commands.command(name="remove", aliases=["rm"], help="キューから指定番号の曲削除。")
    async def remove_command(self, ctx: commands.Context, index: int):
        state = self._get_guild_state(ctx.guild.id)
        if state.queue.empty(): await ctx.send(self._get_message("queue_empty", ctx=ctx)); return
        actual_index = index - 1
        if not (0 <= actual_index < state.queue.qsize()):
            await self._send_msg(ctx.channel, "invalid_queue_number", prefix=ctx.prefix, ctx=ctx)
            return
        queue_list = list(state.queue._queue);
        removed_track = queue_list.pop(actual_index);
        new_q = asyncio.Queue()
        for item in queue_list: await new_q.put(item)
        state.queue = new_q;
        await self._send_msg(ctx.channel, "song_removed", title=removed_track.title, ctx=ctx)

    # --- ▼ ここからが修正箇所です ▼ ---

    @commands.command(name="music_help", aliases=["mhelp"], help="音楽機能に関するヘルプを日英で表示します。")
    async def music_help_command(self, ctx: commands.Context):
        prefix = ctx.prefix
        embed = discord.Embed(
            title="🎵 音楽機能 ヘルプ詳細 / Music Feature - Detailed Help",
            description=(
                f"音楽再生に関するコマンドの詳細な説明です。\n"
                f"Here is a detailed explanation of commands related to music playback.\n\n"
                f"コマンドプレフィックス / Command Prefix: `{prefix}`"
            ),
            color=discord.Color.from_rgb(79, 194, 255)
        )
        command_info_bilingual = {
            "▶️ 再生コントロール / Playback Control": [
                {"name": "play", "args_ja": "<曲名またはURL>", "args_en": "<song name or URL>",
                 "desc_ja": "指定された曲を再生、またはキューに追加します。YouTube, SoundCloudなどのURLや検索語が使えます。",
                 "desc_en": "Plays the specified song or adds it to the queue. Supports URLs from YouTube, SoundCloud, etc., or search terms."},
                {"name": "pause", "args_ja": "", "args_en": "", "desc_ja": "現在再生中の曲を一時停止します。",
                 "desc_en": "Pauses the currently playing song."},
                {"name": "resume", "args_ja": "", "args_en": "", "desc_ja": "一時停止中の曲の再生を再開します。",
                 "desc_en": "Resumes playback of a paused song."},
                {"name": "stop", "args_ja": "", "args_en": "",
                 "desc_ja": "再生を完全に停止し、キューをクリアしてVCから退出します。",
                 "desc_en": "Completely stops playback, clears the queue, and leaves the VC."},
                {"name": "skip", "args_ja": "", "args_en": "",
                 "desc_ja": "現在再生中の曲をスキップして次の曲を再生します。",
                 "desc_en": "Skips the currently playing song and plays the next one in the queue."},
                {"name": "volume", "args_ja": "[音量(0-200)]", "args_en": "[level (0-200)]",
                 "desc_ja": "再生音量を変更します。引数なしで現在の音量を表示。",
                 "desc_en": "Changes the playback volume. Shows current volume if no argument is given."},
            ],
            "💿 キュー管理 / Queue Management": [
                {"name": "queue", "args_ja": "", "args_en": "",
                 "desc_ja": "現在の再生キュー（順番待ちリスト）を表示します。矢印リアクションでページをめくれます。",
                 "desc_en": "Displays the current song queue. You can turn pages with arrow reactions."},
                {"name": "nowplaying", "args_ja": "", "args_en": "", "desc_ja": "現在再生中の曲の情報を表示します。",
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
                {"name": "leave", "args_ja": "", "args_en": "", "desc_ja": "Botをボイスチャンネルから切断します。",
                 "desc_en": "Disconnects the bot from the voice channel."},
            ]
        }
        cog_commands = self.get_commands()
        cog_commands_dict = {cmd.name: cmd for cmd in cog_commands}
        for cmd_obj in cog_commands:
            for alias in cmd_obj.aliases: cog_commands_dict[alias] = cmd_obj
        for category_title_bilingual, commands_in_category in command_info_bilingual.items():
            field_value = ""
            for cmd_info in commands_in_category:
                command = cog_commands_dict.get(cmd_info["name"])
                if command and not command.hidden:
                    usage_ja = f"`{prefix}{command.name}"
                    if cmd_info["args_ja"]: usage_ja += f" {cmd_info['args_ja']}"
                    usage_ja += "`";
                    usage_en = f"`{prefix}{command.name}"
                    if cmd_info["args_en"]: usage_en += f" {cmd_info['args_en']}"
                    usage_en += "`";
                    desc_ja = cmd_info.get("desc_ja", "説明なし。");
                    desc_en = cmd_info.get("desc_en", "No description.")
                    aliases_line_ja = f"\n   *別名: `{', '.join(command.aliases)}`*" if command.aliases else ""
                    aliases_line_en = f"\n   *Aliases: `{', '.join(command.aliases)}`*" if command.aliases else ""
                    entry_ja = f"**{usage_ja}**\n   {desc_ja}{aliases_line_ja}";
                    entry_en = f"**{usage_en}**\n   {desc_en}{aliases_line_en}"
                    field_value += f"{entry_ja}\n\n{entry_en}\n\n---\n\n"
            if field_value:
                field_value = field_value.rsplit("\n\n---\n\n", 1)[0]
                if len(field_value) > 1024:
                    chunks = [field_value[i:i + 1020] for i in range(0, len(field_value), 1020)]
                    for i, chunk in enumerate(chunks):
                        title = f"**{category_title_bilingual} (続き / Cont. {i + 1})**" if i > 0 else f"**{category_title_bilingual}**"
                        embed.add_field(name=title, value=chunk.strip() + ("..." if len(chunk) == 1020 else ""),
                                        inline=False)
                else:
                    embed.add_field(name=f"**{category_title_bilingual}**", value=field_value.strip(), inline=False)
        if not embed.fields: embed.description += "\n利用可能な音楽コマンドが見つかりませんでした。\nNo available music commands found."
        embed.set_footer(
            text="<> は必須引数、[] は任意引数を表します。\n<> denotes a required argument, [] denotes an optional argument.")

        await ctx.send(embed=embed)
        logger.info(f"{prefix}music_help が実行されました。 (User: {ctx.author.id}, Guild: {ctx.guild.id})")


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