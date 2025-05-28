from __future__ import annotations

import asyncio
import logging
# import re # MENTION_PATTERN は LLM Cog に移動
# from base64 import b64encode # LLM Cog に移動
# from dataclasses import dataclass, field # MessageNode は LLM Cog に移動
from datetime import datetime as dt  # type: ignore
from typing import Literal, Optional, Set, Tuple, List, Dict, Any  # Any を追加

import discord  # type: ignore
from discord import app_commands  # type: ignore
from discord.ext import commands  # commands をインポート
import httpx  # Botインスタンスで一元管理
import yaml
# import json # LLM Cog で使用
# import time # LLM Cog で使用
import os
import sys
import shutil
import glob  # cogsのロード用

# openai 関連は LLM Cog へ
# from plugins import load_plugins # メインでプラグイン自体はロードする

# ロギング設定はメインに残す
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(module)s: %(message)s",  # module を追加
)
logger = logging.getLogger(__name__)  # メインファイル用のロガー

# LLM Cog で使用する定数はそちらへ移動
# MAX_MESSAGE_NODES は設定ファイルキーとして管理
MAX_MESSAGE_NODES_LLM_CONFIG_KEY = "max_message_nodes_llm"  # LLM Cogの履歴ノード最大数キー


def load_config(filename: str = "config.yaml") -> dict:
    """YAML 設定ファイルを読み込みます。エラー時はプログラムを終了します。"""
    try:
        with open(filename, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except FileNotFoundError:
        logger.critical(f"設定ファイル {filename} が見つかりません。プログラムを終了します。")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.critical(f"設定ファイル {filename} の読み込み中にYAMLエラー: {e}。プログラムを終了します。")
        sys.exit(1)
    except Exception as e_gen:
        logger.critical(f"設定ファイル {filename} の読み込み中に予期せぬエラー: {e_gen}。プログラムを終了します。")
        sys.exit(1)


class DiscordLLMBot(commands.Bot):
    """LLM対話機能や音楽再生機能などを持つ多機能Discordボットの基盤クラス。"""

    cfg_path: str

    def __init__(self, cfg_path: str = "config.yaml") -> None:
        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)  # 設定ファイルをロード

        # Intents (ボットが必要とする権限) の設定
        intents = discord.Intents.default()
        intents.message_content = True  # メッセージ内容の読み取り (LLM対話、コマンド処理)
        intents.voice_states = True  # ボイスチャンネル状態の検知 (音楽機能)
        intents.guilds = True  # サーバー情報の取得 (on_guild_join など)
        intents.members = self.cfg.get("intents_members", False)  # メンバー情報 (特権、configで制御)

        # ボットのステータスメッセージ設定
        status_message_key = "status_message_format"  # config.yaml で {guild_count} や {user_count} を使えるように
        default_status = "LLMとお話し | /help"
        status_format_str = self.cfg.get(status_message_key, default_status)
        # 実際の表示は on_ready で guild_count などを使ってフォーマットする
        # ここでは初期表示として単純なものを設定
        activity = discord.CustomActivity(name=status_format_str.split('|')[0].strip()[:128])

        super().__init__(
            command_prefix=commands.when_mentioned_or(self.cfg.get("fallback_command_prefix", "!unused_prefix_main!")),
            # type: ignore
            intents=intents,
            activity=activity,
            help_command=None  # カスタムヘルプまたはCogによるヘルプを想定
        )

        # Cog間で共有する可能性のある属性を初期化
        self.message_nodes_llm: dict[int, Any] = {}  # LLM Cog用 (型はLLM Cog内のMessageNode)
        self.last_llm_edit_task_time: Optional[float] = None  # LLM Cogがメッセージ編集タイミングで使用
        self.httpx_client_shared = httpx.AsyncClient()  # 各CogがHTTPリクエストに使用

        # プラグインのロード (ツールコール用、LLM Cogから参照)
        from plugins import load_plugins  # ここでインポートする方がスコープが明確
        self.plugins = load_plugins(self)

        logger.info("メインボット: 読み込まれたプラグイン: [%s]",
                    ", ".join(p.__class__.__name__ for p in self.plugins.values()))
        logger.info("メインボット: 有効なツール (LLM用): [%s]",
                    ", ".join(spec["function"]["name"] for spec in self._enabled_tools()))

        # Cog拡張機能のパスを自動検出
        cog_dir = os.path.join(os.path.dirname(__file__), "cogs")  # cogsフォルダの絶対パス
        self.initial_extensions = [
            f"cogs.{os.path.splitext(os.path.basename(f))[0]}"  # cogs.ファイル名 (拡張子なし)
            for f in glob.glob(os.path.join(cog_dir, "*.py"))  # cogs/*.py を検索
            if not os.path.basename(f).startswith('_')  # __init__.py や _base.py などを除外
        ]
        logger.info(f"メインボット: 検出されたCog拡張機能: {self.initial_extensions}")

        # メインボットに直接定義するスラッシュコマンドを登録
        self._register_main_slash_commands()

    async def setup_hook(self) -> None:
        """ボット起動時にCogをロードし、スラッシュコマンドを同期します。"""
        logger.info("メインボット: setup_hook を開始します...")
        loaded_cogs_count = 0
        for extension_path in self.initial_extensions:
            try:
                await self.load_extension(extension_path)
                logger.info(f"メインボット: Cog '{extension_path}' を正常にロードしました。")
                loaded_cogs_count += 1
            except commands.ExtensionAlreadyLoaded:  # 既にロード済み
                logger.warning(f"メインボット: Cog '{extension_path}' は既にロードされています。")
                loaded_cogs_count += 1  # カウントには含める
            except commands.NoEntryPointError:  # setup関数がない
                logger.error(
                    f"メインボット: Cog '{extension_path}' に setup 関数が見つかりません。ロードをスキップします。")
            except commands.ExtensionFailed as ext_failed_err:  # Cogのsetup関数内でエラー
                logger.error(
                    f"メインボット: Cog '{extension_path}' のロードに失敗 (ExtensionFailed): {ext_failed_err.name}",
                    exc_info=ext_failed_err.original if hasattr(ext_failed_err, 'original') else ext_failed_err)
            except Exception as general_load_err:  # その他のロードエラー
                logger.error(f"メインボット: Cog '{extension_path}' のロード中に予期せぬエラーが発生しました。",
                             exc_info=general_load_err)

        if loaded_cogs_count > 0:
            logger.info(f"メインボット: 合計 {loaded_cogs_count} 個のCog拡張機能をロード/確認しました。")
        else:
            logger.warning(
                "メインボット: ロード対象のCog拡張機能が見つかりませんでした。cogsフォルダを確認してください。")

        # スラッシュコマンドをDiscordサーバーと同期
        try:
            # 特定ギルドのみに同期する場合は guild=discord.Object(id=YOUR_GUILD_ID) を指定
            synced_command_list = await self.tree.sync()
            logger.info(
                f"メインボット: {len(synced_command_list)} 個のスラッシュコマンドをグローバルに登録・同期しました。")
        except Exception as sync_err:
            logger.error("メインボット: スラッシュコマンドの同期中にエラーが発生しました。", exc_info=sync_err)
        logger.info("メインボット: setup_hook を完了しました。")

    async def on_ready(self):
        """ボットがDiscordに接続し、準備が完了したときに呼び出されます。"""
        if self.user is None: return  # まれだが念のため
        logger.info(f'{self.user.name} (ID: {self.user.id}) としてDiscordにログインしました。')
        logger.info(f"接続サーバー数: {len(self.guilds)}")

        # 動的なステータスメッセージの設定 (サーバー数などを反映)
        status_format_str = self.cfg.get("status_message_format", "{bot_name} | /help")
        try:
            guild_count = len(self.guilds)
            # user_count は全サーバーの総ユーザー数で、取得に時間がかかる場合があるため注意
            # member_count = sum(g.member_count for g in self.guilds if g.member_count) # None を避ける
            status_text = status_format_str.format(
                bot_name=self.user.name,
                guild_count=guild_count,
                # user_count=member_count # 必要なら追加
            )[:128]  # Discordの文字数制限
            new_activity = discord.CustomActivity(name=status_text)
            await self.change_presence(activity=new_activity)
            logger.info(f"ボットステータスを「{status_text}」に更新しました。")
        except Exception as e_status:
            logger.error(f"動的ステータス更新中にエラー: {e_status}")

    # on_message は各Cogのリスナーで処理されるため、メインファイルでは通常不要
    # もしメインで処理が必要な場合は、Cogのリスナーと競合しないように注意

    def _enabled_tools(self) -> list[dict]:
        """LLMが使用可能なツール(プラグイン)の仕様リストを返します。"""
        tools_config = self.cfg.get("active_tools", None)  # 設定ファイルから有効なツール名リストを取得
        if tools_config is None:  # 未設定なら全プラグインを有効とみなす
            return [p.tool_spec for p in self.plugins.values() if hasattr(p, 'tool_spec')]
        if not tools_config:  # 空リストなら何も有効化しない
            return []
        # 指定された名前のプラグインのみ有効化
        return [
            p.tool_spec for name, p in self.plugins.items()
            if hasattr(p, 'tool_spec') and name in tools_config
        ]

    def _register_main_slash_commands(self) -> None:
        """このボット本体に直接定義する、Cogに属さない汎用スラッシュコマンドを登録します。"""

        @self.tree.command(name="ping", description="ボットの応答速度 (レイテンシ) を表示します。")
        async def _ping_command(interaction: discord.Interaction):
            latency_ms = self.latency * 1000  # WebSocketレイテンシをミリ秒に変換
            await interaction.response.send_message(f"Pong! 応答速度: {latency_ms:.2f}ms", ephemeral=True)

        @self.tree.command(name="invite", description="このボットをあなたのサーバーに招待するリンクを表示します。")
        async def _invite_command(interaction: discord.Interaction) -> None:
            try:
                client_id_from_cfg = self.cfg.get("client_id")
                # ボット自身のIDをフォールバックとして使用
                bot_client_id_to_use = client_id_from_cfg if client_id_from_cfg else \
                    (self.user.id if self.user else None)

                if not bot_client_id_to_use:
                    await interaction.response.send_message(
                        "エラー: 招待URLを生成できません (ボットのクライアントIDが不明です)。",
                        ephemeral=True
                    )
                    logger.error("招待コマンドエラー: クライアントIDが設定からもボット自身からも取得できませんでした。")
                    return

                # 招待に必要な権限 (config.yaml から取得、なければデフォルト値)
                invite_permissions_str = self.cfg.get("invite_permissions", "412317273088")
                # スコープは bot と applications.commands (スラッシュコマンド用)
                invite_link_url = f"https://discord.com/api/oauth2/authorize?client_id={bot_client_id_to_use}&permissions={invite_permissions_str}&scope=bot%20applications.commands"

                embed = discord.Embed(
                    title="🔗 ボット招待",
                    description=f"{self.user.name if self.user else 'このボット'}をあなたのDiscordサーバーに招待しませんか？",
                    # type: ignore
                    color=discord.Color.brand_green()  # Discordのブランドカラー(緑)
                )
                embed.add_field(name="招待リンク", value=f"[ここをクリックして招待する]({invite_link_url})",
                                inline=False)
                if self.user and self.user.avatar:  # ボットのアバターがあればサムネイルに設定
                    embed.set_thumbnail(url=self.user.avatar.url)
                embed.set_footer(text=f"コマンド実行者: {interaction.user.display_name}")
                await interaction.response.send_message(embed=embed, ephemeral=False)  # 招待リンクは全員に見えるように
            except Exception as e_invite:
                logger.error(f"招待コマンド処理中にエラー発生: {e_invite}", exc_info=True)
                await interaction.response.send_message(
                    "申し訳ありません、招待リンクの表示中にエラーが発生しました。しばらくしてから再度お試しください。",
                    ephemeral=True
                )

        @self.tree.command(name="reloadconfig", description="設定ファイル(config.yaml)を再読み込みします（管理者専用）。")
        async def _reload_config_command(interaction: discord.Interaction) -> None:
            admin_user_ids_cfg = self.cfg.get("admin_user_ids", [])  # 設定から管理者IDリストを取得
            # IDは数値型に変換 (設定ミスで文字列が入っていても対応)
            admin_ids_set = {int(uid) for uid in admin_user_ids_cfg if str(uid).isdigit()}

            if interaction.user.id not in admin_ids_set:  # コマンド実行者が管理者か確認
                await interaction.response.send_message("❌ このコマンドを実行する権限がありません。", ephemeral=True)
                return

            try:
                self.cfg = load_config(self.cfg_path)  # 設定ファイルを再読み込みして self.cfg を更新
                logger.info(
                    f"設定ファイル {self.cfg_path} がユーザー {interaction.user.id} により手動で再読み込みされました。")

                # ロード済みの各Cogに設定変更を通知する (Cog側で対応メソッド実装が必要)
                for cog_name, cog_instance in self.cogs.items():
                    # Cogが `reload_config_from_bot` メソッドを持っているか確認
                    if hasattr(cog_instance, "reload_config_from_bot") and callable(
                            getattr(cog_instance, "reload_config_from_bot")):
                        try:
                            await cog_instance.reload_config_from_bot(self.cfg)
                            logger.info(f"メインボット: Cog '{cog_name}' に設定再読み込みを正常に通知しました。")
                        except Exception as e_cog_reload_notify:
                            logger.error(
                                f"メインボット: Cog '{cog_name}' への設定再読み込み通知中にエラー発生: {e_cog_reload_notify}")
                    elif hasattr(cog_instance, 'cfg'):  # または、Cogが 'cfg' 属性を直接持っている場合はそれを更新
                        cog_instance.cfg = self.cfg  # type: ignore
                        logger.info(f"メインボット: Cog '{cog_name}' の 'cfg' 属性を直接更新しました。")

                await interaction.response.send_message("✅ 設定ファイルを正常に再読み込みしました。", ephemeral=True)
            except Exception as e_reload:  # 再読み込み処理全体でのエラー
                logger.exception("設定ファイルの手動再読み込み中にエラーが発生しました。")
                await interaction.response.send_message(f"⚠️ 設定ファイルの再読み込みに失敗しました: {e_reload}",
                                                        ephemeral=True)

    # LLM関連の主要なメソッド群は LLMInteractionsCog に移動済み


