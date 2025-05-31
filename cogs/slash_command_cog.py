import discord
from discord import app_commands
from discord.ext import commands
import logging
import datetime  # userinfo, serverinfo などで使用

logger = logging.getLogger(__name__)


class SlashCommandsCog(commands.Cog, name="スラッシュコマンド"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # configから必要な値を取得 (Botインスタンスにconfigがロードされている前提)
        # これらのキーは config.yaml のトップレベルに定義することを想定
        self.arona_repository = self.bot.config.get("arona_repository_url", "")
        self.plana_repository = self.bot.config.get("plana_repository_url", "")
        self.support_server_invite = self.bot.config.get("support_server_invite_url", "")
        self.bot_invite_url = self.bot.config.get("bot_invite_url", "")
        # ヘルプメッセージはLLM Cogのものを参照するか、汎用的なものをconfigから取得
        # ここでは、汎用的なヘルプメッセージが config.yaml のトップレベルにあると仮定
        self.generic_help_message = self.bot.config.get("generic_help_message", "ヘルプメッセージが設定されていません。")

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
                roles_str = ", ".join(roles);
                embed.add_field(name=f"ロール ({len(roles)})",
                                value=roles_str[:1020] if len(roles_str) > 1020 else (roles_str or "なし"),
                                inline=False)  # 省略処理を修正
            else:
                embed.add_field(name="ロール", value="なし", inline=False)
            if member.nick: embed.add_field(name="ニックネーム", value=member.nick, inline=True)
            if member.premium_since: embed.add_field(name="サーバーブースト開始",
                                                     value=discord.utils.format_dt(member.premium_since, style='R'),
                                                     inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/userinfo が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    echo_group = app_commands.Group(name="echo", description="テキストをBotにエコーさせるコマンドグループ")

    @echo_group.command(name="say", description="指定されたテキストをBotに発言させます。")
    @app_commands.describe(text_to_echo="Botに発言させるテキスト")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def echo_say(self, interaction: discord.Interaction, text_to_echo: str):
        if interaction.channel:
            try:
                await interaction.response.defer(ephemeral=False, thinking=False)
                await interaction.channel.send(text_to_echo)
                await interaction.followup.send("メッセージを送信しました。", ephemeral=False)
                logger.info(f"/echo say が実行されました。Text: '{text_to_echo}' (User: {interaction.user.id})")
            except discord.errors.Forbidden:
                await interaction.followup.send("メッセージを送信する権限がありません。", ephemeral=False)
            except Exception as e:
                logger.error(f"/echo say でエラー: {e}", exc_info=True)
                await interaction.followup.send("メッセージの送信中にエラーが発生しました。", ephemeral=False)
        else:
            await interaction.response.send_message("このコマンドはテキストチャンネルで実行してください。",
                                                    ephemeral=False)

    @echo_say.error
    async def echo_say_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません (メッセージ管理権限が必要です)。", ephemeral=False)
        else:
            logger.error(f"/echo say で未処理のエラー: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message("コマンドの実行中にエラーが発生しました。", ephemeral=False)
            else:
                await interaction.followup.send("コマンドの実行中にエラーが発生しました。", ephemeral=False)

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

    # --- 以下、追加されたコマンド ---
    @app_commands.command(name="help", description="ヘルプメッセージを表示します")
    async def help_slash(self, interaction: discord.Interaction) -> None:  # メソッド名を _help から変更
        # self.generic_help_message は __init__ で config から取得
        await interaction.response.send_message(self.generic_help_message, ephemeral=False)
        logger.info(f"/help が実行されました。 (User: {interaction.user.id})")

    @app_commands.command(name="arona", description="Arona Music Botのリポジトリを表示します")
    async def arona_repo_slash(self, interaction: discord.Interaction) -> None:  # メソッド名を変更
        if self.arona_repository:  # __init__ で取得したインスタンス変数を使用
            message = f"アロナ (Arona Music Bot) のリポジトリはこちらです！\n{self.arona_repository}"
            await interaction.response.send_message(message, ephemeral=False)
            logger.info(f"/arona が実行されました。 (User: {interaction.user.id})")
        else:
            await interaction.response.send_message("Arona Music BotのリポジトリURLが設定されていません。",
                                                    ephemeral=False)
            logger.warning(f"/arona が実行されましたが、リポジトリURL未設定。 (User: {interaction.user.id})")

    @app_commands.command(name="plana", description="llmcord-JP-planaのリポジトリを表示します")
    async def plana_repo_slash(self, interaction: discord.Interaction) -> None:  # メソッド名を変更
        if self.plana_repository:  # __init__ で取得したインスタンス変数を使用
            message = f"プラナ (llmcord-JP-plana) のリポジトリはこちらです！\n{self.plana_repository}"
            await interaction.response.send_message(message, ephemeral=False)
            logger.info(f"/plana が実行されました。 (User: {interaction.user.id})")
        else:
            await interaction.response.send_message("llmcord-JP-planaのリポジトリURLが設定されていません。",
                                                    ephemeral=False)
            logger.warning(f"/plana が実行されましたが、リポジトリURL未設定。 (User: {interaction.user.id})")

    @app_commands.command(name="support", description="サポートサーバーの招待コードを表示します")
    async def support_server_slash(self, interaction: discord.Interaction) -> None:  # メソッド名を変更
        if self.support_server_invite and self.support_server_invite != "https://discord.gg/HogeFugaPiyo":  # プレースホルダチェックも残す
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

    @app_commands.command(name="invite", description="Botをサーバーに招待します")
    async def invite_bot_slash(self, interaction: discord.Interaction) -> None:  # メソッド名を変更
        if self.bot_invite_url and self.bot_invite_url != "YOUR_INVITE_URL_HERE":  # プレースホルダチェックも
            message = f"私をあなたのサーバーに招待しますか？\n{self.bot_invite_url}"
            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(label="招待リンク", style=discord.ButtonStyle.link, url=self.bot_invite_url))
            await interaction.response.send_message(message, view=view, ephemeral=False)
            logger.info(f"/invite が実行されました。 (User: {interaction.user.id})")
        else:
            # 招待URL未設定の場合のエラーメッセージは、実行者のみに見せる方が良い場合もある
            await interaction.response.send_message(
                "エラー: Botの招待URLが設定されていません。開発者にご連絡ください。",
                ephemeral=True  # ここは True の方が適切かもしれないが、指示通り False に近づけるなら False
            )
            logger.error(f"/invite が実行されましたが、招待URL未設定またはプレースホルダ。 (User: {interaction.user.id})")


async def setup(bot: commands.Bot):
    # Botインスタンスにconfigがロードされているか確認
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("SlashCommandsCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        # Cogのロードを中止させる
        raise commands.ExtensionFailed("SlashCommandsCog", "Botのconfigがロードされていません。")

    cog = SlashCommandsCog(bot)
    await bot.add_cog(cog)
    logger.info("SlashCommandsCogが正常にロードされました。")