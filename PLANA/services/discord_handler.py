# PLANA/services/discord_handler.py

import asyncio
import json
import logging
import os
import re
from asyncio import Queue
from typing import List

from discord import Client, TextChannel


class DiscordLogFormatter(logging.Formatter):
    """
    ログレベルに応じてANSIエスケープコードを使い、文字色を変更するフォーマッター。
    """
    # ANSIカラーコード
    RESET = "\u001b[0m"
    RED = "\u001b[31m"
    YELLOW = "\u001b[33m"
    BLUE = "\u001b[34m"
    WHITE = "\u001b[37m"

    COLOR_MAP = {
        logging.DEBUG: WHITE,
        logging.INFO: BLUE,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED,
    }

    def format(self, record: logging.LogRecord) -> str:
        """
        元のログメッセージをフォーマットし、全体をANSIカラーコードで囲む。
        """
        log_message = super().format(record)
        color = self.COLOR_MAP.get(record.levelno, self.WHITE)
        return f"{color}{log_message}{self.RESET}"


class DiscordLogHandler(logging.Handler):
    """
    Pythonのログを複数のDiscordチャンネルにバッチ送信するためのカスタムロギングハンドラ。
    レートリミットを回避するため、ログをキューに溜め、定期的にまとめて送信します。
    """

    def __init__(self, bot: Client, channel_ids: List[int], interval: float = 5.0,
                 config_path: str = "data/log_channels.json"):
        super().__init__()
        self.bot = bot
        self.channel_ids = channel_ids
        self.interval = interval
        self.config_path = config_path

        self.queue: Queue[str] = Queue()
        self.channels: List[TextChannel] = []
        self._closed = False

        # 無効なチャンネルIDを追跡（連続で失敗した回数）
        self.invalid_channel_attempts: dict[int, int] = {}
        self.max_attempts = 3  # 3回連続で失敗したら削除

        self._task = self.bot.loop.create_task(self._log_sender_loop())

    def add_channel(self, channel_id: int):
        if channel_id not in self.channel_ids:
            self.channel_ids.append(channel_id)
            # 失敗カウントをリセット
            self.invalid_channel_attempts.pop(channel_id, None)

            if self.bot.is_ready():
                channel = self.bot.get_channel(channel_id)
                if isinstance(channel, TextChannel):
                    self.channels.append(channel)
                    print(f"DiscordLogHandler: Immediately added and activated channel {channel_id}.")
                    # 設定を保存
                    asyncio.create_task(self._save_config())
                else:
                    print(
                        f"DiscordLogHandler: Added channel ID {channel_id}, but it's not a valid text channel or not found yet.")
            else:
                self.channels = []
                print(f"DiscordLogHandler: Added channel ID {channel_id}. Will be activated once bot is ready.")
                # 設定を保存
                asyncio.create_task(self._save_config())

    def remove_channel(self, channel_id: int):
        if channel_id in self.channel_ids:
            self.channel_ids.remove(channel_id)
            self.channels = [ch for ch in self.channels if ch.id != channel_id]
            self.invalid_channel_attempts.pop(channel_id, None)
            print(f"DiscordLogHandler: Immediately removed and deactivated channel {channel_id}.")
            # 設定を保存
            asyncio.create_task(self._save_config())

    async def _save_config(self):
        """チャンネルIDリストをJSONファイルに保存"""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

            data = {
                "log_channels": self.channel_ids
            }

            try:
                import aiofiles
                async with aiofiles.open(self.config_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, indent=4, ensure_ascii=False))
            except ImportError:
                with open(self.config_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)

            print(f"DiscordLogHandler: Saved log channel configuration to {self.config_path}")
        except Exception as e:
            print(f"DiscordLogHandler: Failed to save config: {e}")

    async def _remove_invalid_channel(self, channel_id: int, reason: str):
        """無効なチャンネルをリストから削除"""
        if channel_id in self.channel_ids:
            self.channel_ids.remove(channel_id)
            self.channels = [ch for ch in self.channels if ch.id != channel_id]
            self.invalid_channel_attempts.pop(channel_id, None)

            print(f"DiscordLogHandler: ⚠️ Removed invalid channel {channel_id} from config. Reason: {reason}")

            # 設定を保存
            await self._save_config()

    def emit(self, record: logging.LogRecord):
        if self._closed:
            return
        msg = self.format(record)
        msg = self._sanitize_log_message(msg)
        try:
            self.queue.put_nowait(msg)
        except asyncio.QueueFull:
            print("DiscordLogHandler: Log queue is full, dropping message.")

    def _get_display_chars(self, text: str, count: int = 1) -> str:
        """
        テキストから先頭の記号・絵文字・空白を除去し、指定文字数を返す。
        絵文字も1文字としてカウントする。
        """
        # 先頭の引用符、括弧、空白を除去
        cleaned = re.sub(r'^[「『"\'『»«‹›〈〉《》【】〔〕［］｛｝（）()［］\s]+', '', text)

        # 絵文字パターン（Unicode絵文字の範囲）
        emoji_pattern = re.compile(
            "["
            "\U0001F1E0-\U0001F1FF"  # flags (iOS)
            "\U0001F300-\U0001F5FF"  # symbols & pictographs
            "\U0001F600-\U0001F64F"  # emoticons
            "\U0001F680-\U0001F6FF"  # transport & map symbols
            "\U0001F700-\U0001F77F"  # alchemical symbols
            "\U0001F780-\U0001F7FF"  # Geometric Shapes Extended
            "\U0001F800-\U0001F8FF"  # Supplemental Arrows-C
            "\U0001F900-\U0001F9FF"  # Supplemental Symbols and Pictographs
            "\U0001FA00-\U0001FA6F"  # Chess Symbols
            "\U0001FA70-\U0001FAFF"  # Symbols and Pictographs Extended-A
            "\U00002702-\U000027B0"  # Dingbats
            "\U000024C2-\U0001F251"
            "]+", flags=re.UNICODE
        )

        # 絵文字を除去してから文字数を取得
        chars_without_emoji = emoji_pattern.sub('', cleaned)

        if len(chars_without_emoji) >= count:
            return chars_without_emoji[:count]
        elif len(cleaned) >= count:
            return cleaned[:count]
        else:
            return text[:count] if text else ''

    def _sanitize_log_message(self, message: str) -> str:
        # Windowsユーザーパス
        message = re.sub(
            r'[A-Za-z]:\\Users\\[^\\]+\\[^\\]+',
            '********',
            message,
            flags=re.IGNORECASE
        )
        # Session ID
        message = re.sub(
            r'((?:Session ID:?|session)\s+)[a-f09]{32}',
            r'\1****',
            message,
            flags=re.IGNORECASE
        )
        # Session ID (上で取り切れなかった場合)
        message = re.sub(
            r'((?:Session ID:?|session)\s+)([a-f0-9])([a-f0-9]{31})',
            r'\1\2****',
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
            lambda m: f"Guild ****({m.group(2)[:1]}****):",
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
            lambda m: f"Connected to {m.group(1)[:1]}****",
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

        # switch_model_slash等のログ形式: by ユーザー名 -> by ユ****
        message = re.sub(
            r"\bby ([^\s.]+)",
            lambda m: f"by {m.group(1)[:1]}****",
            message
        )

        # send_embed_to_channels形式: 'hoge' の '📗-fuga' -> 'h****' の '📗****'
        # または: 'ギルド名' の 'チャンネル名' -> 'ギ****' の 'チ****'
        message = re.sub(
            r"'([^']+)' の '([^']+)'",
            lambda
                m: f"'{self._get_display_chars(m.group(1), 1)}****' の '{self._get_display_chars(m.group(2), 1)}****'",
            message
        )

        # on_guild_join/on_guild_remove形式: 'サーバー名' (ID: X****) -> 'サ****' (ID: X****)
        message = re.sub(
            r"'([^']+)' \(ID: (\d+\*+)\)",
            lambda m: f"'{self._get_display_chars(m.group(1), 1)}****' (ID: {m.group(2)})",
            message
        )

        # cleanup_task_loop形式: guild: 1**** (サーバー名) -> guild: 1**** (サ****)
        message = re.sub(
            r"guild: (\d+\*+) \(([^)]+)\)",
            lambda m: f"guild: {m.group(1)} ({self._get_display_chars(m.group(2), 1)}****)",
            message
        )
        return message

    async def _process_queue(self):
        """
        キューに溜まったログを全て取り出し、個々のログが途切れないようにチャンク分けして送信する。
        """
        if self.queue.empty():
            return

        # チャンネルの存在確認と更新
        if not self.channels or len(self.channels) != len(self.channel_ids):
            found_channels = []
            channels_to_remove = []

            for cid in self.channel_ids:
                channel = self.bot.get_channel(cid)
                if channel and isinstance(channel, TextChannel):
                    found_channels.append(channel)
                    # 成功したら失敗カウントをリセット
                    self.invalid_channel_attempts.pop(cid, None)
                else:
                    # チャンネルが見つからない場合、失敗カウントを増やす
                    self.invalid_channel_attempts[cid] = self.invalid_channel_attempts.get(cid, 0) + 1

                    if self.invalid_channel_attempts[cid] >= self.max_attempts:
                        # 規定回数失敗したら削除対象に
                        channels_to_remove.append(cid)
                        print(f"DiscordLogHandler: ⚠️ Channel {cid} not found {self.max_attempts} times consecutively.")
                    else:
                        print(
                            f"DiscordLogHandler: Warning - Channel with ID {cid} not found or is not a text channel. (Attempt {self.invalid_channel_attempts[cid]}/{self.max_attempts})")

            self.channels = found_channels

            # 無効なチャンネルを削除
            for cid in channels_to_remove:
                await self._remove_invalid_channel(cid, f"Channel not found after {self.max_attempts} attempts")

        if not self.channels:
            if self.channel_ids:
                print(f"DiscordLogHandler: No valid channels found for IDs {self.channel_ids}. Clearing log queue.")
            while not self.queue.empty():
                self.queue.get_nowait()
            return

        # キューから全てのログを取得
        records = []
        while not self.queue.empty():
            records.append(self.queue.get_nowait())
        if not records:
            return

        # ログを1つのコードブロック内にまとめてチャンク分けする
        chunks = []
        current_logs = []
        # コードブロックのオーバーヘッド: ```ansi\n と \n``` で13文字
        CODE_BLOCK_OVERHEAD = 13
        # Discordの制限2000文字ギリギリを狙う（安全のため少し余裕を持たせる）
        CHUNK_LIMIT = 1990

        for record in records:
            # 改行付きでログを追加した場合のサイズを計算
            log_with_newline = record if not current_logs else f"\n{record}"

            # 現在のログ群 + 新しいログ + コードブロックのオーバーヘッド
            potential_size = sum(len(log) for log in current_logs) + len(log_with_newline) + CODE_BLOCK_OVERHEAD
            if current_logs:
                potential_size += len(current_logs) - 1  # 既存ログ間の改行分

            # 1つのログ自体が制限を超える場合
            if len(record) + CODE_BLOCK_OVERHEAD > CHUNK_LIMIT:
                # 現在のチャンクを確定
                if current_logs:
                    chunks.append("```ansi\n" + "\n".join(current_logs) + "\n```")
                    current_logs = []

                # 長いログを分割して送信（コードブロックなしで）
                for i in range(0, len(record), CHUNK_LIMIT):
                    chunk_part = record[i:i + CHUNK_LIMIT]
                    # 最初の部分にはコードブロック開始、最後の部分には終了を付ける
                    if i == 0:
                        chunk_part = "```ansi\n" + chunk_part
                    if i + CHUNK_LIMIT >= len(record):
                        chunk_part = chunk_part + "\n```"
                    chunks.append(chunk_part)
                continue

            # 追加するとチャンクサイズを超える場合
            if potential_size > CHUNK_LIMIT:
                # 現在のチャンクを確定して新しいチャンクを開始
                chunks.append("```ansi\n" + "\n".join(current_logs) + "\n```")
                current_logs = [record]
            else:
                # 現在のチャンクに追加
                current_logs.append(record)

        # 最後のチャンクを追加
        if current_logs:
            chunks.append("```ansi\n" + "\n".join(current_logs) + "\n```")

        # 全てのチャンネルに、作成したチャンクを送信
        channels_to_remove = []
        for channel in self.channels:
            send_success = False
            for chunk in chunks:
                if not chunk.strip():
                    continue
                try:
                    await channel.send(chunk, silent=True)
                    send_success = True
                    # チャンク間の送信にわずかな遅延を入れ、レートリミットを回避
                    await asyncio.sleep(0.2)
                except discord.errors.Forbidden:
                    # 権限エラー: チャンネルが削除されたか、アクセス権がない
                    print(f"DiscordLogHandler: ⚠️ No permission to send to channel {channel.id}. Marking for removal.")
                    channels_to_remove.append((channel.id, "Forbidden: No permission to send messages"))
                    break
                except discord.errors.NotFound:
                    # チャンネルが見つからない（削除された）
                    print(f"DiscordLogHandler: ⚠️ Channel {channel.id} not found (deleted?). Marking for removal.")
                    channels_to_remove.append((channel.id, "NotFound: Channel has been deleted"))
                    break
                except Exception as e:
                    print(f"Failed to send log to Discord channel {channel.id}: {e}")
                    if not send_success:
                        # 1つも送信できなかった場合のみ失敗カウントを増やす
                        self.invalid_channel_attempts[channel.id] = self.invalid_channel_attempts.get(channel.id, 0) + 1
                        if self.invalid_channel_attempts[channel.id] >= self.max_attempts:
                            channels_to_remove.append(
                                (channel.id, f"Failed to send after {self.max_attempts} attempts: {str(e)}"))
                    break

            # 成功したらカウントリセット
            if send_success:
                self.invalid_channel_attempts.pop(channel.id, None)

        # 無効なチャンネルを削除
        for channel_id, reason in channels_to_remove:
            await self._remove_invalid_channel(channel_id, reason)

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
            # ループ終了時に残っているログを送信
            await self._process_queue()

    def close(self):
        """ハンドラを閉じる。"""
        if self._closed:
            return
        self._closed = True
        if self._task:
            self._task.cancel()
        # 同期的なコンテキストから非同期関数を安全に呼び出す
        if self.bot.loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._process_queue(), self.bot.loop)
                # タイムアウトを設定して待機
                future.result(timeout=self.interval)
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception) as e:
                print(f"Error sending remaining logs on close: {e}")
        super().close()