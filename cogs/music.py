# cogs/music.py

import asyncio
import logging
import random
from typing import Dict, List, Literal, Optional  # Pythonの型ヒント用

import discord  # Discord.pyライブラリ
import yt_dlp  # YouTubeや他のサイトから音声/動画情報を取得するライブラリ
from discord import app_commands  # スラッシュコマンド用
from discord.ext import commands  # Cogやコマンドフレームワーク用

# yt-dlpとffmpegの設定
# yt-dlp オプション
YDL_OPTS_BASE = {
    'format': 'bestaudio/best',  # 最高音質の音声を選択
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',  # ダウンロードする場合のパス形式
    'restrictfilenames': True,  # ファイル名をASCII文字のみに制限
    'noplaylist': False,  # プレイリストの処理を有効にする
    'nocheckcertificate': True,  # SSL証明書の検証をスキップ (自己署名証明書サイト対策)
    'ignoreerrors': False,  # エラーを無視しない
    'logtostderr': False,  # エラーログを標準エラー出力に出さない
    'quiet': True,  # yt-dlp自体のログ出力を抑制
    'no_warnings': True,  # yt-dlpの警告を抑制
    'default_search': 'auto',  # URLでない場合は自動検索 (例: "ytsearch: 曲名")
    'source_address': '0.0.0.0',  # IPv4を優先して使用
    'extract_flat': 'in_playlist',  # プレイリスト内の動画情報を高速に取得 (ストリームURLは取得しない)
    'lazy_playlist': True,  # プレイリスト情報を遅延読み込み
    'playlist_items': '1-20',  # プレイリストから一度に読み込む上限（configで上書き可能）
}

# FFmpeg オプション
FFMPEG_OPTS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',  # 再接続オプション
    'options': '-vn -loglevel quiet',  # 音声のみ(-vn)、FFmpegのログ出力を抑制
}

logger = logging.getLogger('discord.cogs.music')  # このCog専用のロガーを作成


# --- 設定ファイルからメッセージを取得するヘルパー関数 ---
def get_msg(bot_cfg: dict, key: str, **kwargs) -> str:
    """
    設定ファイル (bot_cfg経由) から指定されたキーのメッセージを取得し、
    与えられたキーワード引数でメッセージ内のプレースホルダを置換します。

    Args:
        bot_cfg (dict): ボットの設定情報が含まれる辞書 (通常は bot.cfg)。
        key (str): 取得したいメッセージのキー (例: "play_no_match")。
        **kwargs: メッセージ内のプレースホルダを置換するためのキーワード引数。

    Returns:
        str: フォーマットされたメッセージ文字列。キーが見つからない場合はデフォルトのエラーメッセージ。
    """
    default_message = f"メッセージキー '{key}' が設定ファイルに見つかりません。"
    # music_cog_settings.messages.実際のキー という階層でメッセージを取得
    message_template = bot_cfg.get("music_cog_settings", {}).get("messages", {}).get(key, default_message)
    try:
        # kwargsを使ってプレースホルダ ({placeholder_name}) を置換
        return message_template.format(**kwargs)
    except KeyError as e:
        # フォーマット中に必要なプレースホルダが見つからなかった場合のエラー
        logger.error(f"メッセージキー '{key}' のフォーマット中にエラー: 不足しているプレースホルダ {e}")
        return message_template  # フォーマット失敗時は元のテンプレートを返す (部分的にでも表示するため)
    except Exception as ex:
        logger.error(f"メッセージキー '{key}' のフォーマット中に予期せぬエラー: {ex}")
        return f"メッセージ '{key}' の表示中にエラーが発生しました。"


class SongItem:
    """再生キュー内の個々の曲情報を保持するクラスです。"""

    def __init__(self, source_url: str, info: dict, requested_by: discord.Member, bot_cfg: dict):
        """
        SongItemを初期化します。

        Args:
            source_url (str): 再生に使用するストリームURL。
            info (dict): yt-dlpから取得した楽曲情報を含む辞書。
            requested_by (discord.Member): この曲をリクエストしたDiscordメンバー。
            bot_cfg (dict): ボット全体の設定情報。メッセージ取得などに使用。
        """
        self.source_url: str = source_url
        self.title: str = info.get('title', '不明なタイトル')
        self.webpage_url: str = info.get('webpage_url', '')  # 曲の元のWebページURL
        self.uploader: str = info.get('uploader', '不明なアップローダー')
        self.duration: int = info.get('duration', 0)  # 曲の長さ (秒単位)
        self.thumbnail: Optional[str] = info.get('thumbnail')  # サムネイル画像のURL
        self.requested_by: discord.Member = requested_by
        self.is_live: bool = info.get('is_live', False)  # ライブストリームかどうか
        self.bot_cfg = bot_cfg  # ボット設定を保持 (主にメッセージ取得用)

    def __str__(self):
        """曲の情報を簡易的な文字列で表現します (例: **曲名** (by アーティスト名))。"""
        return f"**{self.title}** (by {self.uploader})"

    def to_embed(self, title_prefix_key: str = "now_playing_title_playing") -> discord.Embed:
        """
        この曲の情報からDiscord Embedオブジェクトを生成します。

        Args:
            title_prefix_key (str): Embedのタイトルに使用する接頭辞のメッセージキー。
                                    config.yamlのmusic_cog_settings.messages以下から参照。

        Returns:
            discord.Embed: 曲情報が設定されたEmbedオブジェクト。
        """
        title_prefix = get_msg(self.bot_cfg, title_prefix_key)

        embed = discord.Embed(
            title=f"{title_prefix}{self.title}",
            url=self.webpage_url,
            color=discord.Color.random()  # ランダムな色を使用
        )
        embed.set_author(name=self.uploader)
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)

        duration_str = f"{self.duration // 60}:{self.duration % 60:02d}" if not self.is_live else "ライブストリーム"
        embed.add_field(name="長さ", value=duration_str)  # 「長さ」もconfig化可能
        embed.add_field(name="リクエスト者", value=self.requested_by.mention)  # 「リクエスト者」もconfig化可能
        return embed


