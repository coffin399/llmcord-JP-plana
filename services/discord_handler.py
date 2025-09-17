# discord_handler.py

import logging
import asyncio
from asyncio import Queue
from discord import Client, TextChannel, Embed, Colour


class DiscordLogHandler(logging.Handler):
    """
    PythonのログをDiscordチャンネルにバッチ送信するためのカスタムロギングハンドラ。
    レートリミットを回避するため、ログをキューに溜め、定期的にまとめて送信します。
    """

    def __init__(self, bot: Client, channel_id: int, interval: float = 5.0):
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id
        self.interval = interval

        self.queue: Queue[str] = Queue()
        self.channel: TextChannel = None
        self._closed = False

        # バックグラウンドでキューを処理するタスクを開始
        self._task = self.bot.loop.create_task(self._log_sender_loop())

    def emit(self, record: logging.LogRecord):
        """
        ログレコードをフォーマットし、キューに追加する。
        このメソッドは同期的コンテキストから呼ばれるため、ブロッキング処理は行わない。
        """
        if self._closed:
            return

        msg = self.format(record)
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            # キューが満杯になることは稀だが、念のため
            print("DiscordLogHandler: Log queue is full, dropping message.")

    async def _process_queue(self):
        """キューに溜まったログを全て取り出し、結合してDiscordに送信する。"""
        if self.queue.empty():
            return

        # チャンネルオブジェクトをまだ取得していなければ取得する
        if self.channel is None:
            self.channel = self.bot.get_channel(self.channel_id)
            if self.channel is None:
                # チャンネルが見つからない場合、キューをクリアしてエラーを防ぐ
                print(f"DiscordLogHandler: Channel with ID {self.channel_id} not found. Clearing log queue.")
                while not self.queue.empty():
                    self.queue.get_nowait()
                return

        # キューから全てのログを取り出す
        records = []
        while not self.queue.empty():
            records.append(self.queue.get_nowait())

        if not records:
            return

        # ログメッセージを結合
        full_log_message = "\n".join(records)

        # Discordの文字数制限(2000)を考慮してメッセージを分割
        # コードブロックの```py\n ... \n```も考慮して1980文字程度に収める
        chunk_size = 1980
        chunks = [full_log_message[i:i + chunk_size] for i in range(0, len(full_log_message), chunk_size)]

        for chunk in chunks:
            try:
                # silent=Trueで非通知メッセージとして送信
                await self.channel.send(f"```py\n{chunk}\n```", silent=True)
            except Exception as e:
                print(f"Failed to send log to Discord: {e}")

    async def _log_sender_loop(self):
        """バックグラウンドで定期的にキュー処理を呼び出すループ。"""
        try:
            # ボットが完全に準備完了になるまで待つ
            await self.bot.wait_until_ready()
            while not self._closed:
                await self._process_queue()
                await asyncio.sleep(self.interval)
        except asyncio.CancelledError:
            # タスクがキャンセルされた場合はループを抜ける
            pass
        except Exception as e:
            print(f"Error in DiscordLogHandler loop: {e}")
        finally:
            # ループ終了時に残っているログを最後に送信しようと試みる
            await self._process_queue()

    def close(self):
        """
        ハンドラを閉じる。バックグラウンドタスクを停止し、残りのログを送信する。
        logging.shutdown()によって自動的に呼ばれる。
        """
        if self._closed:
            return

        self._closed = True

        # バックグラウンドタスクをキャンセル
        if self._task:
            self._task.cancel()

        # イベントループが動いていれば、最後に残ったログを送信する
        if self.bot.loop.is_running():
            try:
                # run_coroutine_threadsafeを使って同期的コンテキストから非同期処理を安全に呼び出す
                future = asyncio.run_coroutine_threadsafe(self._process_queue(), self.bot.loop)
                # 完了を待つ（タイムアウト付き）
                future.result(timeout=self.interval)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
                print(f"Error sending remaining logs on close: {e}")

        super().close()