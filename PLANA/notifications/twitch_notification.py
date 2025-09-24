# PLANA/notification/twitch_notification.py
import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

# エラーハンドラをインポート
from .error.twitch_errors import (ConfigError, DataParsingError,
                                  NotificationError, TwitchAPIError,
                                  TwitchExceptionHandler)

# ロガーの設定
logger = logging.getLogger(__name__)

# --- 定数 ---
SETTINGS_FILE = Path("data/twitch_settings.json")
TWITCH_API_BASE_URL = "https://api.twitch.tv/helix"
TWITCH_AUTH_URL = "https://id.twitch.tv/oauth2/token"


class TwitchNotification(commands.Cog):
    """Twitchの配信開始を通知するCog"""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.handler = TwitchExceptionHandler(self)
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()

        # Twitch API認証情報をconfigから取得
        twitch_config = bot.config.get('twitch', {})
        self.client_id = twitch_config.get('client_id')
        self.client_secret = twitch_config.get('client_secret')
        self.access_token: Optional[str] = None
        self.token_expires_at: int = 0

        # 設定の読み込み
        self.settings: Dict[int, Dict[str, Any]] = self._load_settings()

        # 認証情報がなければタスクを開始しない
        if not self.client_id or not self.client_secret:
            pass
        else:
            self.check_streams.start()

    # ( ... この間のコードは変更ありません ... )
    # --- Cogのライフサイクルイベント ---
    async def cog_unload(self):
        """Cogがアンロードされるときに呼ばれる"""
        self.check_streams.cancel()
        await self.session.close()

    # --- 設定管理 ---
    def _load_settings(self) -> Dict[int, Dict[str, Any]]:
        """設定ファイルを読み込む"""
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            if SETTINGS_FILE.exists():
                with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                    # キーをintに変換
                    return {int(k): v for k, v in json.load(f).items()}
            return {}
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"設定ファイル({SETTINGS_FILE})の読み込みに失敗しました: {e}")
            return {}

    def _save_settings(self):
        """設定ファイルに保存する"""
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e:
            logger.error(f"設定ファイル({SETTINGS_FILE})の保存に失敗しました: {e}")

    # --- Twitch API 関連 ---
    async def _get_twitch_access_token(self):
        """Twitch APIのアプリアクセストークンを取得・更新する"""
        if self.access_token and time.time() < self.token_expires_at:
            return

        logger.info("Twitch APIのアクセストークンを更新します。")
        params = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "grant_type": "client_credentials",
        }
        try:
            async with self.session.post(TWITCH_AUTH_URL, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    self.access_token = data["access_token"]
                    # 期限の1分前に更新するようにマージンを設定
                    self.token_expires_at = time.time() + data["expires_in"] - 60
                    logger.info("Twitch APIのアクセストークンを更新しました。")
                else:
                    text = await resp.text()
                    raise self.handler.handle_api_response_error(resp.status, TWITCH_AUTH_URL, text)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise self.handler.handle_api_error(e, "アクセストークン取得")

    async def _api_request(self, endpoint: str, params: Optional[Dict] = None) -> Dict:
        """Twitch APIへのリクエストを共通化する"""
        await self._get_twitch_access_token()
        headers = {
            "Client-ID": self.client_id,
            "Authorization": f"Bearer {self.access_token}",
        }
        url = f"{TWITCH_API_BASE_URL}/{endpoint}"

        try:
            async with self.session.get(url, headers=headers, params=params) as resp:
                if resp.status == 200:
                    return await resp.json()
                # 認証エラーならトークンを無効化して再試行を促す
                if resp.status == 401:
                    self.access_token = None
                text = await resp.text()
                raise self.handler.handle_api_response_error(resp.status, url, text)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            raise self.handler.handle_api_error(e, f"APIリクエスト: {endpoint}")
        except json.JSONDecodeError as e:
            raise self.handler.handle_json_decode_error(e, f"APIリクエスト: {endpoint}")

    async def get_user_data(self, login_name: str) -> Optional[Dict]:
        """Twitchのログイン名からユーザーデータを取得する"""
        response = await self._api_request("users", params={"login": login_name})
        if response and response.get("data"):
            return response["data"][0]
        return None

    async def get_stream_data(self, user_id: str) -> Optional[Dict]:
        """ユーザーIDから現在の配信データを取得する"""
        response = await self._api_request("streams", params={"user_id": user_id})
        if response and response.get("data"):
            return response["data"][0]
        return None

    # --- バックグラウンドタスク ---
    @tasks.loop(minutes=1)
    async def check_streams(self):
        """定期的に配信ステータスをチェックする"""
        if not self.settings:
            return

        # チェック対象のユーザーIDをまとめる
        user_ids_to_check = [
            config["twitch_user_id"]
            for config in self.settings.values()
            if "twitch_user_id" in config
        ]
        if not user_ids_to_check:
            return

        try:
            # APIを一度に叩く
            response = await self._api_request("streams", params=[("user_id", uid) for uid in user_ids_to_check])
            live_streams = {stream['user_id']: stream for stream in response.get('data', [])}

            for guild_id, config in self.settings.items():
                user_id = config.get("twitch_user_id")
                if not user_id:
                    continue

                channel = self.bot.get_channel(config["notification_channel_id"])
                if not channel:
                    logger.warning(f"ギルド {guild_id} の通知チャンネルが見つかりません。設定をスキップします。")
                    continue

                last_status = config.get("last_status", "offline")
                stream_data = live_streams.get(user_id)

                # 配信が開始された場合
                if stream_data and last_status == "offline":
                    logger.info(f"{stream_data['user_name']} が配信を開始しました。ギルド {guild_id} に通知します。")
                    await self._send_notification(channel, stream_data)
                    config["last_status"] = "online"
                    self._save_settings()

                # 配信が終了した場合
                elif not stream_data and last_status == "online":
                    logger.info(f"{config['twitch_login_name']} の配信が終了しました。")
                    config["last_status"] = "offline"
                    self._save_settings()

        except TwitchAPIError as e:
            logger.warning(f"配信チェック中にAPIエラーが発生しました: {e}")
        except Exception as e:
            self.handler.log_generic_error(e, "配信チェックタスク")

    @check_streams.before_loop
    async def before_check_streams(self):
        await self.bot.wait_until_ready()

    # --- 通知機能 ---
    async def _send_notification(self, channel: discord.TextChannel, stream_data: Dict):
        """通知メッセージを送信する"""
        embed = discord.Embed(
            title=f"🔴LIVE: {stream_data['title']}",
            url=f"https://www.twitch.tv/{stream_data['user_login']}",
            color=discord.Color.purple()
        )
        embed.set_author(
            name=stream_data['user_name'],
            url=f"https://www.twitch.tv/{stream_data['user_login']}"
        )
        embed.add_field(name="ゲーム", value=stream_data.get('game_name', 'N/A'), inline=True)
        embed.add_field(name="視聴者数", value=stream_data.get('viewer_count', 'N/A'), inline=True)

        thumbnail_url = stream_data['thumbnail_url'].replace('{width}', '1280').replace('{height}', '720')
        embed.set_image(url=f"{thumbnail_url}?t={int(time.time())}")  # キャッシュ対策

        embed.set_footer(text="Twitch配信通知")
        embed.timestamp = discord.utils.utcnow()

        try:
            await channel.send(f"{stream_data['user_name']}が配信を開始しました！", embed=embed)
        except discord.Forbidden:
            logger.error(f"チャンネル {channel.id} への通知送信に失敗しました: 権限がありません。")
        except discord.HTTPException as e:
            logger.error(f"チャンネル {channel.id} への通知送信に失敗しました: {e}")

    # ( ... ここまで変更なし ... )

    # --- スラッシュコマンド ---
    @app_commands.command(name="twitch_set", description="Twitch配信通知を設定します。")
    @app_commands.describe(
        twitch_url="通知したいTwitchチャンネルのURL (例: https://www.twitch.tv/twitch)",
        notification_channel="通知を送信するDiscordチャンネル"
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def set_notification(self, interaction: discord.Interaction, twitch_url: str,
                               notification_channel: discord.TextChannel):
        """配信通知を設定するコマンド"""
        ### 変更点 ###
        # ephemeral=False にするため、defer()の引数を変更
        await interaction.response.defer(ephemeral=False)
        guild_id = interaction.guild_id

        try:
            # URLからログイン名を取得
            parsed_url = urlparse(twitch_url)
            if parsed_url.netloc not in ("www.twitch.tv", "twitch.tv"):
                raise ConfigError("無効なTwitchチャンネルURLです。")
            login_name = parsed_url.path.strip('/')
            if not login_name:
                raise ConfigError("URLからチャンネル名を特定できませんでした。")

            # Twitch APIでユーザー情報を取得
            user_data = await self.get_user_data(login_name)
            if not user_data:
                raise TwitchAPIError(f"Twitchユーザー '{login_name}' が見つかりませんでした。")

            # 設定を保存
            self.settings[guild_id] = {
                "twitch_user_id": user_data["id"],
                "twitch_login_name": user_data["login"],
                "twitch_display_name": user_data["display_name"],
                "notification_channel_id": notification_channel.id,
                "last_status": "offline",
            }
            self._save_settings()

            embed = discord.Embed(
                title="✅ Twitch通知設定完了",
                description=f"**{user_data['display_name']}** の配信が開始されたら、{notification_channel.mention} に通知します。",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed)

        except Exception as e:
            message = self.handler.get_user_friendly_message(e)
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.followup.send(message)

    @app_commands.command(name="twitch_remove", description="Twitch配信通知の設定を解除します。")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def remove_notification(self, interaction: discord.Interaction):
        """配信通知を解除するコマンド"""
        guild_id = interaction.guild_id
        if guild_id in self.settings:
            del self.settings[guild_id]
            self._save_settings()
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.response.send_message("✅ Twitch配信通知の設定を解除しました。")
        else:
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.response.send_message("ℹ️ このサーバーにはTwitch配信通知が設定されていません。")

    @app_commands.command(name="twitch_test", description="Twitch配信通知のテストメッセージを送信します。")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def test_notification(self, interaction: discord.Interaction):
        """通知のテストを行うコマンド"""
        ### 変更点 ###
        # ephemeral=False にするため、defer()の引数を変更
        await interaction.response.defer(ephemeral=False)
        guild_id = interaction.guild_id

        if guild_id not in self.settings:
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.followup.send("❌ 通知設定が見つかりません。`/twitch_set`で先に設定してください。")
            return

        config = self.settings[guild_id]
        channel = self.bot.get_channel(config["notification_channel_id"])
        if not channel:
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.followup.send(f"❌ 通知チャンネルが見つかりません。ID: {config['notification_channel_id']}")
            return

        # テスト用のダミーデータ
        test_stream_data = {
            'user_name': config['twitch_display_name'],
            'user_login': config['twitch_login_name'],
            'title': 'これはテスト配信です！',
            'game_name': 'Just Chatting',
            'viewer_count': 1234,
            'thumbnail_url': 'https://static-cdn.jtvnw.net/previews-ttv/live_user_{user_login}-{width}x{height}.jpg'.format(
                user_login=config['twitch_login_name'], width=1280, height=720
            )
        }

        try:
            await self._send_notification(channel, test_stream_data)
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.followup.send(f"✅ {channel.mention} にテスト通知を送信しました。")
        except Exception as e:
            message = self.handler.get_user_friendly_message(e)
            ### 変更点 ###
            # ephemeral=False にするため、引数を削除
            await interaction.followup.send(message)


async def setup(bot: commands.Bot):
    # bot.config が存在し、必要なキーがあるかチェック
    if not hasattr(bot, 'config'):
        logger.critical("Botにconfig属性が見つかりません。config.yamlをロードしてください。")
        return

    twitch_config = bot.config.get('twitch', {})
    if not twitch_config.get('client_id') or not twitch_config.get('client_secret'):
        logger.critical(
            "config.yamlにTwitchの認証情報(client_id, client_secret)が設定されていません。Cogをロードしません。")
        return

    await bot.add_cog(TwitchNotification(bot))