class GuildVoiceState:
    """
    各Discordサーバー（ギルド）ごとの音楽再生に関する状態を管理するクラスです。
    ボイスクライアント、再生キュー、音量、ループ設定などを保持します。
    """

    def __init__(self, bot_client: discord.Client, guild: discord.Guild, bot_cfg: dict):
        """
        GuildVoiceStateを初期化します。

        Args:
            bot_client (discord.Client): ボットのクライアントインスタンス。
            guild (discord.Guild): この状態が関連付けられるDiscordサーバー。
            bot_cfg (dict): ボット全体の設定情報。
        """
        self.bot_client = bot_client
        self.guild = guild
        self.bot_cfg = bot_cfg  # ボット設定を保持
        self.voice_client: Optional[discord.VoiceClient] = None  # Discordボイスクライアント
        self.current_song: Optional[SongItem] = None  # 現在再生中の曲
        self.song_queue: asyncio.Queue[SongItem] = asyncio.Queue()  # 再生待ちの曲のキュー

        default_volume_percent = self.bot_cfg.get("music_cog_settings", {}).get("default_volume", 50)
        self.volume: float = default_volume_percent / 100.0  # 音量 (0.0 から 2.0)

        self.loop_mode: Literal["off", "song", "queue"] = "off"  # ループモード ('off', 'song', 'queue')
        self.now_playing_message: Optional[discord.Message] = None  # 「再生中」情報を表示しているメッセージ
        self._play_next_song_task: Optional[asyncio.Task] = None  # 次の曲を再生するタスク
        self._lock = asyncio.Lock()  # 再生処理の同時実行を防ぐためのロック

    async def _call_play_next_song(self, interaction: Optional[discord.Interaction] = None,
                                   error: Optional[Exception] = None):
        """
        前の曲の再生が終了した (またはエラーが発生した) 後に呼び出され、次の曲の再生処理を開始します。
        このメソッドは主に `voice_client.play()` の `after` コールバックから非同期に呼び出されます。
        """
        if error:
            logger.error(f"Guild {self.guild.id}: 前の曲がエラーで終了: {error}")
            # エラーメッセージを送信するチャンネルを決定 (interactionがあればそこ、なければログのみなど)
            # interaction が常に渡されるわけではないので注意
            target_channel = interaction.channel if interaction and interaction.channel else None
            if not target_channel and self.voice_client and self.voice_client.channel:
                # ボイスチャンネルに関連するテキストチャンネルを探す試み (簡易的)
                target_channel = discord.utils.get(self.guild.text_channels,
                                                   name=self.voice_client.channel.name)  # type: ignore
            if target_channel:
                try:
                    error_msg_text = get_msg(self.bot_cfg, "voice_channel_error", error=str(error)[:100])  # エラーメッセージを短縮
                    await target_channel.send(error_msg_text, delete_after=20)
                except discord.HTTPException:
                    pass  # 送信失敗は無視

        # 既存のplay_next_songタスクがあればキャンセル (重複実行防止)
        if self._play_next_song_task and not self._play_next_song_task.done():
            self._play_next_song_task.cancel()

        # 新しいタスクとして play_next_song を実行
        self._play_next_song_task = self.bot_client.loop.create_task(self.play_next_song())

    async def play_next_song(self):
        """
        再生キューから次の曲を取り出し、再生を開始します。
        ループ設定に応じて、現在の曲を再度キューに入れたり、キュー自体をループさせたりします。
        再生する曲がなくなれば、再生中メッセージを更新します。
        """
        async with self._lock:  # ロックを取得して、このメソッドの同時実行を防ぐ
            if self.voice_client is None or not self.voice_client.is_connected():
                logger.info(f"Guild {self.guild.id}: ボイスチャンネルに接続していないため、再生を停止します。")
                return

            # 既に何か再生中または一時停止中の場合は何もしない (afterコールバックのタイミング問題対策)
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                logger.debug(
                    f"Guild {self.guild.id}: 再生中または一時停止中のため、play_next_songの呼び出しは無視されました。")
                return

            next_song_item: Optional[SongItem] = None

            # ループモードとキューの状態に基づいて次に再生する曲を決定
            if self.loop_mode == "song" and self.current_song:
                next_song_item = self.current_song  # 曲ループ: 現在の曲を再度選択
            elif not self.song_queue.empty():
                next_song_item = self.song_queue.get_nowait()  # キューから次の曲を取得
                if self.loop_mode == "queue" and self.current_song:
                    # キューループ: 再生が終わった現在の曲をキューの末尾に追加
                    await self.song_queue.put(self.current_song)
            elif self.loop_mode == "queue" and self.current_song:
                # キューが空だがキューループが有効で、直前に曲が再生されていた場合
                await self.song_queue.put(self.current_song)  # 現在の曲をキューに戻す
                if not self.song_queue.empty():  # 再度キューが空でないことを確認
                    next_song_item = self.song_queue.get_nowait()

            if next_song_item is None:  # 再生する曲がない場合
                self.current_song = None
                logger.info(f"Guild {self.guild.id}: 再生キューが空です。")
                if self.now_playing_message:
                    try:
                        empty_queue_msg = get_msg(self.bot_cfg, "queue_waiting_empty")
                        await self.now_playing_message.edit(content=empty_queue_msg, embed=None, view=None)
                    except discord.HTTPException:
                        pass  # メッセージ編集失敗は許容
                    self.now_playing_message = None  # メッセージへの参照をクリア
                # TODO: 設定に基づいて非アクティブ時に自動退出する機能
                return

            self.current_song = next_song_item  # 現在再生中の曲を更新

            try:
                # FFmpegを使用して音声ソースを作成し、音量調整を適用
                audio_source = discord.FFmpegPCMAudio(self.current_song.source_url, **FFMPEG_OPTS)
                transformed_source = discord.PCMVolumeTransformer(audio_source, volume=self.volume)

                # 音声再生を開始。再生終了後には _call_play_next_song が呼び出される
                self.voice_client.play(
                    transformed_source,
                    after=lambda e: self.bot_client.loop.create_task(self._call_play_next_song(error=e))
                )

                # 「再生中」メッセージを送信/更新するテキストチャンネルを決定
                target_channel = None
                # 既存のnow_playing_messageがあればそのチャンネルを使用
                if self.now_playing_message and self.now_playing_message.channel.id == self.voice_client.channel.id:  # type: ignore
                    target_channel = self.now_playing_message.channel
                elif self.voice_client.channel:  # ボイスチャンネルが存在する場合
                    # ボイスチャンネルと同じ名前のテキストチャンネルを探す (簡易的なヒューリスティック)
                    target_channel = discord.utils.find(
                        lambda c: c.name == self.voice_client.channel.name and isinstance(c, discord.TextChannel),
                        # type: ignore
                        self.guild.text_channels
                    )
                    if not target_channel and self.guild.text_channels:  # 見つからなければ最初のテキストチャンネル
                        target_channel = self.guild.text_channels[0]
                    if not target_channel:  # それでも見つからなければログ出力して終了
                        logger.warning(
                            f"Guild {self.guild.id}: 「再生中」メッセージを送信する適切なテキストチャンネルが見つかりませんでした。")
                        return
                else:  # ボイスクライアントは接続済みだがチャンネル情報がない異常ケース
                    logger.error(f"Guild {self.guild.id}: ボイスクライアントは接続されていますがチャンネルがNoneです。")
                    return

                # 現在の曲情報からEmbedを作成
                now_playing_embed = self.current_song.to_embed("now_playing_title_playing")

                # 「再生中」メッセージを送信または編集
                view = MusicPlayerView(self, self.bot_cfg)  # 新しいViewインスタンスを作成
                if self.now_playing_message:
                    try:
                        await self.now_playing_message.edit(content=None, embed=now_playing_embed, view=view)
                    except discord.NotFound:  # 元のメッセージが削除されていた場合
                        self.now_playing_message = await target_channel.send(embed=now_playing_embed, view=view)
                    except discord.HTTPException as e:  # その他の編集エラー
                        logger.error(f"now_playing_messageの編集に失敗: {e}")
                        self.now_playing_message = await target_channel.send(embed=now_playing_embed,
                                                                             view=view)  # 新規送信で対応
                else:  # 「再生中」メッセージがまだ存在しない場合
                    self.now_playing_message = await target_channel.send(embed=now_playing_embed, view=view)

            except Exception as e:
                logger.error(
                    f"Guild {self.guild.id}: 曲 {self.current_song.title if self.current_song else 'N/A'} の再生中にエラー: {e}",
                    exc_info=True)
                self.current_song = None  # エラー発生時は現在の曲情報をクリア
                await self._call_play_next_song(error=e)  # 次の曲の再生を試行

    async def stop_playback(self):
        """再生を完全に停止し、再生キューをクリアします。関連するタスクもキャンセルします。"""
        if self._play_next_song_task and not self._play_next_song_task.done():
            self._play_next_song_task.cancel()  # 次の曲再生タスクをキャンセル
        if self.voice_client:
            self.voice_client.stop()  # Discordボイスクライアントの再生を停止
        self.current_song = None  # 現在の曲情報をクリア

        # 再生キューを空にする
        new_queue: asyncio.Queue[SongItem] = asyncio.Queue()
        self.song_queue = new_queue  # Queueを新しい空のインスタンスで置き換え

        if self.now_playing_message:
            try:
                # 再生停止メッセージをconfigから取得 (例: stop_success だが、専用キーが良いかも)
                stop_msg_text = get_msg(self.bot_cfg, "stop_success")  # もしくは "playback_stopped_message" など
                await self.now_playing_message.edit(content=stop_msg_text, embed=None, view=None)  # EmbedとViewをクリア
            except discord.HTTPException:
                pass  # メッセージ編集失敗は許容
            self.now_playing_message = None  # メッセージ参照をクリア
        logger.info(f"Guild {self.guild.id}: 再生を停止し、キューをクリアしました。")


