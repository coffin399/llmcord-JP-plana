import discord
from discord.ext import commands, tasks
import asyncio
import logging
import yaml  # config読み込み用
import random
from typing import Dict, Optional, List, Union, Any
from enum import Enum, auto
import math  # ceil用
from pathlib import Path  # Track.stream_urlがローカルファイルパスか確認するために使用

# ytdlp_wrapperからTrackとextract, ensure_streamをインポート
try:
    # Botのルートディレクトリから見て services.ytdlp_wrapper をインポート
    from services.ytdlp_wrapper import Track, extract as extract_audio_data, ensure_stream
except ImportError as e:
    # ロガーがこの時点ではまだ完全にセットアップされていない可能性があるため、標準printも使う
    print(
        f"[CRITICAL] MusicCog: ytdlp_wrapperのインポートに失敗しました。servicesディレクトリに配置され、__init__.pyが存在するか確認してください。エラー: {e}")
    # MusicCogクラスの初期化やsetup関数でこれらのコンポーネントの存在をチェックし、
    # 見つからなければCogのロードを失敗させるようにする
    Track = None
    extract_audio_data = None
    ensure_stream = None

# --- グローバル定数 ---
DEFAULT_PREFIX = "!!"  # configから読めなかった場合のフォールバック

# --- ロガー設定 ---
# logger名は__name__ (music_cog) になる
logger = logging.getLogger(__name__)


# --- ヘルパー関数 ---
def format_duration(duration_seconds: int) -> str:
    """秒数を HH:MM:SS または MM:SS 形式の文字列に変換する"""
    if duration_seconds < 0:
        return "N/A"
    hours = duration_seconds // 3600
    minutes = (duration_seconds % 3600) // 60
    seconds = duration_seconds % 60
    if hours > 0:
        return f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{minutes:02}:{seconds:02}"


# --- 状態管理用クラス ---
class LoopMode(Enum):
    OFF = auto()  # ループなし
    ONE = auto()  # 現在の曲をループ
    ALL = auto()  # キュー全体をループ


class GuildState:
    """各ギルドの音楽再生状態を管理するクラス"""

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
        new_q = asyncio.Queue()  # 新しいQueueインスタンスを作成
        # 古いQueueからアイテムを取り出し続ける (Queueが空になるまで)
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()  # get_nowaitとセットでtask_doneを呼ぶ
            except asyncio.QueueEmpty:
                break
        # self.queue = new_q # 新しい空のキューインスタンスに置き換える (スレッドセーフではない可能性があるため、通常は推奨されないが、ここではシンプルにする)
        # より安全なのは、既存のキューの中身を空にすることだが、上記の方法で実質的に空になる。


