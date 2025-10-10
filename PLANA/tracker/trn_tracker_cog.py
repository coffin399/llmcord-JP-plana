#PLANA/tracker/trn_tracker_cog.py
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import yaml
from typing import Literal
from error.errors import (
    TRNAPIError,
    PlayerNotFoundError,
    GameNotSupportedError,
    RateLimitError
)

# サポートされているゲームのリスト
SUPPORTED_GAMES = Literal[
    "fortnite",
    "apex",
    "valorant",
    "cod-warzone",
    "csgo",
    "pubg",
    "rocket-league",
    "overwatch",
    "r6siege",
    "splitgate"
]

# ゲーム名とTRN APIのマッピング
GAME_MAPPING = {
    "fortnite": {"name": "Fortnite", "api_path": "fortnite"},
    "apex": {"name": "Apex Legends", "api_path": "apex"},
    "valorant": {"name": "Valorant", "api_path": "valorant"},
    "cod-warzone": {"name": "Call of Duty: Warzone", "api_path": "cod-warzone"},
    "csgo": {"name": "CS:GO", "api_path": "csgo"},
    "pubg": {"name": "PUBG", "api_path": "pubg"},
    "rocket-league": {"name": "Rocket League", "api_path": "rocket-league"},
    "overwatch": {"name": "Overwatch", "api_path": "overwatch"},
    "r6siege": {"name": "Rainbow Six Siege", "api_path": "r6siege"},
    "splitgate": {"name": "Splitgate", "api_path": "splitgate"}
}


class TRNStats(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.config = self.load_config()
        self.api_key = self.config.get('trn_api_key', '')
        self.base_url = "https://api.tracker.gg/api/v2"

    def load_config(self) -> dict:
        """config.yamlから設定を読み込む"""
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            print("Warning: config.yaml not found. Please create one with 'trn_api_key'")
            return {}
        except yaml.YAMLError as e:
            print(f"Error parsing config.yaml: {e}")
            return {}

    async def fetch_stats(self, game: str, username: str, platform: str = "pc") -> dict:
        """TRN APIからプレイヤーの統計情報を取得"""
        game_info = GAME_MAPPING.get(game)
        if not game_info:
            raise GameNotSupportedError(game)

        headers = {
            "TRN-Api-Key": self.api_key,
            "User-Agent": "Discord Bot / TRN Stats Tracker",
            "Accept": "application/json"
        }

        # ゲームごとにAPIエンドポイントが異なる場合があるため調整
        url = f"{self.base_url}/{game_info['api_path']}/standard/profile/{platform}/{username}"

        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    response_text = await response.text()

                    if response.status == 404:
                        raise PlayerNotFoundError(username, game_info['name'])
                    elif response.status == 429:
                        raise RateLimitError()
                    elif response.status == 403:
                        # 403の詳細なエラーメッセージを確認
                        try:
                            error_data = await response.json()
                            error_msg = error_data.get('errors', [{}])[0].get('message', 'Access denied')
                            raise TRNAPIError(f"403 Forbidden: {error_msg}. This game may require API approval.")
                        except:
                            raise TRNAPIError(
                                f"403 Forbidden: This game API may require special approval from Tracker Network")
                    elif response.status != 200:
                        raise TRNAPIError(f"Status {response.status}: {response_text}")

                    return await response.json()
            except aiohttp.ClientError as e:
                raise TRNAPIError(f"Network error: {str(e)}")

    def format_stats_embed(self, data: dict, game: str, username: str) -> discord.Embed:
        """統計情報をDiscord Embedにフォーマット"""
        game_name = GAME_MAPPING[game]["name"]
        embed = discord.Embed(
            title=f"{game_name} Stats - {username}",
            color=discord.Color.blue()
        )

        try:
            # データ構造はゲームによって異なるため、汎用的な処理
            if "data" in data:
                stats = data["data"].get("segments", [])
                if stats:
                    main_stats = stats[0].get("stats", {})

                    # 主要な統計情報を追加
                    for stat_name, stat_data in list(main_stats.items())[:10]:
                        if isinstance(stat_data, dict) and "displayValue" in stat_data:
                            display_name = stat_data.get("displayName", stat_name)
                            display_value = stat_data.get("displayValue", "N/A")
                            embed.add_field(
                                name=display_name,
                                value=display_value,
                                inline=True
                            )

            # プロフィールリンクを追加
            platform_info = data.get("data", {}).get("platformInfo", {})
            if "platformUserId" in platform_info:
                profile_url = f"https://tracker.gg/{game}/profile/{platform_info.get('platformSlug', 'pc')}/{username}"
                embed.add_field(
                    name="Profile",
                    value=f"[View Full Stats]({profile_url})",
                    inline=False
                )

        except (KeyError, IndexError) as e:
            embed.add_field(
                name="Error",
                value="Failed to parse stats data",
                inline=False
            )

        embed.set_footer(text="Powered by Tracker Network")
        return embed

    @app_commands.command(name="tracker", description="Get player stats from Tracker Network")
    @app_commands.describe(
        game="Select the game",
        username="Player username",
        platform="Platform (default: pc)"
    )
    async def tracker(
            self,
            interaction: discord.Interaction,
            game: SUPPORTED_GAMES,
            username: str,
            platform: str = "pc"
    ):
        """プレイヤーの統計情報を取得するコマンド"""
        await interaction.response.defer()

        try:
            # APIから統計情報を取得
            data = await self.fetch_stats(game, username, platform)

            # Embedを作成して送信
            embed = self.format_stats_embed(data, game, username)
            await interaction.followup.send(embed=embed)

        except PlayerNotFoundError as e:
            await interaction.followup.send(
                f"❌ {str(e)}",
                ephemeral=True
            )
        except GameNotSupportedError as e:
            await interaction.followup.send(
                f"❌ {str(e)}",
                ephemeral=True
            )
        except RateLimitError as e:
            await interaction.followup.send(
                f"⏱️ {str(e)}",
                ephemeral=True
            )
        except TRNAPIError as e:
            await interaction.followup.send(
                f"❌ API Error: {str(e)}",
                ephemeral=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ An unexpected error occurred: {str(e)}",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Cogのセットアップ関数"""
    await bot.add_cog(TRNStats(bot))