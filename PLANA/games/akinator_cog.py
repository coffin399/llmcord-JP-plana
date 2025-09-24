import traceback
from typing import Optional, Dict

import akinator
import discord
from discord import app_commands
from discord.ext import commands


class LanguageSelectView(discord.ui.View):
    """言語選択用のビュー"""

    def __init__(self, cog, interaction):
        super().__init__(timeout=60)
        self.cog = cog
        self.interaction = interaction  # コマンド実行者のInteraction

    @discord.ui.select(
        placeholder="言語を選択してください / Choose your language",
        options=[
            discord.SelectOption(label="日本語", value="jp", emoji="🇯🇵"),
            discord.SelectOption(label="English", value="en", emoji="🇺🇸"),
            discord.SelectOption(label="Français", value="fr", emoji="🇫🇷"),
            discord.SelectOption(label="Español", value="es", emoji="🇪🇸"),
            discord.SelectOption(label="Deutsch", value="de", emoji="🇩🇪"),
            discord.SelectOption(label="Italiano", value="it", emoji="🇮🇹"),
            discord.SelectOption(label="Português", value="pt", emoji="🇵🇹"),
            discord.SelectOption(label="Русский", value="ru", emoji="🇷🇺"),
            discord.SelectOption(label="العربية", value="ar", emoji="🇸🇦"),
            discord.SelectOption(label="中文", value="cn", emoji="🇨🇳"),
        ]
    )
    async def language_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        # ボタンを押したユーザーがコマンド実行者本人か確認
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("コマンドを実行した本人しか操作できません。", ephemeral=True)
            return

        language = select.values[0]
        # interaction (component interaction) を渡して、メッセージを編集させる
        await self.cog.start_game_with_language(interaction, language)