class MusicPlayerView(discord.ui.View):
    """
    音楽プレーヤーのボタン (一時停止/再開、スキップなど) を含むDiscord UI Viewです。
    「再生中」メッセージに添付されます。
    """

    def __init__(self, voice_state: GuildVoiceState, bot_cfg: dict,
                 timeout: Optional[float] = None):  # timeout=Noneで永続View
        """
        MusicPlayerViewを初期化します。

        Args:
            voice_state (GuildVoiceState): 関連付けられるギルドのボイス状態。
            bot_cfg (dict): ボット全体の設定情報 (ボタンのラベル等を取得するため)。
            timeout (Optional[float]): Viewがインタラクションを待機する時間 (秒)。Noneで無期限。
        """
        super().__init__(timeout=timeout)
        self.voice_state = voice_state
        self.bot_cfg = bot_cfg  # ボット設定を保持
        self._update_buttons()  # 初期ボタン状態を設定

    def _update_buttons(self):
        """現在の再生状態に応じて、View内のボタンのラベルやスタイルを更新します。"""
        # 一時停止/再開ボタンの状態更新
        pause_resume_button = discord.utils.get(self.children, custom_id="music_pause_resume")
        if isinstance(pause_resume_button, discord.ui.Button):  # 型チェック
            if self.voice_state.voice_client and self.voice_state.voice_client.is_paused():
                pause_resume_button.label = get_msg(self.bot_cfg, "button_resume")
                pause_resume_button.style = discord.ButtonStyle.green
            else:
                pause_resume_button.label = get_msg(self.bot_cfg, "button_pause")
                pause_resume_button.style = discord.ButtonStyle.secondary

        # ループボタンの状態更新
        loop_button = discord.utils.get(self.children, custom_id="music_loop")
        if isinstance(loop_button, discord.ui.Button):  # 型チェック
            if self.voice_state.loop_mode == "off":
                loop_button.label = get_msg(self.bot_cfg, "button_loop_off")
                loop_button.style = discord.ButtonStyle.gray
            elif self.voice_state.loop_mode == "song":
                loop_button.label = get_msg(self.bot_cfg, "button_loop_song")
                loop_button.style = discord.ButtonStyle.blurple
            else:  # queue
                loop_button.label = get_msg(self.bot_cfg, "button_loop_queue")
                loop_button.style = discord.ButtonStyle.primary

        # 他のボタンのラベルも必要に応じてここで更新 (固定ラベルなら不要)
        skip_button = discord.utils.get(self.children, custom_id="music_skip")
        if isinstance(skip_button, discord.ui.Button):
            skip_button.label = get_msg(self.bot_cfg, "button_skip")

        stop_button = discord.utils.get(self.children, custom_id="music_stop")
        if isinstance(stop_button, discord.ui.Button):
            stop_button.label = get_msg(self.bot_cfg, "button_stop")

        shuffle_btn = discord.utils.get(self.children, custom_id="music_shuffle")
        if isinstance(shuffle_btn, discord.ui.Button):
            shuffle_btn.label = get_msg(self.bot_cfg, "button_shuffle")

    # ボタンのデコレータ: ラベルは_update_buttonsで動的に設定するため、ここでは仮の値を指定
    @discord.ui.button(label="...", style=discord.ButtonStyle.secondary, custom_id="music_pause_resume", row=0)
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        """一時停止/再開ボタンが押されたときの処理。"""
        if not self.voice_state.voice_client or not self.voice_state.voice_client.is_connected():
            msg = get_msg(self.bot_cfg, "bot_not_in_voice")
            return await interaction.response.send_message(msg, ephemeral=True)

        if self.voice_state.voice_client.is_paused():
            self.voice_state.voice_client.resume()
            msg = get_msg(self.bot_cfg, "resume_success")
            await interaction.response.send_message(msg, ephemeral=True)
        else:
            self.voice_state.voice_client.pause()
            msg = get_msg(self.bot_cfg, "pause_success")
            await interaction.response.send_message(msg, ephemeral=True)

        self._update_buttons()  # ボタン表示を更新
        if self.voice_state.now_playing_message:  # 元のメッセージのViewも更新
            await self.voice_state.now_playing_message.edit(view=self)

    @discord.ui.button(label="...", style=discord.ButtonStyle.primary, custom_id="music_skip", row=0)
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        """スキップボタンが押されたときの処理。"""
        if not self.voice_state.voice_client or not self.voice_state.voice_client.is_playing():
            msg = get_msg(self.bot_cfg, "skip_no_song")
            return await interaction.response.send_message(msg, ephemeral=True)

        song_title = self.voice_state.current_song.title if self.voice_state.current_song else "現在の曲"
        self.voice_state.voice_client.stop()  # 再生を停止すると `after` コールバックが次の曲を処理
        msg = get_msg(self.bot_cfg, "skip_success", song_title=song_title)
        # ephemeral=False にして、他のユーザーにもスキップされたことがわかるようにしても良い
        await interaction.response.send_message(msg, ephemeral=True)
        # ボタンとViewの更新は play_next_song 内で新しい曲が再生される際に行われる

    @discord.ui.button(label="...", style=discord.ButtonStyle.danger, custom_id="music_stop", row=0)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """停止ボタンが押されたときの処理。"""
        if not self.voice_state.voice_client or not self.voice_state.voice_client.is_connected():
            msg = get_msg(self.bot_cfg, "bot_not_in_voice")
            return await interaction.response.send_message(msg, ephemeral=True)

        await self.voice_state.stop_playback()  # 再生状態の完全停止とキュークリア
        # stop_playback内でnow_playing_messageは更新・クリアされる
        msg = get_msg(self.bot_cfg, "stop_success")
        await interaction.response.send_message(msg, ephemeral=True)

    @discord.ui.button(label="...", style=discord.ButtonStyle.gray, custom_id="music_loop", row=1)
    async def loop(self, interaction: discord.Interaction, button: discord.ui.Button):
        """ループモード切り替えボタンが押されたときの処理。"""
        current_mode = self.voice_state.loop_mode
        next_mode: Literal["off", "song", "queue"]

        if current_mode == "off":
            next_mode = "song"
        elif current_mode == "song":
            next_mode = "queue"
        else:  # queue
            next_mode = "off"
        self.voice_state.loop_mode = next_mode

        msg = get_msg(self.bot_cfg, "loop_success", mode=next_mode)
        await interaction.response.send_message(msg, ephemeral=True)
        self._update_buttons()  # ボタン表示を更新
        if self.voice_state.now_playing_message:  # 元のメッセージのViewも更新
            await self.voice_state.now_playing_message.edit(view=self)

    @discord.ui.button(label="...", style=discord.ButtonStyle.green, custom_id="music_shuffle", row=1)
    async def shuffle(self, interaction: discord.Interaction, button: discord.ui.Button):
        """シャッフルボタンが押されたときの処理。"""
        if self.voice_state.song_queue.empty():
            msg = get_msg(self.bot_cfg, "shuffle_empty")
            return await interaction.response.send_message(msg, ephemeral=True)

        # asyncio.Queue の中身を直接シャッフルするのは推奨されないため、一度リストに取り出す
        queue_list: List[SongItem] = []
        while not self.voice_state.song_queue.empty():
            queue_list.append(self.voice_state.song_queue.get_nowait())

        random.shuffle(queue_list)  # リストをシャッフル

        # シャッフルされたリストから新しいキューを作成
        new_queue: asyncio.Queue[SongItem] = asyncio.Queue()
        for item in queue_list:
            await new_queue.put(item)
        self.voice_state.song_queue = new_queue

        msg = get_msg(self.bot_cfg, "shuffle_success")
        await interaction.response.send_message(msg, ephemeral=True)


