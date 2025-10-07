from typing import Optional, Dict
import asyncio
import logging

import akinator
import discord
from discord import app_commands
from discord.ext import commands

from PLANA.games.error import errors

# このファイル用のロガーを取得
logger = logging.getLogger(__name__)


class LanguageSelectView(discord.ui.View):
    """言語選択用のビュー"""

    def __init__(self, cog, interaction):
        super().__init__(timeout=60)
        self.cog = cog
        self.interaction = interaction

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
        if interaction.user.id != self.interaction.user.id:
            await interaction.response.send_message("コマンドを実行した本人しか操作できません。", ephemeral=True)
            return
        language = select.values[0]
        await self.cog.start_game_with_language(interaction, language)


class GameButtonView(discord.ui.View):
    """ゲーム用のボタンビュー"""

    def __init__(self, cog, game):
        super().__init__(timeout=300)
        self.cog = cog
        self.game = game

    @discord.ui.button(label="はい / Yes", style=discord.ButtonStyle.primary, emoji="✅")
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_answer(interaction, "y")

    @discord.ui.button(label="いいえ / No", style=discord.ButtonStyle.primary, emoji="❌")
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_answer(interaction, "n")

    @discord.ui.button(label="わからない / I Don't Know", style=discord.ButtonStyle.primary, emoji="🤷")
    async def idk_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_answer(interaction, "idk")

    @discord.ui.button(label="たぶんそう / Probably", style=discord.ButtonStyle.primary, emoji="👍")
    async def probably_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_answer(interaction, "p")

    @discord.ui.button(label="たぶん違う / Probably Not", style=discord.ButtonStyle.primary, emoji="👎")
    async def probably_not_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.handle_answer(interaction, "pn")

    @discord.ui.button(label="戻る / Back", style=discord.ButtonStyle.primary, emoji="⬅️", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
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
            await interaction.followup.send("このゲームはあなたのものではありません！", ephemeral=True)
            return

        if self.game.is_guessing:
            await interaction.followup.send("推測中です。上のボタンで回答してください！", ephemeral=True)
            return

        await self.cog._handle_answer(self.game, answer)


class GuessButtonView(discord.ui.View):
    """推測時のボタンビュー"""

    def __init__(self, cog, game):
        super().__init__(timeout=300)
        self.cog = cog
        self.game = game

    @discord.ui.button(label="はい、正解です！ / Yes, Correct!", style=discord.ButtonStyle.green, emoji="✅")
    async def correct_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("このゲームはあなたのものではありません！", ephemeral=True)
            return

        if not self.game or not self.game.is_active:
            await interaction.response.send_message("このゲームは既に終了しています。", ephemeral=True)
            return

        await interaction.response.defer()
        self.disable_all_items()
        try:
            await self.game.message.edit(view=self)
        except:
            pass
        winner_name = self.game.current_guess.get('name', 'Unknown') if self.game.current_guess else 'Unknown'
        victory_message = f"🎉 私の勝利です！\n答えは **{winner_name}** でした！"
        await self._direct_end_game(victory_message, True)

    @discord.ui.button(label="いいえ、違います / No, Wrong", style=discord.ButtonStyle.red, emoji="❌")
    async def wrong_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.game.user_id:
            await interaction.response.send_message("このゲームはあなたのものではありません！", ephemeral=True)
            return

        if not self.game or not self.game.is_active:
            await interaction.response.send_message("このゲームは既に終了しています。", ephemeral=True)
            return

        await interaction.response.defer()
        self.disable_all_items()
        try:
            await self.game.message.edit(view=self)
        except:
            pass
        defeat_message = "😔 私の負けです…\nまた挑戦させてくださいね！"
        await self._direct_end_game(defeat_message, False)

    def disable_all_items(self):
        for item in self.children:
            item.disabled = True

    async def _direct_end_game(self, message: str, is_victory: bool):
        if not self.game or not self.game.is_active:
            return

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
            if self.game.message:
                await self.game.message.edit(embed=embed, view=None)
        except Exception:
            logger.exception("Failed to update message in _direct_end_game")

        try:
            if self.game.channel_id in self.cog.games:
                del self.cog.games[self.game.channel_id]
        except Exception:
            logger.exception("Failed to cleanup game")


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
        self.timeout_count = 0
        self.stuck_count = 0
        self.last_step = 0


class AkinatorCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, AkinatorGame] = {}
        self.GameButtonView = GameButtonView

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
            game = AkinatorGame(aki, interaction.channel_id, interaction.user.id, language)
            self.games[interaction.channel_id] = game

            await aki.start_game(language=language, child_mode=False)

            embed = self._create_question_embed(aki.question, aki.progression, aki.step)
            view = GameButtonView(self, game)
            await interaction.response.edit_message(embed=embed, view=view)
            message = await interaction.original_response()
            game.message = message

        except Exception as e:
            await errors.handle_start_game_error(interaction, e, self)

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
        if not game or not game.is_active:
            return

        try:
            previous_step = game.aki.step

            if answer == "b":
                try:
                    await game.aki.back()
                    game.timeout_count = 0
                    game.stuck_count = 0
                except akinator.CantGoBackAnyFurther:
                    return
            else:
                try:
                    await asyncio.wait_for(game.aki.answer(answer), timeout=15.0)
                    game.timeout_count = 0
                except asyncio.TimeoutError:
                    logger.warning(f"Akinator answer timeout at step {game.aki.step} in channel {game.channel_id}")
                    game.timeout_count += 1

                    if game.timeout_count >= 5:
                        await self._end_game(game, "接続に問題が発生しました。ゲームを終了します。")
                        return

                    embed = discord.Embed(
                        title="⚠️ 接続遅延",
                        description=f"応答が遅れています... もう一度お試しください。(リトライ: {game.timeout_count}/5)",
                        color=discord.Color.orange()
                    )
                    view = GameButtonView(self, game)
                    await game.message.edit(embed=embed, view=view)
                    return

            current_step = game.aki.step

            if current_step == previous_step and answer != "b":
                logger.warning(f"Akinator step did not advance at step {current_step} in channel {game.channel_id}")
                game.stuck_count += 1

                if game.stuck_count >= 5 and current_step >= 50:
                    logger.info(f"Step stuck repeatedly after 50 questions, attempting guess at step {current_step} in channel {game.channel_id}")
                    await self._try_guess(game)
                    return
                elif game.stuck_count >= 5:
                    logger.info(f"Step stuck at {current_step}, but continuing (less than 50 questions) in channel {game.channel_id}")
                    game.stuck_count = 0
            else:
                game.stuck_count = 0

            should_guess = False
            progression = game.aki.progression

            if current_step >= 50:
                if progression >= 98.0:
                    should_guess = True
                    logger.info(f"Very high confidence guess at step {current_step} (progression: {progression}) in channel {game.channel_id}")
            elif current_step >= 65:
                if progression >= 95.0:
                    should_guess = True
                    logger.info(f"High confidence guess at step {current_step} (progression: {progression}) in channel {game.channel_id}")
            elif current_step >= 75:
                if progression >= 90.0:
                    should_guess = True
                    logger.info(f"Medium confidence guess at step {current_step} (progression: {progression}) in channel {game.channel_id}")

            if current_step >= 78:
                should_guess = True
                logger.info(f"Approaching limit, forcing guess at step {current_step} in channel {game.channel_id}")

            if should_guess and not game.is_guessing:
                await self._try_guess(game)
            elif current_step >= 79:
                await self._end_game(game, "質問の上限に達しました。私の負けです！")
            else:
                try:
                    question = game.aki.question
                    if not question or question.strip() == "":
                        logger.warning(f"Empty question received at step {current_step} in channel {game.channel_id}")
                        if current_step >= 50:
                            await self._try_guess(game)
                        return

                    embed = self._create_question_embed(question, progression, current_step)
                    view = GameButtonView(self, game)
                    await game.message.edit(embed=embed, view=view)
                except AttributeError:
                    logger.warning(f"AttributeError getting question at step {current_step} in channel {game.channel_id}", exc_info=True)
                    if current_step >= 50:
                        await self._try_guess(game)
                    return

        except RuntimeError as e:
            await errors.handle_runtime_error(game, e, self)
        except Exception as e:
            logger.exception(f"Error handling answer at step {game.aki.step} in channel {game.channel_id}")

            game.timeout_count += 1
            if game.timeout_count >= 5 and game.aki.step >= 60:
                logger.warning(f"Too many errors after 60 questions, attempting guess at step {game.aki.step} in channel {game.channel_id}")
                await self._try_guess(game)
            else:
                await errors.handle_connection_error(game, self)

    async def _try_guess(self, game: AkinatorGame):
        if game.is_guessing or not game.is_active:
            return

        game.is_guessing = True

        try:
            win_attr = getattr(game.aki, 'win', None)
            if callable(win_attr):
                try:
                    await asyncio.wait_for(game.aki.win(), timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning(f"Akinator win() timeout at step {game.aki.step} in channel {game.channel_id}")
            else:
                logger.info(f"Akinator 'win' is a property (value: {win_attr}), skipping call in channel {game.channel_id}")

            name = None
            description = None
            photo = None

            name = getattr(game.aki, 'name_proposition', None)
            description = getattr(game.aki, 'description_proposition', None)
            photo = getattr(game.aki, 'photo', None)

            if not name:
                first_guess = getattr(game.aki, 'first_guess', {})
                if isinstance(first_guess, dict):
                    name = first_guess.get('name')
                    description = description or first_guess.get('description')
                    photo = photo or first_guess.get('absolute_picture_path')

            if name and name.strip():
                guess_data = {
                    'name': name,
                    'description': description or 'データなし',
                    'absolute_picture_path': photo
                }
                game.current_guess = guess_data

                if game.is_active:
                    embed = self._create_guess_embed(guess_data)
                    view = GuessButtonView(self, game)
                    await game.message.edit(embed=embed, view=view)
                return

            logger.warning(f"No guess data available at step {game.aki.step} in channel {game.channel_id}")
            game.is_guessing = False

            if game.aki.step < 75:
                embed = self._create_question_embed(game.aki.question, game.aki.progression, game.aki.step)
                view = GameButtonView(self, game)
                await game.message.edit(embed=embed, view=view)
            else:
                await self._end_game(game, "申し訳ありません、キャラクターを特定できませんでした。私の負けです！")

        except Exception as e:
            logger.exception(f"Error in _try_guess for game in channel {game.channel_id}")
            await errors.handle_guess_error(game, e, self)

    async def _end_game(self, game: AkinatorGame, message: str):
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
                logger.warning(f"Failed to edit message in _end_game for channel {game.channel_id}: {e}")

        try:
            if game.channel_id in self.games:
                del self.games[game.channel_id]
        except Exception:
            logger.exception(f"Failed to cleanup game in _end_game for channel {game.channel_id}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AkinatorCog(bot))