# --- ボット起動処理 ---
aio_run = asyncio.run  # asyncio.run のエイリアス


def ensure_config_exists(config_file_path: str = "config.yaml",
                         default_config_path: str = "config.default.yaml") -> None:
    """設定ファイルが存在しない場合、デフォルト設定ファイルからコピーします。"""
    if os.path.exists(config_file_path): return  # 既に存在すれば何もしない

    if not os.path.exists(default_config_path):  # デフォルトファイルもなければエラー
        logger.critical(
            f"設定ファイル '{config_file_path}' が存在せず、"
            f"デフォルト設定ファイル '{default_config_path}' も見つかりません。ボットを起動できません。"
        )
        sys.exit(1)  # 致命的エラーとして終了

    try:
        shutil.copy2(default_config_path, config_file_path)  # デフォルト設定をコピー
        logger.warning(
            f"設定ファイル '{config_file_path}' が見つからなかったため、"
            f"'{default_config_path}' からコピーしました。\n"
            f"必要に応じて '{config_file_path}' の内容 (特に bot_token) を編集してから、ボットを再起動してください。"
        )
        sys.exit(0)  # 設定ファイルを作成したので、ユーザーに編集を促して一旦終了
    except Exception as e_copy_config:
        logger.critical(
            f"デフォルト設定ファイル '{default_config_path}' から '{config_file_path}' へのコピー中にエラーが発生: {e_copy_config}"
        )
        sys.exit(1)