class GameButtonView(discord.ui.View):
    """ゲーム用のボタンビュー"""

    def __init__(self, cog, game):
        super().__init__(timeout=300)
        self.cog = cog
        self.game = game

    @discord.ui.button(label="はい / Yes", style=discord.ButtonStyle.primary, emoji="✅")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "y")

    @discord.ui.button(label="いいえ / No", style=discord.ButtonStyle.primary, emoji="❌")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "n")

    @discord.ui.button(label="わからない / I Don't Know", style=discord.ButtonStyle.primary, emoji="🤷")
    async def idk_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "idk")

    @discord.ui.button(label="たぶんそう / Probably", style=discord.ButtonStyle.primary, emoji="👍")
    async def probably_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "p")

    @discord.ui.button(label="たぶん違う / Probably Not", style=discord.ButtonStyle.primary, emoji="👎")
    async def probably_not_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "pn")

    @discord.ui.button(label="戻る / Back", style=discord.ButtonStyle.primary, emoji="⬅️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "b")

    @discord.ui.button(label="終了 / Stop", style=discord.ButtonStyle.danger, emoji="🛑", row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("このゲームはあなたのものではありません！", ephemeral=True)
            return
        await interaction.response.defer()
        await self.cog._end_game(self.game, "ゲームが中断されました。")

    async def handle_answer(self, interaction: discord.Interaction, answer: str):
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("このゲームはあなたのものではありません！", ephemeral=True)
            return

        # 推測中は何もしない（ボタンを押しても反応しない）
        if self.game.is_guessing:
            await interaction.response.send_message("推測中です。上のボタンで回答してください！", ephemeral=True)
            return

        await interaction.response.defer()
        await self.cog._handle_answer(self.game, answer)


class GuessButtonView(discord.ui.View):
    """推測時のボタンビュー"""

    def __init__(self, cog, game):
        super().__init__(timeout=300)
        self.cog = cog
        self.game = game

    @discord.ui.button(label="はい、正解です！ / Yes, Correct!", style=discord.ButtonStyle.green, emoji="✅")
    async def correct_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ユーザーチェック
        # ★修正: self.game.user.id -> self.game.user_id
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("このゲームはあなたのものではありません！", ephemeral=True)
            return

        # 既に終了している場合は何もしない
        if not self.game or not self.game.is_active:
            await interaction.response.send_message("このゲームは既に終了しています。", ephemeral=True)
            return

        await interaction.response.defer()

        # ボタンを無効化
        self.disable_all_items()
        try:
            await self.game.message.edit(view=self)
        except:
            pass

        # 勝利メッセージを作成して終了
        winner_name = self.game.current_guess.get('name', 'Unknown') if self.game.current_guess else 'Unknown'
        victory_message = f"🎉 私の勝利です！\n答えは **{winner_name}** でした！"
        await self._direct_end_game(victory_message, True)

    @discord.ui.button(label="いいえ、違います / No, Wrong", style=discord.ButtonStyle.red, emoji="❌")
    async def wrong_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # ユーザーチェック
        # ★修正: self.game.user.id -> self.game.user_id
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("このゲームはあなたのものではありません！", ephemeral=True)
            return

        # 既に終了している場合は何もしない
        if not self.game or not self.game.is_active:
            await interaction.response.send_message("このゲームは既に終了しています。", ephemeral=True)
            return

        await interaction.response.defer()

        # ボタンを無効化
        self.disable_all_items()
        try:
            await self.game.message.edit(view=self)
        except:
            pass

        # 負けメッセージを作成して終了
        defeat_message = "😔 私の負けです…\nまた挑戦させてくださいね！"
        await self._direct_end_game(defeat_message, False)

    def disable_all_items(self):
        for item in self.children:
            item.disabled = True

    async def _direct_end_game(self, message: str, is_victory: bool):
        """勝敗決定時の終了処理"""
        # 既に終了している場合は何もしない
        if not self.game or not self.game.is_active:
            return

        # ゲームを非アクティブにする（重複実行を防ぐ）
        self.game.is_active = False

        color = discord.Color.green() if is_victory else discord.Color.red()
        title = "🎉 アキネーター - 私の勝利！" if is_victory else "😔 アキネーター - 私の負け..."
        embed = discord.Embed(title=title, description=f"## {message}", color=color)

        if self.game.current_guess:
            name = self.game.current_guess.get('name', 'データなし')
            description = self.game.current_guess.get('description')
            image_url = self.game.current_guess.get('absolute_picture_path')

            embed.add_field(name="🎯 推測したキャラクター", value=f"**{name}**", inline=False)
            if description and description != 'データなし':
                if len(description) > 1024:
                    description = description[:1021] + "..."
                embed.add_field(name="📝 キャラクター情報", value=description, inline=False)
            if image_url:
                embed.set_image(url=image_url)

        embed.set_footer(text="ゲーム終了 - 新しいゲームをするには /akinator を実行してください")

        try:
            # メッセージが存在する場合のみ編集
            if self.game.message:
                await self.game.message.edit(embed=embed, view=None)
        except Exception as e:
            print(f"Failed to update message in _direct_end_game: {e}")

        # ゲームをクリーンアップ
        try:
            if self.game.channel_id in self.cog.games:
                del self.cog.games[self.game.channel_id]
        except Exception as e:
            print(f"Failed to cleanup game: {e}")


class AkinatorGame:
    def __init__(self, aki: akinator.AsyncAkinator, channel_id: int, user_id: int, language: str = "jp"):
        self.aki = aki
        self.channel_id = channel_id
        self.user_id = user_id
        self.language = language
        self.message: Optional[discord.Message] = None
        self.is_active = True
        self.is_guessing = False
        self.current_guess = None


class AkinatorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, AkinatorGame] = {}

    @app_commands.command(name="akinator", description="アキネーターゲームを開始します")
    async def akinator_command(self, interaction: discord.Interaction):
        if interaction.channel_id in self.games:
            await interaction.response.send_message("このチャンネルでは既にゲームが進行中です！", ephemeral=True)
            return

        embed = discord.Embed(
            title="🔮 アキネーター / Akinator (BETA)",
            description=f"{interaction.user.mention} さんがゲームを開始します。\n言語を選択してください。",
            color=discord.Color.blue()
        )
        view = LanguageSelectView(self, interaction)
        await interaction.response.send_message(embed=embed, view=view)

    async def start_game_with_language(self, interaction: discord.Interaction, language: str):
        try:
            aki = akinator.AsyncAkinator()
            # ゲームユーザーは言語選択ボタンを押したユーザー (本人確認済み)
            game = AkinatorGame(aki, interaction.channel_id, interaction.user.id, language)
            self.games[interaction.channel_id] = game

            await aki.start_game(language=language, child_mode=False)

            embed = self._create_question_embed(aki.question, aki.progression, aki.step)
            view = GameButtonView(self, game)

            # 言語選択メッセージを編集して最初の質問を表示
            await interaction.response.edit_message(embed=embed, view=view)
            message = await interaction.original_response()
            game.message = message

        except Exception as e:
            error_msg = f"ゲームの開始中にエラーが発生しました。\n`{type(e).__name__}: {e}`"
            print(f"Akinator start error: {traceback.format_exc()}")
            try:
                # エラーメッセージで編集を試みる
                await interaction.response.edit_message(content=error_msg, embed=None, view=None)
            except discord.HTTPException:
                # 編集に失敗した場合はfollowupで送信
                await interaction.followup.send(error_msg, ephemeral=True)

            if interaction.channel_id in self.games:
                del self.games[interaction.channel_id]

    def _create_question_embed(self, question: str, progression: float, step: int) -> discord.Embed:
        embed = discord.Embed(
            title="🔮 アキネーター (BETA)",
            description=f"**質問 {step + 1}:**\n\n## {question}",
            color=discord.Color.blue()
        )
        progress_bar = self._create_progress_bar(progression)
        embed.add_field(name="進行状況", value=progress_bar, inline=False)

        debug_info = f"Step: {step} | Progression: {progression:.2f}%"
        embed.set_footer(text=f"下のボタンで回答してください！ ({debug_info})")
        return embed

    def _create_progress_bar(self, progression: float) -> str:
        percentage = round(progression, 2)
        filled_blocks = int(percentage / 100 * 20)
        empty_blocks = 20 - filled_blocks
        bar = "█" * filled_blocks + "░" * empty_blocks
        return f"`[{bar}] {percentage}%`"

    def _create_guess_embed(self, guess: dict) -> discord.Embed:
        embed = discord.Embed(
            title="🎯 私の推測は… このキャラクターですか？",
            color=discord.Color.green()
        )
        name = guess.get('name', 'データなし')
        description = guess.get('description')
        image_url = guess.get('absolute_picture_path')

        embed.add_field(name="キャラクター", value=f"### {name}", inline=False)
        if description and description != 'データなし':
            if len(description) > 1024:
                description = description[:1021] + "..."
            embed.add_field(name="説明", value=description, inline=False)
        if image_url:
            embed.set_image(url=image_url)
        embed.set_footer(text="下のボタンで回答してください！")
        return embed

    async def _handle_answer(self, game: AkinatorGame, answer: str):
        """質問への回答を処理"""
        if not game or not game.is_active:
            return

        try:
            if answer == "b":
                try:
                    await game.aki.back()
                except akinator.CantGoBackAnyFurther:
                    return
            else:
                await game.aki.answer(answer)

            should_guess = False
            current_step = game.aki.step
            progression = game.aki.progression
            confidence = game.aki.confidence if hasattr(game.aki, 'confidence') else 0.0

            if hasattr(game.aki, 'win') and game.aki.win:
                if current_step >= 10:
                    print(f"Win property is True at step {current_step}, trying to guess")
                    should_guess = True
                else:
                    print(f"Win is True but too early (step {current_step}), continuing questions")

            if not should_guess:
                if current_step <= 20:
                    threshold = 0.99
                elif current_step <= 40:
                    threshold = 0.97
                elif current_step <= 60:
                    threshold = 0.95
                else:
                    threshold = 0.90

                if confidence >= threshold and hasattr(game.aki, 'name_proposition') and game.aki.name_proposition:
                    print(
                        f"Confidence {confidence:.2f} >= threshold {threshold:.2f} at step {current_step}, trying to guess")
                    should_guess = True

            if not should_guess and current_step >= 75 and confidence >= 0.85:
                print(
                    f"Near question limit (step {current_step}) and high confidence ({confidence:.2f}), forcing guess")
                should_guess = True

            if should_guess and not game.is_guessing:
                await self._try_guess(game)
            elif current_step >= 79:
                await self._end_game(game, "質問の上限に達しました。私の負けです！")
            else:
                embed = self._create_question_embed(
                    game.aki.question,
                    game.aki.progression,
                    game.aki.step
                )
                view = GameButtonView(self, game)
                await game.message.edit(embed=embed, view=view)

        except RuntimeError as e:
            error_msg = str(e)
            print(f"[RuntimeError] at step {game.aki.step}: {error_msg}")
            if "Failed to exclude the proposition" in error_msg:
                if game.aki.step < 20:
                    print("[RuntimeError] Early stage error, continuing with questions")
                    embed = self._create_question_embed(
                        game.aki.question,
                        game.aki.progression,
                        game.aki.step
                    )
                    view = GameButtonView(self, game)
                    await game.message.edit(embed=embed, view=view)
                elif game.aki.step >= 20 and hasattr(game.aki, 'name_proposition') and game.aki.name_proposition:
                    print("[RuntimeError] Attempting guess after server error")
                    await self._try_guess(game)
                else:
                    await self._end_game(game, "サーバーとの通信に問題が発生しました。私の負けです！")
            else:
                print(f"[RuntimeError] Unexpected error: {traceback.format_exc()}")
                await self._handle_connection_error(game)
        except Exception as e:
            print(f"Error handling answer: {traceback.format_exc()}")
            await self._handle_connection_error(game)

    async def _try_guess(self, game: AkinatorGame):
        """キャラクターの推測を試みる"""
        if game.is_guessing or not game.is_active:
            return

        game.is_guessing = True

        try:
            print(f"[NEW_CODE Try Guess] Step: {game.aki.step}, Progression: {game.aki.progression}%")
            print(f"[NEW_CODE Try Guess] Win property: {getattr(game.aki, 'win', 'Not found')}")

            name = getattr(game.aki, 'name_proposition', None)
            description = getattr(game.aki, 'description_proposition', None)
            photo = getattr(game.aki, 'photo', None)

            print(f"[NEW_CODE Try Guess] name: {name}, has_description: {bool(description)}, has_photo: {bool(photo)}")

            if name and name.strip():
                guess_data = {
                    'name': name,
                    'description': description or 'データなし',
                    'absolute_picture_path': photo
                }

                game.current_guess = guess_data
                print(f"[NEW_CODE Try Guess] Success! Guessing: {guess_data['name']}")

                if game.is_active:
                    embed = self._create_guess_embed(guess_data)
                    view = GuessButtonView(self, game)
                    await game.message.edit(embed=embed, view=view)
                return

            print("[NEW_CODE Try Guess] No guess data available, continuing with questions")
            game.is_guessing = False

            if game.aki.step < 75:
                embed = self._create_question_embed(
                    game.aki.question,
                    game.aki.progression,
                    game.aki.step
                )
                view = GameButtonView(self, game)
                await game.message.edit(embed=embed, view=view)
            else:
                await self._end_game(game, "申し訳ありません、キャラクターを特定できませんでした。私の負けです！")

        except Exception as e:
            print(f"[NEW_CODE Try Guess] Error: {traceback.format_exc()}")
            print(f"[NEW_CODE Try Guess] Error details: {str(e)}")

            game.is_guessing = False

            if game.aki.step < 75:
                embed = self._create_question_embed(
                    game.aki.question,
                    game.aki.progression,
                    game.aki.step
                )
                view = GameButtonView(self, game)
                await game.message.edit(embed=embed, view=view)
            else:
                await self._end_game(game, "推測の処理中にエラーが発生しました。私の負けです！")

    async def _handle_connection_error(self, game: AkinatorGame):
        """接続エラー時の処理"""
        if game.is_guessing:
            return
        await self._end_game(game, "Akinatorサーバーとの接続に問題が発生しました。")

    async def _end_game(self, game: AkinatorGame, message: str):
        """通常のゲーム終了処理"""
        if not game or not game.is_active:
            return

        game.is_active = False

        embed = discord.Embed(
            title="🔮 アキネーター(BETA) - ゲーム終了",
            description=message,
            color=discord.Color.red()
        )

        if game.message:
            try:
                await game.message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
                print(f"Failed to edit message in _end_game: {e}")

        try:
            if game.channel_id in self.games:
                del self.games[game.channel_id]
        except Exception as e:
            print(f"Failed to cleanup game in _end_game: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AkinatorCog(bot))