# cogs/music_cog.py
from __future__ import annotations

import asyncio
import logging
import random  # for queue shuffle (optional)
from typing import TYPE_CHECKING, Dict, Optional, List

import discord
from discord import app_commands
from discord.ext import commands
import yt_dlp

if TYPE_CHECKING:
    # main.py の DiscordLLMBot クラスをインポート (型チェック用)
    # 循環参照を避けるため、実際の実行時には使われないようにする
    from main import DiscordLLMBot

# FFmpeg と yt-dlp のオプション
YTDL_FORMAT_OPTIONS = {
    'format': 'bestaudio/best',  # Opusが利用可能なら 'bestaudio[ext=opus]/bestaudio/best' なども検討
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,  # playコマンドでプレイリストURLが渡された場合に対応するためFalse推奨。制御はコード側で。
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',  # IPv4を優先
    'extract_flat': 'discard_in_playlist',  # プレイリスト内の個別の曲情報を取得
    'lazy_playlist': True,  # プレイリスト全体の情報を一度に取得しない
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn',  # ビデオなし
}


class Song:
    def __init__(self, source_url: str, data: dict, requester: discord.Member):
        self.source_url: str = source_url  # ストリーミング用URL
        self.title: str = data.get('title', '不明な曲')
        self.webpage_url: Optional[str] = data.get('webpage_url')
        self.duration: Optional[int] = data.get('duration')  # 秒単位
        self.thumbnail: Optional[str] = data.get('thumbnail')
        self.uploader: Optional[str] = data.get('uploader')
        self.requester: discord.Member = requester

    def create_embed(self, title_prefix: str = "") -> discord.Embed:
        embed = discord.Embed(
            title=f"{title_prefix}{self.title}",
            description=f"[リンク]({self.webpage_url})" if self.webpage_url else "リンクなし",
            color=discord.Color.green() if title_prefix else discord.Color.blurple()
        )
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)

        duration_str = "不明"
        if self.duration:
            m, s = divmod(self.duration, 60)
            h, m = divmod(m, 60)
            if h > 0:
                duration_str = f"{h:d}:{m:02d}:{s:02d}"
            else:
                duration_str = f"{m:02d}:{s:02d}"

        embed.add_field(name="長さ", value=duration_str, inline=True)
        embed.add_field(name="追加者", value=self.requester.mention, inline=True)
        if self.uploader:
            embed.add_field(name="アップローダー", value=self.uploader, inline=True)
        return embed


class MusicQueue(asyncio.Queue):
    def __init__(self, maxsize: int = 0):
        super().__init__(maxsize)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(self._queue)[item.start:item.stop:item.step]
        return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self._queue)

    def get_song_titles(self, limit: int = 10) -> List[str]:
        return [
            f"{i + 1}. {song.title}"
            for i, song in enumerate(list(self._queue)[:limit])
        ]