async def main_async_runner() -> None:  # 関数名を変更 (main_async から)
    """ボットのメイン実行処理（非同期）。設定読み込み、ボット初期化、起動を行います。"""
    config_file = "config.yaml"
    default_config_file = "config.default.yaml"
    ensure_config_exists(config_file, default_config_file)  # 設定ファイルの存在確認と自動生成

    bot_configuration = load_config(config_file)  # 設定ファイルをロード

    discord_bot_token = bot_configuration.get("bot_token")
    if not discord_bot_token:  # トークンがなければ起動不可
        logger.critical(f"設定ファイル '{config_file}' に 'bot_token' が設定されていません。ボットを起動できません。")
        sys.exit(1)

    # 招待URLのログ出力 (client_id が設定にあれば)
    if bot_client_id_for_invite := bot_configuration.get("client_id"):
        invite_permissions_setting = bot_configuration.get("invite_permissions", "412317273088")  # デフォルト権限
        logger.info(
            f"\n===== ボット招待URL (参考) =====\n"
            f"https://discord.com/api/oauth2/authorize?client_id={bot_client_id_for_invite}&permissions={invite_permissions_setting}&scope=bot%20applications.commands\n"
            f"↑↑↑ 表示される権限セットがボットの全機能 (音楽再生等も含む) に対して適切か確認してください ↑↑↑\n"
            f"=================================\n"
        )

    # ボットインスタンスを作成
    bot_app = DiscordLLMBot(cfg_path=config_file)

    try:
        await bot_app.start(discord_bot_token)  # ボットを起動
    except discord.LoginFailure:  # トークン無効エラー
        logger.critical("無効なボットトークンです。Discordへのログインに失敗しました。トークンを確認してください。")
    except Exception as e_bot_start:  # その他の起動時エラー
        logger.critical(f"ボットの起動処理中に予期せぬエラーが発生しました: {e_bot_start}", exc_info=True)
    finally:
        # ボット終了時のクリーンアップ処理
        if bot_app and not bot_app.is_closed():
            await bot_app.close()  # Discord接続を閉じる
        # httpx_client は Botインスタンスの属性として管理されているので、
        # bot_app.close() 内で適切にクローズされるか、個別にクローズ処理が必要ならここに追加
        if bot_app and hasattr(bot_app, 'httpx_client_shared') and \
                bot_app.httpx_client_shared and not bot_app.httpx_client_shared.is_closed:
            await bot_app.httpx_client_shared.aclose()
            logger.info("共有httpxクライアントをクローズしました。")
        logger.info("ボットのシャットダウン処理が完了しました。")


if __name__ == "__main__":
    try:
        aio_run(main_async_runner())  # メインの非同期関数を実行
    except KeyboardInterrupt:  # Ctrl+C での終了
        logger.info("Ctrl+Cによるキーボード割り込みを検知。ボットをシャットダウンします...")
    except SystemExit as sys_exit_event:  # sys.exit() が呼ばれた場合
        # 終了コードに応じてログレベルを変更 (0は正常終了)
        exit_log_level = logging.INFO if sys_exit_event.code == 0 else logging.WARNING
        logger.log(exit_log_level, f"プログラムが終了コード {sys_exit_event.code} で終了しました。")
    except Exception as unhandled_global_exception:  # その他のハンドルされなかったグローバルな例外
        logger.critical("メイン処理中にハンドルされない致命的なエラーが発生しました。",
                        exc_info=unhandled_global_exception)
    finally:
        logger.info("ボットプロセスが完全に終了します。")