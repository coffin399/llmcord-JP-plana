# your_bot_project/cogs/general_commands.py

import discord
from discord import app_commands
from discord.ext import commands
import logging

# main.py から必要な定数や関数をインポート
# (実際の main.py のファイル名に合わせてください)
from main import (
    load_config, # load_config をインポート
    ARONA_REPOSITORY,
    PLANA_REPOSITORY,
    SUPPORT_SERVER_INVITE_LINK,
    INVITE_URL,
    # DiscordLLMBot クラスの型ヒントのためにもインポート
    # from main import DiscordLLMBot # 循環参照を避けるため、型ヒントでは文字列リテラルや Any を使う
)
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from main import DiscordLLMBot


class GeneralCommandsCog(commands.Cog):
    def __init__(self, bot: 'DiscordLLMBot'): # commands.Bot 型または DiscordLLMBot 型
        self.bot: 'DiscordLLMBot' = bot

    @app_commands.command(name="help", description="ヘルプメッセージを表示します")
    async def help_command(self, interaction: discord.Interaction) -> None:
        # self.bot.cfg は DiscordLLMBot インスタンスの cfg 属性を指す
        help_text = self.bot.cfg.get("help_message", "ヘルプメッセージが設定されていません。")
        await interaction.response.send_message(help_text, ephemeral=False)

    @app_commands.command(name="arona", description="arona music botのリポジトリを表示します")
    async def arona_command(self, interaction: discord.Interaction) -> None:
        if ARONA_REPOSITORY and ARONA_REPOSITORY != "":
            message = f"アロナのリポジトリはこちらです！\n{ARONA_REPOSITORY}"
            await interaction.response.send_message(message, ephemeral=False)
        else: # リポジトリが設定されていない場合のエラーハンドリングを追加（推奨）
            await interaction.response.send_message("Aronaリポジトリのリンクが設定されていません。", ephemeral=True)


    @app_commands.command(name="plana", description="llmcord-JP-planaのリポジトリを表示します")
    async def plana_command(self, interaction: discord.Interaction) -> None:
        if PLANA_REPOSITORY and PLANA_REPOSITORY != "":
            message = f"プラナのリポジトリはこちらです！\n{PLANA_REPOSITORY}"
            await interaction.response.send_message(message, ephemeral=False)
        else: # リポジトリが設定されていない場合のエラーハンドリングを追加（推奨）
            await interaction.response.send_message("Planaリポジトリのリンクが設定されていません。", ephemeral=True)

    @app_commands.command(name="support", description="サポートサーバーの招待コードを表示します")
    async def support_command(self, interaction: discord.Interaction) -> None:
        invite_link_to_use = SUPPORT_SERVER_INVITE_LINK
        if invite_link_to_use and invite_link_to_use != "https://discord.gg/HogeFugaPiyo":
            message = f"サポートサーバーへの招待リンクはこちらです！\n{invite_link_to_use}"
            await interaction.response.send_message(message, ephemeral=False)
        else:
            await interaction.response.send_message(
                "申し訳ありませんが、現在サポートサーバーの招待リンクが設定されていません。\n管理者にお問い合わせください。",
                ephemeral=False
            )

    @app_commands.command(name="invite", description="Botをサーバーに招待します")
    async def invite_command(self, interaction: discord.Interaction) -> None:
        try:
            if not INVITE_URL or INVITE_URL == "YOUR_INVITE_URL_HERE":
                await interaction.response.send_message(
                    "エラー: 招待URLが設定されていません。開発者(Discord:coffin299)にご連絡ください。",
                    ephemeral=True
                )
                # logging.error("Error: INVITE_URL is not set.") # loggingはmain.py側で行うか、Cog内でもインポート
                print("Error: INVITE_URL is not set in the code.") # オリジナルに合わせる
                return

            embed = discord.Embed(
                title="🔗 ボット招待",
                description=(
                    f"PLANAをあなたのサーバーに招待しませんか？\n" # ボット名はinteraction.client.user.display_name等で取得可能
                    "以下のリンクから招待できます。"
                ),
                color=discord.Color.brand_green()
            )
            embed.add_field(
                name="招待リンク",
                value=f"[ここをクリックして招待する]({INVITE_URL})",
                inline=False
            )
            # ボットのアイコン (self.bot.user は commands.Bot インスタンスの user 属性)
            if self.bot.user and self.bot.user.avatar:
                embed.set_thumbnail(url=self.bot.user.avatar.url)
            # elif interaction.client.user and interaction.client.user.avatar: # こちらでも可
            #    embed.set_thumbnail(url=interaction.client.user.avatar.url)


            embed.set_footer(text=f"コマンド実行者: {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed)
        except Exception as e:
            print(f"Error in invite command: {e}") # オリジナルに合わせる
            await interaction.response.send_message(
                "申し訳ありません、招待リンクの表示中にエラーが発生しました。\n"
                "しばらくしてからもう一度お試しいただくか、開発者(Discord:coffin299)にご連絡ください。",
                ephemeral=True
            )

    @app_commands.command(name="reloadconfig",description="config.yaml を再読み込みします（管理者専用）")
    async def reload_config_command(self, interaction: discord.Interaction) -> None:
        admin_ids = set(self.bot.cfg.get("admin_user_ids", []))
        if interaction.user.id not in admin_ids:
            await interaction.response.send_message(
                "❌ このコマンドを実行する権限がありません。",
                ephemeral=True)
            return

        try:
            # self.bot.cfg_path は DiscordLLMBot インスタンスの属性
            self.bot.cfg = load_config(self.bot.cfg_path)

            # DiscordLLMBot インスタンスの属性を更新
            self.bot.SYSTEM_PROMPT = self.bot.cfg.get("system_prompt")
            self.bot.STARTER_PROMPT = self.bot.cfg.get("starter_prompt")
            self.bot.ERROR_MESSAGES = self.bot.cfg.get("error_msg", {}) or {}
            # enabled_cogs も再読み込み (ただし動的なCogリロードはしない)
            if hasattr(self.bot, 'enabled_cogs'):
                self.bot.enabled_cogs = self.bot.cfg.get("enabled_cogs", [])


            await interaction.response.send_message(
                "✅ 設定を再読み込みしました。(Cog自体の変更を反映するにはボットの再起動が必要です)", ephemeral=True) # Cogは動的リロード非対応
            logging.info("config.yaml を手動再読み込みしました。") # logging は main.py で設定済み
        except Exception as e:
            logging.exception("設定の手動再読み込みに失敗")
            await interaction.response.send_message(
                f"⚠️ 再読み込みに失敗しました: {e}", ephemeral=True)

# Cogをボットに登録するための必須関数
async def setup(bot: 'DiscordLLMBot'): # commands.Bot 型または DiscordLLMBot 型
    await bot.add_cog(GeneralCommandsCog(bot))
    logging.info("Cog 'GeneralCommandsCog' がロードされました。")