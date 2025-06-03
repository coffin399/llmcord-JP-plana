import discord
from discord import app_commands
from discord.ext import commands
import logging
import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class SlashCommandsCog(commands.Cog, name="スラッシュコマンド"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.arona_repository = self.bot.config.get("arona_repository_url", "")
        self.plana_repository = self.bot.config.get("plana_repository_url", "")
        self.support_server_invite = self.bot.config.get("support_server_invite_url", "")

        self.bot_invite_url = self.bot.config.get("bot_invite_url")
        if not self.bot_invite_url:
            logger.error(
                "CRITICAL: shittim.config.yaml に 'bot_invite_url' が設定されていません。/invite コマンドは機能しません。")
        elif self.bot_invite_url in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]: 
            logger.error(
                "CRITICAL: shittim.config.yaml の 'bot_invite_url' がプレースホルダのままです。/invite コマンドは正しく機能しません。shittim.config.yamlを確認してください。")

        self.generic_help_message_text_ja = self.bot.config.get("generic_help_message_ja","ヘルプ")
        self.generic_help_message_text_en = self.bot.config.get("generic_help_message_en","Help")

    async def get_prefix_from_config(self) -> str:
        prefix = "!!"
        if hasattr(self.bot, 'config') and self.bot.config:
            cfg_prefix = self.bot.config.get('prefix')
            if isinstance(cfg_prefix, str) and cfg_prefix:
                prefix = cfg_prefix
        return prefix

    @app_commands.command(name="ping",
                          description="Botの現在のレイテンシを表示します。/ Shows the bot's current latency.")
    async def ping(self, interaction: discord.Interaction):
        latency_ms = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="Pong! 🏓",
            description=f"現在のレイテンシ / Current Latency: `{latency_ms}ms`",
            color=discord.Color.green() if latency_ms < 150 else (
                discord.Color.orange() if latency_ms < 300 else discord.Color.red())
        )
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/ping が実行されました。レイテンシ: {latency_ms}ms (User: {interaction.user.id})")

    @app_commands.command(name="serverinfo",
                          description="現在のサーバーに関する情報を表示します。/ Displays information about the current server.")
    async def serverinfo(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message(
                "このコマンドはサーバー内でのみ使用できます。\nThis command can only be used within a server.",
                ephemeral=False)
            return
        guild = interaction.guild

        embed = discord.Embed(title=f"{guild.name} のサーバー情報 / Server Information", color=discord.Color.blue())
        if guild.icon: embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="サーバーID / Server ID", value=guild.id, inline=True)

        owner_display = "不明 / Unknown"
        if guild.owner:
            owner_display = guild.owner.mention
        elif guild.owner_id:  # オーナーIDだけでも取得できれば
            try:
                owner_user = await self.bot.fetch_user(guild.owner_id)
                owner_display = owner_user.mention if owner_user else f"ID: {guild.owner_id}"
            except discord.NotFound:
                owner_display = f"ID: {guild.owner_id} (取得不可 / Not found)"
            except Exception as e:
                logger.warning(f"オーナー情報の取得に失敗 (ID: {guild.owner_id}): {e}")
                owner_display = f"ID: {guild.owner_id} (エラー / Error)"
        embed.add_field(name="オーナー / Owner", value=owner_display, inline=True)

        embed.add_field(name="メンバー数 / Member Count", value=guild.member_count, inline=True)
        embed.add_field(name="テキストチャンネル数 / Text Channels", value=len(guild.text_channels), inline=True)
        embed.add_field(name="ボイスチャンネル数 / Voice Channels", value=len(guild.voice_channels), inline=True)
        embed.add_field(name="ロール数 / Roles", value=len(guild.roles), inline=True)

        created_at_text = discord.utils.format_dt(guild.created_at, style='F')
        embed.add_field(name="作成日時 / Created At", value=created_at_text, inline=False)

        verification_level_str_ja = str(guild.verification_level).capitalize()  # これは日本語のEnum名ではない
        verification_level_str_en = guild.verification_level.name.replace('_', ' ').capitalize()  # Enumの .name から取得
        embed.add_field(name="認証レベル / Verification Level",
                        value=f"{verification_level_str_en}", inline=True)  # 英語ベースで表示

        if guild.features:
            features_str = ", ".join(f"`{f.replace('_', ' ').title()}`" for f in guild.features)
            embed.add_field(name="サーバー機能 / Server Features", value=features_str, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/serverinfo が実行されました。 (Server: {guild.id}, User: {interaction.user.id})")

    @app_commands.command(name="userinfo",
                          description="指定されたユーザーの情報を表示します。/ Displays information about the specified user.")
    @app_commands.describe(
        user="情報を表示するユーザー（任意、デフォルトはコマンド実行者） / User to display information for (optional, defaults to you)")
    async def userinfo(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        target_user = user or interaction.user

        embed = discord.Embed(title=f"{target_user.display_name} のユーザー情報 / User Information",
                              color=target_user.accent_color or discord.Color.blurple())
        if target_user.display_avatar: embed.set_thumbnail(url=target_user.display_avatar.url)

        username_display = f"{target_user.name}#{target_user.discriminator}" if target_user.discriminator != '0' else target_user.name
        embed.add_field(name="ユーザー名 / Username", value=username_display, inline=True)
        embed.add_field(name="ユーザーID / User ID", value=target_user.id, inline=True)

        bot_status_ja = "はい" if target_user.bot else "いいえ"
        bot_status_en = "Yes" if target_user.bot else "No"
        embed.add_field(name="Botアカウントか / Bot Account?", value=f"{bot_status_ja} / {bot_status_en}", inline=True)

        created_at_text = discord.utils.format_dt(target_user.created_at, style='F')
        embed.add_field(name="アカウント作成日時 / Account Created", value=created_at_text, inline=False)

        if interaction.guild and isinstance(target_user, discord.Member):
            member: discord.Member = target_user  # メンバーオブジェクトであることを明示

            joined_at_text = "不明 / Unknown"
            if member.joined_at:
                joined_at_text = discord.utils.format_dt(member.joined_at, style='F')
            embed.add_field(name="サーバー参加日時 / Joined Server", value=joined_at_text, inline=False)

            roles = [r.mention for r in reversed(member.roles) if r.name != "@everyone"]
            roles_count = len(roles)
            roles_display_value = "なし / None"
            if roles:
                roles_str = ", ".join(roles)
                if len(roles_str) > 1020:
                    roles_display_value = roles_str[:1017] + "..."
                else:
                    roles_display_value = roles_str
            embed.add_field(name=f"ロール ({roles_count}) / Roles ({roles_count})", value=roles_display_value,
                            inline=False)

            if member.nick:
                embed.add_field(name="ニックネーム / Nickname", value=member.nick, inline=True)
            if member.premium_since:
                premium_text = discord.utils.format_dt(member.premium_since, style='R')  # 相対時間
                embed.add_field(name="サーバーブースト開始 / Server Boosting Since", value=premium_text, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/userinfo が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    @app_commands.command(name="avatar",
                          description="指定されたユーザーのアバター画像URLを表示します。/ Displays the avatar of the specified user.")
    @app_commands.describe(
        user="アバターを表示するユーザー（任意、デフォルトはコマンド実行者） / User whose avatar to display (optional, defaults to you)")
    async def avatar_command(self, interaction: discord.Interaction, user: Optional[discord.User] = None):
        target_user = user or interaction.user
        avatar_url = target_user.display_avatar.url
        embed = discord.Embed(title=f"{target_user.display_name} のアバター / Avatar",
                              color=target_user.accent_color or discord.Color.default())
        embed.set_image(url=avatar_url)
        embed.add_field(name="画像URL / Image URL", value=f"[リンク / Link]({avatar_url})")
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/avatar が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    @app_commands.command(name="arona",
                          description="Arona Music Botのリポジトリを表示します / Shows the Arona Music Bot repository")
    async def arona_repo_slash(self, interaction: discord.Interaction) -> None:
        if self.arona_repository:
            message_ja = f"アロナ (Arona Music Bot) のリポジトリはこちらです！\n{self.arona_repository}"
            message_en = f"Here is the repository for Arona (Arona Music Bot)!\n{self.arona_repository}"
            await interaction.response.send_message(f"{message_ja}\n\n{message_en}", ephemeral=False)
            logger.info(f"/arona が実行されました。 (User: {interaction.user.id})")
        else:
            message_ja = "Arona Music BotのリポジトリURLが設定されていません。"
            message_en = "The repository URL for Arona Music Bot is not set."
            await interaction.response.send_message(f"{message_ja}\n{message_en}", ephemeral=False)
            logger.warning(f"/arona が実行されましたが、リポジトリURL未設定。 (User: {interaction.user.id})")

    @app_commands.command(name="plana",
                          description="llmcord-JP-planaのリポジトリを表示します / Shows the llmcord-JP-plana repository")
    async def plana_repo_slash(self, interaction: discord.Interaction) -> None:
        if self.plana_repository:
            message_ja = f"プラナ (llmcord-JP-plana) のリポジトリはこちらです！\n{self.plana_repository}"
            message_en = f"Here is the repository for Plana (llmcord-JP-plana)!\n{self.plana_repository}"
            await interaction.response.send_message(f"{message_ja}\n\n{message_en}", ephemeral=False)
            logger.info(f"/plana が実行されました。 (User: {interaction.user.id})")
        else:
            message_ja = "llmcord-JP-planaのリポジトリURLが設定されていません。"
            message_en = "The repository URL for llmcord-JP-plana is not set."
            await interaction.response.send_message(f"{message_ja}\n{message_en}", ephemeral=False)
            logger.warning(f"/plana が実行されましたが、リポジトリURL未設定。 (User: {interaction.user.id})")

    @app_commands.command(name="support",
                          description="サポートサーバーの招待コードを表示します / Shows the support server invite code")
    async def support_server_slash(self, interaction: discord.Interaction) -> None:
        if self.support_server_invite and self.support_server_invite != "https://discord.gg/HogeFugaPiyo":
            message_ja = f"サポートサーバーへの招待リンクはこちらです！\n{self.support_server_invite}"
            message_en = f"Here is the invitation link to our support server!\n{self.support_server_invite}"
            await interaction.response.send_message(f"{message_ja}\n\n{message_en}", ephemeral=False)
            logger.info(f"/support が実行されました。 (User: {interaction.user.id})")
        else:
            message_ja = "申し訳ありませんが、現在サポートサーバーの招待リンクが設定されていません。\n管理者にお問い合わせください。"
            message_en = "Sorry, the invitation link for the support server is not currently set.\nPlease contact an administrator."
            await interaction.response.send_message(f"{message_ja}\n\n{message_en}", ephemeral=False)
            logger.warning(
                f"/support が実行されましたが、招待リンク未設定またはプレースホルダ。 (User: {interaction.user.id})")

    @app_commands.command(name="invite",
                          description="このBotをあなたのサーバーに招待します。/ Invites this bot to your server.")
    async def invite_bot_slash(self, interaction: discord.Interaction) -> None:
        invite_url_to_display = self.bot_invite_url
        bot_name = self.bot.user.name if self.bot.user else "This Bot"

        if invite_url_to_display and invite_url_to_display not in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]:
            title_ja = f"{bot_name} をサーバーに招待"
            title_en = f"Invite {bot_name} to Your Server"
            desc_ja = "下のボタンからPLANAをあなたのサーバーに招待できます！"
            desc_en = "You can invite PLANA to your server using the button below!"

            embed = discord.Embed(
                title=f"{title_ja} / {title_en}",
                description=f"{desc_ja}\n\n{desc_en}",
                color=discord.Color.og_blurple()
            )
            if self.bot.user and self.bot.user.avatar:
                embed.set_thumbnail(url=self.bot.user.avatar.url)

            footer_ja = f"{bot_name} をご利用いただきありがとうございます！"
            footer_en = f"Thank you for using {bot_name}!"
            embed.set_footer(text=f"{footer_ja}\n{footer_en}")

            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="サーバーに招待 / Invite to Server", style=discord.ButtonStyle.link,
                                            url=invite_url_to_display, emoji="💌"))
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
            logger.info(f"/invite が実行されました。 (User: {interaction.user.id})")
        else:
            error_message_ja = "エラー: Botの招待URLが `config.yaml` に正しく設定されていません。\nBotの管理者にご連絡ください。"
            error_message_en = "Error: The bot's invitation URL is not set correctly in `config.yaml`.\nPlease contact the bot administrator."
            await interaction.response.send_message(f"{error_message_ja}\n\n{error_message_en}", ephemeral=True)
            logger.error(
                f"/invite が実行されましたが、招待URLがconfig.yamlに未設定またはプレースホルダです。 (User: {interaction.user.id})")

    @app_commands.command(name="help", description="Botのヘルプ情報を表示します。/ Displays bot help information.")
    async def help_slash_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        bot_name_ja = self.bot.user.name if self.bot.user else "当Bot"
        bot_name_en = self.bot.user.name if self.bot.user else "This Bot"
        bot_avatar_url = self.bot.user.avatar.url if self.bot.user and self.bot.user.avatar else None
        prefix = await self.get_prefix_from_config()

        embed = discord.Embed(
            title=f"{bot_name_ja} ヘルプ / {bot_name_en} Help",
            description=f"{self.generic_help_message_text_ja}\n\n{self.generic_help_message_text_en}",
            color=discord.Color.teal()
        )
        if bot_avatar_url:
            embed.set_thumbnail(url=bot_avatar_url)

        desc_ja_detail = "より詳細な情報は、以下のコマンドで確認できます。"
        desc_en_detail = "For more detailed information, please check the following commands:"
        llm_help_cmd_ja = "• **AI対話機能:** `/llm_help` (または `/llm_help_en`)"
        llm_help_cmd_en = "• **AI Chat (LLM):** `/llm_help` (or `/llm_help_en`)"
        music_help_cmd_ja = "• **音楽再生機能:** `/music_help`"
        music_help_cmd_en = "• **Music Playback:** `/music_help`"

        prefix_info_ja = f"プレフィックスコマンドも利用可能です (現在のプレフィックス: `{prefix}` )。"
        prefix_info_en = f"(Prefix commands are also available. Current prefix: `{prefix}` )"

        embed.add_field(
            name="詳細情報 / More Information",
            value=f"{desc_ja_detail}\n{llm_help_cmd_ja}\n{music_help_cmd_ja}\n{prefix_info_ja}\n\n"
                  f"{desc_en_detail}\n{llm_help_cmd_en}\n{music_help_cmd_en}\n{prefix_info_en}",
            inline=False
        )

        main_features_title_ja = "主な機能"
        main_features_ja_val = (
            "- **AIとの対話 (LLM):** メンションで話しかけるとAIが応答します。画像も認識可能です。\n"
            "- **音楽再生:** ボイスチャンネルで音楽を再生、キュー管理、各種操作ができます。\n"
            "- **情報表示:** サーバー情報、ユーザー情報、Botのレイテンシなどを表示します。"
        )
        main_features_en_val = (
            "- **AI Chat (LLM):** Mention the bot to talk with AI. It can also recognize images (if model supports).\n"
            "- **Music Playback:** Play music in voice channels, manage queues, and perform various operations.\n"
            "- **Information Display:** Show server info, user info, bot latency, etc."
        )
        embed.add_field(
            name=f"{main_features_title_ja} / Main Features",
            value=f"{main_features_ja_val}\n\n{main_features_en_val}",
            inline=False
        )

        utility_title_ja = "便利なコマンド"
        utility_cmds_ja = [f"`/ping` - Botの応答速度を確認", f"`/serverinfo` - サーバー情報を表示",
                           f"`/userinfo [ユーザー]` - ユーザー情報を表示", f"`/avatar [ユーザー]` - アバター画像を表示",
                           f"`/invite` - Botの招待リンクを表示"]
        utility_cmds_en = [f"`/ping` - Check bot's latency", f"`/serverinfo` - Display server info",
                           f"`/userinfo [user]` - Display user info", f"`/avatar [user]` - Display avatar",
                           f"`/invite` - Display bot invite link"]

        if self.support_server_invite and self.support_server_invite != "https://discord.gg/HogeFugaPiyo":
            utility_cmds_ja.append(f"`/support` - サポートサーバー招待")
            utility_cmds_en.append(f"`/support` - Support server invite")
        if self.plana_repository:
            utility_cmds_ja.append(f"`/plana` - Plana (Bot)リポジトリ")
            utility_cmds_en.append(f"`/plana` - Plana (Bot) repository")

        embed.add_field(
            name=f"{utility_title_ja} / Useful Commands",
            value="\n".join(utility_cmds_ja) + "\n\n" + "\n".join(utility_cmds_en),
            inline=False
        )

        footer_ja = "<> は必須引数、[] は任意引数を表します。"
        footer_en = "<> denotes a required argument, [] denotes an optional argument."
        embed.set_footer(text=f"{footer_ja}\n{footer_en}")

        view_items = []
        if self.bot_invite_url and self.bot_invite_url not in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]:
            view_items.append(discord.ui.Button(label="Botを招待 / Invite Bot", style=discord.ButtonStyle.link,
                                                url=self.bot_invite_url))

        if view_items:
            view = discord.ui.View()
            for item in view_items: view.add_item(item)
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=embed, ephemeral=False)

        logger.info(f"/help (概要) が実行されました。 (User: {interaction.user.id})")

async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("SlashCommandsCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        raise commands.ExtensionFailed("SlashCommandsCog", "Botのconfigがロードされていません。")

    cog = SlashCommandsCog(bot)
    await bot.add_cog(cog)
    logger.info("SlashCommandsCogが正常にロードされました。")