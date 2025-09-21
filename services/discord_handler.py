import logging
import asyncio
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
        self.channels: List[TextChannel] = [] # チャンネルオブジェクトをリストで保持
        self._closed = False

        self._task = self.bot.loop.create_task(self._log_sender_loop())

    def emit(self, record: logging.LogRecord):
        """
        ログレコードをフォーマットし、キューに追加する。
        """
        if self._closed:
            return
        msg = self.format(record)
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            print("DiscordLogHandler: Log queue is full, dropping message.")

    async def _process_queue(self):
        """キューに溜まったログを全て取り出し、結合して登録済みの全チャンネルに送信する。"""
        if self.queue.empty():
            return

        # チャンネルオブジェクトのリストをまだ作成していなければ作成する
        if not self.channels and self.channel_ids:
            found_channels = []
            for cid in self.channel_ids:
                channel = self.bot.get_channel(cid)
                if channel and isinstance(channel, TextChannel):
                    found_channels.append(channel)
                else:
                    # チャンネルが見つからない、またはテキストチャンネルでない場合は警告
                    print(f"DiscordLogHandler: Warning - Channel with ID {cid} not found or is not a text channel.")
            self.channels = found_channels

        # 送信先の有効なチャンネルが一つもない場合は、キューをクリアして終了
        if not self.channels:
            if self.channel_ids:
                print(f"DiscordLogHandler: No valid channels found for IDs {self.channel_ids}. Clearing log queue.")
            while not self.queue.empty():
                self.queue.get_nowait()
            return

        # キューから全てのログを取り出す
        records = []
        while not self.queue.empty():
            records.append(self.queue.get_nowait())
        if not records:
            return

        full_log_message = "\n".join(records)
        chunk_size = 1980
        chunks = [full_log_message[i:i + chunk_size] for i in range(0, len(full_log_message), chunk_size)]

        # 全ての登録済みチャンネルに送信
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