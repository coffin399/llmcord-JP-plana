from __future__ import annotations

import asyncio
import logging
import re
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime as dt
from typing import Literal, Optional, Set, Tuple, List

import discord
from discord import app_commands
import httpx
import yaml
import glob
import json
import time
import os
import sys
import shutil
# openai から RateLimitError をインポート
from openai import AsyncOpenAI, RateLimitError
from google import genai


from plugins import load_plugins

# ロギングを設定
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# ビジョンモデルのタグ
VISION_MODEL_TAGS: Tuple[str, ...] = (
    "gpt-4o",
    "claude-3",
    "gemini",
    "pixtral",
    "llava",
    "vision",
)
# ユーザー名をサポートするプロバイダー
PROVIDERS_SUPPORTING_USERNAMES: Tuple[str, ...] = ("openai", "x-ai")

# 許可される添付ファイルの種類
ALLOWED_FILE_TYPES: Tuple[str, ...] = ("image", "text")
INVITE_URL = "https://discord.com/api/oauth2/authorize?client_id=1031673203774464160&permissions=412317273088&scope=bot"

# 許可されるチャンネルの種類
ALLOWED_CHANNEL_TYPES: Tuple[discord.ChannelType, ...] = (
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.private,
)

# 埋め込みの色
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
# ストリーミング表示インジケーター なんか表示されない
STREAMING_INDICATOR = "<:stream:1313474295372058758>"
# メッセージ編集の遅延秒数
EDIT_DELAY_SECONDS = 1
# メッセージノードの最大数 (会話履歴の最大長に関わる)
MAX_MESSAGE_NODES = 100
# メンションを検出するための正規表現パターン
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