class MusicCog(commands.Cog, name="音楽再生"):
    def __init__(self, bot: DiscordLLMBot):  # main.pyのDiscordLLMBotクラスを型ヒント
        self.bot: DiscordLLMBot = bot
        self.voice_clients: Dict[int, discord.VoiceClient] = {}
        self.queues: Dict[int, MusicQueue] = {}
        self.current_song: Dict[int, Song] = {}
        self.ytdl = yt_dlp.YoutubeDL(YTDL_FORMAT_OPTIONS)
        self.ffmpeg_executable: str = self.bot.cfg.get("ffmpeg_path", "ffmpeg")

    def get_queue(self, guild_id: int) -> MusicQueue:
        if guild_id not in self.queues:
            self.queues[guild_id] = MusicQueue()
        return self.queues[guild_id]

    async def _join_voice_channel(self, interaction: discord.Interaction) -> Optional[discord.VoiceClient]:
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("まずボイスチャンネルに参加してください。", ephemeral=True)
            return None

        channel = interaction.user.voice.channel
        vc: Optional[discord.VoiceClient] = interaction.guild.voice_client

        if vc:
            if vc.channel.id == channel.id:
                return vc  # 既に同じチャンネルにいる
            try:
                await vc.move_to(channel)
                logging.info(f"Bot moved to voice channel: {channel.name} in guild {interaction.guild.name}")
                # interaction.responseが既に使われている場合があるので、ここではメッセージを送らない
                return vc
            except asyncio.TimeoutError:
                await interaction.followup.send(f"`{channel.name}` への移動にタイムアウトしました。", ephemeral=True)
                return None
        else:
            try:
                vc = await channel.connect()
                self.voice_clients[interaction.guild.id] = vc
                logging.info(f"Bot connected to voice channel: {channel.name} in guild {interaction.guild.name}")
                return vc
            except asyncio.TimeoutError:
                await interaction.followup.send(f"`{channel.name}` への接続にタイムアウトしました。", ephemeral=True)
                return None
            except discord.ClientException as e:
                await interaction.followup.send(f"ボイスチャンネルへの接続に失敗しました: {e}", ephemeral=True)
                return None

    async def _play_next_song_after_hook(self, guild_id: int, error=None):
        if error:
            logging.error(f"Player error in guild {guild_id}: {error}")
            # TODO: エラーをチャンネルに通知する処理

        # current_songをNoneにする前に、キューから次の曲を取得できればそれが新しいcurrent_songになる
        # self.current_song.pop(guild_id, None)
        # asyncio.create_taskではなく、self.bot.loop.create_taskを使う
        self.bot.loop.create_task(self._play_next_song(guild_id))

    async def _play_next_song(self, guild_id: int):
        vc = self.voice_clients.get(guild_id)
        if not vc or not vc.is_connected():
            logging.warning(f"Voice client not found or not connected for guild {guild_id} in _play_next_song.")
            # ここでキューやカレントソングをクリアするなどの後処理が必要かもしれない
            self.current_song.pop(guild_id, None)
            if guild_id in self.queues:
                self.queues[guild_id].clear()
            return

        if vc.is_playing() or vc.is_paused():
            return  # 既に何か再生中/一時停止中

        queue = self.get_queue(guild_id)
        if queue.empty():
            self.current_song.pop(guild_id, None)
            # TODO: 一定時間後に自動退出する機能など
            # await asyncio.sleep(300) # 5分
            # if not vc.is_playing() and vc.is_connected() and queue.empty():
            #     await vc.disconnect()
            #     self.voice_clients.pop(guild_id, None)
            return

        try:
            song_to_play: Song = await queue.get()
        except asyncio.QueueEmpty:  # 万が一
            self.current_song.pop(guild_id, None)
            return

        self.current_song[guild_id] = song_to_play

        try:
            player = discord.FFmpegPCMAudio(song_to_play.source_url, executable=self.ffmpeg_executable,
                                            **FFMPEG_OPTIONS)
            source = discord.PCMVolumeTransformer(player)  # 音量調整のため

            vc.play(source, after=lambda e: self.bot.loop.create_task(self._play_next_song_after_hook(guild_id, e)))

            # 現在再生中の曲情報を、playコマンドが実行されたチャンネルなどに通知したい場合
            # interactionオブジェクトがないため、最後にコマンドが使われたチャンネルを記憶しておくなどの工夫が必要
            # 例: self.last_played_channel[guild_id] = interaction.channel
            # if self.last_played_channel.get(guild_id):
            #    await self.last_played_channel[guild_id].send(embed=song_to_play.create_embed("再生開始: "))
            logging.info(f"Playing '{song_to_play.title}' in guild {guild_id}")

        except Exception as e:
            logging.error(f"Error playing song {song_to_play.title}: {e}", exc_info=True)
            # TODO: エラーをチャンネルに通知
            self.current_song.pop(guild_id, None)  # エラー時は現在の曲をクリア
            self.bot.loop.create_task(self._play_next_song(guild_id))  # 次の曲へ

    @app_commands.command(name="join", description="ボットを指定のボイスチャンネルに参加させます。")
    async def join_command(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        vc = await self._join_voice_channel(interaction)
        if vc:
            await interaction.followup.send(f"`{vc.channel.name}` に接続しました。", ephemeral=True)
        # エラーメッセージは _join_voice_channel 内で送信されるか、ここで再度送る

    @app_commands.command(name="leave", description="ボットをボイスチャンネルから退出させます。")
    async def leave_command(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            queue = self.get_queue(interaction.guild.id)
            queue.clear()
            self.current_song.pop(interaction.guild.id, None)
            if vc.is_playing():
                vc.stop()  # 再生を停止
            await vc.disconnect()
            self.voice_clients.pop(interaction.guild.id, None)
            await interaction.followup.send("ボイスチャンネルから退出しました。", ephemeral=True)
        else:
            await interaction.followup.send("ボットはボイスチャンネルに接続していません。", ephemeral=True)

    @app_commands.command(name="play", description="指定された曲を再生します (URLまたは検索語)。")
    @app_commands.describe(query="再生したい曲のURLまたは検索キーワード")
    async def play_command(self, interaction: discord.Interaction, query: str):
        await interaction.response.defer(thinking=True, ephemeral=False)  # ユーザーに見えるように

        vc = await self._join_voice_channel(interaction)
        if not vc:
            # _join_voice_channel でエラーメッセージが送信されているはず
            # interaction.response.defer が使われているので followup で
            if not interaction.response.is_done():  # まだ何も送ってなければ
                await interaction.followup.send("ボイスチャンネルへの参加に失敗しました。", ephemeral=True)
            return

        try:
            # yt_dlp はブロッキング操作なので、executor で実行
            loop = asyncio.get_event_loop()
            data = await loop.run_in_executor(None, lambda: self.ytdl.extract_info(query, download=False))
        except Exception as e:
            logging.error(f"Error fetching song info for query '{query}': {e}", exc_info=True)
            await interaction.followup.send(f"曲情報の取得に失敗しました: ```{e}```")
            return

        if not data:
            await interaction.followup.send("曲情報が見つかりませんでした。")
            return

        songs_to_add: List[Song] = []
        is_playlist = 'entries' in data

        if is_playlist:
            # プレイリストの場合、最大100曲まで処理 (設定可能にしても良い)
            entries = data['entries'][:100]
            for entry in entries:
                if entry and 'url' in entry:  # ストリームURLがあるものだけ
                    songs_to_add.append(Song(source_url=entry['url'], data=entry, requester=interaction.user))
            if not songs_to_add:
                await interaction.followup.send("プレイリストから有効な曲が見つかりませんでした。")
                return
            playlist_title = data.get('title', '無題のプレイリスト')
            embed_title = f"プレイリスト '{playlist_title}' ({len(songs_to_add)}曲) をキューに追加"
        else:  # 単一の曲
            if 'url' in data:
                songs_to_add.append(Song(source_url=data['url'], data=data, requester=interaction.user))
                embed_title = "キューに追加しました"
            else:
                await interaction.followup.send("有効なストリームURLが見つかりませんでした。")
                return

        if not songs_to_add:  # 上のチェックでほぼカバーされるはず
            await interaction.followup.send("キューに追加できる曲がありませんでした。")
            return

        queue = self.get_queue(interaction.guild.id)
        for song_obj in songs_to_add:
            await queue.put(song_obj)

        # 最初の曲の情報を表示
        first_song_embed = songs_to_add[0].create_embed(title_prefix="追加: ")
        if is_playlist:
            first_song_embed.title = embed_title  # プレイリスト全体のタイトルで上書き
            first_song_embed.description = f"最初の曲: [{songs_to_add[0].title}]({songs_to_add[0].webpage_url})\n他 {len(songs_to_add) - 1} 曲"

        await interaction.followup.send(embed=first_song_embed)

        if not vc.is_playing() and not vc.is_paused():
            self.bot.loop.create_task(self._play_next_song(interaction.guild.id))

    @app_commands.command(name="stop", description="再生を停止し、キューをクリアします。")
    async def stop_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.is_connected():
            queue = self.get_queue(interaction.guild.id)
            queue.clear()
            self.current_song.pop(interaction.guild.id, None)
            if vc.is_playing() or vc.is_paused():
                vc.stop()
            await interaction.followup.send("再生を停止し、キューをクリアしました。")
        else:
            await interaction.followup.send("ボットはボイスチャンネルに接続していないか、再生中ではありません。")

    @app_commands.command(name="skip", description="現在再生中の曲をスキップします。")
    async def skip_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        vc = interaction.guild.voice_client
        current = self.current_song.get(interaction.guild.id)

        if vc and vc.is_playing() and current:
            skipped_song_title = current.title
            vc.stop()  # afterフックが次の曲を再生
            await interaction.followup.send(
                embed=discord.Embed(description=f"⏭️ `{skipped_song_title}` をスキップしました。",
                                    color=discord.Color.light_grey()))
        else:
            await interaction.followup.send("スキップする曲がありません。", ephemeral=True)

    @app_commands.command(name="pause", description="再生を一時停止します。")
    async def pause_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.is_playing():
            vc.pause()
            await interaction.followup.send("⏸️ 再生を一時停止しました。")
        else:
            await interaction.followup.send("一時停止する曲がありません。")

    @app_commands.command(name="resume", description="一時停止中の再生を再開します。")
    async def resume_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        vc = interaction.guild.voice_client
        if vc and vc.is_paused():
            vc.resume()
            await interaction.followup.send("▶️ 再生を再開しました。")
        else:
            await interaction.followup.send("再開する曲がありません。")

    @app_commands.command(name="queue", description="現在の再生キューを表示します。")
    async def queue_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        queue = self.get_queue(interaction.guild.id)
        current = self.current_song.get(interaction.guild.id)

        if not current and queue.empty():
            await interaction.followup.send("キューは空です。", ephemeral=True)
            return

        embed = discord.Embed(title="再生キュー", color=discord.Color.blue())
        if current:
            embed.add_field(name="再生中",
                            value=f"🎶 [{current.title}]({current.webpage_url}) (追加者: {current.requester.mention})",
                            inline=False)

        if not queue.empty():
            queue_list_str = []
            for i, song in enumerate(list(queue)[:10]):  # 最大10件表示
                queue_list_str.append(
                    f"{i + 1}. [{song.title}]({song.webpage_url or ''}) (追加者: {song.requester.mention})")

            if queue_list_str:
                embed.add_field(name=f"次の曲 ({len(queue)}曲)", value="\n".join(queue_list_str), inline=False)
            if len(queue) > 10:
                embed.set_footer(text=f"他 {len(queue) - 10} 曲がキューにあります。")

        if not embed.fields:  # 再生中でもなくキューも空 (実際には上のifで弾かれるはず)
            await interaction.followup.send("キューは空です。", ephemeral=True)
            return

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="nowplaying", description="現在再生中の曲情報を表示します。")
    async def nowplaying_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        current = self.current_song.get(interaction.guild.id)
        if not current:
            await interaction.followup.send("現在再生中の曲はありません。", ephemeral=True)
            return

        await interaction.followup.send(embed=current.create_embed("再生中: "))

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        # ボットがVCにいて、ボット以外のメンバーがいなくなった場合、一定時間後に退出する処理
        if member.bot or not member.guild:
            return

        vc = member.guild.voice_client
        if not vc or not vc.is_connected():
            return

        # ボット自身が切断された場合
        if member.id == self.bot.user.id and not after.channel:
            logging.info(f"Bot disconnected from voice channel in guild {member.guild.name}")
            self.voice_clients.pop(member.guild.id, None)
            queue = self.get_queue(member.guild.id)
            queue.clear()
            self.current_song.pop(member.guild.id, None)
            return

        # チャンネル内のボット以外のユーザー数をチェック
        # ボットが接続しているチャンネルでメンバーの移動があった場合
        if before.channel == vc.channel or after.channel == vc.channel:
            # ボットがいるチャンネルのメンバーリストを取得 (ボット自身を除く)
            human_members_in_vc = [m for m in vc.channel.members if not m.bot]
            if not human_members_in_vc:  # ボットしか残っていない場合
                logging.info(f"No human users left in {vc.channel.name} with bot. Consider auto-disconnect timer.")
                # await asyncio.sleep(self.bot.cfg.get("music_auto_leave_delay", 300)) # configから遅延時間取得
                # if vc.is_connected() and not [m for m in vc.channel.members if not m.bot]:
                #     await vc.disconnect() # ... (leaveコマンドと同様の後処理)
                pass

    async def cog_unload(self):
        # Cogがアンロードされるときのクリーンアップ
        logging.info("MusicCog is being unloaded. Disconnecting from voice channels.")
        for guild_id, vc in list(self.voice_clients.items()):  # イテレート中に変更する可能性があるのでリストのコピー
            if vc and vc.is_connected():
                if vc.is_playing() or vc.is_paused():
                    vc.stop()
                await vc.disconnect()
            self.voice_clients.pop(guild_id, None)
            if guild_id in self.queues:
                self.queues[guild_id].clear()
            self.current_song.pop(guild_id, None)
        logging.info("MusicCog unloaded and voice clients disconnected.")


async def setup(bot: DiscordLLMBot):  # main.pyのDiscordLLMBotクラスを型ヒント
    music_cog = MusicCog(bot)
    await bot.add_cog(music_cog)
    logging.info("MusicCog がロードされ、ボットに追加されました。")