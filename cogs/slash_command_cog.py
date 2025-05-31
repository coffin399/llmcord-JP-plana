import discord
from discord import app_commands
from discord.ext import commands
import logging
import datetime

logger = logging.getLogger(__name__)


class SlashCommandsCog(commands.Cog, name="スラッシュコマンド"):  # クラス名とCog名を変更
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Botの現在のレイテンシを表示します。")
    async def ping(self, interaction: discord.Interaction):
        """Botの応答速度（レイテンシ）をミリ秒単位で表示します。"""
        latency_ms = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="Pong! 🏓",
            description=f"現在のレイテンシ: `{latency_ms}ms`",
            color=discord.Color.green() if latency_ms < 150 else (
                discord.Color.orange() if latency_ms < 300 else discord.Color.red())
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"/ping が実行されました。レイテンシ: {latency_ms}ms (User: {interaction.user.id})")

    @app_commands.command(name="serverinfo", description="現在のサーバーに関する情報を表示します。")
    async def serverinfo(self, interaction: discord.Interaction):
        """コマンドが実行されたサーバーの詳細情報を表示します。"""
        if not interaction.guild:
            await interaction.response.send_message("このコマンドはサーバー内でのみ使用できます。", ephemeral=True)
            return

        guild = interaction.guild
        embed = discord.Embed(
            title=f"{guild.name} のサーバー情報",
            color=discord.Color.blue()
        )
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        embed.add_field(name="サーバーID", value=guild.id, inline=True)
        embed.add_field(name="オーナー", value=guild.owner.mention if guild.owner else "不明", inline=True)
        embed.add_field(name="メンバー数", value=guild.member_count, inline=True)
        embed.add_field(name="テキストチャンネル数", value=len(guild.text_channels), inline=True)
        embed.add_field(name="ボイスチャンネル数", value=len(guild.voice_channels), inline=True)
        embed.add_field(name="ロール数", value=len(guild.roles), inline=True)
        embed.add_field(name="作成日時", value=discord.utils.format_dt(guild.created_at, style='F'), inline=False)

        verification_level_str = str(guild.verification_level).capitalize()
        embed.add_field(name="認証レベル", value=verification_level_str, inline=True)

        if guild.features:
            embed.add_field(name="サーバー機能",
                            value=", ".join(f"`{feature.replace('_', ' ').title()}`" for feature in guild.features),
                            inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"/serverinfo が実行されました。 (Server: {guild.id}, User: {interaction.user.id})")

    @app_commands.command(name="userinfo", description="指定されたユーザーの情報を表示します。")
    @app_commands.describe(user="情報を表示するユーザー（任意、デフォルトはコマンド実行者）")
    async def userinfo(self, interaction: discord.Interaction, user: discord.User = None):
        """
        指定されたユーザー（またはコマンド実行者）の詳細情報を表示します。
        ユーザーはサーバーメンバーである必要はありません。
        """
        target_user = user or interaction.user

        embed = discord.Embed(
            title=f"{target_user.display_name} のユーザー情報",
            color=target_user.accent_color or discord.Color.blurple()
        )
        embed.set_thumbnail(url=target_user.display_avatar.url)

        embed.add_field(name="ユーザー名",
                        value=f"{target_user.name}#{target_user.discriminator}" if target_user.discriminator != '0' else target_user.name,
                        inline=True)
        embed.add_field(name="ユーザーID", value=target_user.id, inline=True)
        embed.add_field(name="Botアカウントか", value="はい" if target_user.bot else "いいえ", inline=True)

        created_at_dt = discord.utils.format_dt(target_user.created_at, style='F')
        embed.add_field(name="アカウント作成日時", value=created_at_dt, inline=False)

        if interaction.guild and isinstance(target_user, discord.Member):
            member: discord.Member = target_user
            joined_at_dt = discord.utils.format_dt(member.joined_at, style='F') if member.joined_at else "不明"
            embed.add_field(name="サーバー参加日時", value=joined_at_dt, inline=False)

            roles = [role.mention for role in reversed(member.roles) if role.name != "@everyone"]
            if roles:
                roles_str = ", ".join(roles)
                if len(roles_str) > 1020:
                    roles_str = roles_str[:1017] + "..."
                embed.add_field(name=f"ロール ({len(roles)})", value=roles_str or "なし", inline=False)
            else:
                embed.add_field(name="ロール", value="なし", inline=False)

            if member.nick:
                embed.add_field(name="ニックネーム", value=member.nick, inline=True)

            if member.premium_since:
                premium_dt = discord.utils.format_dt(member.premium_since, style='R')
                embed.add_field(name="サーバーブースト開始", value=premium_dt, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"/userinfo が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    echo_group = app_commands.Group(name="echo", description="テキストをBotにエコーさせるコマンドグループ")

    @echo_group.command(name="say", description="指定されたテキストをBotに発言させます。")
    @app_commands.describe(text_to_echo="Botに発言させるテキスト")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def echo_say(self, interaction: discord.Interaction, text_to_echo: str):
        if interaction.channel:
            try:
                await interaction.response.defer(ephemeral=True, thinking=False)
                await interaction.channel.send(text_to_echo)
                await interaction.followup.send("メッセージを送信しました。", ephemeral=True)
                logger.info(f"/echo say が実行されました。Text: '{text_to_echo}' (User: {interaction.user.id})")
            except discord.errors.Forbidden:
                await interaction.followup.send("メッセージを送信する権限がありません。", ephemeral=True)
            except Exception as e:
                logger.error(f"/echo say でエラー: {e}", exc_info=True)
                await interaction.followup.send("メッセージの送信中にエラーが発生しました。", ephemeral=True)
        else:
            await interaction.response.send_message("このコマンドはテキストチャンネルで実行してください。",
                                                    ephemeral=True)

    @echo_say.error
    async def echo_say_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "このコマンドを実行する権限がありません (メッセージ管理権限が必要です)。", ephemeral=True)
        else:
            logger.error(f"/echo say で未処理のエラー: {error}", exc_info=True)
            if not interaction.response.is_done():
                await interaction.response.send_message("コマンドの実行中にエラーが発生しました。", ephemeral=True)
            else:
                await interaction.followup.send("コマンドの実行中にエラーが発生しました。", ephemeral=True)

    @app_commands.command(name="avatar", description="指定されたユーザーのアバター画像URLを表示します。")
    @app_commands.describe(user="アバターを表示するユーザー（任意、デフォルトはコマンド実行者）")
    async def avatar_command(self, interaction: discord.Interaction, user: discord.User = None):
        target_user = user or interaction.user

        embed = discord.Embed(
            title=f"{target_user.display_name} のアバター",
            color=target_user.accent_color or discord.Color.default()
        )
        embed.set_image(url=target_user.display_avatar.url)
        embed.add_field(name="画像URL", value=f"[リンク]({target_user.display_avatar.url})")

        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info(f"/avatar が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")


async def setup(bot: commands.Bot):
    cog = SlashCommandsCog(bot)  # クラス名を変更
    await bot.add_cog(cog)
    logger.info("SlashCommandsCogが正常にロードされました。")  # ログメッセージも変更