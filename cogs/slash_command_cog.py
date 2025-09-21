import discord
from discord import app_commands
from discord.ext import commands
import logging
import datetime
from typing import Optional
import random  # ガチャ機能のために追加
import re  # nDnダイスロールのために追加

logger = logging.getLogger(__name__)


class SlashCommandsCog(commands.Cog, name="スラッシュコマンド"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # configから必要な値を取得
        self.arona_repository = self.bot.config.get("arona_repository_url", "")
        self.plana_repository = self.bot.config.get("plana_repository_url", "")

        # サポート連絡先の設定
        self.support_x_url = self.bot.config.get("support_x_url", "https://x.com/coffin299")
        self.support_discord_id = self.bot.config.get("support_discord_id", "coffin299")

        self.bot_invite_url = self.bot.config.get("bot_invite_url")

        if not self.bot_invite_url:
            logger.error(
                "CRITICAL: config.yaml に 'bot_invite_url' が設定されていません。/invite コマンドは機能しません。")
        elif self.bot_invite_url in ["YOUR_BOT_INVITE_LINK_HERE", "HOGE_FUGA_PIYO"]:
            logger.error(
                "CRITICAL: 'bot_invite_url' がプレースホルダのままです。/invite コマンドは正しく機能しません。config.yamlを確認してください。")

        self.generic_help_message_text_ja = self.bot.config.get("generic_help_message_ja", "ヘルプ")
        self.generic_help_message_text_en = self.bot.config.get("generic_help_message_en", "Help")

    async def get_prefix_from_config(self) -> str:
        prefix = "!!"
        if hasattr(self.bot, 'config') and self.bot.config:
            cfg_prefix = self.bot.config.get('prefix')
            if isinstance(cfg_prefix, str) and cfg_prefix:
                prefix = cfg_prefix
        return prefix

    # (gacha, diceroll, roll などのコマンドは変更なし)
    @app_commands.command(name="gacha",
                          description="ブルーアーカイブ風の生徒募集（ガチャ）を行います。/ Recruits students like in Blue Archive.")
    @app_commands.describe(rolls="募集回数を選択します。/ Select the number of recruitments.")
    @app_commands.choices(rolls=[
        app_commands.Choice(name="10回募集 (10 Rolls)", value=10),
        app_commands.Choice(name="1回募集 (1 Roll)", value=1),
    ])
    async def gacha(self, interaction: discord.Interaction, rolls: app_commands.Choice[int]):
        await interaction.response.defer(ephemeral=False)

        num_rolls = rolls.value
        results = []  # レアリティ(int)のリストを格納

        # 募集処理
        if num_rolls == 10:
            for _ in range(9):
                results.append(self._get_single_recruit())
            results.append(self._get_single_recruit(guaranteed_star2=True))
        else:
            results.append(self._get_single_recruit())

        # レアリティの存在チェック
        has_star_3 = 3 in results

        # Embedの色を設定
        embed_color = discord.Color.purple() if has_star_3 else discord.Color.gold()

        # レアリティを絵文字に変換
        rarity_to_emoji = {1: "🟦", 2: "🟨", 3: "🟪"}
        emoji_results = [rarity_to_emoji[r] for r in results]

        # 絵文字を文字列に整形 (10回の場合は5個で改行)
        if num_rolls == 10:
            result_text = "".join(emoji_results[:5]) + "\n" + "".join(emoji_results[5:])
        else:
            result_text = emoji_results[0]

        # Embedを作成
        embed = discord.Embed(
            title="生徒募集 結果 / Recruitment Results",
            description=f"{interaction.user.mention} 先生の募集結果です。",
            color=embed_color
        )

        embed.add_field(name="ガチャ結果/Gacha results", value=result_text,
                        inline=False)
        embed.set_footer(text="提供割合: 🟪(☆3): 3.0%, 🟨(☆2): 18.5%, 🟦(☆1): 78.5%")

        await interaction.followup.send(embed=embed)
        logger.info(f"/gacha ({num_rolls}回) が実行されました。 (User: {interaction.user.id})")

    @app_commands.command(name="diceroll",
                          description="指定された範囲でダイスを振ります。/ Rolls a dice within the specified range.")
    @app_commands.describe(
        min_value="ダイスの最小値 / The minimum value of the dice",
        max_value="ダイスの最大値 / The maximum value of the dice"
    )
    async def diceroll(self, interaction: discord.Interaction, min_value: int, max_value: int):
        """指定された範囲でダイスを振るコマンド"""
        # 入力値のバリデーション
        if min_value > max_value:
            await interaction.response.send_message(
                "エラー: 最小値は最大値より大きくできません。\nError: The minimum value cannot be greater than the maximum value.",
                ephemeral=True
            )
            return

        # ダイスロールの実行
        result = random.randint(min_value, max_value)

        # 結果をEmbedで表示
        embed = discord.Embed(
            title="🎲 ダイスロール結果 / Dice Roll Result",
            description=f"{interaction.user.mention} がダイスを振りました！",
            color=discord.Color.green()
        )
        embed.add_field(name="指定範囲 / Range", value=f"`{min_value}` ～ `{max_value}`", inline=False)
        embed.add_field(name="出た目 / Result", value=f"**{result}**", inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        await interaction.response.send_message(embed=embed)
        logger.info(
            f"/diceroll が実行されました。 (User: {interaction.user.id}, Range: {min_value}-{max_value}, Result: {result})")

    @app_commands.command(name="roll",
                          description="nDn形式でダイスを振ります (例: 2d6+3)。/ Rolls dice in nDn format (e.g., 2d6+3).")
    @app_commands.describe(
        expression="ダイスの表記 (例: 1d100, 2d6+5, 3d8-2) / Dice notation (e.g., 1d100, 2d6+5, 3d8-2)"
    )
    async def roll(self, interaction: discord.Interaction, expression: str):
        """nDn形式でダイスを振るコマンド"""
        match = re.match(r'(\d*)d(\d+)\s*([+-]\s*\d+)?', expression.lower().strip())

        if not match:
            await interaction.response.send_message(
                "エラー: 不正なダイス表記です。`1d100`や`2d6+5`のような形式で入力してください。\n"
                "Error: Invalid dice notation. Please use a format like `1d100` or `2d6+5`.",
                ephemeral=True
            )
            return

        dice_count_str, dice_sides_str, modifier_str = match.groups()
        dice_count = int(dice_count_str) if dice_count_str else 1
        dice_sides = int(dice_sides_str)
        modifier = int(modifier_str.replace(" ", "")) if modifier_str else 0

        MAX_DICE_COUNT = 100
        MAX_DICE_SIDES = 10000
        if not (1 <= dice_count <= MAX_DICE_COUNT):
            await interaction.response.send_message(
                f"エラー: ダイスの数は1から{MAX_DICE_COUNT}の間で指定してください。\n"
                f"Error: The number of dice must be between 1 and {MAX_DICE_COUNT}.",
                ephemeral=True
            )
            return
        if not (1 <= dice_sides <= MAX_DICE_SIDES):
            await interaction.response.send_message(
                f"エラー: ダイスの面は1から{MAX_DICE_SIDES}の間で指定してください。\n"
                f"Error: The number of sides must be between 1 and {MAX_DICE_SIDES}.",
                ephemeral=True
            )
            return

        rolls = [random.randint(1, dice_sides) for _ in range(dice_count)]
        total = sum(rolls)
        final_result = total + modifier

        embed = discord.Embed(
            title="🎲 ダイスロール結果 / Dice Roll Result",
            description=f"{interaction.user.mention} がダイスを振りました！",
            color=discord.Color.purple()
        )

        input_expression = f"{dice_count}d{dice_sides}"
        if modifier > 0:
            input_expression += f" + {modifier}"
        elif modifier < 0:
            input_expression += f" - {abs(modifier)}"
        embed.add_field(name="入力 / Input", value=f"`{input_expression}`", inline=False)

        rolls_str = ", ".join(map(str, rolls))
        if len(rolls_str) > 1000:
            rolls_str = rolls_str[:997] + "..."
        embed.add_field(name="各ダイスの出目 / Individual Rolls", value=f"[{rolls_str}]", inline=False)

        result_str = f"**{final_result}**"
        if modifier != 0 or dice_count > 1:
            details = f" (合計: {total}"
            if modifier > 0:
                details += f" + {modifier}"
            elif modifier < 0:
                details += f" - {abs(modifier)}"
            details += ")"
            result_str += details

        embed.add_field(name="最終結果 / Final Result", value=result_str, inline=False)
        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)

        await interaction.response.send_message(embed=embed)
        logger.info(
            f"/roll が実行されました。 (User: {interaction.user.id}, Expression: {expression}, Result: {final_result})")

    ### ▼▼▼ 変更箇所 ▼▼▼ ###
    @app_commands.command(name="check",
                          description="ダイスロールと、任意で条件判定を行います。/ Rolls dice and optionally performs a check.")
    @app_commands.describe(
        expression="ダイスの表記 (例: 1d100, 2d6+5) / Dice notation (e.g., 1d100, 2d6+5)",
        condition="[任意] 比較条件 / [Optional] Comparison condition",
        target="[任意] 目標値 / [Optional] Target number"
    )
    @app_commands.choices(condition=[
        app_commands.Choice(name="< (より小さい)", value="<"),
        app_commands.Choice(name="<= (以下)", value="<="),
        app_commands.Choice(name="> (より大きい)", value=">"),
        app_commands.Choice(name=">= (以上)", value=">="),
        app_commands.Choice(name="= (等しい)", value="=="),
    ])
    async def check(self,
                    interaction: discord.Interaction,
                    expression: str,
                    condition: Optional[str] = None,
                    target: Optional[int] = None):
        """ダイスロールと、任意で条件判定を行うコマンド"""

        # --- 引数のバリデーション ---
        if (condition is None and target is not None) or (condition is not None and target is None):
            await interaction.response.send_message(
                "エラー: 判定を行うには、`条件`と`目標値`の両方を指定してください。\n"
                "Error: To perform a check, you must specify both a `condition` and a `target` number.",
                ephemeral=True
            )
            return

        # --- ダイス表記のパースとロール ---
        match = re.match(r'(\d*)d(\d+)\s*([+-]\s*\d+)?', expression.lower().strip())
        if not match:
            await interaction.response.send_message(
                "エラー: 不正なダイス表記です。`1d100`や`2d6+5`のような形式で入力してください。\n"
                "Error: Invalid dice notation. Please use a format like `1d100` or `2d6+5`.",
                ephemeral=True
            )
            return

        dice_count_str, dice_sides_str, modifier_str = match.groups()
        dice_count = int(dice_count_str) if dice_count_str else 1
        dice_sides = int(dice_sides_str)
        modifier = int(modifier_str.replace(" ", "")) if modifier_str else 0

        MAX_DICE_COUNT = 100
        MAX_DICE_SIDES = 10000
        if not (1 <= dice_count <= MAX_DICE_COUNT) or not (1 <= dice_sides <= MAX_DICE_SIDES):
            await interaction.response.send_message(
                f"エラー: ダイスの数(1〜{MAX_DICE_COUNT})または面(1〜{MAX_DICE_SIDES})が不正です。\n"
                f"Error: Invalid number of dice (1-{MAX_DICE_COUNT}) or sides (1-{MAX_DICE_SIDES}).",
                ephemeral=True
            )
            return

        rolls = [random.randint(1, dice_sides) for _ in range(dice_count)]
        total = sum(rolls)
        final_result = total + modifier

        # --- 結果の表示 (判定の有無で分岐) ---
        is_check = condition is not None and target is not None

        if is_check:
            # 判定ありの場合
            success = False
            # targetがNoneでないことをis_checkで確認済みなので、型チェッカーを黙らせる
            target_val = target or 0
            if condition == "<":
                success = final_result < target_val
            elif condition == "<=":
                success = final_result <= target_val
            elif condition == ">":
                success = final_result > target_val
            elif condition == ">=":
                success = final_result >= target_val
            elif condition == "==":
                success = final_result == target_val

            status_text = "Success!" if success else "Failure!"
            status_emoji = "✅" if success else "❌"
            embed_color = discord.Color.green() if success else discord.Color.red()

            embed = discord.Embed(
                title=f"{status_emoji} 判定ロール結果 / Check Roll Result",
                description=f"{interaction.user.mention} が判定を行いました！",
                color=embed_color
            )

            dice_expression = f"{dice_count}d{dice_sides}"
            if modifier > 0:
                dice_expression += f"+{modifier}"
            elif modifier < 0:
                dice_expression += f"{modifier}"

            rolls_str = ", ".join(map(str, rolls))
            display_condition = condition.replace("==", "=")

            result_details = (
                f"**{status_text}** ⟵ `{final_result}` {display_condition} `{target}` "
                f"⟵ `[{rolls_str}]` {dice_expression}"
            )
            embed.add_field(name="結果 / Result", value=result_details, inline=False)

        else:
            # 判定なしの場合 (/roll と同じ)
            embed = discord.Embed(
                title="🎲 ダイスロール結果 / Dice Roll Result",
                description=f"{interaction.user.mention} がダイスを振りました！",
                color=discord.Color.purple()
            )

            input_expression = f"{dice_count}d{dice_sides}"
            if modifier > 0:
                input_expression += f" + {modifier}"
            elif modifier < 0:
                input_expression += f" - {abs(modifier)}"
            embed.add_field(name="入力 / Input", value=f"`{input_expression}`", inline=False)

            rolls_str = ", ".join(map(str, rolls))
            if len(rolls_str) > 1000: rolls_str = rolls_str[:997] + "..."
            embed.add_field(name="各ダイスの出目 / Individual Rolls", value=f"[{rolls_str}]", inline=False)

            result_str = f"**{final_result}**"
            if modifier != 0 or dice_count > 1:
                details = f" (合計: {total}"
                if modifier > 0:
                    details += f" + {modifier}"
                elif modifier < 0:
                    details += f" - {abs(modifier)}"
                details += ")"
                result_str += details
            embed.add_field(name="最終結果 / Final Result", value=result_str, inline=False)

        embed.timestamp = datetime.datetime.now(datetime.timezone.utc)
        await interaction.response.send_message(embed=embed)

        log_message = (f"/check が実行されました。 (User: {interaction.user.id}, Expression: {expression}"
                       f"{f' {condition} {target}' if is_check else ''}, Result: {final_result}"
                       f"{f', Success: {success}' if is_check else ''})")
        logger.info(log_message)

    ### ▲▲▲ 変更箇所 ▲▲▲ ###

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
        elif guild.owner_id:
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

        verification_level_str_en = guild.verification_level.name.replace('_', ' ').capitalize()
        embed.add_field(name="認証レベル / Verification Level",
                        value=f"{verification_level_str_en}", inline=True)

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

        # Botかどうかの表示は、target_user.botで判定
        is_bot = "はい" if target_user.bot else "いいえ"
        is_bot_en = "Yes" if target_user.bot else "No"
        embed.add_field(name="Botアカウントか / Bot Account?", value=f"{is_bot} / {is_bot_en}", inline=True)

        created_at_text = discord.utils.format_dt(target_user.created_at, style='F')
        embed.add_field(name="アカウント作成日時 / Account Created", value=created_at_text, inline=False)

        # サーバー固有の情報を表示
        if interaction.guild and isinstance(target_user, discord.Member):
            member: discord.Member = target_user

            # サーバー参加日時
            joined_at_text = "不明 / Unknown"
            if member.joined_at:
                joined_at_text = discord.utils.format_dt(member.joined_at, style='F')
            embed.add_field(name="サーバー参加日時 / Joined Server", value=joined_at_text, inline=False)

            # ロール一覧
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

            # --- 評価セクション (対象がBotか人間かで分岐) ---
            if member.bot:
                # Botの場合の評価
                evaluation_lines = []
                if member.public_flags.verified_bot:
                    evaluation_lines.append("✅ **認証済みBot** / Verified Bot")
                else:
                    evaluation_lines.append("❌ **未認証Bot** / Unverified Bot")

                if member.guild_permissions.administrator:
                    evaluation_lines.append("👑 **管理者権限** / Administrator Privileges")
                else:
                    evaluation_lines.append("🔧 **標準権限** / Standard Privileges")

                embed.add_field(name="Botの評価 / Bot Evaluation", value="\n".join(evaluation_lines), inline=False)

            else:
                # 人間の場合の評価
                # 参加順位
                if member.joined_at:
                    sorted_members = sorted(interaction.guild.members,
                                            key=lambda m: m.joined_at or datetime.datetime.max.replace(
                                                tzinfo=datetime.timezone.utc))
                    try:
                        join_position = sorted_members.index(member) + 1
                        embed.add_field(name="参加順位 / Join Rank", value=f"{join_position}番目 / th", inline=True)
                    except ValueError:
                        pass  # メンバーが見つからない場合は何もしない

                # 重要な権限
                perms = member.guild_permissions
                notable_perms_ja = {
                    "管理者": perms.administrator, "サーバー管理": perms.manage_guild,
                    "ロール管理": perms.manage_roles, "追放": perms.kick_members, "BAN": perms.ban_members,
                }
                user_perms = [name for name, has_perm in notable_perms_ja.items() if has_perm]
                perms_display = "なし / None"
                if user_perms:
                    perms_display = "✅ **管理者**" if "管理者" in user_perms else ", ".join(user_perms)
                embed.add_field(name="重要な権限 / Key Permissions", value=perms_display, inline=False)

                # タイムアウト情報
                if member.timed_out_until:
                    timeout_text = discord.utils.format_dt(member.timed_out_until, style='R')
                    embed.add_field(name="⏳ タイムアウト中 / Timed Out", value=f"終了: {timeout_text}", inline=True)

            # ニックネームとブースト情報 (共通)
            if member.nick:
                embed.add_field(name="ニックネーム / Nickname", value=member.nick, inline=True)
            if member.premium_since:
                premium_text = discord.utils.format_dt(member.premium_since, style='R')
                embed.add_field(name="サーバーブースト開始 / Server Boosting Since", value=premium_text, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=False)
        logger.info(f"/userinfo が実行されました。 (TargetUser: {target_user.id}, Requester: {interaction.user.id})")

    # (avatar, arona, plana, support, invite などのコマンドは変更なし)
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
                          description="開発者へのお問い合わせ方法を表示します / Shows how to contact the developer")
    async def support_contact_slash(self, interaction: discord.Interaction) -> None:
        """開発者への連絡方法を表示するコマンド"""

        # Embedの作成
        embed = discord.Embed(
            title="💬 お問い合わせ / Contact Support",
            description="Botに関するご質問・ご要望・不具合報告などは、以下の方法でお気軽にお問い合わせください。\n"
                        "For questions, requests, or bug reports about the bot, please feel free to contact us using the methods below.",
            color=discord.Color.blue()
        )

        # X (Twitter) での連絡
        embed.add_field(
            name="🐦 X (Twitter)",
            value=f"DMまたはメンションでお問い合わせください。\n"
                  f"Please contact via DM or mention.\n"
                  f"[**@coffin299**]({self.support_x_url})",
            inline=False
        )

        # Discord での連絡
        embed.add_field(
            name="💬 Discord",
            value=f"DiscordのDMでお問い合わせください。\n"
                  f"Please contact via Discord DM.\n"
                  f"**ユーザー名 / Username:** `{self.support_discord_id}`",
            inline=False
        )

        # 注意事項
        embed.add_field(
            name="📝 ご連絡時のお願い / When Contacting",
            value="• Botを使用しているサーバー名をお知らせください。\n"
                  "• 具体的な問題や要望をお書きください。\n"
                  "• スクリーンショットがあれば添付してください。\n\n"
                  "• Please mention the server name where you're using the bot.\n"
                  "• Describe the specific issue or request.\n"
                  "• Attach screenshots if available.",
            inline=False
        )

        # フッター
        embed.set_footer(text="お気軽にお問い合わせください！ / Feel free to contact us!")

        # ボタンビューの作成
        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="X (Twitter)で連絡 / Contact on X",
                style=discord.ButtonStyle.link,
                url=self.support_x_url,
                emoji="🐦"
            )
        )

        await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        logger.info(f"/support が実行されました。 (User: {interaction.user.id})")

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

    # ================================================================
    # ▼▼▼ 統合されたヘルプコマンド ▼▼▼
    # ================================================================
    @app_commands.command(name="help",
                          description="Botのヘルプ情報とAI利用ガイドラインを表示します。/ Displays help and AI usage guidelines.")
    async def help_slash_command(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        bot_name_ja = self.bot.user.name if self.bot.user else "当Bot"
        bot_name_en = self.bot.user.name if self.bot.user else "This Bot"
        bot_avatar_url = self.bot.user.avatar.url if self.bot.user and self.bot.user.avatar else None
        prefix = await self.get_prefix_from_config()

        embed = discord.Embed(
            title=f"📜 {bot_name_ja} ヘルプ＆ガイドライン / {bot_name_en} Help & Guidelines",
            description=f"{self.generic_help_message_text_ja}\n\n{self.generic_help_message_text_en}",
            color=discord.Color.teal()
        )
        if bot_avatar_url:
            embed.set_thumbnail(url=bot_avatar_url)

        # --- 1. 基本的な使い方 ---
        desc_ja_detail = "より詳細な情報は、以下のコマンドで確認できます。"
        desc_en_detail = "For more detailed information, please check the following commands:"
        llm_help_cmd_ja = "• **AI対話機能:** `/llm_help` (または `/llm_help_en`)"
        llm_help_cmd_en = "• **AI Chat (LLM):** `/llm_help` (or `/llm_help_en`)"
        music_help_cmd_ja = "• **音楽再生機能:** `/music_help`"
        music_help_cmd_en = "• **Music Playback:** `/music_help` (or `/music_help_en`)"

        prefix_info_ja = f"プレフィックスコマンドも利用可能です (現在のプレフィックス: `{prefix}` )。"
        prefix_info_en = f"(Prefix commands are also available. Current prefix: `{prefix}` )"

        embed.add_field(
            name="基本情報 / Basic Information",
            value=f"{desc_ja_detail}\n{llm_help_cmd_ja}\n{music_help_cmd_ja}\n{prefix_info_ja}\n\n"
                  f"{desc_en_detail}\n{llm_help_cmd_en}\n{music_help_cmd_en}\n{prefix_info_en}",
            inline=False
        )

        main_features_title_ja = "主な機能"
        main_features_ja_val = (
            "- **AIとの対話 (LLM):** メンションで話しかけるとAIが応答します。画像も認識可能です。\n"
            "- **音楽再生:** ボイスチャンネルで音楽を再生、キュー管理、各種操作ができます。\n"
            "- **画像検索:** 猫の画像を表示できます。\n"
            "- **情報表示:** サーバー情報、ユーザー情報、Botのレイテンシなどを表示します。"
        )
        main_features_en_val = (
            "- **AI Chat (LLM):** Mention the bot to talk with AI. It can also recognize images (if model supports).\n"
            "- **Music Playback:** Play music in voice channels, manage queues, and perform various operations.\n"

            "- **Image Search:** Display cat pictures.\n"
            "- **Information Display:** Show server info, user info, bot latency, etc."
        )
        embed.add_field(
            name=f"{main_features_title_ja} / Main Features",
            value=f"{main_features_ja_val}\n\n{main_features_en_val}",
            inline=False
        )

        # --- 2. AI利用ガイドライン ---
        embed.add_field(
            name="--- 📜 AI利用ガイドライン / AI Usage Guidelines ---",
            value="AI機能を安全にご利用いただくため、以下の内容を必ずご確認ください。\n*Please review the following to ensure safe use of the AI features.*",
            inline=False
        )

        # ガイドラインの目的と構成
        embed.add_field(
            name="1. 目的と対象AI / Purpose & Target AI",
            value=(
                "**【目的】** 本ガイドラインは、BotのAI機能を安全にご利用いただくために、技術的・法的リスクを低減させることを目的とします。\n"
                "*Purpose: This guideline aims to reduce technical and legal risks to ensure the safe use of the bot's AI features.*\n\n"
                "**【対象AI】** 本Botは、内部的にMistral AIやGoogle Geminiなどのサードパーティ製生成AIモデルを利用しています。\n"
                "*Target AI: This bot internally uses third-party generative AI models such as Mistral AI and Google Gemini.*"
            ),
            inline=False
        )

        # データ入力時の注意
        embed.add_field(
            name="⚠️ 2. データ入力時の注意 / Precautions for Data Input",
            value=(
                "以下の情報は、AIの学習や意図しない漏洩に繋がる危険性があるため、**絶対に入力しないでください。**\n"
                "***NEVER input the following information**, as it poses a risk of being used for AI training or unintentional leakage.*\n\n"
                "1. **個人情報・秘密情報:** 氏名、連絡先、NDA対象情報、自組織の機密情報など\n"
                "   *Personal/Confidential Info: Name, contact details, NDA-protected info, your organization's sensitive data, etc.*\n"
                "2. **第三者の知的財産:** 許可のない著作物(文章,コード等)、登録商標、意匠(ロゴ,デザイン)など\n"
                "   *Third-Party IP: Copyrighted works (text, code), trademarks, or designs without permission.*"
            ),
            inline=False
        )

        # 生成物利用時の注意
        embed.add_field(
            name="✅ 3. 生成物利用時の注意 / Precautions for Using Generated Output",
            value=(
                "1. **内容の不正確さ:** 生成物には虚偽や偏見が含まれる可能性があります。**必ずファクトチェックを行い、自己の責任で利用してください。**\n"
                "   *Inaccuracy: The output may contain falsehoods. **Always fact-check and use it at your own risk.***\n"
                "2. **権利侵害リスク:** 生成物が意図せず既存の著作物等と類似し、第三者の権利を侵害する可能性があります。\n"
                "   *Rights Infringement Risk: The output may unintentionally resemble existing works, potentially infringing on third-party rights.*\n"
                "3. **著作権の不発生:** AIによる生成物に著作権は発生しない、または権利が限定的となる可能性があります。\n"
                "   *No Copyright: Copyright may not apply to AI-generated output, or rights may be limited.*\n"
                "4. **AIポリシーの遵守:** 基盤となるAI（Mistral AI, Gemini等）の利用規約やポリシーも適用されます。\n"
                "   *Adherence to Policies: The terms of the underlying AI (e.g., Mistral AI, Gemini) also apply.*"
            ),
            inline=False
        )

        # 禁止事項
        embed.add_field(
            name="🚫 4. 禁止事項と同意 / Prohibited Uses & Agreement",
            value=(
                "法令や公序良俗に反する利用、他者の権利を侵害する利用、差別的・暴力的・性的なコンテンツの生成は固く禁じます。\n"
                "*Use that violates laws, infringes on rights, or generates discriminatory, violent, or explicit content is strictly prohibited.*\n\n"
                "**本Botの利用をもって、本ガイドラインに同意したものとみなします。**\n"
                "***By using this bot, you are deemed to have agreed to these guidelines.***"
            ),
            inline=False
        )

        embed.add_field(name="--- ガイドラインここまで / End of Guidelines ---", value="\u200b", inline=False)

        # --- 3. その他の便利なコマンド ---
        utility_title_ja = "便利なコマンド"
        ### ▼▼▼ 変更箇所 ▼▼▼ ###
        utility_cmds_ja = [
            f"`/check <表記> [条件] [目標値]` - ダイスロールと任意での条件判定を行います。",
            f"`/roll <表記>` - nDn形式でダイスを振ります (例: 2d6+3)。",
            f"`/diceroll <最小値> <最大値>` - 指定範囲でダイスを振ります。",
            f"`/gacha` - ブルーアーカイブ風の募集（ガチャ）をシミュレートします。",
            f"`/ping` - Botの応答速度を確認",
            f"`/serverinfo` - サーバー情報を表示",
            f"`/userinfo [ユーザー]` - ユーザー情報を表示",
            f"`/avatar [ユーザー]` - アバター画像を表示",
            f"`/invite` - Botの招待リンクを表示",
            f"`/meow` - ランダムな猫の画像を表示",
            f"`/support` - 開発者への連絡方法を表示"
        ]
        utility_cmds_en = [
            f"`/check <notation> [cond] [target]` - Rolls dice and optionally performs a check.",
            f"`/roll <notation>` - Rolls dice in nDn format (e.g., 2d6+3).",
            f"`/diceroll <min_value> <max_value>` - Rolls a dice in a specified range.",
            f"`/gacha` - Simulates student recruitment (gacha) like in Blue Archive.",
            f"`/ping` - Check bot's latency",
            f"`/serverinfo` - Display server info",
            f"`/userinfo [user]` - Display user info",
            f"`/avatar [user]` - Display avatar",
            f"`/invite` - Display bot invite link",
            f"`/meow` - Displays a random cat picture",
            f"`/support` - Shows how to contact the developer"
        ]
        ### ▲▲▲ 変更箇所 ▲▲▲ ###

        if self.plana_repository:
            utility_cmds_ja.append(f"`/plana` - Plana (Bot)リポジトリ")
            utility_cmds_en.append(f"`/plana` - Plana (Bot) repository")
        if self.arona_repository:
            utility_cmds_ja.append(f"`/arona` - Arona (Music)リポジトリ")
            utility_cmds_en.append(f"`/arona` - Arona (Music) repository")

        embed.add_field(
            name=f"{utility_title_ja} / Useful Commands",
            value="\n".join(utility_cmds_ja) + "\n\n" + "\n".join(utility_cmds_en),
            inline=False
        )

        footer_ja = "<> は必須引数、[] は任意引数を表します。ガイドラインは予告なく変更される場合があります。"
        footer_en = "<> denotes a required argument, [] denotes an optional argument. Guidelines are subject to change."
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

        logger.info(f"/help (統合版) が実行されました。 (User: {interaction.user.id})")


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("SlashCommandsCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        raise commands.ExtensionFailed("SlashCommandsCog", "Botのconfigがロードされていません。")

    cog = SlashCommandsCog(bot)
    await bot.add_cog(cog)
    logger.info("SlashCommandsCogが正常にロードされました。")