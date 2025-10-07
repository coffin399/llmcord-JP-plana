import asyncio
import logging
import re
from asyncio import Queue
from typing import List

from discord import Client, TextChannel


class DiscordLogHandler(logging.Handler):
    """
    Pythonのログを複数のDiscordチャンネルにバッチ送信するためのカスタムロギングハンドラ。
    レートリミットを回避するため、ログをキューに溜め、定期的にまとめて送信します。
    """

    def __init__(self, bot: Client, channel_ids: List[int], interval: float = 5.0):
        super().__init__()
        self.bot = bot
        self.channel_ids = channel_ids
        self.interval = interval

        self.queue: Queue[str] = Queue()
        self.channels: List[TextChannel] = []  # チャンネルオブジェクトをリストで保持
        self._closed = False

        self._task = self.bot.loop.create_task(self._log_sender_loop())

    def add_channel(self, channel_id: int):
        """ログ送信先チャンネルを動的に追加し、即時反映させる。"""
        if channel_id not in self.channel_ids:
            self.channel_ids.append(channel_id)
            if self.bot.is_ready():
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, TextChannel):
                    self.channels.append(channel)
                    print(f"DiscordLogHandler: Immediately added and activated channel {channel_id}.")
                else:
                    print(
                        f"DiscordLogHandler: Added channel ID {channel_id}, but it's not a valid text channel or not found yet.")
            else:
                self.channels = []
                print(f"DiscordLogHandler: Added channel ID {channel_id}. Will be activated once bot is ready.")

    def remove_channel(self, channel_id: int):
        """ログ送信先チャンネルを動的に削除し、即時反映させる。"""
        if channel_id in self.channel_ids:
            self.channel_ids.remove(channel_id)
            self.channels = [ch for ch in self.channels if ch.id != channel_id]
            print(f"DiscordLogHandler: Immediately removed and deactivated channel {channel_id}.")

    def emit(self, record: logging.LogRecord):
        """
        ログレコードをフォーマットし、センシティブ情報を伏字化してキューに追加する。
        """
        if self._closed:
            return
        msg = self.format(record)
        msg = self._sanitize_log_message(msg)
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            print("DiscordLogHandler: Log queue is full, dropping message.")


    def _get_display_chars(self, text: str, count: int = 2) -> str:
        """
        テキストから表示用の文字を取得する。
        引用符や記号を除外して、実際の文字（英数字、日本語など）から指定数を取得。
        """
        # 引用符や一般的な記号を除外
        cleaned = re.sub(r'^[「『"\'『»«‹›〈〉《》【】〔〕［］｛｝（）()［］\s]+', '', text)
        return cleaned[:count] if len(cleaned) >= count else text[:count]

    def _sanitize_log_message(self, message: str) -> str:
        """
        ログメッセージからセンシティブな情報を部分的に伏字化する。
        - Windowsのユーザーパス
        - Discord Gateway Session ID
        - Discordのサーバー名、ユーザー名、チャンネル名 (先頭文字のみ表示)
        - Discordの各種ID (完全に伏字化または部分的に伏字化)
        - ユーザーbioの内容
        """
        # Windowsユーザーパス
        message = re.sub(
            r'[A-Za-z]:\\Users\\[^\\]+\\[^\\]+',
            '********',
            message,
            flags=re.IGNORECASE
        )

        # Session ID
        message = re.sub(
            r'((?:Session ID:?|session)\s+)[a-f0-9]{32}',
            r'\1****',
            message,
            flags=re.IGNORECASE
        )

        # LLMCog形式: guild='サーバー名(ID or 匿名化済み)' -> guild='サー****(****)'
        message = re.sub(
            r"guild='([^']+)\([^)]+\)'",
            lambda m: f"guild='{self._get_display_chars(m.group(1), 2)}****(****)'",
            message
        )

        # LLMCog形式: author='ユーザー名(ID or 匿名化済み)' -> author='ユー****(****)'
        message = re.sub(
            r"author='([^']+)\([^)]+\)'",
            lambda m: f"author='{self._get_display_chars(m.group(1), 2)}****(****)'",
            message
        )

        # LLMCog形式: channel='チャンネル名(ID or 匿名化済み)' -> channel='チャ****(****)'
        message = re.sub(
            r"channel='([^']+)\([^)]+\)'",
            lambda m: f"channel='{self._get_display_chars(m.group(1), 2)}****(****)'",
            message
        )

        # MusicCog形式: Guild ID (サーバー名): -> Guild ****(サー****):
        message = re.sub(
            r"Guild (\d+) \(([^)]+)\):",
            lambda m: f"Guild ****({m.group(2)[:2]}****):",
            message
        )

        # IDのみなので完全匿名化を維持
        message = re.sub(
            r"Channel ID \d+ \(Guild ID \d+\)",
            "Channel ID **** (Guild ID ****)",
            message
        )

        # MusicCog形式: Connected to チャンネル名 -> Connected to チャ****
        message = re.sub(
            r"Connected to (.+)",
            lambda m: f"Connected to {m.group(1)[:2]}****",
            message
        )

        # BioManager形式: for user [ID] (ユーザー名) -> for user X**** (ユ****)
        message = re.sub(
            r"for user (\d+) \(([^)]+)\)",
            lambda m: f"for user {m.group(1)[:1]}**** ({m.group(2)[:1]}****)",
            message
        )

        # BioManager形式: for user [ID] -> for user X**** (括弧がない場合)
        message = re.sub(
            r"for user (\d+)(?!\s*\()",
            lambda m: f"for user {m.group(1)[:1]}****",
            message
        )

        # BioManager形式: Content: 'ユーザーbio' -> Content: 'ユ****'
        message = re.sub(
            r"Content: '([^']+)'",
            lambda m: f"Content: '{m.group(1)[:1]}****'",
            message
        )

        # Twitch通知形式: ギルド [ID] のチャンネル [ID] -> ギルド X**** のチャンネル Y****
        message = re.sub(
            r"ギルド (\d+) のチャンネル (\d+)",
            lambda m: f"ギルド {m.group(1)[:1]}**** のチャンネル {m.group(2)[:1]}****",
            message
        )

        # メッセージID形式: message ID: 1425082992111386664 -> message ID: 1****
        message = re.sub(
            r"message ID:? (\d+)",
            lambda m: f"message ID: {m.group(1)[:1]}****",
            message,
            flags=re.IGNORECASE
        )

        # 一般的なDiscord ID (18-19桁の数字) -> X****
        message = re.sub(
            r"(?<!\d)(\d{17,19})(?!\d)",
            lambda m: f"{m.group(1)[:1]}****",
            message
        )

        return message

    async def _process_queue(self):
        """キューに溜まったログを全て取り出し、結合して登録済みの全チャンネルに送信する。"""
        if self.queue.empty():
            return

        if not self.channels or len(self.channels) != len(self.channel_ids):
            found_channels = []
            for cid in self.channel_ids:
                channel = self.bot.get_channel(cid)
                if channel and isinstance(channel, TextChannel):
                    found_channels.append(channel)
                else:
                    print(f"DiscordLogHandler: Warning - Channel with ID {cid} not found or is not a text channel.")
            self.channels = found_channels

        if not self.channels:
            if self.channel_ids:
                print(f"DiscordLogHandler: No valid channels found for IDs {self.channel_ids}. Clearing log queue.")
            while not self.queue.empty():
                self.queue.get_nowait()
            return

        records = []
        while not self.queue.empty():
            records.append(self.queue.get_nowait())
        if not records:
            return

        full_log_message = "\n".join(records)
        chunk_size = 1980
        chunks = [full_log_message[i:i + chunk_size] for i in range(0, len(full_log_message), chunk_size)]

        for channel in self.channels:
            for chunk in chunks:
                try:
                    await channel.send(f"```py\n{chunk}\n```", silent=True)
                except Exception as e:
                    print(f"Failed to send log to Discord channel {channel.id}: {e}")

    async def _log_sender_loop(self):
        """バックグラウンドで定期的にキュー処理を呼び出すループ。"""
        try:
            await self.bot.wait_until_ready()
            while not self._closed:
                await self._process_queue()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"Error in DiscordLogHandler loop: {e}")
        finally:
            await self._process_queue()

    def close(self):
        """ハンドラを閉じる。"""
        if self._closed:
            return
        self._closed = True
        if self._task:
            self._task.cancel()
        if self.bot.loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._process_queue(), self.bot.loop)
                future.result(timeout=self.interval)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
                print(f"Error sending remaining logs on close: {e}")
        super().close()