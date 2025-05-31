from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from typing import Optional, Dict, List # Dict, List を追加

import discord
from discord.ext import commands
import httpx
import yaml

from plugins import load_plugins

# ロギングを設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# メッセージノードの最大数 (会話履歴の最大長に関わる) - DiscordLLMBotクラスで使うのでここに移動
MAX_MESSAGE_NODES = 100


def load_config(filename: str = "config.yaml") -> dict:
    """YAML 設定ファイルを読み込み (または再読み込み) ます。"""
    with open(filename, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


class DiscordLLMBot(commands.Bot):
    """会話を LLM に転送する Discord ボットです。"""

    cfg_path: str
    cfg: dict # cfgの型ヒントを追加
    message_nodes: Dict[int, 'MessageNode'] # MessageNodeはcogs.llm_cogで定義されるためフォワード参照
    last_task_time: Optional[float]
    httpx_client: httpx.AsyncClient
    SYSTEM_PROMPT: Optional[str]
    STARTER_PROMPT: Optional[str]
    ERROR_MESSAGES: Dict[str, str]
    plugins: Dict[str, any] # pluginの型はload_pluginsの実装による

    def __init__(self, cfg_path: str = "config.yaml") -> None:
        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)

        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True

        activity = discord.CustomActivity(
            name=(self.cfg.get("status_message") or "github.com/jakobdylanc/llmcord")[:128]
        )

        super().__init__(
            command_prefix=commands.when_mentioned_or(self.cfg.get("command_prefix", "!!")),
            intents=intents,
            activity=activity
        )

        self.message_nodes = {}
        self.last_task_time = None
        self.httpx_client = httpx.AsyncClient()

        self.SYSTEM_PROMPT = self.cfg.get("system_prompt")
        self.STARTER_PROMPT = self.cfg.get("starter_prompt")
        self.ERROR_MESSAGES = self.cfg.get("error_msg", {}) or {}

        self.plugins = load_plugins(self)

        logging.info("読み込まれたプラグイン: [%s]", ", ".join(p.__class__.__name__ for p in self.plugins.values()))
        logging.info("有効なツール: [%s]", ", ".join(spec["function"]["name"] for spec in self._enabled_tools()))


    async def setup_hook(self) -> None:
        """クライアント接続後に一度だけ呼ばれます。Cogをロードし、アプリケーションコマンドを同期します。"""
        await self._load_all_cogs()
        # グローバルコマンドとして同期 (ギルド指定なし)
        await self.tree.sync()
        logging.info("スラッシュコマンドを同期しました。")


    async def _load_all_cogs(self):
        """'cogs' ディレクトリ内の全てのCogをロードします。"""
        cogs_dir = "cogs"
        if not os.path.exists(cogs_dir):
            logging.info(f"'{cogs_dir}' ディレクトリが見つかりません。Cogのロードをスキップします。")
            return

        loaded_cogs_count = 0
        for filename in os.listdir(cogs_dir):
            if filename.endswith(".py") and not filename.startswith("_"):
                extension_name = f"{cogs_dir}.{filename[:-3]}"
                try:
                    await self.load_extension(extension_name)
                    logging.info(f"Cog '{filename}' をロードしました。")
                    loaded_cogs_count += 1
                except commands.ExtensionAlreadyLoaded:
                    try:
                        await self.reload_extension(extension_name)
                        logging.info(f"Cog '{filename}' を再ロードしました。")
                    except Exception as e_reload:
                        logging.error(f"Cog '{filename}' の再ロードに失敗しました。", exc_info=e_reload)
                except commands.NoEntryPointError:
                    logging.error(f"Cog '{filename}' に setup 関数が見つかりません。ロードできません。")
                except commands.ExtensionFailed as ef:
                    logging.error(f"Cog '{filename}' のロードに失敗しました (ExtensionFailed): {ef.name}",
                                  exc_info=ef.original)
                except Exception as e:
                    logging.error(f"Cog '{filename}' のロード中に予期しないエラーが発生しました。", exc_info=e)

        if loaded_cogs_count > 0:
            logging.info(f"{loaded_cogs_count}個のCogをロードしました。")
        else:
            if os.path.exists(cogs_dir) and not any(
                    f.endswith(".py") and not f.startswith("_") for f in os.listdir(cogs_dir)):
                logging.info(f"'{cogs_dir}' ディレクトリにロード可能なCogファイルが見つかりませんでした。")
            elif os.path.exists(cogs_dir):
                logging.warning("Cogのロードに成功しませんでした。ログを確認してください。")

    async def on_ready(self):
        logging.info(f"{self.user} としてログインしました (ID: {self.user.id})")
        logging.info(f"接続ギルド数: {len(self.guilds)}")

    def _enabled_tools(self) -> list[dict]:
        """有効なツール仕様のリストを返します。"""
        want = self.cfg.get("active_tools", None)
        if want is None:
            return [p.tool_spec for p in self.plugins.values()]
        if not want:
            return []
        return [p.tool_spec for n, p in self.plugins.items() if n in want]


aio_run = asyncio.run


def ensure_config(cfg_path: str = "config.yaml",
                  default_path: str = "config.default.yaml") -> None:
    if os.path.exists(cfg_path):
        return

    if not os.path.exists(default_path):
        logging.critical(
            f"{cfg_path} が無く、{default_path} も見つからないため起動できません。")
        sys.exit(1)

    shutil.copy2(default_path, cfg_path)
    logging.warning(
        f"{cfg_path} が無かったため {default_path} をコピーしました。\n"
        f"必要に応じて編集してから再度起動してください。")
    sys.exit(0)


async def _main() -> None:
    ensure_config()
    cfg = load_config() # load_configはグローバル関数として呼び出し

    # client_id は config.yaml から取得する想定
    # discord.py v2.0以降、bot招待URLにはApplication ID (client_idと同じ場合が多い) を使います
    if application_id := cfg.get("application_id", cfg.get("client_id")):
        logging.info(
            "\n\nボット招待 URL:\n"
            "https://discord.com/api/oauth2/authorize?client_id=%s&permissions=412317273088&scope=bot\n",
            application_id,
        )

    bot_token = cfg.get("bot_token")
    if not bot_token:
        logging.critical("config.yaml に 'bot_token' が見つかりません。ボットを起動できません。")
        sys.exit(1)

    # DiscordLLMBotのインスタンス作成時にcfg_pathを渡す
    bot = DiscordLLMBot(cfg_path="config.yaml")
    await bot.start(bot_token)


if __name__ == "__main__":
    try:
        aio_run(_main())
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt によりボットがシャットダウンしています。")
    except SystemExit:
        logging.info("SystemExit によりボットがシャットダウンしています。")
    except Exception as e:
        logging.exception(f"ボットの起動/実行中にハンドルされていないエラーが発生しました: {e}")