def load_config(filename: str = "config.yaml") -> dict:
    """YAML 設定ファイルを読み込み (または再読み込み) ます。"""
    with open(filename, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def should_respond(client_user: discord.User, message: discord.Message) -> bool:
    """高速パス – このメッセージに応答すべきかどうかを判断します。"""
    # チャンネルの種類が許可されているか確認
    if message.channel.type not in ALLOWED_CHANNEL_TYPES:
        return False
    # DMの場合はmention不要、サーバーの場合はmention必須
    if message.channel.type != discord.ChannelType.private and client_user not in message.mentions:
        return False
    # ボット自身のメッセージには応答しない
    if message.author.bot:
        return False
    return True


@dataclass
class MessageNode:
    """メッセージチェーンにおける1つの頂点を表現します。"""

    text: Optional[str] = None
    images: List[dict] = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    next_message: Optional[discord.Message] = None
    has_bad_attachments: bool = False  # 不正な添付ファイルがあるか
    fetch_next_failed: bool = False  # 次のメッセージの取得に失敗したか
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # ノード処理中のロック


class DiscordLLMBot(discord.Client):
    """会話を LLM に転送する Discord ボットです。"""

    cfg_path: str

    def __init__(self, cfg_path: str = "config.yaml") -> None:
        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)

        intents = discord.Intents.default()
        intents.message_content = True
        intents.voice_states = True  # ボイスチャンネルの状態変化を検知するために追加

        activity = discord.CustomActivity(
            name=(self.cfg.get("status_message") or "github.com/jakobdylanc/llmcord")[:128]
        )
        super().__init__(intents=intents, activity=activity)

        self.tree = app_commands.CommandTree(self)
        self._register_slash_commands()  # 既存のスラッシュコマンド登録

        self.message_nodes: dict[int, MessageNode] = {}
        self.last_task_time: Optional[float] = None
        self.httpx_client = httpx.AsyncClient()

        self.SYSTEM_PROMPT: str | None = self.cfg.get("system_prompt")
        self.STARTER_PROMPT: str | None = self.cfg.get("starter_prompt")
        self.PREFILL_PROMPT: str | None = self.cfg.get("prefill_prompt")  # starter_promptではなくprefill_promptを使用
        self.ERROR_MESSAGES: dict[str, str] = self.cfg.get("error_msg", {}) or {}

        self.plugins = load_plugins(self)

        logging.info("読み込まれたプラグイン: [%s]", ", ".join(p.__class__.__name__ for p in self.plugins.values()))
        logging.info("有効なツール: [%s]", ", ".join(spec["function"]["name"] for spec in self._enabled_tools()))

        # Cogのパスを格納するリスト (cogsフォルダ内の .py ファイルを自動検出)
        self.initial_extensions = [
            f"cogs.{os.path.splitext(os.path.basename(f))[0]}" for f in glob.glob(os.path.join("cogs", "*.py"))
        ]

    async def setup_hook(self) -> None:
        """クライアント接続後に一度だけ呼ばれます。アプリケーションコマンドを同期し、Cogをロードします。"""
        # グローバルコマンドとして同期 (ギルド指定なし)
        # 既存のスラッシュコマンドがここで同期される
        # await self.tree.sync() # Cog内のコマンドも同期されるため、ここでまとめて同期するか、Cogロード後に行う
        # logging.info("スラッシュコマンドを登録しました。") # メッセージはCogロード後に移動

        # Cogをロード
        for extension in self.initial_extensions:
            try:
                await self.load_extension(extension)
                logging.info(f"Cog '{extension}' を正常にロードしました。")
            except discord.ext.commands.ExtensionAlreadyLoaded:
                logging.warning(f"Cog '{extension}' は既にロードされています。")
            except Exception as e:
                logging.error(f"Cog '{extension}' のロードに失敗しました。", exc_info=e)

        # すべてのコマンド (本体 + Cog) を同期
        # 特定のギルドにコマンドを登録したい場合は guild=discord.Object(id=YOUR_GUILD_ID) を追加
        await self.tree.sync()
        logging.info("スラッシュコマンドを登録しました。")

    async def on_message(self, message: discord.Message) -> None:
        """着信した従来のテキストメッセージ (@mention で始まるもの) を処理します。"""
        # 応答すべきメッセージかチェック
        if not should_respond(self.user, message):
            return
        # ユーザーまたはチャンネルが認証されているかチェック
        if not self._is_authorised(message):
            return

        provider_model = self.cfg.get("model", "")
        if not provider_model:
            logging.error("config.yaml に 'model' キーが見つかりません – 中止します。")
            return

        try:
            # プロバイダーとモデル名を分割
            provider, model = provider_model.split("/", 1)
        except ValueError:
            logging.error(f"無効なモデル形式 '{provider_model}' です。形式は 'provider/model' である必要があります。")
            # オプション: Discordにエラーメッセージを送信
            try:
                await message.reply(content="無効なモデル設定です。ボットの設定を確認してください。", silent=True)
            except Exception:
                pass  # エラーメッセージ送信失敗は無視
            return

        # 設定からプロバイダー固有の設定を取得
        provider_cfg = self.cfg["providers"].get(provider)
        if not provider_cfg:
            logging.error("config.yaml にプロバイダー '%s' が見つかりません – 中止します。", provider)
            # オプション: Discordにエラーメッセージを送信
            try:
                await message.reply(
                    content=f"プロバイダー '{provider}' が設定されていません。ボットの設定を確認してください。",
                    silent=True)
            except Exception:
                pass  # エラーメッセージ送信失敗は無視
            return

        # OpenAI クライアントを初期化
        openai_client = AsyncOpenAI(
            base_url=provider_cfg.get("base_url"),
            api_key=provider_cfg.get("api_key", "sk-no-key-required"),
        )

        # モデルが画像を処理できるか、ユーザー名をサポートするか確認
        accept_images = any(tag in model for tag in VISION_MODEL_TAGS)
        accept_usernames = provider in PROVIDERS_SUPPORTING_USERNAMES

        # 設定からメッセージ処理のパラメータを取得
        max_text = self.cfg.get("max_text", 5_000)
        max_images = self.cfg.get("max_images", 0) if accept_images else 0
        max_messages = self.cfg.get("max_messages", 5)
        max_message_length = 2_000  # Discord メッセージの最大長は 2000 文字

        # 会話履歴を遡ってメッセージチェーンを構築
        messages, user_warnings = await self._build_message_chain(
            message,
            max_messages,
            max_text,
            max_images,
            accept_images,
            accept_usernames,
        )

        # --- メッセージ受信ログ ---
        server_name = message.guild.name if message.guild else "DM"  # サーバー名を取得、DMの場合は"DM"
        user_name = message.author.display_name  # ユーザー名を取得

        logging.info(
            "[%s] ユーザー: %s (ID: %s) | 添付ファイル: %d | 会話メッセージ数: %d | 内容: %s",
            server_name,  # サーバー名を追加
            user_name,  # ユーザー名を追加
            message.author.id,
            len(message.attachments),
            len(messages),
            message.content,
        )
        # --- メッセージ受信ログここまで ---

        # システムプロンプトとスターターメッセージを API に送るメッセージリストに追加
        api_messages = []
        if self.SYSTEM_PROMPT:
            api_messages.append({"role": "system", "content": self.SYSTEM_PROMPT})
        if self.STARTER_PROMPT:
            api_messages.append({"role": "assistant", "content": self.STARTER_PROMPT})

        api_messages.extend(messages)  # 構築したメッセージチェーンを追加
        
        if self.PREFILL_PROMPT:
            api_messages.append({"role": "assistant", "content": self.PREFILL_PROMPT})

        # LLM レスポンスを生成して Discord に送信
        await self._generate_and_send_response(
            api_messages,  # システム/スタータープロンプトを含むリストを使用
            message,  # 起点となるユーザーメッセージ
            user_warnings,  # ユーザー向け警告のセット
            openai_client,
            model,
            max_message_length,
        )
        
    def _enabled_tools(self) -> list[dict]:
        want = self.cfg.get("active_tools", None)
        if want is None:
            # config.yaml に active_tools が未定義 → すべて有効
            return [p.tool_spec for p in self.plugins.values()]
        if not want:
            # 空リスト（[]） → 何も有効化しない
            return []
        # 名前一致したものだけ
        return [p.tool_spec for n, p in self.plugins.items() if n in want]

    def _register_slash_commands(self) -> None:
        """ローカルの CommandTree にスラッシュコマンドを登録します。"""

        @self.tree.command(name="help", description="ヘルプメッセージを表示します")
        async def _help(interaction: discord.Interaction) -> None:  # noqa: WPS430
            help_text = self.cfg.get("help_message", "ヘルプメッセージが設定されていません。")
            await interaction.response.send_message(help_text, ephemeral=False)

        @self.tree.command(name="invite", description="BOTの招待コードを表示します")
        async def _invite(interaction: discord.Interaction) -> None:  # noqa: WPS430
            """ボットの招待リンクを表示します。"""
            try:
                if not INVITE_URL or INVITE_URL == "YOUR_INVITE_URL_HERE":
                    # URLが設定されていない場合のエラーメッセージ (実行者のみ)
                    await interaction.response.send_message(
                        "エラー: 招待URLが設定されていません。開発者(Discord:coffin299)にご連絡ください。",
                        ephemeral=True
                    )
                    print("Error: INVITE_URL is not set in the code.")  # ログにも出力
                    return

                # Embedメッセージを作成して招待リンクを表示
                embed = discord.Embed(
                    title="🔗 ボット招待",
                    description=(
                        f"PLANAをあなたのサーバーに招待しませんか？\n"
                        "以下のリンクから招待できます。"
                    ),
                    color=discord.Color.brand_green()  # または discord.Color.blue() などお好みの色
                )
                embed.add_field(
                    name="招待リンク",
                    value=f"[ここをクリックして招待する]({INVITE_URL})",  # 用意されたURLを使用
                    inline=False
                )
                # ボットのアイコンをサムネイルとして設定
                if interaction.client.user and interaction.client.user.avatar:
                    embed.set_thumbnail(url=interaction.client.user.avatar.url)
                elif self.bot.user and self.bot.user.avatar:  # Cog の場合など、botインスタンスからアクセスする場合
                    embed.set_thumbnail(url=self.bot.user.avatar.url)

                embed.set_footer(text=f"コマンド実行者: {interaction.user.display_name}")

                # チャンネルの全員が見えるようにメッセージを送信
                await interaction.response.send_message(embed=embed)

            except Exception as e:
                # エラー発生時のフォールバックメッセージ (これは実行者のみに見えるようにする方が良いでしょう)
                print(f"Error in invite command: {e}")  # ログにエラーを出力
                await interaction.response.send_message(
                    "申し訳ありません、招待リンクの表示中にエラーが発生しました。\n"
                    "しばらくしてからもう一度お試しいただくか、開発者(Discord:coffin299)にご連絡ください。",
                    ephemeral=True  # エラーメッセージは実行者のみが良い場合が多い
                )

        @self.tree.command(name="reloadconfig",
                           description="config.yaml を再読み込みします（管理者専用）")
        async def _reload_config(interaction: discord.Interaction) -> None:
            admin_ids = set(self.cfg.get("admin_user_ids", []))
            if interaction.user.id not in admin_ids:
                await interaction.response.send_message(
                    "❌ このコマンドを実行する権限がありません。",
                    ephemeral=True)
                return

            try:
                self.cfg = load_config(self.cfg_path)

                # 読み直した内容をキャッシュにも反映
                self.SYSTEM_PROMPT  = self.cfg.get("system_prompt")
                self.STARTER_PROMPT = self.cfg.get("starter_prompt")
                self.ERROR_MESSAGES = self.cfg.get("error_msg", {}) or {}

                await interaction.response.send_message(
                    "✅ 設定を再読み込みしました。", ephemeral=True)
                logging.info("config.yaml を手動再読み込みしました。")
            except Exception as e:
                logging.exception("設定の手動再読み込みに失敗")
                await interaction.response.send_message(
                    f"⚠️ 再読み込みに失敗しました: {e}", ephemeral=True)

    def _is_authorised(self, message: discord.Message) -> bool:
        """投稿者またはチャンネルが対話することを許可されているか確認します。"""
        allowed_channels = set(self.cfg.get("allowed_channel_ids", []))
        allowed_roles = set(self.cfg.get("allowed_role_ids", []))

        chan_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)
        # まずチャンネル制限をチェック
        if allowed_channels and chan_id not in allowed_channels and parent_id not in allowed_channels:
            logging.info(
                f"ユーザー {message.author.id} からのチャンネル {chan_id} でのメッセージはブロックされました: チャンネルが許可されていません。")
            return False

        # 設定でロールが指定されている場合、ロール制限をチェック
        if allowed_roles:
            # DMではroles属性がないため、hasattrで確認
            if hasattr(message.author, 'roles'):
                user_role_ids = {role.id for role in message.author.roles}
                if not user_role_ids & allowed_roles:
                    # 必要に応じて、必要なロールを持っていないことをユーザーに通知 (エフェメラルメッセージ)
                    # これには interaction オブジェクトが必要ですが、on_message では利用できません。
                    # なので、現時点ではログに記録するだけです。
                    logging.info(
                        f"ユーザー {message.author.id} からのチャンネル {chan_id} でのメッセージはブロックされました: ユーザーが必要なロールを持っていません。")
                    return False
            elif allowed_roles:
                # 設定でロールが必須だが DM (ロールがない) の場合、アクセスを拒否
                # 望ましい動作によっては、サーバーにロールが設定されている場合でも DM を許可することがあります。
                # 現在のロジックでは、allowed_roles が設定されている場合は DM を拒否します。
                logging.info(
                    f"ユーザー {message.author.id} からの DM チャンネルでのメッセージはブロックされました: ロールが必要ですが DM では利用できません。")
                return False

        return True

    async def _build_message_chain(
            self,
            new_msg: discord.Message,
            max_messages: int,
            max_text: int,
            max_images: int,
            accept_images: bool,
            accept_usernames: bool,
    ) -> tuple[list[dict], Set[str]]:
        """スレッドを遡ってコンテキストを収集します。"""
        messages: list[dict] = []
        user_warnings: set[str] = set()
        curr_msg: Optional[discord.Message] = new_msg

        # 無限ループを避けるために、訪問済みのメッセージIDを追跡するためのセットを使用
        visited_messages: Set[int] = set()

        while curr_msg and len(messages) < max_messages:
            # このメッセージIDを既に処理したかチェック
            if curr_msg.id in visited_messages:
                logging.warning(f"メッセージチェーンのメッセージ ID {curr_msg.id} でループを検出しました。停止します。")
                user_warnings.add("⚠️ 会話履歴にループを検出しました。ここで停止します。")
                break  # 無限ループを防ぐためにチェーンの構築を停止

            visited_messages.add(curr_msg.id)

            node = self.message_nodes.setdefault(curr_msg.id, MessageNode())
            async with node.lock:
                # ノードがまだ処理されていないか、再処理が必要な場合 (例: 添付ファイルが取得されていない)
                if node.text is None or not node.images:  # シンプルなチェック、より洗練されたものにする可能性あり
                    await self._process_message_node(node, curr_msg, accept_images, max_text)

                # ノードから API 用のコンテンツを構成
                content = self._compose_message_content(node, max_text, max_images)
                if content:
                    # コンテンツがテキストの場合、空白のみでないことを確認
                    if isinstance(content, str) and not content.strip():
                        logging.debug(f"ID {curr_msg.id} からの空のテキストメッセージをスキップします")
                        # テキストが空で画像もない場合、このメッセージはコンテンツに貢献しません
                        # ただし、チェーン構造 (何かに返信しているなど) には重要である可能性があります
                        # ループは続行しますが、空のコンテンツは messages リストに追加しません
                        pass  # チェーンの次のメッセージへ続行
                    else:
                        # API ペイロードを作成
                        payload: dict = {"content": content, "role": node.role}
                        if accept_usernames and node.user_id:
                            payload["name"] = str(node.user_id)
                        messages.append(payload)
                else:
                    # メッセージノードが処理後に空のコンテンツになった場合をログに記録
                    logging.debug(f"メッセージ ID {curr_msg.id} は空のコンテンツとして処理されました。")

                # ユーザー向け警告を更新
                self._update_user_warnings(node, max_text, max_images, user_warnings)

                # curr_msg を更新する前に fetch_next_failed をチェック
                if node.fetch_next_failed:
                    user_warnings.add(
                        f"⚠️ 会話チェーンの前のメッセージの取得に失敗しました。会話が不完全な可能性があります。"
                    )
                    break  # 取得に失敗した場合、チェーンの構築を停止

                # 次に処理するメッセージを決定
                # メッセージ制限に達したかどうかを、next_message を設定する *前* にチェック
                if len(messages) == max_messages:
                    user_warnings.add(
                        f"⚠️ 直近の {len(messages)} 件のメッセージのみを使用しています。"
                    )
                    break  # メッセージ制限に達した場合、停止

                # まだ制限に達していない場合にのみ、次のメッセージの設定を試行
                await self._set_next_message(node, curr_msg)
                curr_msg = node.next_message

        # ループ終了後、メッセージ制限によって停止したかチェック
        if curr_msg and len(messages) == max_messages:
            user_warnings.add(f"⚠️ 直近の {max_messages} 件のメッセージのみを使用しています。")

        # 構築したメッセージリストを逆順にして返す (API は古い順を期待)
        return messages[::-1], user_warnings

    async def _process_message_node(
            self,
            node: MessageNode,
            msg: discord.Message,
            accept_images: bool,
            max_text: int,
    ) -> None:
        """Discord メッセージを MessageNode に解析します。"""

        raw_content = msg.content or ""
        # メンションをユーザーの表示名に置換
        replaced_content = await self._replace_mentions(raw_content)

        # ボット自身でない場合、表示名をテキストに追加
        if msg.author != self.user:
            account_name = msg.author.name
            # メンション置換後の実際のテキストコンテンツがある場合にのみ、表示名を前に付加
            message_content = f"`User({account_name})`: {replaced_content}" if replaced_content else account_name
        else:
            # ボット自身のメッセージの場合、メンション置換後のコンテンツをそのまま使用
            message_content = replaced_content

        # 許可された種類の添付ファイルを取得
        good_atts: dict[str, list[discord.Attachment]] = {
            ft: [att for att in msg.attachments if att.content_type and ft in att.content_type]
            for ft in ALLOWED_FILE_TYPES
        }
        attachment_texts = []
        # テキスト添付ファイルをフェッチしてテキストリストに追加
        for att in good_atts["text"]:
            try:
                text = await self._fetch_attachment_text(att)
                attachment_texts.append(text)
            except Exception as e:
                logging.warning(f"テキスト添付ファイル {att.id} のフェッチに失敗しました: {e}")
                # ユーザー向け警告を追加
                # user_warnings.add(f"⚠️ テキスト添付ファイル '{att.filename}' の読み取りに失敗しました。")
                node.has_bad_attachments = True  # 問題のある添付ファイルがあるとしてマーク

        # 埋め込みの説明テキストを取得
        embed_desc = [embed.description for embed in msg.embeds if embed.description]

        # すべてのテキストソースを結合
        # 結合前に None または空文字列をフィルタリング
        all_texts = [message_content] + embed_desc + attachment_texts
        node.text = "\n".join(filter(None, all_texts)).strip()  # strip() で先頭/末尾の空白を除去

        # メッセージがボットへのメンションで始まる場合、メンション部分を削除
        # これは表示名を追加した *後* に行うことで、「ユーザー名: メッセージテキスト」の構造を保ちます
        if node.text.startswith(self.user.mention):
            node.text = node.text.replace(self.user.mention, "", 1).lstrip()
            # ユーザーメッセージであれば、表示名は既に追加されています

        # 画像が許可されている場合、画像を処理
        if accept_images:
            node.images = []
            for att in good_atts["image"]:
                try:
                    img_data = await self._process_image(att)
                    node.images.append(img_data)
                except Exception as e:
                    logging.warning(f"画像添付ファイル {att.id} の処理に失敗しました: {e}")
                    # ユーザー向け警告を追加
                    # user_warnings.add(f"⚠️ 画像添付ファイル '{att.filename}' の処理に失敗しました。")
                    node.has_bad_attachments = True  # 問題のある添付ファイルがあるとしてマーク
        else:
            node.images = []  # 画像が許可されていない場合、空のリストを保証

        # メッセージのロールを設定
        node.role = "assistant" if msg.author == self.user else "user"
        # ユーザーメッセージの場合、ユーザーIDを設定
        node.user_id = msg.author.id if node.role == "user" else None
        # 許可されたファイル種類以外の添付ファイルがあるかチェック
        if len(msg.attachments) > sum(len(good_atts.get(ft, [])) for ft in ALLOWED_FILE_TYPES):
            node.has_bad_attachments = True

        # next_message はここで設定しません。ロジックに基づいて _build_message_chain で処理されます。
        # 次のメッセージ (参照、履歴) を見つけるロジックは _build_message_chain 内にあります。
        # self._set_next_message は、ノード処理後に _build_message_chain から呼び出されます。

    async def _fetch_attachment_text(self, att: discord.Attachment) -> str:
        """テキスト添付ファイルをフェッチして文字列として返します。"""
        response = await self.httpx_client.get(att.url, follow_redirects=True)  # follow_redirects を追加
        response.raise_for_status()  # 不良なステータスコードに対して例外を発生
        return response.text

    async def _process_image(self, att: discord.Attachment) -> dict:
        """画像添付ファイルをフェッチし、Base64 エンコードして API 用の形式で返します。"""
        response = await self.httpx_client.get(att.url, follow_redirects=True)  # follow_redirects を追加
        response.raise_for_status()  # 不良なステータスコードに対して例外を発生
        b64 = b64encode(response.content).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{att.content_type};base64,{b64}"},
        }

    async def _replace_mentions(self, content: str) -> str:
        """メッセージ内のユーザーメンションをユーザーの表示名に置換します。"""
        user_ids = {int(m.group(1)) for m in MENTION_PATTERN.finditer(content)}
        users: dict[int, str] = {}
        for uid in user_ids:
            try:
                # キャッシュから取得を試み、なければフェッチ
                user = self.get_user(uid) or await self.fetch_user(uid)
                users[uid] = user.display_name if user else f"User{uid}"
            except discord.NotFound:
                logging.warning(f"メンション置換中にユーザー ID {uid} が見つかりませんでした。")
                users[uid] = f"不明なユーザー{uid}"  # フォールバック名
            except Exception as e:
                logging.error(f"メンション置換用のユーザー {uid} のフェッチ中にエラー: {e}")
                users[uid] = f"エラーユーザー{uid}"  # エラー時のフォールバック名

        # メンションパターンに一致する部分を、ユーザーIDに対応する表示名に置換
        # .get() を使用し、見つからない場合は元のメンション文字列を保持
        return MENTION_PATTERN.sub(lambda m: users.get(int(m.group(1)), m.group(0)), content)

    async def _set_next_message(self, node: MessageNode, msg: discord.Message) -> None:
        """次のメッセージ (このメッセージが返信しているもの、または履歴の前のメッセージ) を判断してフェッチします。"""
        next_msg: Optional[discord.Message] = None
        try:  # <-- この try ブロックが最後までを囲みます
            # 優先度 1: メッセージ参照 (返信)
            if msg.reference and msg.reference.message_id:
                try:  # <-- これはネストされた try ブロックです (参照メッセージのフェッチ用)
                    # キャッシュから取得を試み、なければフェッチ
                    next_msg = msg.reference.cached_message or await msg.channel.fetch_message(msg.reference.message_id)
                except (discord.NotFound, discord.HTTPException):  # <-- ネストされた try に対応する except
                    logging.debug(f"参照されたメッセージ {msg.reference.message_id} のフェッチに失敗しました (参照)。")
                    # 参照メッセージが見つからない、または取得エラーが発生した場合、失敗をマーク
                    # ただし、他の方法 (履歴など) を試す可能性は残します。
                    node.fetch_next_failed = True

            # 優先度 2: 参照がなく、かつボットがメンションされているか DM の場合、履歴の前のメッセージ
            # このロジックは、明示的な返信参照がない場合に、DM 内またはボットがメンションされているメッセージで、
            # 同じユーザーからの連続したメッセージをリンクしようとするヒューリスティックです。
            # 参照が見つからず、かつフェッチ失敗がマークされていない場合のみ履歴を試行
            if next_msg is None and not node.fetch_next_failed and (
                    self.user.mention in msg.content or msg.channel.type == discord.ChannelType.private):
                # history fetching could also raise HTTPException, let the outer catch handle it
                history_msgs = [m async for m in msg.channel.history(before=msg, limit=1)]
                if history_msgs:
                    prev_msg = history_msgs[0]
                    # 前のメッセージがボットからのもの、または DM でユーザーからのものである場合にのみリンク
                    # かつ標準的なメッセージタイプ (参加メッセージなどではない) であること
                    if prev_msg.type in {discord.MessageType.default, discord.MessageType.reply} and (
                            prev_msg.author == self.user or (
                            msg.channel.type == discord.ChannelType.private and prev_msg.author == msg.author)
                    ):
                        next_msg = prev_msg

            # 優先度 3: スレッドの最初のメッセージの場合、スレッドの親メッセージ
            # 参照や履歴メッセージが見つからず、かつフェッチ失敗がマークされていない場合のみスレッド親を試行
            if next_msg is None and not node.fetch_next_failed and msg.channel.type == discord.ChannelType.public_thread:
                thread = msg.channel
                # このメッセージが実際にスレッドの最初のメッセージであるかチェック
                if thread.starter_message and thread.starter_message.id == msg.id:
                    if thread.parent_id:
                        try:  # <-- これはネストされた try ブロックです (親メッセージのフェッチ用)
                            # 親チャンネルを取得 (これもエラーの可能性あり)
                            parent_channel = await self.fetch_channel(thread.parent_id)
                            if isinstance(parent_channel,
                                          (discord.TextChannel, discord.ForumChannel)):  # メッセージをフェッチ可能なチャンネルタイプであることを確認
                                # 親チャンネルから、スレッドのスターターメッセージと同じ ID を持つメッセージをフェッチ
                                next_msg = await parent_channel.fetch_message(msg.id)
                            else:
                                logging.debug(
                                    f"スレッド {thread.id} の親チャンネル {thread.parent_id} はフェッチ可能なタイプではありません。")
                        except (discord.NotFound, discord.HTTPException):  # <-- ネストされた try に対応する except
                            logging.debug(
                                f"親チャンネル {thread.parent_id} からスレッドスターターメッセージ ({msg.id}) のフェッチに失敗しました (スレッド親)。")
                            node.fetch_next_failed = True  # 失敗をマーク

        except Exception as e:  # <-- 外側の try に対応する except ブロックを追加
            # 上記の try ブロック内で発生した、ネストされた try...except で捕捉されないすべての例外をここで捕捉
            logging.exception(
                f"メッセージチェーンの次のメッセージ設定中に予期しないエラーが発生しました (メッセージID: {msg.id})")
            node.fetch_next_failed = True  # このノードからのチェーン構築は失敗したとしてマーク
            next_msg = None  # エラー発生時は next_msg を None に設定

        node.next_message = next_msg

        if node.next_message:
            logging.debug(f"メッセージ ID {msg.id} は前のメッセージ ID {node.next_message.id} にリンクされました。")
        else:  # next_msg が None の場合
            logging.debug(
                f"メッセージ ID {msg.id} はチェーンの終端です (参照なし、関連履歴なし、スレッドスターターではない)。")

        # ノードの next_message を更新
        node.next_message = next_msg

        # デバッグ用にリンクされたメッセージをログに記録
        if node.next_message:
            logging.debug(f"メッセージ ID {msg.id} は前のメッセージ ID {node.next_message.id} にリンクされました。")
        elif next_msg is None:
            logging.debug(
                f"メッセージ ID {msg.id} はチェーンの終端です (参照なし、関連履歴なし、スレッドスターターではない)。")

    def _compose_message_content(
            self, node: MessageNode, max_text: int, max_images: int
    ) -> str | list:
        """テキストと画像を処理し、API 用のコンテンツを構成します。"""
        # スライスまたは長さチェックの前に、テキストが None でないことを確認
        limited_text = node.text[:max_text] if node.text is not None else ""
        # スライスする前に、画像リストが None でないことを確認
        limited_images = node.images[:max_images] if node.images is not None else []

        content: list = []

        # 制限後、存在し、かつ空白のみでない場合、テキストパートを追加
        if limited_text.strip():
            content.append({"type": "text", "text": limited_text})

        # 制限後、存在する場合、画像パートを追加
        if limited_images:
            content.extend(limited_images)

        # テキストのみが存在し、それが唯一のアイテムである場合、文字列のみを返す
        if len(content) == 1 and content[0]["type"] == "text":
            return content[0]["text"]
        # 複数のパート (テキスト + 画像、または複数画像 - 後者は現在の構造では可能性低い) がある場合、
        # または画像のみの場合、リスト構造を返す。
        # コンテンツが空 (テキストも画像もない) の場合、空文字列または空リストを返すか？ API は文字列またはリストを期待。
        # コンテンツが抽出されなかった場合、空文字列を返すのがより安全に見える。
        if not content:
            return ""  # コンテンツが生成されなかった場合は空文字列を返す

        # マルチモーダルまたは純粋な画像コンテンツの場合はリストを返す
        return content

    def _update_user_warnings(
            self, node: MessageNode, max_text: int, max_images: int, warnings: set[str]
    ) -> None:
        """メッセージノード処理に基づいてユーザー向け警告を追加します。"""
        err = self.ERROR_MESSAGES
        # スライスされた長さではなく、元のテキスト長さをチェック
        if node.text is not None and len(node.text) > max_text:
            # 設定またはデフォルトのエラーメッセージを使用
            warnings.add(
                err.get("msg_max_text_size", "⚠️ メッセージテキストが切り詰められました (>{max_text} 文字)。").format(
                    max_text=max_text))

        # スライスされた数ではなく、元の画像数をチェック
        if node.images is not None and len(node.images) > max_images:
            if max_images > 0:
                # 設定またはデフォルトのエラーメッセージを使用
                warnings.add(
                    err.get("msg_max_image_size", "⚠️ 最初の {max_images} 件の画像のみを使用しています。").format(
                        max_images=max_images))
            else:
                # このケースは、画像処理の前に捕捉されるべきですが、フォールバックとして残します
                # 設定またはデフォルトのエラーメッセージを使用
                warnings.add(err.get("msg_error_image", "⚠️ このモデルまたは設定では画像はサポートされていません。"))

        if node.has_bad_attachments:
            # 設定またはデフォルトのエラーメッセージを使用
            warnings.add(err.get("msg_error_attachment",
                                 "⚠️ サポートされていない添付ファイルをスキップしました、または添付ファイル (テキスト/画像) の処理に失敗しました。"))

        if node.fetch_next_failed:
            # 設定またはデフォルトのエラーメッセージを使用
            warnings.add(err.get("msg_fetch_failed",
                                 "⚠️ 会話履歴の前のメッセージの取得に失敗しました。チェーンが不完全な可能性があります。").format())

    async def _generate_and_send_response(
            self,
            messages: list[dict],
            origin: discord.Message,
            user_warnings: set[str],
            openai_client: AsyncOpenAI,
            model: str,
            max_message_length: int,
    ) -> None:
        """LLM からレスポンスを生成し、ストリーミングまたは一括で Discord に送信します (tool call対応)。"""
        response_msgs: list[discord.Message] = []
        last_message_buffer = ""
        edit_task: Optional[asyncio.Task] = None
        self.last_task_time = dt.now().timestamp()

        initial_warnings_text = " ".join(sorted(user_warnings))
        user_warnings.clear()

        api_kwargs_base = dict(
            model=model,
            stream=True,
            tools=self._enabled_tools(),
            tool_choice="auto",
            extra_body=self.cfg.get("extra_api_parameters", {}),
        )

        max_tool_loops = 3
        while max_tool_loops:
            api_kwargs = dict(api_kwargs_base, messages=messages)
            tool_call_data_for_assistant: dict[str, dict[str, str | list[str]]] = {}
            assistant_text_content_buffer = ""

            saw_tool_call = False

            try:
                async with origin.channel.typing():
                    async for chunk in await openai_client.chat.completions.create(**api_kwargs):
                        choice = chunk.choices[0]

                        tc_delta_list = getattr(choice.delta, "tool_calls", None)
                        if tc_delta_list:
                            saw_tool_call = True
                            for tc_delta in tc_delta_list:
                                if tc_delta.id not in tool_call_data_for_assistant:
                                    tool_call_data_for_assistant[tc_delta.id] = {
                                        "name": tc_delta.function.name or "",
                                        "arguments_chunks": []
                                    }

                                if tc_delta.function.name and not tool_call_data_for_assistant[tc_delta.id]["name"]:
                                    tool_call_data_for_assistant[tc_delta.id]["name"] = tc_delta.function.name

                                if tc_delta.function.arguments:
                                    tool_call_data_for_assistant[tc_delta.id]["arguments_chunks"].append(
                                        tc_delta.function.arguments)
                            continue

                        delta_content = choice.delta.content
                        if delta_content is not None:
                            if saw_tool_call:
                                assistant_text_content_buffer += delta_content
                            else:
                                last_message_buffer += delta_content

                            if not saw_tool_call:
                                if not response_msgs and initial_warnings_text:
                                    last_message_buffer = initial_warnings_text + " " + last_message_buffer
                                    initial_warnings_text = ""

                                content_to_send_as_new_message = None
                                if len(last_message_buffer) > max_message_length:
                                    content_to_send_as_new_message = last_message_buffer[:max_message_length]
                                    last_message_buffer = last_message_buffer[max_message_length:]

                                if content_to_send_as_new_message is not None:
                                    if response_msgs:
                                        if edit_task is not None and not edit_task.done():
                                            await edit_task
                                    msg_to_reply = origin if not response_msgs else response_msgs[-1]
                                    try:
                                        content_to_send_final = content_to_send_as_new_message + "\u2026"
                                        msg = await msg_to_reply.reply(
                                            content=content_to_send_final,
                                            silent=True,
                                        )
                                        self.message_nodes[msg.id] = MessageNode(
                                            text=content_to_send_as_new_message,
                                            next_message=msg_to_reply
                                        )
                                        await self.message_nodes[msg.id].lock.acquire()
                                        response_msgs.append(msg)
                                        self.last_task_time = dt.now().timestamp()
                                    except Exception as send_e:
                                        logging.error(f"メッセージパートの送信に失敗しました (新規): {send_e}")
                                        try:
                                            await (response_msgs[-1] if response_msgs else origin).reply(
                                                content=self.ERROR_MESSAGES.get("send_failed_part",
                                                                                "⚠️ メッセージの途中で送信に失敗しました。").format(),
                                                silent=True)
                                        except Exception:
                                            pass
                                    return

                            ready_to_edit = (
                                    response_msgs
                                    and last_message_buffer
                                    and (edit_task is None or edit_task.done())
                                    and dt.now().timestamp() - self.last_task_time >= EDIT_DELAY_SECONDS
                            )
                            finish_reason = getattr(choice, "finish_reason", None)
                            is_final_chunk_trigger = finish_reason is not None

                            if ready_to_edit or is_final_chunk_trigger:
                                if response_msgs:
                                    if edit_task is not None and not edit_task.done():
                                        await edit_task
                                    content_to_edit = last_message_buffer
                                    if not is_final_chunk_trigger:
                                        content_to_edit += "\u2026"
                                    msg_to_edit = response_msgs[-1]
                                    edit_task = asyncio.create_task(self._perform_edit(msg_to_edit, content_to_edit))
                                    self.last_task_time = dt.now().timestamp()

                        if choice.finish_reason == "tool_calls":
                            break

                    if saw_tool_call:
                        assistant_tool_calls_list = []
                        
                        for call_id, details in tool_call_data_for_assistant.items():
                            function_name = details["name"]
                            arguments_str = "".join(details["arguments_chunks"])
                            assistant_tool_calls_list.append({
                                "id": call_id,
                                "type": "function",
                                "function": {
                                    "name": function_name,
                                    "arguments": arguments_str
                                }
                            })

                        if assistant_tool_calls_list:

                            messages.append({
                                "role": "assistant",
                                "content": assistant_text_content_buffer.strip() if assistant_text_content_buffer.strip() else "",
                                "tool_calls": assistant_tool_calls_list
                            })
                            assistant_text_content_buffer = ""
                            
                            for call in assistant_tool_calls_list:
                                tool_name = call["function"]["name"]

                                actives = self.cfg.get("active_tools", None)
                                if (
                                    tool_name not in self.plugins
                                    or (actives is not None and tool_name not in actives)
                                ):
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": call["id"],
                                        "name": tool_name,
                                        "content": f"[{tool_name}] は無効化されています。",
                                    })
                                    continue

                                plugin = self.plugins[tool_name]
                                args = json.loads(call["function"]["arguments"])
                                result = await plugin.run(arguments=args, bot=self)

                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": call["id"],
                                    "name": tool_name,
                                    "content": result,
                                })

                        else:
                            logging.error("Tool call detected but tool details found empty.")
                            messages.append({
                                "role": "user",
                                "content": "Tool call was attempted but failed because the tool details were missing or not recognized."
                            })

                        max_tool_loops -= 1
                        last_message_buffer = ""
                        continue

                    else:
                        final_content = last_message_buffer
                        if final_content:
                            try:
                                msg = await origin.reply(
                                    content=final_content,
                                    silent=True,
                                )
                                self.message_nodes[msg.id] = MessageNode(
                                    text=final_content,
                                    next_message=origin
                                )
                                await self.message_nodes[msg.id].lock.acquire()
                                response_msgs.append(msg)
                            except Exception as send_e:
                                logging.error(f"最終メッセージの送信に失敗しました: {send_e}")
                        break

            except RateLimitError:
                logging.warning("OpenAI Rate Limit Error (429) が発生しました。")
                ratelimit_msg = self.ERROR_MESSAGES.get(
                    "ratelimit_error",
                    "⚠️ 現在、リクエストが多すぎるため応答できません。後でもう一度試してください！"
                )
                try:
                    await origin.reply(content=ratelimit_msg, silent=True)
                except Exception as e:
                    logging.error(f"レート制限エラーメッセージの送信に失敗しました: {e}")
                return

            except Exception:
                logging.exception("レスポンス生成中にエラーが発生しました (一般)。")
                general_error_msg = self.ERROR_MESSAGES.get(
                    "general_error",
                    "⚠️ レスポンスの生成中に予期しないエラーが発生しました。後でもう一度試してください！"
                )
                msg_to_reply_on_error = response_msgs[-1] if response_msgs else origin
                try:
                    await msg_to_reply_on_error.reply(content=general_error_msg, silent=True)
                except Exception as e:
                    logging.error(f"一般的なエラーメッセージの送信に失敗しました: {e}")
                return

        if edit_task is not None and not edit_task.done():
            try:
                await edit_task
            except Exception as e:
                logging.error(f"最終編集タスクの完了待ち中にエラー: {e}")

        if response_msgs or last_message_buffer:
            if not response_msgs:
                # no message sent yet, so send one
                if last_message_buffer:
                    try:
                        msg = await origin.reply(content=last_message_buffer, silent=True)
                        self.message_nodes[msg.id] = MessageNode(text=last_message_buffer, next_message=origin)
                        await self.message_nodes[msg.id].lock.acquire()
                        response_msgs.append(msg)
                    except Exception as e:
                        logging.error(f"最終メッセージの送信に失敗しました: {e}")
                        try:
                            await origin.reply(
                                content=self.ERROR_MESSAGES.get("send_failed_final",
                                                                "⚠️ レスポンスの送信に失敗しました。"),
                                silent=True,
                            )
                        except Exception:
                            pass
            else:
                try:
                    await self._perform_edit(response_msgs[-1], last_message_buffer)
                except Exception as e:
                    logging.error(f"最終メッセージ ({response_msgs[-1].id}) の最終編集に失敗しました: {e}")

        full_parts = []
        for msg in response_msgs:
            node = self.message_nodes.get(msg.id)
            if node and node.text is not None:
                full_parts.append(node.text)
        full_response_text = "".join(full_parts)
        logging.info(
            "LLMレスポンス完了 (起点ID: %s): %s",
            origin.id,
            full_response_text[:500] + ("..." if len(full_response_text) > 500 else ""),
        )

        for msg in response_msgs:
            node = self.message_nodes.get(msg.id)
            if node:
                node.text = full_response_text
                if node.lock.locked():
                    node.lock.release()
            else:
                logging.warning(f"メッセージノード {msg.id} が見つかりませんでした (最終処理).")

        if len(self.message_nodes) > MAX_MESSAGE_NODES:
            over = len(self.message_nodes) - MAX_MESSAGE_NODES
            mids_to_pop = sorted(self.message_nodes)[:over]
            logging.info(f"Pruning {over} old message-nodes...")
            for mid in mids_to_pop:
                node = self.message_nodes.get(mid)
                if not node:
                    continue
                try:
                    await asyncio.wait_for(node.lock.acquire(), timeout=0.1)
                    node.lock.release()
                except asyncio.TimeoutError:
                    logging.debug(f"Skipping locked node {mid}.")
                except Exception as e:
                    logging.error(f"Error pruning node {mid}: {e}")

    async def _perform_edit(self, msg: discord.Message, content: str) -> None:
        """エラー処理付きでメッセージ編集を安全に実行します。"""
        try:
            # コンテンツが変更されている場合のみ編集
            if content != msg.content:
                await msg.edit(content=content)
            # logging.debug(f"メッセージ {msg.id} をコンテンツ長 {len(content)} で編集しました。") # オプションの詳細ログ
        except discord.NotFound:
            logging.warning(f"おそらく削除されたメッセージ {msg.id} を編集しようとしました。")
        except discord.HTTPException as e:
            logging.warning(f"メッセージ {msg.id} の編集中に HTTPException: {e}")
            # 特に高速なストリーミングでは、編集のレート制限が発生する可能性があります
            # 頻繁に発生する場合、バックオフや編集スキップを検討
        except Exception as e:
            logging.error(f"メッセージ {msg.id} の編集中に予期しないエラー: {e}")

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
    cfg = load_config()
    if client_id := cfg.get("client_id"):
        logging.info(
            "\n\nボット招待 URL:\n"
            "https://discord.com/api/oauth2/authorize?client_id=%s&permissions=412317273088&scope=bot\n",
            client_id,
        )
    bot = DiscordLLMBot("config.yaml")
    await bot.start(cfg["bot_token"])


if __name__ == "__main__":
    # CTRL+C でシャットダウンを検知するために try/except を使用
    try:
        # 将来的に graceful shutdown が必要になった場合に備えて asyncio.run のラッパーを使用
        # 現時点では、シンプルな実行で十分です。
        aio_run(_main())
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt によりボットがシャットダウンしています。")
    except SystemExit:
        logging.info("SystemExit によりボットがシャットダウンしています。")
    except Exception as e:
        logging.exception(f"ボットの起動/実行中にハンドルされていないエラーが発生しました: {e}")