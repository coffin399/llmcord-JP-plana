import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp
import os
import uuid
import asyncio
import time
from functools import partial

# --- 設定項目 ---
# Discordのファイルアップロード上限 (デフォルトは8MB)
MAX_FILE_SIZE_BYTES = 8 * 1024 * 1024
DOWNLOAD_DIR = "temp_audio"


# --- 設定項目ここまで ---

class YtdlpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.progress_message = None
        self.last_update_time = 0
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    def progress_hook(self, d):
        """yt-dlpの進捗フック（別スレッドから呼び出される）"""
        if d['status'] == 'downloading':
            # 更新頻度を制限（Discordのレートリミット対策）
            current_time = time.time()
            if current_time - self.last_update_time < 1.5:
                return

            total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
            if total_bytes is None:
                return

            downloaded_bytes = d.get('downloaded_bytes', 0)
            percentage = downloaded_bytes / total_bytes * 100

            # プログレスバーの文字列を生成
            bar = '█' * int(percentage / 5) + '─' * (20 - int(percentage / 5))
            progress_text = (
                f"**ダウンロード中...**\n"
                f"`[{bar}] {percentage:.2f}%`\n"
                f"`{downloaded_bytes / (1024 * 1024):.2f}MB / {total_bytes / (1024 * 1024):.2f}MB`"
            )

            # メッセージ編集のコルーチンをスレッドセーフに呼び出す
            coro = self.progress_message.edit(content=progress_text)
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
            self.last_update_time = current_time

    @app_commands.command(name="ytdlp", description="YouTubeから音声をダウンロードしてアップロードします。")
    @app_commands.describe(
        query="YouTubeのURLまたは検索キーワード",
        audio_format="出力する音声フォーマット"
    )
    @app_commands.choices(audio_format=[
        app_commands.Choice(name="MP3", value="mp3"),
        app_commands.Choice(name="M4A", value="m4a"),
        app_commands.Choice(name="Opus", value="opus"),
        app_commands.Choice(name="FLAC", value="flac"),
        app_commands.Choice(name="WAV", value="wav"),
    ])
    async def ytdlp(self, interaction: discord.Interaction, query: str, audio_format: str):
        await interaction.response.defer(thinking=True)

        # 一意なファイル名を生成
        unique_id = uuid.uuid4()
        output_filename = f"{unique_id}.{audio_format}"
        output_path = os.path.join(DOWNLOAD_DIR, output_filename)

        # 変換前の一時ファイル名を保持するための変数を初期化
        temp_file_ext = None

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': os.path.join(DOWNLOAD_DIR, f"{unique_id}.%(ext)s"),  # 変換前の一時ファイル
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': audio_format,
                'preferredquality': '192',  # mp3の場合の品質
            }],
            'noplaylist': True,  # プレイリストの場合は最初の1件のみ
            'default_search': 'ytsearch',  # URLでない場合はYouTubeで検索
            'quiet': True,
            'no_warnings': True,
        }

        try:
            # --- 1. 情報取得とファイルサイズチェック ---
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = await asyncio.to_thread(ydl.extract_info, query, download=False)

                if 'entries' in info:
                    info = info['entries'][0]

                best_audio_format = next((f for f in info['formats'] if f['format_id'] == info['format_id']), None)
                filesize = best_audio_format.get('filesize') or best_audio_format.get('filesize_approx')
                temp_file_ext = best_audio_format.get('ext')  # 変換前ファイルの拡張子を保存

                if filesize and filesize > MAX_FILE_SIZE_BYTES:
                    await interaction.followup.send(
                        f"エラー: ファイルサイズがDiscordのアップロード上限を超えています。\n"
                        f"推定サイズ: **{filesize / (1024 * 1024):.2f}MB** (上限: {MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f}MB)",
                        ephemeral=True
                    )
                    return

                video_title = info.get('title', 'Unknown Title')
                self.progress_message = await interaction.followup.send(
                    f"**{video_title}** のダウンロード準備をしています...")

            # --- 2. ダウンロード実行 ---
            ydl_opts['progress_hooks'] = [self.progress_hook]
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                await asyncio.to_thread(ydl.download, [info['webpage_url']])

            # --- 3. アップロードと後処理 ---
            if not os.path.exists(output_path):
                await self.progress_message.edit(
                    content="エラー: ファイルの変換に失敗しました。FFmpegがインストールされているか確認してください。")
                return

            final_size = os.path.getsize(output_path)
            if final_size > MAX_FILE_SIZE_BYTES:
                await self.progress_message.edit(
                    content=f"エラー: ダウンロード後のファイルサイズが上限を超えました。\n"
                            f"ファイルサイズ: **{final_size / (1024 * 1024):.2f}MB**"
                )
                os.remove(output_path)
                return

            # ===== 変更点 =====
            # メッセージを「アップロード中」に編集
            await self.progress_message.edit(content="アップロード中...")

            # ファイルを新しいメッセージとして送信
            discord_file = discord.File(output_path, filename=f"{video_title}.{audio_format}")
            await interaction.followup.send(file=discord_file)

            # 元のプログレスメッセージを「完了」に編集
            await self.progress_message.edit(content=f"✅ **{video_title}** のダウンロードとアップロードが完了しました。")
            # ==================

        except yt_dlp.utils.DownloadError as e:
            error_message = f"エラー: ダウンロードに失敗しました。\n`{str(e)}`"
            if self.progress_message:
                await self.progress_message.edit(content=error_message)
            else:
                await interaction.followup.send(error_message, ephemeral=True)
        except Exception as e:
            error_message = f"予期せぬエラーが発生しました。\n`{e}`"
            if self.progress_message:
                await self.progress_message.edit(content=error_message)
            else:
                await interaction.followup.send(error_message, ephemeral=True)
        finally:
            # 一時ファイルを確実に削除
            if os.path.exists(output_path):
                os.remove(output_path)
            # yt-dlpが生成した変換前の一時ファイルも削除しておく
            if temp_file_ext:
                temp_file_path = os.path.join(DOWNLOAD_DIR, f"{unique_id}.{temp_file_ext}")
                if os.path.exists(temp_file_path):
                    os.remove(temp_file_path)


async def setup(bot: commands.Bot):
    await bot.add_cog(YtdlpCog(bot))