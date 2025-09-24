# PLANA/cogs/images/image_commands_cog.py

import logging
import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from PLANA.images.error import errors

logger = logging.getLogger(__name__)

# --- 設定キー (config.yaml から読み込むことを想定) ---
THECATAPI_API_KEY = "thecatapi_api_key"
# User-Agent設定
BOT_USER_AGENT = "PlanaDiscordBot/1.0"


class ImageCommandsCog(commands.Cog, name="画像検索"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        self.bot_user_agent = self.bot.config.get("bot_user_agent", BOT_USER_AGENT)
        headers = {"User-Agent": self.bot_user_agent}
        self.http_session = aiohttp.ClientSession(headers=headers)

        self.thecatapi_key = self.bot.config.get(THECATAPI_API_KEY)
        if not self.thecatapi_key:
            logger.warning("TheCatAPIのAPIキーが設定されていません。レート制限が厳しくなる可能性があります。")

    async def cog_unload(self):
        await self.http_session.close()
        logger.info("ImageCommandsCog: aiohttpセッションを閉じました。")

    @app_commands.command(name="meow", description="可愛い猫の画像を表示します。 / Displays a cute cat picture.")
    async def meow_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        try:
            cat_api_headers = {}
            if self.thecatapi_key:
                cat_api_headers["x-api-key"] = self.thecatapi_key

            request_headers = {**self.http_session.headers, **cat_api_headers}

            async with self.http_session.get("https://api.thecatapi.com/v1/images/search",
                                             headers=request_headers) as response:
                if response.status == 200:
                    data = await response.json()
                    # 正常なデータかチェック
                    if data and isinstance(data, list) and len(data) > 0 and data[0].get("url"):
                        # --- 成功時の処理 ---
                        cat_url = data[0]["url"]
                        embed = discord.Embed(title="にゃーん！ / Meow!", color=discord.Color.random())
                        embed.set_image(url=cat_url)
                        embed.set_footer(text="Powered by thecatapi.com")
                        await interaction.followup.send(embed=embed, ephemeral=False)
                        logger.info(f"/meow: 猫画像送信成功 - {cat_url} (User: {interaction.user.id})")
                    else:
                        await errors.handle_meow_invalid_data(interaction)
                else:
                    await errors.handle_meow_api_error(interaction, response)

        # ▼▼▼ 変更点: 各エラーハンドリングを委譲 ▼▼▼
        except aiohttp.ClientError as e: # aiohttpの接続関連エラーを広く捕捉
            await errors.handle_meow_connection_error(interaction, e)
        except Exception as e:
            await errors.handle_meow_unexpected_error(interaction, e)


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("ImageCommandsCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        raise commands.ExtensionFailed("ImageCommandsCog", "Botのconfigがロードされていません。")

    await bot.add_cog(ImageCommandsCog(bot))
    logger.info("ImageCommandsCogが正常にロードされました。")