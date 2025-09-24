# PLANA/cogs/akinator/errors.py

import traceback
import discord
import akinator
from typing import TYPE_CHECKING

# 型チェック時のみ循環参照を解決するためにインポート
if TYPE_CHECKING:
    from PLANA.games.akinator_cog import AkinatorCog, AkinatorGame

async def handle_start_game_error(interaction: discord.Interaction, error: Exception, cog: "AkinatorCog"):
    """ゲーム開始時のエラーを処理する"""
    error_msg = f"ゲームの開始中にエラーが発生しました。\n`{type(error).__name__}: {error}`"
    print(f"Akinator start error: {traceback.format_exc()}")
    try:
        # エラーメッセージで編集を試みる
        await interaction.response.edit_message(content=error_msg, embed=None, view=None)
    except discord.HTTPException:
        # 編集に失敗した場合はfollowupで送信
        await interaction.followup.send(error_msg, ephemeral=True)

    # ゲームオブジェクトが残っていれば削除
    if interaction.channel_id in cog.games:
        del cog.games[interaction.channel_id]

async def handle_runtime_error(game: "AkinatorGame", error: RuntimeError, cog: "AkinatorCog"):
    """回答処理中の特定のRuntimeErrorを処理する"""
    error_msg = str(error)
    print(f"[RuntimeError] at step {game.aki.step}: {error_msg}")

    # "Failed to exclude the proposition" というサーバー側の特定のエラーを処理
    if "Failed to exclude the proposition" in error_msg:
        if game.aki.step < 20:
            print("[RuntimeError] ゲーム序盤のエラーのため、質問を継続します。")
            # 次の質問を表示してゲームを続行
            embed = cog._create_question_embed(game.aki.question, game.aki.progression, game.aki.step)
            view = cog.GameButtonView(cog, game) # GameButtonViewはCogの内部クラスと仮定
            await game.message.edit(embed=embed, view=view)
        elif game.aki.step >= 20 and hasattr(game.aki, 'name_proposition') and game.aki.name_proposition:
            print("[RuntimeError] サーバーエラー後ですが、推測を試みます。")
            await cog._try_guess(game)
        else:
            await cog._end_game(game, "サーバーとの通信に問題が発生しました。私の負けです！")
    else:
        # その他のRuntimeErrorは予期せぬものとして処理
        print(f"[RuntimeError] 予期せぬエラー: {traceback.format_exc()}")
        await handle_connection_error(game, cog)

async def handle_connection_error(game: "AkinatorGame", cog: "AkinatorCog"):
    """接続関連のエラーを処理する"""
    if game.is_guessing:
        return
    await cog._end_game(game, "Akinatorサーバーとの接続に問題が発生しました。")

async def handle_guess_error(game: "AkinatorGame", error: Exception, cog: "AkinatorCog"):
    """推測処理中のエラーを処理する"""
    print(f"[Guess Error] Error during guessing: {traceback.format_exc()}")
    print(f"[Guess Error] Error details: {str(error)}")

    game.is_guessing = False

    # エラーが発生しても、質問数が上限に達していなければゲームを続行
    if game.aki.step < 75:
        embed = cog._create_question_embed(game.aki.question, game.aki.progression, game.aki.step)
        view = cog.GameButtonView(cog, game) # GameButtonViewはCogの内部クラスと仮定
        await game.message.edit(embed=embed, view=view)
    else:
        await cog._end_game(game, "推測の処理中にエラーが発生しました。私の負けです！")