class MusicCog(commands.Cog, name="Music"):
    """音楽再生機能に関連するコマンドをまとめたCogです。"""

    def __init__(self, bot: discord.Client):
        """
        MusicCogを初期化します。

        Args:
            bot (discord.Client): ボットのクライアントインスタンス。
                                  ここから `bot.cfg` を参照して設定を読み込みます。
        """
        self.bot: discord.Client = bot
        self.voice_states: Dict[int, GuildVoiceState] = {}  # ギルドIDとGuildVoiceStateのマッピング

        # メインのボットインスタンスから設定(cfg)を取得
        self.bot_cfg = getattr(bot, 'cfg', {})
        if not self.bot_cfg:
            # 設定が見つからない場合はエラーログを出し、デフォルト値で動作を試みる
            logger.error(
                "MusicCog: ボットの設定(cfg)が見つかりません。メッセージや一部機能の読み込みに失敗する可能性があります。")

        # yt-dlp のオプションをconfigから読み込んで上書き
        self.ydl_opts = YDL_OPTS_BASE.copy()  # 基本オプションをコピー
        playlist_items_cfg = self.bot_cfg.get("music_cog_settings", {}).get("yt_dlp_playlist_items",
                                                                            YDL_OPTS_BASE['playlist_items'])
        self.ydl_opts['playlist_items'] = playlist_items_cfg

        self.ffmpeg_opts = FFMPEG_OPTS.copy()  # 基本オプションをコピー
        # FFmpegオプションもconfigから読み込む場合はここに追加
        # ffmpeg_options_cfg = self.bot_cfg.get("music_cog_settings", {}).get("ffmpeg_options", FFMPEG_OPTS['options'])
        # self.ffmpeg_opts['options'] = ffmpeg_options_cfg

    def _get_guild_state(self, guild: discord.Guild) -> GuildVoiceState:
        """指定されたギルドのGuildVoiceStateインスタンスを取得または新規作成します。"""
        if guild.id not in self.voice_states:
            # GuildVoiceStateの初期化時にボット設定(bot_cfg)を渡す
            self.voice_states[guild.id] = GuildVoiceState(self.bot, guild, self.bot_cfg)
        return self.voice_states[guild.id]

    async def _ensure_voice_channel(self, interaction: discord.Interaction) -> Optional[GuildVoiceState]:
        """
        コマンド実行者がボイスチャンネルにいることを確認し、ボットをそのチャンネルに接続させます。
        成功した場合はGuildVoiceStateを、失敗した場合はNoneを返します。
        エラーメッセージは内部で送信されます。
        """
        if not interaction.guild:  # サーバー内コマンドであることを確認
            msg = get_msg(self.bot_cfg, "server_only_command")
            # response.send_message は defer されていない場合に使う
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return None

        state = self._get_guild_state(interaction.guild)

        if not interaction.user or not isinstance(interaction.user, discord.Member):
            msg = get_msg(self.bot_cfg, "user_info_error")
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return None

        author_voice_state = interaction.user.voice
        if not author_voice_state or not author_voice_state.channel:  # ユーザーがボイスチャンネルにいない場合
            msg = get_msg(self.bot_cfg, "voice_channel_required")
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return None

        target_channel_to_connect = author_voice_state.channel  # 接続先のボイスチャンネル

        # ボットが未接続、または異なるチャンネルにいる場合
        if state.voice_client is None or not state.voice_client.is_connected():
            try:
                # ボイスチャンネルに接続
                state.voice_client = await target_channel_to_connect.connect(timeout=10.0, reconnect=True)
                logger.info(
                    f"Guild {interaction.guild.id}: ボイスチャンネル {target_channel_to_connect.name} に接続しました。")
            except asyncio.TimeoutError:  # 接続タイムアウト
                msg = get_msg(self.bot_cfg, "join_timeout")
                # deferされていれば followup で応答
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)  # まれだが念のため
                return None
            except Exception as e:  # その他の接続エラー
                msg = get_msg(self.bot_cfg, "failed_to_join_voice", error=e)
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                logger.error(f"Guild {interaction.guild.id}: ボイスチャンネルへの接続に失敗: {e}", exc_info=True)
                return None
        elif state.voice_client.channel.id != target_channel_to_connect.id:  # ボットが既に別のチャンネルにいる場合
            try:
                # ユーザーのいるチャンネルに移動
                await state.voice_client.move_to(target_channel_to_connect)
                logger.info(
                    f"Guild {interaction.guild.id}: ボイスチャンネル {target_channel_to_connect.name} に移動しました。")
            except Exception as e:  # 移動失敗
                # 移動失敗メッセージもconfig化可能
                fail_move_msg = f"ボイスチャンネルの移動に失敗しました: `{e}`"
                if interaction.response.is_done():
                    await interaction.followup.send(fail_move_msg, ephemeral=True)
                else:
                    await interaction.response.send_message(fail_move_msg, ephemeral=True)
                logger.error(f"Guild {interaction.guild.id}: ボイスチャンネルの移動に失敗: {e}", exc_info=True)
                return None

        return state  # 成功時はGuildVoiceStateを返す

    async def _ytdl_extract_info(self, query: str, requested_by: discord.Member,
                                 loop: Optional[asyncio.AbstractEventLoop] = None) -> List[SongItem]:
        """
        yt-dlpを使用して、指定されたクエリ (曲名やURL) から楽曲情報を非同期で抽出します。
        プレイリストの処理にも対応しています。

        Args:
            query (str): 検索クエリまたはURL。
            requested_by (discord.Member): 曲をリクエストしたメンバー。
            loop (Optional[asyncio.AbstractEventLoop]): 使用するイベントループ。Noneの場合は現在のループを取得。

        Returns:
            List[SongItem]: 抽出された楽曲情報のリスト。

        Raises:
            yt_dlp.utils.DownloadError: 情報の取得に失敗した場合。
        """
        loop = loop or asyncio.get_event_loop()

        # MusicCogインスタンスが持つyt-dlpオプションを使用
        ydl_opts_final = self.ydl_opts.copy()
        # URLでないクエリ (検索) の場合は、プレイリスト関連のオプションを調整することがある
        if not query.startswith(('http:', 'https:', 'rtmp:')):
            ydl_opts_final['default_search'] = 'ytsearch'  # YouTubeで検索するように指定
            # 検索時は通常最初の1件のみを取得対象とすることが多い
            if 'playlist_items' in ydl_opts_final:
                # ydl_opts_final.pop('playlist_items') # プレイリスト上限を無視する場合
                pass
            ydl_opts_final['max_downloads'] = 1  # 検索結果が複数ヒットする場合、最初の1つだけを処理

        with yt_dlp.YoutubeDL(ydl_opts_final) as ydl:
            try:
                # run_in_executor でブロッキング処理であるyt-dlpの呼び出しを非同期実行
                data = await loop.run_in_executor(None, lambda: ydl.extract_info(query, download=False))
            except yt_dlp.utils.DownloadError as e:  # yt-dlp固有のダウンロードエラー
                logger.warning(f"yt-dlp download error for query '{query}': {e}")
                # エラーメッセージをconfigから取得して整形
                if hasattr(e, 'exc_info') and e.exc_info and len(e.exc_info) > 1:
                    actual_error_message = str(e.exc_info[1])  # type: ignore
                    # エラーメッセージが長すぎる場合があるので、適宜切り詰めることも検討
                    raise yt_dlp.utils.DownloadError(
                        get_msg(self.bot_cfg, "play_ytdl_error", error=actual_error_message[:200])) from e
                raise yt_dlp.utils.DownloadError(get_msg(self.bot_cfg, "play_ytdl_error", error=f"「{query}」")) from e

        song_items: List[SongItem] = []
        if data is None:  # yt-dlpが情報を返さなかった場合
            logger.warning(f"yt-dlpがクエリ '{query}' に対してNoneを返しました")
            return []

        # SongItemオブジェクトを作成する際に、bot_cfgも渡す
        if '_type' in data and data['_type'] == 'playlist':  # プレイリストの場合
            for entry in data.get('entries', []):  # 各エントリを処理
                if entry and 'url' in entry:  # ストリームURLが含まれているか確認
                    song_items.append(
                        SongItem(source_url=entry['url'], info=entry, requested_by=requested_by, bot_cfg=self.bot_cfg))
        elif 'url' in data:  # 単一の曲の場合
            song_items.append(
                SongItem(source_url=data['url'], info=data, requested_by=requested_by, bot_cfg=self.bot_cfg))

        # 一部のエクストラクタでは単一の結果も 'entries' に入れることがあるため、フォールバック処理
        if not song_items and 'entries' in data:
            for entry in data.get('entries', []):
                if entry and 'url' in entry:
                    song_items.append(
                        SongItem(source_url=entry['url'], info=entry, requested_by=requested_by, bot_cfg=self.bot_cfg))
                    break  # 最初の1件のみ取得

        return song_items

    # --- スラッシュコマンドの定義 ---
    # 各コマンドの応答メッセージは config.yaml から取得するように変更

    @app_commands.command(name="join", description="ボットをあなたが現在いるボイスチャンネルに参加させます。")
    async def join(self, interaction: discord.Interaction):
        # deferで「考え中...」を表示し、タイムアウトを防ぐ (ephemeral=Trueで実行者のみに見える)
        await interaction.response.defer(ephemeral=True)
        state = await self._ensure_voice_channel(interaction)  # ボイスチャンネルへの接続処理
        if state and state.voice_client and state.voice_client.is_connected():
            # 接続成功メッセージをconfigから取得
            msg = get_msg(self.bot_cfg, "join_success", channel_mention=state.voice_client.channel.mention)
            await interaction.followup.send(msg, ephemeral=True)
        # _ensure_voice_channel内でエラーメッセージは送信されるため、ここでは成功時のみ応答

    @app_commands.command(name="leave", description="ボットをボイスチャンネルから退出させ、再生を停止します。")
    async def leave(self, interaction: discord.Interaction):
        if not interaction.guild:  # サーバー内コマンドであるか確認
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)

        state = self._get_guild_state(interaction.guild)
        if state.voice_client and state.voice_client.is_connected():  # ボットが接続中の場合
            channel_name = state.voice_client.channel.name
            await state.stop_playback()  # 再生停止とキュークリア
            await state.voice_client.disconnect()  # ボイスチャンネルから切断
            state.voice_client = None  # ボイスクライアント参照をクリア
            # 退出成功メッセージ (ephemeral=Falseでチャンネルの全員に見えるようにする例)
            msg = get_msg(self.bot_cfg, "leave_success", channel_name=channel_name)
            await interaction.response.send_message(msg, ephemeral=False)
        else:  # ボットが接続していない場合
            msg = get_msg(self.bot_cfg, "bot_not_in_voice")
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="play", description="指定された曲名やURLの曲またはプレイリストを再生キューに追加します。")
    @app_commands.describe(query="再生したい曲名、YouTube等のURL、またはプレイリストのURL")
    async def play(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer()  # defer (ephemeral=Falseで後ほど全員に見えるメッセージを送る想定)

        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.followup.send(msg, ephemeral=True)

        # ボイスチャンネルへの接続と状態取得
        state = await self._ensure_voice_channel(interaction)
        if not state or not state.voice_client:  # 接続失敗時は _ensure_voice_channel が応答
            return

        try:
            # 楽曲情報を抽出 (_ytdl_extract_info にリクエスト者情報も渡す)
            song_items = await self._ytdl_extract_info(query, requested_by=interaction.user,
                                                       loop=self.bot.loop)  # type: ignore
        except yt_dlp.utils.DownloadError as e:  # yt-dlp固有のエラー (_ytdl_extract_info内で整形済み)
            await interaction.followup.send(str(e), ephemeral=True)
            return
        except Exception as e:  # その他の予期せぬエラー
            logger.error(f"クエリ '{query}' の _ytdl_extract_info でハンドルされないエラー: {e}", exc_info=True)
            msg = get_msg(self.bot_cfg, "play_ytdl_unexpected_error", error=e)
            await interaction.followup.send(msg, ephemeral=True)
            return

        if not song_items:  # 曲が見つからなかった場合
            msg = get_msg(self.bot_cfg, "play_no_match", query=query)
            await interaction.followup.send(msg, ephemeral=True)
            return

        # 抽出された曲をキューに追加
        num_added = 0
        for song_item in song_items:
            await state.song_queue.put(song_item)
            num_added += 1

        first_song = song_items[0]  # 最初の曲の情報 (Embed表示用)
        user_display_name = interaction.user.display_name  # type: ignore

        # Embedメッセージの作成 (単数/複数でメッセージを分ける)
        if num_added == 1:  # 1曲だけ追加された場合
            title = get_msg(self.bot_cfg, "play_added_to_queue_single")
            description = get_msg(self.bot_cfg, "play_added_to_queue_description_single",
                                  song_title_artist=str(first_song))
            footer_text = get_msg(self.bot_cfg, "play_footer_requested_by", user_name=user_display_name)
        else:  # 複数曲 (プレイリストなど) が追加された場合
            title = get_msg(self.bot_cfg, "play_added_to_queue_multiple", count=num_added)
            description = get_msg(self.bot_cfg, "play_added_to_queue_description_multiple",
                                  song_title_artist=str(first_song), other_count=num_added - 1)
            footer_text = get_msg(self.bot_cfg, "play_footer_playlist_requested_by", user_name=user_display_name)

        msg_embed = discord.Embed(title=title, description=description, color=discord.Color.green())
        if first_song.thumbnail: msg_embed.set_thumbnail(url=first_song.thumbnail)
        if interaction.user.avatar:
            msg_embed.set_footer(text=footer_text, icon_url=interaction.user.avatar.url)  # type: ignore
        else:
            msg_embed.set_footer(text=footer_text)

        await interaction.followup.send(embed=msg_embed)  # Embedメッセージを送信

        # ボットが再生中でない場合、再生を開始
        if state.voice_client and not state.voice_client.is_playing() and not state.voice_client.is_paused():
            await state.play_next_song()

            # MusicPlayerViewのためにinteractionを保持する場合 (現在はView初期化時にstateを渡す方式)
        # state.now_playing_message_interaction = interaction

    @app_commands.command(name="skip", description="現在再生中の曲をスキップします。")
    async def skip(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if not state.voice_client or not state.voice_client.is_playing():
            msg = get_msg(self.bot_cfg, "skip_no_song")
            return await interaction.response.send_message(msg, ephemeral=True)

        song_title = state.current_song.title if state.current_song else "現在の曲"
        state.voice_client.stop()  # `after`コールバックが次の曲を処理
        msg = get_msg(self.bot_cfg, "skip_success", song_title=song_title)
        await interaction.response.send_message(msg, ephemeral=False)  # スキップは全員に見えるように

    @app_commands.command(name="stop", description="音楽の再生を停止し、キューを完全にクリアします。")
    async def stop(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if not state.voice_client or not state.voice_client.is_connected():
            msg = get_msg(self.bot_cfg, "bot_not_in_voice")
            return await interaction.response.send_message(msg, ephemeral=True)

        await state.stop_playback()  # 再生状態の停止とキュークリア
        msg = get_msg(self.bot_cfg, "stop_success")
        await interaction.response.send_message(msg, ephemeral=False)  # 停止は全員に見えるように

    @app_commands.command(name="pause", description="現在再生中の音楽を一時停止します。")
    async def pause(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if state.voice_client and state.voice_client.is_playing() and not state.voice_client.is_paused():
            state.voice_client.pause()
            msg = get_msg(self.bot_cfg, "pause_success")
            await interaction.response.send_message(msg, ephemeral=False)
        else:
            msg = get_msg(self.bot_cfg, "pause_not_playing_or_paused")
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="resume", description="一時停止中の音楽の再生を再開します。")
    async def resume(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if state.voice_client and state.voice_client.is_paused():
            state.voice_client.resume()
            msg = get_msg(self.bot_cfg, "resume_success")
            await interaction.response.send_message(msg, ephemeral=False)
        else:
            msg = get_msg(self.bot_cfg, "resume_not_paused")
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="queue", description="現在の再生キューの状況を表示します。")
    async def queue(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        embed = discord.Embed(title=get_msg(self.bot_cfg, "queue_title"), color=discord.Color.blue())

        # 再生中の曲情報をEmbedに追加
        now_playing_field_name = get_msg(self.bot_cfg, "queue_now_playing_field")
        if state.current_song:
            embed.add_field(name=now_playing_field_name,
                            value=f"{state.current_song} (リクエスト: {state.current_song.requested_by.mention})",
                            inline=False)
        else:
            embed.add_field(name=now_playing_field_name, value=get_msg(self.bot_cfg, "queue_now_playing_none"),
                            inline=False)

        # 待機中の曲情報をEmbedに追加
        waiting_field_name = get_msg(self.bot_cfg, "queue_waiting_field", count=state.song_queue.qsize())
        if state.song_queue.empty():
            embed.add_field(name=waiting_field_name, value=get_msg(self.bot_cfg, "queue_waiting_empty"), inline=False)
        else:
            # キューの先頭から最大10曲を表示
            queue_list = list(state.song_queue._queue)[:10]  # type: ignore
            queue_text = ""
            for i, song in enumerate(queue_list):
                queue_text += f"{i + 1}. {song} (リクエスト: {song.requested_by.mention})\n"

            if state.song_queue.qsize() > 10:  # 10曲を超える場合は省略表示
                queue_text += get_msg(self.bot_cfg, "queue_waiting_more", count=state.song_queue.qsize() - 10)
            embed.add_field(name=waiting_field_name,
                            value=queue_text if queue_text else get_msg(self.bot_cfg, "queue_now_playing_none"),
                            inline=False)

        # ループモードをフッターに表示
        embed.set_footer(text=get_msg(self.bot_cfg, "queue_loop_mode_footer", loop_mode=state.loop_mode))
        await interaction.response.send_message(embed=embed, ephemeral=False)

    @app_commands.command(name="nowplaying", description="現在再生中の曲の詳細情報を表示します。")
    async def nowplaying(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if state.current_song and state.voice_client and (
                state.voice_client.is_playing() or state.voice_client.is_paused()):
            # タイトルのプレフィックスを再生状態に応じて変更
            title_key = "now_playing_title_playing"
            if state.voice_client.is_paused():
                title_key = "now_playing_title_paused"
            embed = state.current_song.to_embed(title_key)
            # MusicPlayerViewを添付してインタラクティブな操作を可能に
            await interaction.response.send_message(embed=embed, view=MusicPlayerView(state, self.bot_cfg),
                                                    ephemeral=False)
        else:
            msg = get_msg(self.bot_cfg, "now_playing_none")
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="volume", description="音楽の再生音量を調整します (0から200の範囲)。")
    @app_commands.describe(level="設定したい音量レベル (例: 50 は 50%)")
    async def volume(self, interaction: discord.Interaction, level: app_commands.Range[int, 0, 200]):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if not state.voice_client or not state.voice_client.is_connected():
            msg = get_msg(self.bot_cfg, "bot_not_in_voice")
            return await interaction.response.send_message(msg, ephemeral=True)

        state.volume = level / 100.0  # 0-200 を 0.0-2.0 に変換
        # 再生中の場合、即座に音量を反映
        if state.voice_client.source and hasattr(state.voice_client.source, 'volume'):
            state.voice_client.source.volume = state.volume  # type: ignore

        msg = get_msg(self.bot_cfg, "volume_success", level=level)
        await interaction.response.send_message(msg, ephemeral=False)

    @app_commands.command(name="loop", description="再生のループモードを設定します。")
    @app_commands.describe(mode="ループモード (off: ループなし, song: 現在の曲をループ, queue: キュー全体をループ)")
    async def loop(self, interaction: discord.Interaction, mode: Literal["off", "song", "queue"]):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        state.loop_mode = mode  # ループモードを更新
        msg = get_msg(self.bot_cfg, "loop_success", mode=mode)
        await interaction.response.send_message(msg, ephemeral=False)

        # now_playingメッセージのViewも更新 (ボタン表示が変わるため)
        if state.now_playing_message and isinstance(state.now_playing_message.view, MusicPlayerView):
            state.now_playing_message.view._update_buttons()  # type: ignore
            try:
                await state.now_playing_message.edit(view=state.now_playing_message.view)
            except discord.HTTPException:
                pass  # 編集失敗は許容

    @app_commands.command(name="shuffle", description="再生キュー内の曲順をランダムにシャッフルします。")
    async def shuffle(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if state.song_queue.empty():
            msg = get_msg(self.bot_cfg, "shuffle_empty")
            return await interaction.response.send_message(msg, ephemeral=True)

        # キューの中身を一度リストに取り出してシャッフル
        queue_list: List[SongItem] = []
        while not state.song_queue.empty():
            queue_list.append(state.song_queue.get_nowait())

        random.shuffle(queue_list)  # リストをランダムに並び替え

        # シャッフルされたリストから新しいキューを再構築
        new_queue: asyncio.Queue[SongItem] = asyncio.Queue()
        for item in queue_list:
            await new_queue.put(item)
        state.song_queue = new_queue

        msg = get_msg(self.bot_cfg, "shuffle_success")
        await interaction.response.send_message(msg, ephemeral=False)

    @app_commands.command(name="remove", description="再生キューから指定した番号の曲を削除します。")
    @app_commands.describe(index="削除したい曲のキュー内での番号 (queueコマンドで確認できます)")
    async def remove(self, interaction: discord.Interaction, index: int):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if index <= 0 or index > state.song_queue.qsize():  # 無効なインデックスの場合
            msg = get_msg(self.bot_cfg, "remove_invalid_index", max_index=state.song_queue.qsize())
            return await interaction.response.send_message(msg, ephemeral=True)

        # 指定されたインデックスの曲を削除するためにキューを再構築
        temp_queue_list: List[SongItem] = []
        removed_song: Optional[SongItem] = None
        for i in range(state.song_queue.qsize()):
            song = state.song_queue.get_nowait()
            if i + 1 == index:  # ユーザーは1から始まる番号で指定するため、i+1で比較
                removed_song = song
            else:
                temp_queue_list.append(song)

        # 新しいキューに再格納
        new_queue: asyncio.Queue[SongItem] = asyncio.Queue()
        for song_in_list in temp_queue_list:
            await new_queue.put(song_in_list)
        state.song_queue = new_queue

        if removed_song:
            msg = get_msg(self.bot_cfg, "remove_success", song_title=removed_song.title)
            await interaction.response.send_message(msg, ephemeral=False)
        else:  # 通常ここには到達しないはず (インデックスチェックがあるため)
            msg = get_msg(self.bot_cfg, "remove_fail")
            await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="clearqueue",
                          description="再生キュー内の待機中の曲をすべて削除します（現在再生中の曲は停止しません）。")
    async def clearqueue(self, interaction: discord.Interaction):
        if not interaction.guild:
            msg = get_msg(self.bot_cfg, "server_only_command")
            return await interaction.response.send_message(msg, ephemeral=True)
        state = self._get_guild_state(interaction.guild)

        if state.song_queue.empty():
            msg = get_msg(self.bot_cfg, "clearqueue_empty")
            return await interaction.response.send_message(msg, ephemeral=True)

        count = state.song_queue.qsize()
        # 新しい空のキューで置き換えることでクリア
        new_queue: asyncio.Queue[SongItem] = asyncio.Queue()
        state.song_queue = new_queue

        msg = get_msg(self.bot_cfg, "clearqueue_success", count=count)
        await interaction.response.send_message(msg, ephemeral=False)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        """
        ボイスチャンネルの状態が変化したときに呼び出されるリスナー。
        例: ボットがチャンネル内で一人になった場合に自動で退出する処理。
        """
        if not member.guild or not self.bot.user:  # メンバーがサーバーに属しているか、ボットユーザーが存在するか確認
            return

        state = self._get_guild_state(member.guild)  # 該当ギルドのボイス状態を取得
        if not state.voice_client or not state.voice_client.is_connected():  # ボットが接続していなければ何もしない
            return

        # ボット自身がボイスチャンネルから切断された場合 (手動またはキックなど)
        if member.id == self.bot.user.id and after.channel is None:
            log_msg = get_msg(self.bot_cfg, "voice_bot_disconnected_log", guild_id=member.guild.id)
            logger.info(log_msg)
            await state.stop_playback()  # 再生を停止
            state.voice_client = None  # ボイスクライアント参照をクリア
            # 必要であれば voice_states からこのギルドのエントリを削除することも検討
            # self.voice_states.pop(member.guild.id, None)
            return

        # ボットがいるチャンネルで、ボット以外のユーザーがいなくなった場合 (ボットが一人ぼっちになった場合)
        # before.channel はメンバーが以前いたチャンネル、after.channel はメンバーが移動した先のチャンネル
        # state.voice_client.channel はボットが現在いるチャンネル
        if state.voice_client.channel == before.channel and \
                (after.channel is None or state.voice_client.channel.id != after.channel.id):
            # チャンネル内のメンバー数を確認 (ボット自身も含む)
            # チャンネルにメンバーがまだいるか、かつボットだけかを確認
            if state.voice_client.channel and len(state.voice_client.channel.members) == 1 and \
                    state.voice_client.channel.members[0].id == self.bot.user.id:

                auto_leave_delay = self.bot_cfg.get("music_cog_settings", {}).get("auto_leave_delay_seconds", 60)

                log_msg_disconnecting = get_msg(self.bot_cfg, "voice_alone_disconnecting_log",
                                                guild_id=member.guild.id, channel_name=state.voice_client.channel.name)
                logger.info(log_msg_disconnecting)

                await asyncio.sleep(auto_leave_delay)  # 設定された秒数だけ待機

                # 再度状態を確認 (待機中に他のユーザーが参加した可能性もあるため)
                if state.voice_client and state.voice_client.is_connected() and \
                        state.voice_client.channel and len(state.voice_client.channel.members) == 1:
                    log_msg_inactive = get_msg(self.bot_cfg, "voice_disconnect_inactivity_log",
                                               guild_id=member.guild.id)
                    logger.info(log_msg_inactive)
                    await state.stop_playback()  # 再生停止
                    await state.voice_client.disconnect()  # ボイスチャンネルから切断
                    state.voice_client = None  # ボイスクライアント参照をクリア


async def setup(bot: discord.Client):  # discord.Client を受け取るように変更
    """
    MusicCogをボットにセットアップ（追加）するための非同期関数。
    メインのボットファイルから `load_extension` で呼び出されます。
    """
    # MusicCogのインスタンスを作成 (botインスタンスを渡すことで、Cog内からbot.cfg等にアクセス可能に)
    music_cog_instance = MusicCog(bot)
    await bot.add_cog(music_cog_instance)  # 作成したCogインスタンスをボットに追加
    logger.info("MusicCogが正常にロードされ、設定が適用されました。")