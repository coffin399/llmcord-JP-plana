import discord
from discord import app_commands
from discord.ext import commands
import logging
import datetime
from typing import Optional  # Optionalをインポート

logger = logging.getLogger(__name__)


class SlashCommandsCog(commands.Cog, name="スラッシュコマンド"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.arona_repository = self.bot.config.get("arona_repository_url", "")
        self.plana_repository = self.bot.config.get("plana_repository_url", "")
        self.support_server_invite = self.bot.config.get("support_server_invite_url", "")
        self.bot_invite_url = self.bot.config.get("bot_invite_url")
        if not self.bot_invite_url:
            logger.error("CRITICAL: config.yaml に 'bot_invite_url' が設定されていません。")
        elif self.bot_invite_url in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]:
            logger.error("CRITICAL: 'bot_invite_url' がプレースホルダのままです。")
        self.generic_help_message_text = self.bot.config.get("generic_help_message",
                                                             "ヘルプメッセージが設定されていません。")

    async def get_prefix_from_config(self) -> str:
        prefix = "!!"
        if hasattr(self.bot, 'config') and self.bot.config:
            cfg_prefix = self.bot.config.get('prefix')
            if isinstance(cfg_prefix, str) and cfg_prefix:
                prefix = cfg_prefix
        return prefix

    # --- 既存コマンド (ping, serverinfo, userinfo, avatar_command, arona, plana, support は変更なし) ---
    @app_commands.command(name="ping", description="Botの現在のレイテンシを表示します。")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="Pong! 🏓",
            description=f"現在のレイテンシ: `{latency_ms}ms`",
            color=discord.Color.green() if latency_ms < 150 else (
                discord.Color.orange() if latency_ms < 300 else discord.Color.red())
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/ping が実行されました。レイテンシ: {latency_ms}ms (User: {interaction.user.id})")

    @app_commands.command(name="serverinfo", description="現在のサーバーに関する情報を表示します。")
    async def serverinfo(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("このコマンドはサーバー内でのみ使用できます。", ephemeral=False)
            return
        guild = interaction.guild
        embed = discord.Embed(title=f"{guild.name} のサーバー情報", color=discord.Color.blue())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="サーバーID", value=guild.id, inline=True)
        embed.add_field(name="オーナー", value=guild.owner.mention if guild.owner else "不明", inline=True)
        embed.add_field(name="メンバー数", value=guild.member_count, inline=True)
        embed.add_field(name="テキストチャンネル数", value=len(guild.text_channels), inline=True)
        embed.add_field(name="ボイスチャンネル数", value=len(guild.voice_channels), inline=True)
        embed.add_field(name="ロール数", value=len(guild.roles), inline=True)
        embed.add_field(name="作成日時", value=discord.utils.format_dt(guild.created_at, style='F'), inline=False)
        embed.add_field(name="認証レベル", value=str(guild.verification_level).capitalize(), inline=True)
        if guild.features:
            embed.add_field(name="サーバー機能",
                            value=", ".join(f"`{f.replace('_', ' ').title()}`" for f in guild.features), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/serverinfo が実行されました。 (Server: {guild.id}, User: {interaction.user.id})")

    @app_commands.command(name="userinfo", description="指定されたユーザーの情報を表示します。")
    @app_commands.describe(user="情報を表示するユーザー（任意、デフォルトはコマンド実行者）")
    async def userinfo(self, interaction: discord.Interaction, user: discord.User = None):
        target_user = user or interaction.user
        embed = discord.Embed(title=f"{target_user.display_name} のユーザー情報",
                              color=target_user.accent_color or discord.Color.blurple())
        embed.set_thumbnail(url=target_user.display_avatar.url)
        username_val = f"{target_user.name}#{target_user.discriminator}" if target_user.discriminator != '0' else target_user.name
        embed.add_field(name="ユーザー名", value=username_val, inline=True)
        embed.add_field(name="ユーザーID", value=target_user.id, inline=True)
        embed.add_field(name="Botアカウントか", value="はい" if target_user.bot else "いいえ", inline=True)
        embed.add_field(name="アカウント作成日時", value=discord.utils.format_dt(target_user.created_at, style='F'),
                        inline=False)
        if interaction.guild and isinstance(target_user, discord.Member):
            member: discord.Member = target_user
            embed.add_field(name="サーバー参加日時",
                            value=discord.utils.format_dt(member.joined_at, style='F') if member.joined_at else "不明",
                            inline=False)
            roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
            if roles:
                roles_str = ", ".join(roles)
                embed.add_field(name=f"ロール ({len(roles)})",
                                value=roles_str[:1020] if len(roles_str) > 1020 else (roles_str or "なし"),
                                inline=False)
            else:
                embed.add_field(name="ロール", value="なし", inline=False)
            if member.nick: embed.add_field(name="ニックネーム", value=member.nick, inline=True)
            if member.premium_since: embed.add_field(name="サーバーブースト開始",
                                                     value=discord.utils.format_dt(member.premium_since, style='R'),
                                                     inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/userinfo が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    @app_commands.command(name="avatar", description="指定されたユーザーのアバター画像URLを表示します。")
    @app_commands.describe(user="アバターを表示するユーザー（任意、デフォルトはコマンド実行者）")
    async def avatar_command(self, interaction: discord.Interaction, user: discord.User = None):
        target_user = user or interaction.user
        embed = discord.Embed(title=f"{target_user.display_name} のアバター",
                              color=target_user.accent_color or discord.Color.default())
        embed.set_image(url=target_user.display_avatar.url)
        embed.add_field(name="画像URL", value=f"[リンク]({target_user.display_avatar.url})")
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/avatar が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    @app_commands.command(name="arona", description="Arona Music Botのリポジトリを表示します")
    async def arona_repo_slash(self, interaction: discord.Interaction) -> None:
        if self.arona_repository:
            message = f"アロナ (Arona Music Bot) のリポジトリはこちらです！\n{self.arona_repository}"
            await interaction.response.send_message(message, ephemeral=False)
            logger.info(f"/arona が実行されました。 (User: {interaction.user.id})")
        else:
            await interaction.response.send_message("Arona Music BotのリポジトリURLが設定されていません。",
                                                    ephemeral=False)
            logger.warning(f"/arona が実行されましたが、リポジトリURL未設定。 (User: {interaction.user.id})")

    @app_commands.command(name="plana", description="llmcord-JP-planaのリポジトリを表示します")
    async def plana_repo_slash(self, interaction: discord.Interaction) -> None:
        if self.plana_repository:
            message = f"プラナ (llmcord-JP-plana) のリポジトリはこちらです！\n{self.plana_repository}"
            await interaction.response.send_message(message, ephemeral=False)
            logger.info(f"/plana が実行されました。 (User: {interaction.user.id})")
        else:
            await interaction.response.send_message("llmcord-JP-planaのリポジトリURLが設定されていません。",
                                                    ephemeral=False)
            logger.warning(f"/plana が実行されましたが、リポジトリURL未設定。 (User: {interaction.user.id})")

    @app_commands.command(name="support", description="サポートサーバーの招待コードを表示します")
    async def support_server_slash(self, interaction: discord.Interaction) -> None:
        if self.support_server_invite and self.support_server_invite != "https://discord.gg/HogeFugaPiyo":
            message = f"サポートサーバーへの招待リンクはこちらです！\n{self.support_server_invite}"
            await interaction.response.send_message(message, ephemeral=False)
            logger.info(f"/support が実行されました。 (User: {interaction.user.id})")
        else:
            await interaction.response.send_message(
                "申し訳ありませんが、現在サポートサーバーの招待リンクが設定されていません。\n管理者にお問い合わせください。",
                ephemeral=False
            )
            logger.warning(
                f"/support が実行されましたが、招待リンク未設定またはプレースホルダ。 (User: {interaction.user.id})")

    @app_commands.command(name="invite", description="このBotをあなたのサーバーに招待します。")
    async def invite_bot_slash(self, interaction: discord.Interaction) -> None:
        invite_url_to_display = self.bot_invite_url
        if invite_url_to_display and invite_url_to_display not in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]:
            message_title = f"{self.bot.user.name} をサーバーに招待"
            message_description = "下のボタンからPLANAをあなたのサーバーに招待できます！"
            embed = discord.Embed(title=message_title, description=message_description,
                                  color=discord.Color.og_blurple())
            if self.bot.user and self.bot.user.avatar: embed.set_thumbnail(url=self.bot.user.avatar.url)
            embed.set_footer(text=f"{self.bot.user.name} をご利用いただきありがとうございます！")
            view = discord.ui.View();
            view.add_item(
                discord.ui.Button(label="サーバーに招待する", style=discord.ButtonStyle.link, url=invite_url_to_display,
                                  emoji="💌"))
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            logger.info(f"/invite が実行されました。 (User: {interaction.user.id})")
        else:
            error_message = "エラー: Botの招待URLが `config.yaml` に正しく設定されていません。\nBotの管理者にご連絡ください。"
            await interaction.response.send_message(error_message, ephemeral=True)
            logger.error(
                f"/invite が実行されましたが、招待URLがconfig.yamlに未設定またはプレースホルダです。 (User: {interaction.user.id})")

    # --- ここから新しい /help コマンド ---
    @app_commands.command(name="help", description="Botのヘルプ情報を表示します。特定の機能のヘルプも表示可能です。")
    @app_commands.describe(module="ヘルプを表示したい機能 (例: llm, music)")
    async def help_slash_command(self, interaction: discord.Interaction, module: Optional[str] = None):
        """
        Botの機能に関するヘルプ情報を表示します。
        'module' 引数に 'llm' または 'music' を指定すると、各機能の詳細なヘルプを表示します。
        引数なしの場合は、全体の概要と各詳細ヘルプへの案内を表示します。
        """
        await interaction.response.defer(ephemeral=False)  # ephemeral=False

        bot_name = self.bot.user.name if self.bot.user else "当Bot"
        bot_avatar_url = self.bot.user.avatar.url if self.bot.user and self.bot.user.avatar else None
        prefix = await self.get_prefix_from_config()

        if module:
            module_lower = module.lower()
            if module_lower == "llm":
                llm_cog = self.bot.get_cog("LLM")
                if llm_cog and hasattr(llm_cog, 'llm_help_slash'):  # LLMCogにllm_help_slashがあると仮定
                    # LLMCogのヘルプコマンドを直接呼び出すのは推奨されないため、Embedを生成するメソッドを呼び出す
                    if hasattr(llm_cog, 'generate_llm_help_embed'):  # LLMCogにこのメソッドがあると仮定
                        embed = await llm_cog.generate_llm_help_embed(interaction)  # interactionを渡す
                        await interaction.followup.send(embed=embed, ephemeral=False)
                    else:  # フォールバック
                        await interaction.followup.send(f"LLM機能の詳細ヘルプは `/llm_help` コマンドで確認できます。",
                                                        ephemeral=False)
                    return
                else:
                    await interaction.followup.send("LLM機能モジュールが見つからないか、ヘルプ機能が実装されていません。",
                                                    ephemeral=False)
                    return
            elif module_lower == "music":
                music_cog = self.bot.get_cog("音楽")
                if music_cog and hasattr(music_cog, 'music_help_slash'):  # MusicCogにmusic_help_slashがあると仮定
                    if hasattr(music_cog, 'get_music_commands_help_embed'):  # MusicCogにこのメソッドがあると仮定
                        embed = music_cog.get_music_commands_help_embed(prefix)  # プレフィックスを渡す
                        await interaction.followup.send(embed=embed, ephemeral=False)
                    else:  # フォールバック
                        await interaction.followup.send(f"音楽機能の詳細ヘルプは `/music_help` コマンドで確認できます。",
                                                        ephemeral=False)
                    return
                else:
                    await interaction.followup.send(
                        "音楽機能モジュールが見つからないか、ヘルプ機能が実装されていません。", ephemeral=False)
                    return
            else:
                await interaction.followup.send(f"'{module}' という機能モジュールのヘルプは現在提供されていません。\n"
                                                f"利用可能なモジュール: `llm`, `music`", ephemeral=False)
                return

        # 引数なしの場合: 全体ヘルプ
        embed = discord.Embed(
            title=f"{bot_name} 機能概要ヘルプ",
            description=self.generic_help_message_text + \
                        f"\n\nより詳細な情報は、以下のコマンドで確認できます。\n"
                        f"• AI対話機能: `/help module:llm` または `/llm_help`\n"
                        f"• 音楽再生機能: `/help module:music` または `/music_help`\n"
                        f"\nプレフィックスコマンドも利用可能です (現在のプレフィックス: `{prefix}` )。",
            color=discord.Color.teal()
        )
        if bot_avatar_url:
            embed.set_thumbnail(url=bot_avatar_url)

        embed.add_field(
            name="主な機能",
            value="- **AIとの対話 (LLM):** メンションで話しかけるとAIが応答します。画像も認識可能です。\n"
                  "- **音楽再生:** ボイスチャンネルで音楽を再生、キュー管理、各種操作ができます。\n"
                  "- **情報表示:** サーバー情報、ユーザー情報、Botのレイテンシなどを表示します。",
            inline=False
        )

        other_commands_value = (
            f"`/ping` - Botの応答速度を確認\n"
            f"`/serverinfo` - サーバー情報を表示\n"
            f"`/userinfo [ユーザー]` - ユーザー情報を表示\n"
            f"`/avatar [ユーザー]` - アバター画像を表示\n"
            f"`/invite` - Botの招待リンクを表示\n"
        )
        if self.support_server_invite and self.support_server_invite != "https://discord.gg/HogeFugaPiyo":
            other_commands_value += f"`/support` - サポートサーバーの招待リンクを表示\n"
        if self.plana_repository:
            other_commands_value += f"`/plana` - Plana (このBot) のリポジトリを表示\n"

        embed.add_field(name="便利なコマンド", value=other_commands_value.strip(), inline=False)

        if self.bot_invite_url and self.bot_invite_url not in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]:
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(label="Botをサーバーに招待", style=discord.ButtonStyle.link, url=self.bot_invite_url))
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=embed, ephemeral=False)

        logger.info(f"/help (概要) が実行されました。 (User: {interaction.user.id})")
    # --- /help コマンドここまで ---


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("SlashCommandsCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        raise commands.ExtensionFailed("SlashCommandsCog", "Botのconfigがロードされていません。")

    cog = SlashCommandsCog(bot)
    await bot.add_cog(cog)
    logger.info("SlashCommandsCogが正常にロードされました。")