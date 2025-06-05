import discord
from discord import app_commands
from discord.ext import commands
import logging
import random
import aiohttp
import json  # APIレスポンスのパース用 (Yande.re や TheCatAPI で使う可能性)
from typing import Optional, List
import urllib.parse

logger = logging.getLogger(__name__)

# --- 設定キー (config.yaml から読み込むことを想定) ---
THECATAPI_API_KEY = "thecatapi_api_key"
# User-Agent設定
BOT_USER_AGENT = "PlanaDiscordBot/1.0 (YourDiscordTagOrContactInfo)"  # 適切に設定


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
                    if data and isinstance(data, list) and len(data) > 0 and data[0].get("url"):
                        cat_url = data[0]["url"]
                        embed = discord.Embed(title="にゃーん！ / Meow!", color=discord.Color.random())
                        embed.set_image(url=cat_url)
                        embed.set_footer(text="Powered by thecatapi.com")
                        await interaction.followup.send(embed=embed, ephemeral=False)
                        logger.info(f"/meow: 猫画像送信成功 - {cat_url} (User: {interaction.user.id})")
                    else:
                        await interaction.followup.send(
                            "猫の画像が見つかりませんでした。\nCould not find a cat picture.", ephemeral=True)
                        logger.warning("/meow: TheCatAPIからのデータ形式が不正または空です。")
                else:
                    error_text = await response.text()
                    await interaction.followup.send(
                        f"猫の画像の取得に失敗しました (ステータス: {response.status})。\nFailed to fetch a cat picture (Status: {response.status}).",
                        ephemeral=True)
                    logger.error(f"/meow: TheCatAPIエラー - Status: {response.status}, Response: {error_text[:200]}")
        except aiohttp.ClientConnectorError as e:
            logger.error(f"/meow コマンド実行中に接続エラー: {e}", exc_info=True)
            await interaction.followup.send(
                "猫画像サイトへの接続に失敗しました。\nFailed to connect to the cat picture site.", ephemeral=True)
        except Exception as e:
            logger.error(f"/meow コマンド実行中に予期せぬエラー: {e}", exc_info=True)
            await interaction.followup.send(
                "猫の画像の取得中に予期せぬエラーが発生しました。\nAn unexpected error occurred while fetching a cat picture.",
                ephemeral=True)

    @app_commands.command(name="yandere",
                          description="Yande.re から画像を表示します。タグは任意です。 / Shows an image from Yande.re. Tags are optional.")
    @app_commands.describe(
        tags="検索するタグ (スペース区切り、指定なしでランダム) / Tags to search (space separated, random if not specified)")
    async def yandere_command(self, interaction: discord.Interaction, tags: Optional[str] = None):
        await interaction.response.defer(ephemeral=False)

        if interaction.channel and not interaction.channel.is_nsfw():  # NSFWチェックは重要
            await interaction.followup.send(
                "このコマンドはNSFWチャンネルでのみ使用できます。\nThis command can only be used in NSFW channels.",
                ephemeral=True
            )
            logger.info(f"/yandere: NSFWでないチャンネルでの実行試行をブロック (Channel: {interaction.channel_id})")
            return

        search_query_params = {"limit": 100}
        display_tags = "ランダム / Random"

        if tags:
            processed_tags = tags.strip()
            if processed_tags:
                search_query_params["tags"] = processed_tags
            display_tags = tags

        api_url = "https://yande.re/post.json"
        logger.info(
            f"/yandere: APIリクエスト - URL: {api_url}, Params: {search_query_params} (User: {interaction.user.id})")

        try:
            async with self.http_session.get(api_url, params=search_query_params) as response:
                if response.status == 200:
                    data = await response.json()
                    if data and isinstance(data, list):
                        valid_posts = [post for post in data if post.get("file_url") and not post.get("is_banned")]
                        if valid_posts:
                            selected_post = random.choice(valid_posts)
                            image_url = selected_post.get("file_url")
                            post_id = selected_post.get("id")
                            post_url = f"https://yande.re/post/show/{post_id}" if post_id else "不明な投稿 / Unknown Post"

                            embed = discord.Embed(
                                title=f"Yande.re 画像検索結果 / Image from Yande.re",
                                description=f"タグ / Tags: `{display_tags}`\n[投稿を見る / View Post]({post_url})",
                                color=discord.Color.from_rgb(255, 172, 190)
                            )
                            embed.set_image(url=image_url)
                            embed.set_footer(text="Powered by Yande.re API")
                            await interaction.followup.send(embed=embed, ephemeral=False)
                            logger.info(f"/yandere: 画像送信成功 - {image_url} (User: {interaction.user.id})")
                        else:
                            no_results_msg = f"指定されたタグ「{display_tags}」に一致する画像が見つかりませんでした。\nNo images found for the tags: {display_tags}"
                            if not tags: no_results_msg = "ランダムな画像が見つかりませんでした。\nCould not find random images."
                            await interaction.followup.send(no_results_msg, ephemeral=True)
                    elif isinstance(data, dict) and "success" in data and data["success"] is False:
                        error_msg = data.get("reason", "Unknown API error")
                        await interaction.followup.send(
                            f"Yande.re APIエラー: {error_msg}\nYande.re API Error: {error_msg}", ephemeral=True)
                        logger.warning(f"/yandere: Yande.re APIがエラー応答: {data}, Tags: {tags}")
                    else:
                        await interaction.followup.send(
                            "画像の検索結果を取得できませんでした (データ形式不正)。\nCould not retrieve image search results (invalid data format).",
                            ephemeral=True)
                        logger.warning(
                            f"/yandere: Yande.re APIからのデータ形式が不正です。 Tags: {tags}, Response: {str(data)[:200]}")
                else:
                    error_text = await response.text()
                    await interaction.followup.send(
                        f"画像の検索に失敗しました (ステータス: {response.status})。\nImage search failed (Status: {response.status}).",
                        ephemeral=True)
                    logger.error(
                        f"/yandere: Yande.re APIエラー - Status: {response.status}, Tags: {tags}, Response: {error_text[:200]}")
        except aiohttp.ClientConnectorError as e:
            logger.error(f"/yandere コマンド実行中に接続エラー (Tags: {tags}): {e}", exc_info=True)
            await interaction.followup.send("画像サイトへの接続に失敗しました。\nFailed to connect to the image site.",
                                            ephemeral=True)
        except Exception as e:
            logger.error(f"/yandere コマンド実行中に予期せぬエラー (Tags: {tags}): {e}", exc_info=True)
            await interaction.followup.send(
                "画像の検索中に予期せぬエラーが発生しました。\nAn unexpected error occurred during the image search.",
                ephemeral=True)

    # Danbooru関連の定数とコマンド (danbooru_command) を削除


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("ImageCommandsCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        raise commands.ExtensionFailed("ImageCommandsCog", "Botのconfigがロードされていません。")

    await bot.add_cog(ImageCommandsCog(bot))
    logger.info("ImageCommandsCogが正常にロードされました。")