# --- Music Cog 本体 ---
class MusicCog(commands.Cog, name="音楽"):
    """音楽再生機能を提供するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # ytdlp_wrapperコンポーネントの存在確認
        if not Track or not extract_audio_data or not ensure_stream:
            logger.critical(
                "MusicCog: 必須コンポーネント(Track/extract/ensure_stream)がytdlp_wrapperからインポートできませんでした。音楽機能は利用できません。")
            # Cogのロードを失敗させるために例外を送出する
            raise commands.ExtensionFailed(self.qualified_name, "必須コンポーネントのインポート失敗")

        self.config = self._load_bot_config()
        self.music_config = self.config.get('music', {})

        log_level_str = self.music_config.get('log_level', 'INFO').upper()
        numeric_level = getattr(logging, log_level_str, logging.INFO)
        # loggerのレベルを設定 (ハンドラレベルも影響する)
        logger.setLevel(numeric_level)
        if not logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            # ハンドラのレベルも設定 (loggerレベルより優先されることはない)
            handler.setLevel(numeric_level)
            logger.addHandler(handler)
        else:  # 既にハンドラがある場合は、そのレベルも確認・設定
            for h in logger.handlers:
                h.setLevel(numeric_level)

        self.guild_states: Dict[int, GuildState] = {}

        self.ffmpeg_path = self.music_config.get('ffmpeg_path', 'ffmpeg')
        self.ffmpeg_before_options = self.music_config.get('ffmpeg_before_options',
                                                           "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5")
        self.ffmpeg_options = self.music_config.get('ffmpeg_options', "-vn")

        self.auto_leave_timeout = self.music_config.get('auto_leave_timeout', 60)
        self.max_queue_size = self.music_config.get('max_queue_size', 100)

    def _load_bot_config(self) -> dict:
        if hasattr(self.bot, 'config') and self.bot.config:
            return self.bot.config
        else:
            logger.warning(
                "Botインスタンスに 'config' 属性が見つからないか空です。config.yamlから直接読み込みを試みます。")
            try:
                with open('config.yaml', 'r', encoding='utf-8') as f:
                    loaded_config = yaml.safe_load(f)
                    if not hasattr(self.bot, 'config') or not self.bot.config:
                        self.bot.config = loaded_config
                    return loaded_config
            except FileNotFoundError:
                logger.error("config.yamlが見つかりません。")
                return {}
            except yaml.YAMLError as e:
                logger.error(f"config.yaml の解析エラー: {e}")
                return {}

    def _get_guild_state(self, guild_id: int) -> GuildState:
        if guild_id not in self.guild_states:
            self.guild_states[guild_id] = GuildState(self.bot, guild_id, self.config)
        return self.guild_states[guild_id]

    def _get_message(self, key: str, **kwargs) -> str:
        messages_dict = self.music_config.get('messages', {})
        template = messages_dict.get(key, f"メッセージキー '{key}' が見つかりません。")

        prefix_val = DEFAULT_PREFIX  # デフォルト
        if hasattr(self.bot, 'command_prefix'):
            prefix = self.bot.command_prefix
            if isinstance(prefix, (list, tuple)):
                prefix_val = prefix[0]
            elif isinstance(prefix, str):
                prefix_val = prefix
            # callable の場合は ctx が必要なので、ここでは DEFAULT_PREFIX を使う

        kwargs.setdefault('prefix', prefix_val)

        try:
            return template.format(**kwargs)
        except KeyError as e:
            logger.error(f"メッセージキー '{key}' のフォーマット中にエラー: 不足しているプレースホルダ {e}")
            return f"メッセージ '{key}' の表示エラー (開発者向け: {e})"

    async def _send_msg(self, channel: discord.TextChannel, message_key: str, **kwargs):
        if not channel: return None  # チャンネルがない場合はNoneを返す
        content = self._get_message(message_key, **kwargs)
        try:
            return await channel.send(content)
        except discord.Forbidden:
            logger.warning(f"チャンネル {channel.name} ({channel.id}) へのメッセージ送信権限がありません。")
        except discord.HTTPException as e:
            logger.error(f"メッセージ送信中にHTTPエラー: {e.status} - {e.text}")
        return None  # 送信失敗時もNoneを返す

    async def _ensure_voice(self, ctx: commands.Context, connect_if_not_in: bool = True) -> Optional[
        discord.VoiceClient]:
        state = self._get_guild_state(ctx.guild.id)
        state.update_last_text_channel(ctx.channel.id)

        if not ctx.author.voice or not ctx.author.voice.channel:
            await self._send_msg(ctx.channel, "join_voice_channel_first")
            return None

        if state.voice_client is None or not state.voice_client.is_connected():
            if connect_if_not_in:
                try:
                    state.voice_client = await ctx.author.voice.channel.connect(timeout=15.0, reconnect=True)
                    logger.info(
                        f"ギルド {ctx.guild.name} のボイスチャンネル {state.voice_client.channel.name} に接続しました。")
                except asyncio.TimeoutError:
                    logger.error(f"ギルド {ctx.guild.name} のボイスチャンネルへの接続がタイムアウトしました。")
                    await self._send_msg(ctx.channel, "error_playing",
                                         error="ボイスチャンネルへの接続がタイムアウトしました。")
                    return None
                except Exception as e:
                    logger.error(f"ボイスチャンネル接続エラー: {e}", exc_info=True)
                    await self._send_msg(ctx.channel, "error_playing",
                                         error=f"ボイスチャンネル接続に失敗 ({type(e).__name__})")
                    return None
            else:
                await self._send_msg(ctx.channel, "bot_not_in_voice_channel")
                return None

        if state.voice_client.channel != ctx.author.voice.channel:
            await self._send_msg(ctx.channel, "must_be_in_same_channel")
            return None

        return state.voice_client

    async def _play_next_song(self, guild_id: int):
        state = self._get_guild_state(guild_id)

        if state.auto_leave_task and not state.auto_leave_task.done():
            state.auto_leave_task.cancel()
            state.auto_leave_task = None  # キャンセルしたらタスク参照をクリア

        if state.is_paused:
            return

        if state.voice_client is None or not state.voice_client.is_connected():
            logger.info(f"ギルドID {guild_id}: VC未接続または切断済みのため再生処理を中止。")
            await self._cleanup_guild_state(guild_id)
            return

        if state.voice_client.is_playing() or state.voice_client.is_paused():  # 二重再生防止
            # 稀に stop() が呼ばれた直後に is_playing() がまだ True のことがあるため、
            # after コールバックに任せるのが基本。ここではログ出力程度に留める。
            logger.debug(f"ギルドID {guild_id}: _play_next_song が呼ばれましたが、既に再生中または一時停止中です。")
            return

        track_to_play: Optional[Track] = None

        if state.current_track and state.loop_mode == LoopMode.ONE:
            track_to_play = state.current_track
        else:
            if state.queue.empty():
                old_current_track = state.current_track  # ループALLのために保持
                state.current_track = None
                state.is_playing = False
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    if channel: await self._send_msg(channel, "queue_ended")

                # ループALLの場合、最後に再生した曲をキューに戻す (キューが空になった直後)
                if old_current_track and state.loop_mode == LoopMode.ALL:
                    await state.queue.put(old_current_track)
                    # 再度キューから取得を試みる
                    if not state.queue.empty():
                        track_to_play = await state.queue.get()
                        state.queue.task_done()
                    else:  # やはり空なら自動退出
                        self._schedule_auto_leave(guild_id)
                        return
                else:  # ループALLでないか、old_current_trackがない場合
                    self._schedule_auto_leave(guild_id)
                    return
            else:  # キューに曲がある場合
                track_to_play = await state.queue.get()
                state.queue.task_done()

        if not track_to_play:  # 何らかの理由で再生するトラックがない
            logger.warning(
                f"ギルドID {guild_id}: 再生するトラックが見つかりませんでした。キューの状態を確認してください。")
            self._schedule_auto_leave(guild_id)  # 念のため
            return

        state.current_track = track_to_play
        state.is_playing = True
        state.is_paused = False

        text_channel = self.bot.get_channel(state.last_text_channel_id) if state.last_text_channel_id else None

        try:
            try:
                if track_to_play.stream_url and Path(track_to_play.stream_url).is_file():
                    logger.debug(f"ローカルファイルのためensure_streamをスキップ: {track_to_play.stream_url}")
                elif track_to_play.url and not track_to_play.url.startswith("ytsearch"):
                    logger.debug(f"ensure_stream実行前: {track_to_play.stream_url} (URL: {track_to_play.url})")
                    # ytdlp_wrapperの設定を渡す必要がある場合 (グローバル設定と異なる場合)
                    # ytdl_opts_for_ensure = {...}
                    updated_track = await ensure_stream(track_to_play)  # ytdl_opts=ytdl_opts_for_ensure のように渡せる
                    if updated_track.stream_url:
                        track_to_play.stream_url = updated_track.stream_url
                        logger.debug(f"ensure_stream実行後: {track_to_play.stream_url}")
                    else:
                        raise RuntimeError("ストリームURLの取得/更新に失敗（空の結果）")
                elif not track_to_play.stream_url:
                    raise RuntimeError("再生可能なストリームURLがありません。")

            except RuntimeError as e_stream:
                logger.error(f"ギルドID {guild_id}: ストリームURL処理エラー ({track_to_play.title}): {e_stream}")
                if text_channel:
                    await self._send_msg(text_channel, "error_playing_stream", error=str(e_stream))
                if state.loop_mode == LoopMode.ALL and track_to_play:
                    await state.queue.put(track_to_play)
                state.current_track = None
                asyncio.create_task(self._play_next_song(guild_id))
                return

            # FFmpegPCMAudioのexecutable引数はffmpegのパス
            source = discord.FFmpegPCMAudio(
                track_to_play.stream_url,
                executable=self.ffmpeg_path,
                before_options=self.ffmpeg_before_options,
                options=self.ffmpeg_options,
            )
            transformed_source = discord.PCMVolumeTransformer(source, volume=state.volume)

            state.voice_client.play(
                transformed_source,
                after=lambda e: self._song_finished_callback(e, guild_id)
            )
            logger.info(f"ギルドID {guild_id}: 再生開始 - {track_to_play.title}")

            if text_channel:
                if state.now_playing_message:
                    try:
                        await state.now_playing_message.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass
                state.now_playing_message = await self._send_msg(
                    text_channel, "now_playing",
                    title=track_to_play.title,
                    duration=format_duration(track_to_play.duration),
                    requester_id=track_to_play.requester_id
                )

        except Exception as e:
            logger.error(
                f"ギルドID {guild_id}: 曲 '{track_to_play.title if track_to_play else 'N/A'}' の再生準備中に予期せぬエラー: {e}",
                exc_info=True)
            if text_channel:
                await self._send_msg(text_channel, "error_playing", error=str(e))
            if state.loop_mode == LoopMode.ALL and track_to_play:
                await state.queue.put(track_to_play)
            state.current_track = None
            asyncio.create_task(self._play_next_song(guild_id))

    def _song_finished_callback(self, error: Optional[Exception], guild_id: int):
        state = self._get_guild_state(guild_id)

        # 以前再生していた曲を保持 (ループALL用)
        finished_track = state.current_track
        state.is_playing = False  # is_playing は曲が終わったらFalse
        # state.current_track は次の曲が始まるまで保持されるか、ここでNoneにする

        if error:
            logger.error(f"ギルドID {guild_id}: 再生エラー (after callback): {error}")
            if state.last_text_channel_id:
                text_channel = self.bot.get_channel(state.last_text_channel_id)
                if text_channel:
                    asyncio.run_coroutine_threadsafe(
                        self._send_msg(text_channel, "error_playing", error=str(error)),
                        self.bot.loop
                    )

        # ループALLの場合、再生し終わった曲をキューの最後尾に戻す
        # LoopMode.ONE の場合は、_play_next_song が同じ曲を再生する
        if finished_track and state.loop_mode == LoopMode.ALL:
            # afterコールバックは別スレッドで実行されるため、asyncio.Queue操作はrun_coroutine_threadsafeで行う
            async def _add_finished_to_queue():
                if finished_track:  # 再確認
                    await state.queue.put(finished_track)

            asyncio.run_coroutine_threadsafe(_add_finished_to_queue(), self.bot.loop)

        # 次の曲を再生 (LoopMode.ONE もここで処理される)
        coro = self._play_next_song(guild_id)
        asyncio.run_coroutine_threadsafe(coro, self.bot.loop)

    def _schedule_auto_leave(self, guild_id: int):
        state = self._get_guild_state(guild_id)

        if state.auto_leave_task and not state.auto_leave_task.done():
            state.auto_leave_task.cancel()
            state.auto_leave_task = None  # キャンセルしたらタスク参照をクリア

        # 再生中でなく、キューも空の場合のみスケジュール
        if not state.is_playing and not state.is_paused and state.queue.empty() and not state.current_track:
            # ボイスチャンネルにボット以外のメンバーがいないことを確認
            if state.voice_client and state.voice_client.is_connected():
                human_members = [m for m in state.voice_client.channel.members if not m.bot]
                if not human_members:
                    logger.info(f"ギルドID {guild_id}: 自動退出タイマーを開始します ({self.auto_leave_timeout}秒)。")
                    state.auto_leave_task = asyncio.create_task(self._auto_leave_coroutine(guild_id))
                else:
                    logger.debug(f"ギルドID {guild_id}: ユーザーがいるため自動退出タイマーは開始しません。")
            else:
                logger.debug(f"ギルドID {guild_id}: VC未接続のため自動退出タイマーは開始しません。")

    async def _auto_leave_coroutine(self, guild_id: int):
        state = self._get_guild_state(guild_id)

        # Countdownメッセージはスキップすることも検討 (頻繁に表示されるのを避けるため)
        # if state.last_text_channel_id:
        #     channel = self.bot.get_channel(state.last_text_channel_id)
        #     if channel:
        #         await self._send_msg(channel, "auto_leave_empty_channel_countdown", timeout=self.auto_leave_timeout)

        await asyncio.sleep(self.auto_leave_timeout)

        # スリープ後、再度チャンネルの状態と再生状態を確認
        if state.voice_client and state.voice_client.is_connected() and \
                not state.is_playing and not state.is_paused and state.queue.empty() and not state.current_track:
            human_members = [m for m in state.voice_client.channel.members if not m.bot]
            if not human_members:
                logger.info(f"ギルドID {guild_id}: タイムアウト。ボイスチャンネルから自動退出します。")
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    if channel: await self._send_msg(channel, "auto_left_empty_channel")
                await state.voice_client.disconnect()  # これが on_voice_state_update をトリガーする
                # await self._cleanup_guild_state(guild_id) # on_voice_state_update で処理されるので不要な場合も
            else:
                logger.info(f"ギルドID {guild_id}: 自動退出キャンセル (ユーザーがチャンネルに再参加または再生が開始)。")
        else:
            logger.info(f"ギルドID {guild_id}: 自動退出処理中に状態が変化したため、退出を中止します。")

    async def _cleanup_guild_state(self, guild_id: int):
        logger.debug(f"ギルドID {guild_id}: ギルド状態をクリーンアップします。")
        if guild_id in self.guild_states:
            state = self.guild_states[guild_id]  # _get_guild_state を使うと新規作成される可能性があるため直接アクセス
            if state.voice_client and state.voice_client.is_connected():
                state.voice_client.stop()

            state.voice_client = None  # VCオブジェクトへの参照を切る
            state.current_track = None
            await state.clear_queue()
            state.is_playing = False
            state.is_paused = False
            state.loop_mode = LoopMode.OFF
            if state.now_playing_message:
                try:
                    await state.now_playing_message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
                state.now_playing_message = None
            if state.auto_leave_task and not state.auto_leave_task.done():
                state.auto_leave_task.cancel()
                state.auto_leave_task = None

            # ギルドステート自体を削除する (メモリ解放のため)
            del self.guild_states[guild_id]
            logger.info(f"ギルドID {guild_id}: GuildStateオブジェクトを削除しました。")

    # --- Cogイベントリスナー ---
    @commands.Cog.listener()
    async def on_ready(self):
        logger.info(f"{self.bot.user.name} の MusicCog が正常にロードされました。")
        activity_template = self.music_config.get('bot_activity_playing', "音楽を再生中 | {prefix}help")

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
        if member.bot and member.id != self.bot.user.id:
            return

        guild_id = member.guild.id
        if guild_id not in self.guild_states:  # まだこのギルドのStateがない場合は何もしない
            return
        state = self._get_guild_state(guild_id)

        if not state.voice_client or not state.voice_client.is_connected():
            return

        # ボット自身が切断された場合 (手動または何らかの理由で)
        if member.id == self.bot.user.id and before.channel and not after.channel:
            logger.info(
                f"ギルドID {guild_id}: ボットがVC {before.channel.name} から切断されました。状態をクリーンアップします。")
            await self._cleanup_guild_state(guild_id)
            return

        # ボットがいるチャンネルでの変化のみを監視
        if state.voice_client.channel != before.channel and state.voice_client.channel != after.channel:
            # もしボットがいたチャンネルから全員抜けた場合 (before.channel がボットのチャンネル)
            if before.channel == state.voice_client.channel:
                human_members_in_old_channel = [m for m in before.channel.members if not m.bot]
                if not human_members_in_old_channel:
                    if not state.is_playing and not state.is_paused:
                        if not state.auto_leave_task or state.auto_leave_task.done():
                            self._schedule_auto_leave(guild_id)
            return

        # ボットがいるチャンネルのメンバー構成が変わった場合
        current_vc_channel = state.voice_client.channel
        human_members_in_vc = [m for m in current_vc_channel.members if not m.bot]

        if not human_members_in_vc:  # ボットしか残っていない場合
            if not state.is_playing and not state.is_paused:  # 再生中でも一時停止中でもない
                if not state.auto_leave_task or state.auto_leave_task.done():
                    self._schedule_auto_leave(guild_id)
        else:  # 他のユーザーがいる場合
            if state.auto_leave_task and not state.auto_leave_task.done():
                logger.info(f"ギルドID {guild_id}: ユーザーがVCに参加/残存のため、自動退出タイマーをキャンセルします。")
                state.auto_leave_task.cancel()
                state.auto_leave_task = None
                if state.last_text_channel_id:
                    channel = self.bot.get_channel(state.last_text_channel_id)
                    # キャンセルメッセージは状況によって出すか判断 (例: countdownが出ていた場合など)
                    # if channel: await self._send_msg(channel, "auto_leave_countdown_cancelled")

    # --- コマンド ---
    @commands.command(name="join", aliases=["connect", "j"], help="ボットを指定したボイスチャンネルに接続します。")
    async def join_command(self, ctx: commands.Context, *, channel: Optional[discord.VoiceChannel] = None):
        state = self._get_guild_state(ctx.guild.id)
        state.update_last_text_channel(ctx.channel.id)

        target_channel = channel or (ctx.author.voice.channel if ctx.author.voice else None)

        if not target_channel:
            await self._send_msg(ctx.channel, "join_voice_channel_first")
            return

        if state.voice_client and state.voice_client.is_connected():
            if state.voice_client.channel == target_channel:
                await self._send_msg(ctx.channel, "already_connected")
                return
            try:
                await state.voice_client.move_to(target_channel)
                logger.info(f"ギルド {ctx.guild.name}: ボイスチャンネルを {target_channel.name} に移動しました。")
                await ctx.message.add_reaction("✅")  # 成功リアクション
            except Exception as e:
                logger.error(f"チャンネル移動エラー: {e}", exc_info=True)
                await self._send_msg(ctx.channel, "error_playing", error=f"チャンネル移動に失敗 ({type(e).__name__})")
        else:
            try:
                state.voice_client = await target_channel.connect(timeout=15.0, reconnect=True)
                logger.info(f"ギルド {ctx.guild.name}: ボイスチャンネル {target_channel.name} に接続しました。")
                await ctx.message.add_reaction("✅")  # 成功リアクション
            except asyncio.TimeoutError:
                logger.error(f"ギルド {ctx.guild.name}: ボイスチャンネルへの接続がタイムアウトしました。")
                await self._send_msg(ctx.channel, "error_playing",
                                     error="ボイスチャンネルへの接続がタイムアウトしました。")
            except Exception as e:
                logger.error(f"チャンネル接続エラー: {e}", exc_info=True)
                await self._send_msg(ctx.channel, "error_playing", error=f"チャンネル接続に失敗 ({type(e).__name__})")

    @commands.command(name="leave", aliases=["disconnect", "dc", "bye"], help="ボットをボイスチャンネルから切断します。")
    async def leave_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)  # State取得は最初に行う
        state.update_last_text_channel(ctx.channel.id)

        if not state.voice_client or not state.voice_client.is_connected():
            await self._send_msg(ctx.channel, "bot_not_in_voice_channel")
            return

        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} によりボイスチャンネルから切断します。")
        await self._send_msg(ctx.channel, "leaving_voice_channel")
        await state.voice_client.disconnect()  # on_voice_state_update が cleanup をトリガー

    @commands.command(name="play", aliases=["p"],
                      help="指定された曲を再生、またはキューに追加します。\nURLまたは検索クエリを指定できます。")
    async def play_command(self, ctx: commands.Context, *, query: str):
        state = self._get_guild_state(ctx.guild.id)

        vc = await self._ensure_voice(ctx, connect_if_not_in=True)
        if not vc: return

        if state.queue.qsize() >= self.max_queue_size:
            await self._send_msg(ctx.channel, "max_queue_size_reached", max_size=self.max_queue_size)
            return

        nico_email = self.music_config.get('niconico', {}).get('email')
        nico_password = self.music_config.get('niconico', {}).get('password')
        max_playlist_items = self.music_config.get('max_playlist_items', 50)

        async with ctx.typing():
            try:
                extracted_media = await extract_audio_data(
                    query,
                    shuffle_playlist=False,  # MusicCog側でコマンドとしてシャッフルを提供
                    nico_email=nico_email,
                    nico_password=nico_password,
                    max_playlist_items=max_playlist_items
                )
            except RuntimeError as e:
                logger.error(f"音声データ抽出中にRuntimeError: {e} (Query: {query})")
                await self._send_msg(ctx.channel, "error_fetching_song", error=str(e))
                return
            except Exception as e:
                logger.error(f"音声データ抽出中に予期せぬエラー: {e} (Query: {query})", exc_info=True)
                await self._send_msg(ctx.channel, "error_fetching_song", error=type(e).__name__)
                return

        if not extracted_media:
            await self._send_msg(ctx.channel, "search_no_results", query=query)
            return

        tracks_to_add: List[Track] = []
        if isinstance(extracted_media, list):
            tracks_to_add.extend(extracted_media)
        else:
            tracks_to_add.append(extracted_media)

        if not tracks_to_add:
            await self._send_msg(ctx.channel, "search_no_results", query=query)  # 再度チェック
            return

        added_count = 0
        first_added_track_info = None  # プレイリストでない場合や最初の曲の情報を保持

        for track in tracks_to_add:
            if state.queue.qsize() < self.max_queue_size:
                track.requester_id = ctx.author.id
                track.original_query = query  # プレイリストの場合、全曲に同じクエリが紐づく
                await state.queue.put(track)
                if added_count == 0:  # 最初の有効なトラック
                    first_added_track_info = {
                        "title": track.title,
                        "duration": format_duration(track.duration),
                        "requester_id": track.requester_id
                    }
                added_count += 1
            else:
                await self._send_msg(ctx.channel, "max_queue_size_reached", max_size=self.max_queue_size)
                break

        if added_count == 0:
            pass  # メッセージはmax_queue_size_reachedで送信済みか、search_no_results
        elif len(tracks_to_add) > 1 and added_count > 0:  # プレイリストから1曲以上追加
            await self._send_msg(ctx.channel, "added_playlist_to_queue", count=added_count)
        elif added_count == 1 and first_added_track_info:  # 1曲だけ追加 (元が単曲 or プレイリストだが1曲のみ成功)
            await self._send_msg(ctx.channel, "added_to_queue", **first_added_track_info)

        if not state.is_playing and not state.is_paused and added_count > 0:
            asyncio.create_task(self._play_next_song(ctx.guild.id))

    @commands.command(name="skip", aliases=["s", "next"], help="現在再生中の曲をスキップします。")
    async def skip_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=False)
        if not vc: return

        if not state.current_track and state.queue.empty():  # 再生中でもなくキューも空
            await self._send_msg(ctx.channel, "nothing_to_skip")
            return

        if not state.is_playing and not state.is_paused and not state.current_track:  # 再生関連の状態がない
            await self._send_msg(ctx.channel, "nothing_to_skip")
            return

        skipped_title = state.current_track.title if state.current_track else "キューの次の曲"
        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} により {skipped_title} をスキップします。")
        await self._send_msg(ctx.channel, "skipped_song", title=skipped_title)

        state.voice_client.stop()  # afterコールバックが次の曲を再生

    @commands.command(name="stop", help="再生を停止し、キューをクリアします。")
    async def stop_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=False)
        if not vc: return

        logger.info(f"ギルド {ctx.guild.name}: {ctx.author.name} により再生を停止し、キューをクリアします。")
        await self._send_msg(ctx.channel, "stopped_playback")

        state.loop_mode = LoopMode.OFF  # stopしたらループも解除
        await state.clear_queue()
        state.current_track = None  # current_trackもクリア

        if state.voice_client and state.voice_client.is_playing():
            state.voice_client.stop()  # 再生を停止
        # is_playing, is_paused は stop() または after コールバックで適切に処理される
        state.is_playing = False
        state.is_paused = False

        if state.now_playing_message:
            try:
                await state.now_playing_message.delete()
            except (discord.NotFound, discord.Forbidden):
                pass
            state.now_playing_message = None

        self._schedule_auto_leave(ctx.guild.id)  # 再生停止したので自動退出タイマー開始検討

    @commands.command(name="pause", help="再生を一時停止します。")
    async def pause_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=False)
        if not vc: return

        if not state.is_playing:  # is_playing で判定 (再生中でなければ一時停止できない)
            # メッセージキー 'not_currently_playing' などを追加検討
            await ctx.send(self._get_message("error_playing", error="再生中ではありません。"))
            return
        if state.is_paused:
            # メッセージキー 'already_paused' などを追加検討
            await ctx.send(self._get_message("error_playing", error="既に一時停止中です。"))
            return

        state.voice_client.pause()
        state.is_paused = True
        # state.is_playing = False # pauseしたらplayingではない、という考え方もできるが、ここではis_playingは再生実態を示すため変更しない
        await self._send_msg(ctx.channel, "playback_paused")

    @commands.command(name="resume", aliases=["unpause"], help="一時停止中の再生を再開します。")
    async def resume_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=False)
        if not vc: return

        if not state.is_paused:
            # メッセージキー 'not_paused' などを追加検討
            await ctx.send(self._get_message("error_playing", error="一時停止中ではありません。"))
            return

        state.voice_client.resume()
        state.is_paused = False
        # state.is_playing = True # resumeしたらplaying
        await self._send_msg(ctx.channel, "playback_resumed")

    @commands.command(name="volume", aliases=["vol"], help="音量を変更します (0-200)。")
    async def volume_command(self, ctx: commands.Context, volume: Optional[int] = None):
        state = self._get_guild_state(ctx.guild.id)
        # VCにいなくても音量設定はできるが、反映はVC接続後になる

        if volume is None:
            current_vol_percent = int(state.volume * 100)
            # メッセージキー 'current_volume' などを追加検討
            await ctx.send(
                self._get_message("volume_set", volume=current_vol_percent).replace("設定しました", f"です (現在値)"))
            return

        if not (0 <= volume <= 200):
            await self._send_msg(ctx.channel, "invalid_volume")
            return

        state.volume = volume / 100.0
        if state.voice_client and state.voice_client.source and isinstance(state.voice_client.source,
                                                                           discord.PCMVolumeTransformer):
            state.voice_client.source.volume = state.volume

        await self._send_msg(ctx.channel, "volume_set", volume=volume)

    @commands.command(name="queue", aliases=["q", "list"], help="現在の再生キューを表示します。")
    async def queue_command(self, ctx: commands.Context, page: int = 1):
        state = self._get_guild_state(ctx.guild.id)
        state.update_last_text_channel(ctx.channel.id)

        if state.queue.empty() and not state.current_track:
            await self._send_msg(ctx.channel, "queue_empty")
            return

        items_per_page = 10
        embed = discord.Embed(
            title=self._get_message("queue_title", count=state.queue.qsize() + (1 if state.current_track else 0)),
            color=discord.Color.blue())

        description_lines = []

        current_queue_list = list(state.queue._queue)  # キューの現在のスナップショット
        all_tracks_for_display = []

        if state.current_track:
            all_tracks_for_display.append(state.current_track)
        all_tracks_for_display.extend(current_queue_list)

        if not all_tracks_for_display:
            await self._send_msg(ctx.channel, "queue_empty")  # 再度チェック
            return

        start_offset = 1 if state.current_track else 0  # 現在再生中の曲を表示する場合のオフセット

        # ページネーションの計算 (キュー内の曲のみを対象とするか、再生中も含めるか)
        # ここではキュー内の曲 (current_queue_list) をページネーション対象とする
        total_queued_items = len(current_queue_list)
        total_pages = math.ceil(total_queued_items / items_per_page) if total_queued_items > 0 else 1

        if page < 1 or (page > total_pages and total_pages > 0):  # total_pagesが0の時はpage=1のみ許容
            # メッセージキー 'invalid_page_number' などを追加検討
            await ctx.send(self._get_message("error_playing",
                                             error=f"無効なページ番号です。最大ページ: {total_pages if total_pages > 0 else 1}"))
            return

        # 表示するアイテムの範囲を計算 (キュー内)
        q_start_index = (page - 1) * items_per_page
        q_end_index = q_start_index + items_per_page

        # 1ページ目で、かつ現在再生中の曲がある場合はそれを最初に表示
        if page == 1 and state.current_track:
            track = state.current_track
            prefix_char = ":arrow_forward:" if state.is_playing else (
                ":pause_button:" if state.is_paused else ":musical_note:")
            description_lines.append(
                f"**{prefix_char} {track.title}** (`{format_duration(track.duration)}`) - リクエスト: <@{track.requester_id}>"
            )

        # キューの該当ページ部分を表示
        for i, track in enumerate(current_queue_list[q_start_index:q_end_index], start=q_start_index + 1):
            description_lines.append(
                self._get_message("queue_entry", index=i, title=track.title, duration=format_duration(track.duration),
                                  requester_id=track.requester_id)
            )

        if not description_lines:  # 何も表示するものがない場合 (ありえないはずだが念のため)
            await self._send_msg(ctx.channel, "queue_empty")
            return

        embed.description = "\n".join(description_lines)
        if total_pages > 1:
            embed.set_footer(text=f"ページ {page}/{total_pages}")

        await ctx.send(embed=embed)

    @commands.command(name="shuffle", aliases=["sh"], help="再生キューをシャッフルします。")
    async def shuffle_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=False)
        if not vc: return

        if state.queue.qsize() < 2:  # シャッフルする意味がない
            # メッセージキー 'queue_too_short_to_shuffle' などを追加検討
            await ctx.send(self._get_message("error_playing", error="シャッフルするにはキューに2曲以上必要です。"))
            return

        queue_list = list(state.queue._queue)
        random.shuffle(queue_list)

        new_q = asyncio.Queue()
        for item in queue_list:
            await new_q.put(item)
        state.queue = new_q

        await self._send_msg(ctx.channel, "queue_shuffled")

    @commands.command(name="nowplaying", aliases=["np", "current"], help="現在再生中の曲情報を表示します。")
    async def nowplaying_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        state.update_last_text_channel(ctx.channel.id)

        if not state.current_track:  # is_playing や is_paused は current_track があって初めて意味を持つ
            await self._send_msg(ctx.channel, "now_playing_nothing")
            return

        track = state.current_track
        status_icon = ":arrow_forward:" if state.is_playing else (
            ":pause_button:" if state.is_paused else ":musical_note:")

        embed = discord.Embed(
            title=f"{status_icon} {track.title}",
            description=(
                f"長さ: `{format_duration(track.duration)}`\n"
                f"リクエスト: <@{track.requester_id}>\n"
                f"ループモード: `{state.loop_mode.name.lower()}`"  # ループ状態も表示
            ),
            color=discord.Color.green() if state.is_playing else (
                discord.Color.orange() if state.is_paused else discord.Color.light_grey())
        )
        if track.thumbnail:
            embed.set_thumbnail(url=track.thumbnail)

        await ctx.send(embed=embed)

    @commands.command(name="clear", aliases=["clr"], help="再生キューをクリアします（再生中の曲は影響を受けません）。")
    async def clear_command(self, ctx: commands.Context):
        state = self._get_guild_state(ctx.guild.id)
        vc = await self._ensure_voice(ctx, connect_if_not_in=False)  # VCにいなくてもキューはクリアできる
        # if not vc: return # 必須ではない

        await state.clear_queue()
        await self._send_msg(ctx.channel, "queue_cleared")

    @commands.command(name="loop", aliases=["repeat"],
                      help="ループモードを設定します (off, one, all)。引数なしで現在の状態を表示。")
    async def loop_command(self, ctx: commands.Context, mode: Optional[str] = None):
        state = self._get_guild_state(ctx.guild.id)
        # vc = await self._ensure_voice(ctx, connect_if_not_in=False) # VCにいなくても設定は可能
        # if not vc: return

        if mode is None:
            # メッセージキー 'current_loop_mode' などを追加検討
            await ctx.send(self._get_message("loop_all").replace("キュー全体をループ再生します。",
                                                                 f"現在のループモード: {state.loop_mode.name.lower()}"))
            return

        mode_lower = mode.lower()
        if mode_lower == "off" or mode_lower == "none":
            state.loop_mode = LoopMode.OFF
            await self._send_msg(ctx.channel, "loop_off")
        elif mode_lower == "one" or mode_lower == "song" or mode_lower == "track":
            state.loop_mode = LoopMode.ONE
            await self._send_msg(ctx.channel, "loop_one")
        elif mode_lower == "all" or mode_lower == "queue":
            state.loop_mode = LoopMode.ALL
            await self._send_msg(ctx.channel, "loop_all")
        else:
            await self._send_msg(ctx.channel, "invalid_loop_option")

    @commands.command(name="remove", aliases=["rm"], help="キューから指定した番号の曲を削除します。")
    async def remove_command(self, ctx: commands.Context, index: int):
        state = self._get_guild_state(ctx.guild.id)
        # vc = await self._ensure_voice(ctx, connect_if_not_in=False) # VCにいなくても操作可能
        # if not vc: return

        if state.queue.empty():
            # メッセージキー 'queue_empty_for_remove' などを追加検討
            await ctx.send(self._get_message("queue_empty"))
            return

        actual_index = index - 1

        if not (0 <= actual_index < state.queue.qsize()):
            await self._send_msg(ctx.channel, "invalid_queue_number", prefix=self.bot.command_prefix)  # prefixを渡す例
            return

        queue_list = list(state.queue._queue)
        removed_track = queue_list.pop(actual_index)

        new_q = asyncio.Queue()
        for item in queue_list:
            await new_q.put(item)
        state.queue = new_q

        await self._send_msg(ctx.channel, "song_removed", title=removed_track.title)


# --- Cogセットアップ関数 ---
async def setup(bot: commands.Bot):
    """Cogをボットに登録するためのセットアップ関数"""
    if not hasattr(bot, 'config'):
        logger.warning(
            "Botインスタンスに 'config' 属性がありません。MusicCogが独自にconfig.yamlをロードしようと試みますが、これは非推奨です。main.pyでBotにconfigをロードしてください。")

    # ytdlp_wrapperからのインポートが成功したか再度確認
    if not Track or not extract_audio_data or not ensure_stream:
        logger.critical(
            "MusicCog: 必須コンポーネント(Track/extract/ensure_stream)がインポートできませんでした。MusicCogはロードされません。")
        # ExtensionNotLoaded や ExtensionFailed を raise して Cog のロードを明示的に失敗させる
        raise commands.ExtensionFailed("MusicCog", "必須ライブラリ ytdlp_wrapper のコンポーネントが見つかりません。")

    try:
        cog_instance = MusicCog(bot)  # ここで __init__ 内のコンポーネントチェックも走る
        await bot.add_cog(cog_instance)
        logger.info("MusicCogが正常にロードされました。")
    except commands.ExtensionFailed as e:  # __init__からのExtensionFailedをキャッチ
        logger.error(f"MusicCogの初期化中にエラーが発生しました (ExtensionFailed): {e}")
        raise  # 再送出してBotに通知
    except Exception as e:
        logger.error(f"MusicCogのセットアップ中に予期しないエラーが発生しました: {e}", exc_info=True)
        raise