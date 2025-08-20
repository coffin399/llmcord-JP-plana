import discord
from discord.ext import commands
from discord import app_commands
import akinator
import asyncio
from typing import Optional, Dict
import traceback


class AkinatorGame:
    """アキネーターゲームのセッション管理クラス"""

    def __init__(self, aki: akinator.AsyncAkinator, channel_id: int, user_id: int):
        self.aki = aki
        self.channel_id = channel_id
        self.user_id = user_id
        self.message: Optional[discord.Message] = None
        self.is_active = True
        self.is_guessing = False
        self.current_guess = None
        self.retry_count = 0  # リトライカウンター
        self.max_retries = 3  # 最大リトライ回数


class AkinatorCog(commands.Cog):
    """Discord用Akinatorボット"""
    REACTIONS = {
        "✅": "y", "❌": "n", "🤷": "idk",
        "👍": "p", "👎": "pn", "⬅️": "b",
    }
    CONTROL_REACTIONS = {"🛑": "stop"}

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.games: Dict[int, AkinatorGame] = {}

    @app_commands.command(name="akinator", description="アキネーターゲームを開始します")
    async def akinator_command(self, interaction: discord.Interaction):
        """アキネーターゲームを開始"""
        if interaction.channel_id in self.games:
            await interaction.response.send_message("このチャンネルでは既にゲームが進行中です！", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            # Akinatorインスタンスの作成と初期化
            aki = akinator.AsyncAkinator()
            game = AkinatorGame(aki, interaction.channel_id, interaction.user.id)
            self.games[interaction.channel_id] = game

            # ゲーム開始（リトライ付き）
            for attempt in range(3):
                try:
                    await aki.start_game(language="jp", child_mode=False)
                    break
                except Exception as e:
                    if attempt == 2:
                        raise e
                    await asyncio.sleep(1)

            question = aki.question
            embed = self._create_question_embed(question, aki.progression, aki.step)
            message = await interaction.followup.send(embed=embed)
            game.message = message
            await self._add_reactions(message)

        except Exception as e:
            error_msg = f"ゲームの開始中にエラーが発生しました。\n`{type(e).__name__}: {e}`"
            print(f"Akinator start error: {traceback.format_exc()}")
            await interaction.followup.send(error_msg, ephemeral=True)
            if interaction.channel_id in self.games:
                del self.games[interaction.channel_id]

    def _create_question_embed(self, question: str, progression: float, step: int) -> discord.Embed:
        """質問用の埋め込みを作成"""
        embed = discord.Embed(
            title="🔮 アキネーター",
            description=f"**質問 {step + 1}:**\n\n## {question}",
            color=discord.Color.blue()
        )
        choices = [
            "✅ はい (Yes)", "❌ いいえ (No)", "🤷 わからない (I don't know)",
            "👍 たぶんそう (Probably)", "👎 たぶん違う (Probably not)",
            "⬅️ 前の質問に戻る (Back)", "🛑 ゲームを終了 (Stop)"
        ]
        embed.add_field(name="選択肢 (リアクションで回答)", value="\n".join(choices), inline=False)
        progress_bar = self._create_progress_bar(progression)
        embed.add_field(name="進行状況", value=progress_bar, inline=False)
        embed.set_footer(text="質問に答えてキャラクターを当てさせよう！")
        return embed

    def _create_progress_bar(self, progression: float) -> str:
        """進行状況バーを作成"""
        percentage = progression
        filled_blocks = int(percentage / 100 * 20)
        empty_blocks = 20 - filled_blocks
        bar = "█" * filled_blocks + "░" * empty_blocks
        return f"`[{bar}] {percentage:.2f}%`"

    def _create_guess_embed(self, guess: dict) -> discord.Embed:
        """推測結果の埋め込みを作成"""
        embed = discord.Embed(
            title="🎯 私の推測は… このキャラクターですか？",
            color=discord.Color.green()
        )

        # デバッグ: 推測オブジェクトの内容を確認
        print(f"Creating embed for guess: {guess}")
        print(f"Guess type: {type(guess)}")
        if isinstance(guess, dict):
            print(f"Guess keys: {guess.keys()}")

        # キャラクター名の取得（複数のキー名に対応）
        name = "Unknown"
        if isinstance(guess, dict):
            name = (guess.get('name') or
                    guess.get('character') or
                    guess.get('Name') or
                    guess.get('answer') or
                    'Unknown')
        elif hasattr(guess, 'name'):
            name = guess.name
        elif isinstance(guess, str):
            name = guess

        embed.add_field(name="キャラクター", value=f"### {name}", inline=False)

        # 説明の取得
        description = "データなし"
        if isinstance(guess, dict):
            description = (guess.get('description') or
                           guess.get('Description') or
                           guess.get('desc') or
                           'データなし')
        elif hasattr(guess, 'description'):
            description = guess.description

        if len(description) > 1024:
            description = description[:1021] + "..."
        embed.add_field(name="説明", value=description, inline=False)

        # 画像URLの取得と設定（'image'キーも追加）
        image_url = None
        if isinstance(guess, dict):
            image_url = (guess.get('absolute_picture_path') or
                         guess.get('picture_path') or
                         guess.get('image') or  # このキーに対応
                         guess.get('Image') or
                         guess.get('photo'))
        elif hasattr(guess, 'absolute_picture_path'):
            image_url = guess.absolute_picture_path
        elif hasattr(guess, 'photo'):
            image_url = guess.photo

        if image_url:
            try:
                embed.set_image(url=image_url)
                print(f"Successfully set image: {image_url}")
            except Exception as e:
                print(f"Failed to set image: {e}")

        embed.set_footer(text="✅ はい、正解です！ | ❌ いいえ、違います")
        return embed

    async def _add_reactions(self, message: discord.Message):
        """リアクションを追加"""
        reactions = list(self.REACTIONS.keys()) + list(self.CONTROL_REACTIONS.keys())
        for emoji in reactions:
            try:
                await message.add_reaction(emoji)
            except discord.Forbidden:
                pass

    async def _add_guess_reactions(self, message: discord.Message):
        """推測時のリアクションを追加"""
        for emoji in ["✅", "❌"]:
            try:
                await message.add_reaction(emoji)
            except discord.Forbidden:
                pass

    async def _safe_answer(self, game: AkinatorGame, answer: str) -> bool:
        """安全に回答を送信（リトライ機能付き）"""
        for attempt in range(game.max_retries):
            try:
                await game.aki.answer(answer)
                game.retry_count = 0  # 成功したらリセット
                return True
            except Exception as e:
                print(f"Answer attempt {attempt + 1} failed: {e}")
                if "Failed to submit" in str(e) or "timeout" in str(e).lower():
                    # セッションが切れた可能性がある場合、新しいセッションを開始
                    if attempt < game.max_retries - 1:
                        try:
                            # 新しいAkinatorインスタンスを作成
                            new_aki = akinator.AsyncAkinator()
                            await new_aki.start_game(language="jp", child_mode=False)

                            # 現在の進行状況を可能な限り復元
                            # （注：完全な復元は不可能なので、最初からやり直しになる）
                            game.aki = new_aki
                            return True
                        except Exception as reconnect_error:
                            print(f"Reconnection failed: {reconnect_error}")
                            await asyncio.sleep(1)
                    else:
                        return False
                else:
                    return False
        return False

    @commands.Cog.listener()
    async def on_reaction_add(self, reaction: discord.Reaction, user: discord.User):
        """リアクション追加時の処理"""
        if user.bot or reaction.message.channel.id not in self.games:
            return

        game = self.games[reaction.message.channel.id]

        # 権限チェック
        if not game.is_active or reaction.message.id != game.message.id or user.id != game.user_id:
            if not user.bot and user.id != self.bot.user.id:
                try:
                    await reaction.remove(user)
                except discord.Forbidden:
                    pass
            return

        # リアクション削除
        try:
            await reaction.remove(user)
        except discord.Forbidden:
            pass

        emoji = str(reaction.emoji)

        # 推測フェーズの処理
        if game.is_guessing:
            await self._handle_guess_response(game, emoji)
            return

        # 終了コマンドの処理
        if emoji in self.CONTROL_REACTIONS:
            await self._end_game(game, "ゲームが中断されました。")
            return

        # 通常の質問への回答処理
        if emoji in self.REACTIONS:
            await self._handle_answer(game, self.REACTIONS[emoji])

    async def _handle_guess_response(self, game: AkinatorGame, emoji: str):
        """推測への回答を処理"""
        if emoji == "✅":
            winner_name = "Unknown"
            if game.current_guess:
                winner_name = (game.current_guess.get('name') or
                               game.current_guess.get('character') or
                               'Unknown')
            await self._end_game(game, f"🎉 やった！正解です！ **{winner_name}** でしたね！")
        elif emoji == "❌":
            await self._end_game(game, "うーん、残念！私の負けです…また挑戦してくださいね！")

    async def _handle_answer(self, game: AkinatorGame, answer: str):
        """通常の回答を処理"""
        try:
            # 戻るボタンの処理
            if answer == "b":
                try:
                    await game.aki.back()
                except (akinator.CantGoBackAnyFurther, Exception):
                    return
            else:
                # ステップ数制限のチェック
                if game.aki.step >= 80:
                    await self._end_game(game, "質問の上限に達しました。降参です！")
                    return

                # 回答を送信（リトライ機能付き）
                success = await self._safe_answer(game, answer)
                if not success:
                    # リトライが全て失敗した場合
                    await self._handle_connection_error(game)
                    return

            # 進行状況をチェックして推測フェーズへ移行
            # progressionが高くなったら推測を試みる（ただし早すぎない）
            if game.aki.progression >= 80 and game.aki.step >= 5 and not game.is_guessing:
                print(f"Attempting guess at progression: {game.aki.progression}, step: {game.aki.step}")
                await self._try_guess(game)
            elif game.aki.step >= 25 and not game.is_guessing:
                # ステップ数が多い場合も推測を試みる
                print(f"Attempting guess due to high step count: {game.aki.step}")
                await self._try_guess(game)
            else:
                # 次の質問を表示
                embed = self._create_question_embed(
                    game.aki.question,
                    game.aki.progression,
                    game.aki.step
                )
                await game.message.edit(embed=embed)

        except Exception as e:
            print(f"Error handling answer: {traceback.format_exc()}")
            await self._handle_connection_error(game)

    async def _try_guess(self, game: AkinatorGame):
        """推測を試みる"""
        try:
            # まず choose メソッドを引数なしで呼び出す
            if hasattr(game.aki, 'choose') and callable(game.aki.choose):
                try:
                    # choose メソッドを呼び出して推測を確定させる
                    await game.aki.choose()  # 引数なしで呼び出す
                    print("Called choose method successfully")

                    # choose 後に属性が更新されるまで少し待つ
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"Choose method error: {e}")

            # win プロパティを再度チェック
            win_result = game.aki.win
            print(f"Win result after choose: {win_result}")

            # 推測データの取得を複数の方法で試す
            guess = None

            # 方法1: name_proposition などの属性から直接取得（主要な方法）
            if hasattr(game.aki, 'name_proposition') and game.aki.name_proposition:
                # name_propositionが有効な値か確認
                name_prop = game.aki.name_proposition
                # 「思い浮かべているのは」などの無効な値を除外
                if name_prop and not any(x in str(name_prop) for x in ['思い浮かべている', '考えている', 'thinking']):
                    guess = {
                        'name': name_prop,
                        'description': getattr(game.aki, 'description_proposition', 'データなし') or 'データなし',
                        'image': getattr(game.aki, 'photo', None),
                        'id': getattr(game.aki, 'id_proposition', None)
                    }
                    print(f"Created valid guess from name_proposition: {guess}")

            # 方法2: proposition 属性から取得（name_propositionが無効な場合）
            if not guess and hasattr(game.aki, 'proposition'):
                prop = game.aki.proposition
                # propositionが有効な推測データか確認
                if prop and not any(x in str(prop) for x in ['思い浮かべている', '考えている', 'thinking']):
                    if isinstance(prop, dict):
                        guess = prop
                    else:
                        guess = {
                            'name': str(prop),
                            'description': getattr(game.aki, 'description_proposition', 'データなし') or 'データなし',
                            'image': getattr(game.aki, 'photo', None)
                        }
                    print(f"Created guess from proposition: {guess}")

            # 方法3: winがTrueの場合、属性を再確認
            if not guess and win_result:
                print("Win is True, checking all attributes again...")
                for attr in ['name_proposition', 'proposition', 'description_proposition', 'photo']:
                    if hasattr(game.aki, attr):
                        value = getattr(game.aki, attr)
                        print(f"{attr}: {value}")

                # 何らかの名前データがある場合
                name = getattr(game.aki, 'name_proposition', None) or getattr(game.aki, 'proposition', None)
                if name and not any(x in str(name) for x in ['思い浮かべている', '考えている']):
                    guess = {
                        'name': str(name),
                        'description': getattr(game.aki, 'description_proposition', 'データなし') or 'データなし',
                        'image': getattr(game.aki, 'photo', None)
                    }

            # 有効な推測データがある場合のみ推測フェーズに移行
            if guess and guess.get('name') and not any(
                    x in guess['name'] for x in ['思い浮かべている', '考えている', 'thinking', 'キャラクターを特定中']):
                game.current_guess = guess
                game.is_guessing = True

                print(f"Using valid guess: {guess}")
                embed = self._create_guess_embed(guess)
                await game.message.clear_reactions()
                await game.message.edit(embed=embed)
                await self._add_guess_reactions(game.message)
            else:
                # 推測が取得できない場合、質問を続ける
                print(f"No valid guess available, continuing questions...")
                print(f"Current state - Progression: {game.aki.progression}, Step: {game.aki.step}")

                # まだ質問の余地がある場合は続ける
                if game.aki.step < 30:
                    embed = self._create_question_embed(
                        game.aki.question,
                        game.aki.progression,
                        game.aki.step
                    )
                    await game.message.edit(embed=embed)
                else:
                    await self._end_game(game, "申し訳ありません、キャラクターを特定できませんでした。")

        except Exception as e:
            print(f"Guess error: {traceback.format_exc()}")
            # エラーが発生しても質問を続ける
            if game.aki.step < 30:
                embed = self._create_question_embed(
                    game.aki.question,
                    game.aki.progression,
                    game.aki.step
                )
                await game.message.edit(embed=embed)
            else:
                await self._end_game(game, "推測の取得中にエラーが発生しました。")

    async def _handle_connection_error(self, game: AkinatorGame):
        """接続エラーを処理"""
        error_embed = discord.Embed(
            title="⚠️ 接続エラー",
            description="Akinatorサーバーとの接続に問題が発生しました。\n"
                        "しばらく待ってから `/akinator` コマンドで新しいゲームを開始してください。",
            color=discord.Color.orange()
        )

        try:
            await game.message.edit(embed=error_embed)
            await game.message.clear_reactions()
        except discord.NotFound:
            pass

        # ゲームを削除
        if game.channel_id in self.games:
            del self.games[game.channel_id]

    async def _end_game(self, game: AkinatorGame, message: str):
        """ゲームを終了"""
        if not game.is_active:
            return

        game.is_active = False

        embed = discord.Embed(
            title="🔮 アキネーター - ゲーム終了",
            description=message,
            color=discord.Color.red()
        )

        if game.message:
            try:
                await game.message.edit(embed=embed, view=None)
                await game.message.clear_reactions()
            except (discord.NotFound, discord.Forbidden):
                pass

        if game.channel_id in self.games:
            del self.games[game.channel_id]


async def setup(bot: commands.Bot):
    await bot.add_cog(AkinatorCog(bot))