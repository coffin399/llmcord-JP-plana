# cogs/llmcord.py

from __future__ import annotations

import asyncio
import json
import logging
import re
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime as dt  # type: ignore
from typing import Literal, Optional, Set, Tuple, List, Dict, Any  # Any を追加

import discord  # type: ignore
import httpx
from discord.ext import commands  # commands をインポート
# openai から RateLimitError をインポート
from openai import AsyncOpenAI, RateLimitError  # type: ignore

# 定数: Visionモデルタグなど
VISION_MODEL_TAGS: Tuple[str, ...] = (
    "gpt-4o", "claude-3", "gemini", "pixtral", "llava", "vision",
)
PROVIDERS_SUPPORTING_USERNAMES: Tuple[str, ...] = ("openai", "x-ai")
ALLOWED_FILE_TYPES: Tuple[str, ...] = ("image", "text")  # 許可する添付ファイルの種類
ALLOWED_CHANNEL_TYPES: Tuple[discord.ChannelType, ...] = (  # LLMが応答するチャンネルの種類
    discord.ChannelType.text, discord.ChannelType.public_thread,
    discord.ChannelType.private_thread, discord.ChannelType.private,
)
STREAMING_INDICATOR = "🔄"  # ストリーミング中を示すインジケータ (例: 絵文字)
EDIT_DELAY_SECONDS = 1.2  # メッセージ編集の遅延 (秒)
# MAX_MESSAGE_NODES はメインのBotクラスで管理、または設定ファイルから取得する方が良い
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")  # メンション検出用正規表現

logger = logging.getLogger('discord.cogs.llm_interactions')  # このCog専用のロガー


@dataclass
class MessageNode:
    """会話履歴内の単一メッセージを表すノード。"""
    text: Optional[str] = None  # メッセージのテキスト内容
    images: List[dict] = field(default_factory=list)  # 添付画像 (API形式)
    role: Literal["user", "assistant"] = "assistant"  # メッセージの役割 (ユーザーまたはアシスタント)
    user_id: Optional[int] = None  # ユーザーID (ユーザーメッセージの場合)
    next_message: Optional[discord.Message] = None  # 会話履歴を遡る際の次のdiscord.Messageオブジェクト
    has_bad_attachments: bool = False  # サポート外または処理失敗した添付ファイルがあったか
    fetch_next_failed: bool = False  # 次のメッセージの取得に失敗したか
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)  # ノード処理中の排他制御用ロック


def should_respond_to_llm(bot_user: Optional[discord.User], message: discord.Message) -> bool:
    """このメッセージにLLMが応答すべきかどうかを判断します。"""
    if bot_user is None: return False  # ボットユーザーが未確定の場合は応答しない
    # 許可されたチャンネルタイプか確認
    if message.channel.type not in ALLOWED_CHANNEL_TYPES: return False
    # DMでないサーバーチャンネルの場合、ボットへのメンションが必須
    if message.channel.type != discord.ChannelType.private and bot_user not in message.mentions:
        return False
    # ボット自身のメッセージには応答しない
    if message.author.id == bot_user.id: return False
    # (オプション) 他のボットのメッセージにも応答しない
    if message.author.bot: return False
    return True


class LLMInteractionsCog(commands.Cog, name="LLM 対話機能"):  # Cog名を日本語に
    """LLM (大規模言語モデル) との対話処理を担当するCogです。"""

    def __init__(self, bot: commands.Bot):
        self.bot: commands.Bot = bot
        self.cfg = getattr(bot, 'cfg', {})  # メインのボットインスタンスから設定(cfg)を取得
        if not self.cfg:
            logger.error(
                "LLMInteractionsCog: ボットの設定(cfg)が見つかりません。一部機能が正しく動作しない可能性があります。")

        # メッセージノードはBotインスタンスで一元管理されているものを参照
        if not hasattr(bot, 'message_nodes_llm'):  # 専用の属性名に変更
            bot.message_nodes_llm = {}  # type: ignore # 存在しなければ作成
        self.message_nodes: dict[int, MessageNode] = bot.message_nodes_llm  # type: ignore

        # httpx_clientもBotインスタンスのものを共有
        if not hasattr(bot, 'httpx_client_shared') or bot.httpx_client_shared is None:  # type: ignore
            bot.httpx_client_shared = httpx.AsyncClient()  # type: ignore
        self.httpx_client: httpx.AsyncClient = bot.httpx_client_shared  # type: ignore

        # LLM関連のプロンプトやエラーメッセージを設定から読み込み
        self.SYSTEM_PROMPT: str | None = self.cfg.get("system_prompt")
        self.STARTER_PROMPT: str | None = self.cfg.get("starter_prompt")
        self.PREFILL_PROMPT: str | None = self.cfg.get("prefill_prompt")
        # エラーメッセージは常に辞書であることを保証
        self.ERROR_MESSAGES: dict[str, str] = self.cfg.get("error_msg", {}) or {}

        # last_task_time (メッセージ編集用) もBotインスタンスの属性を参照・更新
        if not hasattr(bot, 'last_llm_edit_task_time'):
            bot.last_llm_edit_task_time = None  # type: ignore
        # このCog内で使用する際は self.bot.last_llm_edit_task_time を介してアクセス

        logger.info("LLM対話Cogが初期化されました。")

    def _is_authorised_for_llm(self, message: discord.Message) -> bool:
        """投稿者またはチャンネルがLLMとの対話を許可されているか確認します。"""
        allowed_channels_cfg = self.cfg.get("allowed_channel_ids", [])
        allowed_roles_cfg = self.cfg.get("allowed_role_ids", [])
        # IDは数値型であることを期待して変換 (設定ミス防止のためisdigitでチェック)
        allowed_channels = {int(cid) for cid in allowed_channels_cfg if str(cid).isdigit()}
        allowed_roles = {int(rid) for rid in allowed_roles_cfg if str(rid).isdigit()}

        chan_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)  # スレッドの親チャンネルID

        # チャンネルID制限の確認 (許可リストが空でなければ評価)
        if allowed_channels and not (chan_id in allowed_channels or parent_id in allowed_channels):
            logger.info(
                f"ユーザー {message.author.id} (チャンネル {chan_id}) からのLLMリクエストはブロック: 許可外チャンネル。")
            return False

        # ロールID制限の確認 (許可リストが空でなければ評価)
        if allowed_roles:
            if isinstance(message.author, discord.Member) and hasattr(message.author, 'roles'):  # サーバーメンバーの場合
                user_role_ids = {role.id for role in message.author.roles}
                if not user_role_ids.intersection(allowed_roles):  # 共通のロールがなければブロック
                    logger.info(
                        f"ユーザー {message.author.id} (チャンネル {chan_id}) からのLLMリクエストはブロック: 許可外ロール。")
                    return False
            elif message.channel.type == discord.ChannelType.private:  # DMの場合 (ロールなし)
                # ロール制限がある場合、DMからのLLMリクエストは許可しないポリシー
                logger.info(f"ユーザー {message.author.id} (DM) からのLLMリクエストはブロック: ロール制限あり。")
                return False
        return True  # すべての認証チェックをパス

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """着信したメッセージを処理し、条件を満たせばLLMに応答させます。"""
        if self.bot.user is None: return  # ボットがまだ準備できていない場合は無視

        # LLMが応答すべきメッセージか、基本的なフィルタリング
        if not should_respond_to_llm(self.bot.user, message):
            return

        # さらに詳細な認証チェック (チャンネル/ロール)
        if not self._is_authorised_for_llm(message):
            return

        provider_model_str = self.cfg.get("model", "")
        if not provider_model_str:
            logger.error("設定ファイル (config.yaml) に 'model' キーが見つかりません。LLM応答を中止します。")
            return

        try:
            provider, model_name = provider_model_str.split("/", 1)
        except ValueError:
            logger.error(
                f"無効なモデル形式 '{provider_model_str}' です。形式は 'provider/model' であるべきです。LLM応答を中止します。")
            try:
                await message.reply(content="LLMモデルの設定形式が無効です。管理者に確認してください。", silent=True)
            except discord.HTTPException:
                pass  # エラーメッセージ送信失敗は無視
            return

        provider_cfg = self.cfg.get("providers", {}).get(provider)
        if not provider_cfg:
            logger.error(f"設定ファイルにプロバイダー '{provider}' の設定が見つかりません。LLM応答を中止します。")
            try:
                await message.reply(
                    content=f"LLMプロバイダー '{provider}' の設定がありません。管理者に確認してください。", silent=True)
            except discord.HTTPException:
                pass
            return

        # OpenAIクライアント (または指定プロバイダーのクライアント) を初期化
        llm_client = AsyncOpenAI(  # 名前をより汎用的にしても良い (例: generic_llm_client)
            base_url=provider_cfg.get("base_url"),
            api_key=provider_cfg.get("api_key", "sk-no-key-required"),  # APIキーがない場合はダミー値を設定
        )

        # モデルの能力や設定を読み込み
        accept_images = any(tag in model_name for tag in VISION_MODEL_TAGS)
        accept_usernames = provider in PROVIDERS_SUPPORTING_USERNAMES
        max_text_len = self.cfg.get("max_text_history", 5000)  # 履歴内のテキスト最大長
        max_images_count = self.cfg.get("max_images_history", 1) if accept_images else 0  # 履歴内の画像最大数
        max_messages_history = self.cfg.get("max_messages_history", 10)  # 履歴の最大メッセージ数
        max_discord_msg_len = 1990  # Discordの1メッセージあたりの最大文字数 (余裕を持たせる)

        # 会話履歴を構築
        api_messages, user_warnings = await self._build_message_chain(
            message, max_messages_history, max_text_len, max_images_count, accept_images, accept_usernames
        )

        # 受信ログ
        logger.info(
            "[%s] LLM宛ユーザー: %s (ID: %s) | 添付ファイル数: %d | 構築済み履歴メッセージ数: %d | メッセージ内容プレビュー: %s",
            message.guild.name if message.guild else "DM", message.author.display_name, message.author.id,
            len(message.attachments), len(api_messages),  # api_messages は構築後のもの
            message.content[:100] + ("..." if len(message.content) > 100 else ""),  # 内容は一部プレビュー
        )

        # APIに送信する最終的なメッセージリストを作成
        final_api_messages_to_send: List[dict] = []
        if self.SYSTEM_PROMPT: final_api_messages_to_send.append({"role": "system", "content": self.SYSTEM_PROMPT})
        if self.STARTER_PROMPT: final_api_messages_to_send.append({"role": "assistant", "content": self.STARTER_PROMPT})
        final_api_messages_to_send.extend(api_messages)  # 構築した会話履歴
        if self.PREFILL_PROMPT: final_api_messages_to_send.append({"role": "assistant", "content": self.PREFILL_PROMPT})

        # LLMからのレスポンスを生成・送信
        await self._generate_and_send_response(
            final_api_messages_to_send, message, user_warnings, llm_client, model_name, max_discord_msg_len
        )

    async def _build_message_chain(self, new_msg: discord.Message, max_messages: int, max_text: int, max_images: int,
                                   accept_images: bool, accept_usernames: bool) -> tuple[list[dict], Set[str]]:
        """過去のメッセージを遡り、LLM API用のメッセージリストとユーザー向け警告を構築します。"""
        messages_for_api: list[dict] = []
        user_warnings: set[str] = set()
        current_discord_message: Optional[discord.Message] = new_msg
        visited_message_ids: Set[int] = set()  # 無限ループ防止用

        while current_discord_message and len(messages_for_api) < max_messages:
            if current_discord_message.id in visited_message_ids:
                logger.warning(
                    f"会話履歴の構築中にメッセージID {current_discord_message.id} でループを検出。停止します。")
                user_warnings.add(self.ERROR_MESSAGES.get("loop_detected_history",
                                                          "⚠️ 会話履歴でループが見つかりました。処理を中断します。"))
                break
            visited_message_ids.add(current_discord_message.id)

            # メッセージノードを取得または新規作成 (self.message_nodes はBotインスタンスの属性を参照)
            node = self.message_nodes.setdefault(current_discord_message.id, MessageNode())
            async with node.lock:  # ノード処理の排他制御
                # ノードが未処理か、画像処理が必要な場合 (画像を受け付ける設定で、ノードに画像がなく、メッセージに添付ファイルがある)
                needs_processing = node.text is None
                if accept_images and not node.images and current_discord_message.attachments:
                    needs_processing = True

                if needs_processing:  # ノード内容を解析・格納
                    await self._process_message_node(node, current_discord_message, accept_images)

                # ノードからAPI用コンテンツを生成 (テキストや画像の長さを制限)
                api_content = self._compose_message_content(node, max_text, max_images)

                if api_content:  # 有効なコンテンツがある場合のみAPIメッセージリストに追加
                    # コンテンツが文字列で実質空、またはリストで実質空の場合は追加しない
                    is_empty_str = isinstance(api_content, str) and not api_content.strip()
                    is_empty_list = isinstance(api_content, list) and not any(
                        (part.get("text", "").strip() if isinstance(part, dict) and part.get(
                            "type") == "text" else False) or
                        (isinstance(part, dict) and part.get("type") == "image_url")
                        for part in api_content  # type: ignore
                    )

                    if not (is_empty_str or is_empty_list):
                        payload: dict = {"content": api_content, "role": node.role}
                        if accept_usernames and node.user_id:  # ユーザー名をサポートするプロバイダーの場合
                            payload["name"] = str(node.user_id)  # OpenAI APIではnameは文字列
                        messages_for_api.append(payload)
                else:  # コンテンツが生成されなかった場合
                    logger.debug(f"メッセージID {current_discord_message.id} は空のコンテンツとして処理(スキップ)。")

                # このノード処理に関するユーザー向け警告を追加
                self._update_user_warnings_for_node(node, max_text, max_images, user_warnings)

                # 次のメッセージ取得に失敗した場合は履歴構築を中断
                if node.fetch_next_failed:
                    user_warnings.add(self.ERROR_MESSAGES.get("msg_fetch_failed",
                                                              "⚠️ 以前のメッセージ取得に失敗。履歴が不完全な可能性があります。"))
                    break

                # 履歴のメッセージ数上限に達したら中断
                if len(messages_for_api) >= max_messages:
                    break

                    # 次に遡るDiscordメッセージを決定
                await self._set_next_discord_message_for_node(node, current_discord_message)
                current_discord_message = node.next_message  # 次のメッセージへ

        # ループ終了後、上限に達していた場合の警告
        if current_discord_message and len(messages_for_api) >= max_messages:
            user_warnings.add(
                self.ERROR_MESSAGES.get("max_messages_limit_reached",
                                        "⚠️ 履歴のメッセージ数上限 ({max_messages}件) に達しました。").format(
                    max_messages=len(messages_for_api))
            )

        # APIは古い順のメッセージリストを期待するため、収集したリストを逆順にして返す
        return messages_for_api[::-1], user_warnings

    async def _process_message_node(self, node: MessageNode, msg: discord.Message, accept_images: bool) -> None:
        """Discordメッセージを解析し、情報をMessageNodeオブジェクトに格納します。"""
        raw_content = msg.content or ""
        # メッセージ内のメンションをユーザー名に置換 (LLMがIDを理解しづらいため)
        processed_content = await self._replace_mentions_with_names(raw_content, msg.guild)

        # ボット以外のユーザーの発言には、発言者情報を付加
        if msg.author != self.bot.user:
            author_identifier = msg.author.display_name if isinstance(msg.author, discord.Member) else msg.author.name
            # テキストがある場合のみユーザー情報を付加
            final_text_content = f"`User({author_identifier})`: {processed_content}".strip() if processed_content else f"`User({author_identifier})`"
        else:  # ボット自身の発言はそのまま
            final_text_content = processed_content

        attachment_texts: List[str] = []  # 添付テキストファイルの内容
        node.images = []  # 添付画像リストを初期化
        node.has_bad_attachments = False  # 不正な添付ファイルフラグをリセット

        # 添付ファイルを処理
        for att in msg.attachments:
            if att.content_type:  # Content-Typeがある場合のみ処理
                if "text" in att.content_type:  # テキストファイルの場合
                    try:
                        text_content = await self._fetch_attachment_text(att)
                        # ファイル名と内容の一部をテキストに追加 (長すぎる場合は切り詰め)
                        attachment_texts.append(
                            f"\n--- 添付テキスト: {att.filename} ---\n{text_content[:1000]}\n--- 添付終了 ---")
                    except Exception as e:
                        logger.warning(f"テキスト添付ファイル '{att.filename}' (ID: {att.id}) の取得失敗: {e}")
                        node.has_bad_attachments = True
                elif "image" in att.content_type and accept_images:  # 画像ファイルで、画像処理が有効な場合
                    try:
                        img_data = await self._process_image_attachment(att)
                        node.images.append(img_data)
                    except Exception as e:
                        logger.warning(f"画像添付ファイル '{att.filename}' (ID: {att.id}) の処理失敗: {e}")
                        node.has_bad_attachments = True
                else:  # サポートされていない種類の添付ファイル
                    node.has_bad_attachments = True
            else:  # Content-Typeがない添付ファイルも不正とみなす
                node.has_bad_attachments = True

        # Embed内の説明文も収集
        embed_descriptions = [embed.description for embed in msg.embeds if embed.description]
        # すべてのテキスト情報を結合 (メッセージ本体、Embed、添付テキストファイル)
        all_text_parts = [final_text_content] + embed_descriptions + attachment_texts
        node.text = "\n".join(filter(None, all_text_parts)).strip()  # filter(None,...) で空文字列を除外

        # メッセージがボットへのメンションで始まる場合、そのメンション部分を削除
        if self.bot.user and node.text.startswith(self.bot.user.mention):
            node.text = node.text.replace(self.bot.user.mention, "", 1).lstrip()

        # メッセージの役割 (role) とユーザーIDを設定
        node.role = "assistant" if msg.author == self.bot.user else "user"
        node.user_id = msg.author.id if node.role == "user" else None

    async def _fetch_attachment_text(self, attachment: discord.Attachment) -> str:
        """テキスト添付ファイルの内容を非同期に取得し、文字列として返します。"""
        response = await self.httpx_client.get(attachment.url, follow_redirects=True)
        response.raise_for_status()  # HTTPエラーの場合は例外を発生させる
        try:  # UTF-8でのデコードを試みる
            return response.content.decode('utf-8')
        except UnicodeDecodeError:  # 失敗した場合はhttpxのデフォルトエンコーディングにフォールバック
            return response.text

    async def _process_image_attachment(self, attachment: discord.Attachment) -> dict:
        """画像添付ファイルを非同期に取得し、Base64エンコードしてAPI送信用の辞書形式で返します。"""
        response = await self.httpx_client.get(attachment.url, follow_redirects=True)
        response.raise_for_status()
        base64_encoded_image = b64encode(response.content).decode('utf-8')
        return {"type": "image_url",
                "image_url": {"url": f"data:{attachment.content_type};base64,{base64_encoded_image}"}}

    async def _replace_mentions_with_names(self, content: str, guild: Optional[discord.Guild]) -> str:
        """メッセージ内のユーザーメンションを、可能であれば表示名またはユーザー名に置換します。"""
        if not MENTION_PATTERN.search(content): return content  # メンションがなければ何もしない

        async def get_name_for_mention(user_id: int) -> str:
            """指定されたユーザーIDの表示名またはユーザー名を取得します。"""
            try:
                if guild:  # サーバー内であればメンバー情報を取得
                    member = guild.get_member(user_id) or await guild.fetch_member(user_id)
                    return member.display_name  # サーバーでの表示名
                else:  # DMなどサーバー外であればユーザー情報を取得
                    user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
                    return user.name  # Discordユーザー名
            except discord.NotFound:
                logger.warning(f"メンション置換: ユーザーID {user_id} が見つかりません。")
                return f"不明なユーザー({user_id})"  # フォールバック
            except discord.HTTPException:
                logger.warning(f"メンション置換: ユーザーID {user_id} の情報取得中にHTTPエラー。")
                return f"ユーザー({user_id},取得エラー)"  # フォールバック
            except Exception as e:
                logger.error(f"メンション置換: ユーザーID {user_id} の情報取得中に予期せぬエラー: {e}")
                return f"ユーザー({user_id},内部エラー)"  # フォールバック

        # メッセージ内の全メンションIDに対して名前を取得 (重複回避のため辞書使用)
        user_id_to_name_map: Dict[int, str] = {}
        for match in MENTION_PATTERN.finditer(content):
            user_id = int(match.group(1))
            if user_id not in user_id_to_name_map:  # まだ取得していなければ
                user_id_to_name_map[user_id] = await get_name_for_mention(user_id)

        # 実際にメンション文字列を置換する関数
        def replace_function(m: re.Match) -> str:
            uid = int(m.group(1))
            # マップから名前を取得して置換。見つからなければ元のメンション文字列を維持。
            # メンション自体も残したい場合は `@名前` のようにする
            return f"@{user_id_to_name_map.get(uid, m.group(0))}"

        return MENTION_PATTERN.sub(replace_function, content)

    async def _set_next_discord_message_for_node(self, node: MessageNode, current_msg: discord.Message) -> None:
        """現在のメッセージに基づき、履歴を遡るための次のメッセージを決定し、ノードに設定します。"""
        next_discord_msg: Optional[discord.Message] = None
        node.fetch_next_failed = False  # 失敗フラグをリセット

        try:
            # 優先度1: メッセージが何かに返信している場合 (参照)
            if current_msg.reference and current_msg.reference.message_id:
                try:
                    referenced_msg = current_msg.reference.cached_message  # キャッシュにあればそれを使用
                    if not referenced_msg:  # キャッシュになければAPIから取得
                        # 参照先のチャンネルを取得 (DMや別チャンネルの可能性も考慮)
                        target_channel_for_ref = self.bot.get_channel(current_msg.reference.channel_id) or \
                                                 await self.bot.fetch_channel(current_msg.reference.channel_id)
                        if isinstance(target_channel_for_ref,
                                      (discord.TextChannel, discord.Thread, discord.DMChannel)):  # 型チェック
                            referenced_msg = await target_channel_for_ref.fetch_message(
                                current_msg.reference.message_id)
                    next_discord_msg = referenced_msg
                except (discord.NotFound, discord.HTTPException) as e_ref:
                    logger.debug(f"参照メッセージ {current_msg.reference.message_id} の取得失敗: {e_ref}")
                    node.fetch_next_failed = True  # 参照取得失敗は履歴構築に影響大

            # 優先度2: (参照がなく、取得失敗もしていない場合) 履歴の直前のメッセージ
            # ただし、ボットへのメンションがあるかDMの場合にこのロジックを適用することが多い
            if next_discord_msg is None and not node.fetch_next_failed:
                is_dm_or_bot_mention = (self.bot.user and self.bot.user.mention in current_msg.content) or \
                                       (current_msg.channel.type == discord.ChannelType.private)

                if is_dm_or_bot_mention and isinstance(current_msg.channel,
                                                       (discord.TextChannel, discord.Thread, discord.DMChannel)):
                    try:
                        # チャンネル履歴から1つ前のメッセージを取得 (limit=1)
                        async for prev_msg_in_history in current_msg.channel.history(before=current_msg, limit=1):
                            # 前のメッセージがボット自身のもの、またはDMで同じユーザーからの続きである場合にリンク
                            is_bot_reply_candidate = prev_msg_in_history.author == self.bot.user
                            is_dm_continuation_candidate = (current_msg.channel.type == discord.ChannelType.private and \
                                                            prev_msg_in_history.author == current_msg.author)

                            # メッセージタイプが通常または返信であることも確認 (参加メッセージなどを除外)
                            if prev_msg_in_history.type in {discord.MessageType.default, discord.MessageType.reply} and \
                                    (is_bot_reply_candidate or is_dm_continuation_candidate):
                                next_discord_msg = prev_msg_in_history
                            break  # 1つ取得したらループ終了 (limit=1なので通常1回)
                    except discord.HTTPException as e_hist:
                        logger.debug(f"メッセージ {current_msg.id} の履歴取得中にエラー: {e_hist}")
                        # 履歴取得失敗は必ずしも致命的ではないため、fetch_next_failed は立てないことも

            # (優先度3: スレッドの親メッセージへのリンクは複雑なため、ここでは省略)

        except Exception as e_outer:  # このtryブロック全体の予期せぬエラー
            logger.exception(
                f"メッセージチェーンの次のメッセージ設定中に予期せぬエラー (メッセージID: {current_msg.id})")
            node.fetch_next_failed = True  # 予期せぬエラー時は失敗として扱う
            next_discord_msg = None  # next_msg を None にリセット

        node.next_message = next_discord_msg  # 決定した次のメッセージをノードに設定

        # デバッグログ
        if node.next_message:
            logger.debug(f"メッセージID {current_msg.id} は前のメッセージID {node.next_message.id} にリンクされました。")
        elif not node.fetch_next_failed:  # リンク先がなく、取得失敗もしていない場合はチェーンの始点
            logger.debug(f"メッセージID {current_msg.id} は会話チェーンの始点と判断されました。")

    def _compose_message_content(self, node: MessageNode, max_text_len: int,
                                 max_images_count: int) -> str | list | None:
        """MessageNodeからAPI送信用コンテンツを生成。テキストや画像数を制限。内容がなければNone。"""
        limited_text = (node.text[:max_text_len] if node.text else "").strip()  # テキストを指定長で切り詰め
        limited_images = node.images[:max_images_count] if node.images else []  # 画像を指定数で切り詰め

        if not limited_text and not limited_images: return None  # テキストも画像もなければNone

        if not limited_images: return limited_text  # 画像がなければテキストのみ返す (空文字列の可能性あり)

        # 画像がある場合はマルチモーダル形式のリストで返す
        content_parts: List[dict] = []
        if limited_text:  # テキストがあればテキストパートを追加
            content_parts.append({"type": "text", "text": limited_text})

        content_parts.extend(limited_images)  # 画像パートを追加 (API形式の辞書のリスト)

        return content_parts if content_parts else None  # 実質空ならNone

    def _update_user_warnings_for_node(self, node: MessageNode, max_text_len: int, max_images_count: int,
                                       warnings_set: set[str]) -> None:
        """個々のノード処理結果に基づき、ユーザー向け警告を warnings_set に追加します。"""
        err_msg_templates = self.ERROR_MESSAGES  # 設定ファイルからのエラーメッセージテンプレート

        # テキスト長制限を超えた場合の警告
        if node.text is not None and len(node.text) > max_text_len:
            warnings_set.add(
                err_msg_templates.get("msg_max_text_size",
                                      "⚠️ メッセージテキストが長すぎるため切り詰められました (>{L}文字)。")
                .format(L=max_text_len)
            )

        # 画像数制限を超えた場合の警告
        if node.images is not None and len(node.images) > max_images_count:
            if max_images_count > 0:  # 画像が許可されているが数を超えた
                warnings_set.add(
                    err_msg_templates.get("msg_max_image_size", "⚠️ 添付画像が多いため、最初の{N}件のみを使用します。")
                    .format(N=max_images_count)
                )
            else:  # 画像が許可されていない設定の場合
                warnings_set.add(
                    err_msg_templates.get("msg_error_image", "⚠️ この設定では画像はサポートされていません。"))

        # 不正/サポート外の添付ファイルがあった場合の警告
        if node.has_bad_attachments:
            warnings_set.add(err_msg_templates.get("msg_error_attachment",
                                                   "⚠️ サポート外の添付ファイル、または処理に失敗した添付ファイルはスキップされました。"))

    async def _generate_and_send_response(self, api_messages_to_send: list[dict],
                                          origin_discord_message: discord.Message, user_warnings_from_history: set[str],
                                          llm_client: AsyncOpenAI, model_name: str, max_discord_msg_len: int) -> None:
        """LLMからレスポンスを生成し、ストリーミングでDiscordに送信します (ツールコール対応)。"""
        response_discord_messages: list[discord.Message] = []  # 送信したDiscordメッセージのリスト
        current_message_buffer = ""  # 現在構築中のDiscordメッセージ内容
        edit_task: Optional[asyncio.Task] = None  # メッセージ編集タスク

        # self.bot.last_llm_edit_task_time を参照・更新
        self.bot.last_llm_edit_task_time = dt.now().timestamp()  # type: ignore

        # 履歴構築時の警告を初期メッセージに追加 (あれば)
        initial_warnings_text = " ".join(sorted(list(user_warnings_from_history)))
        user_warnings_from_history.clear()  # 一度表示したらクリア

        # 有効なツールリストを取得 (メインBotインスタンスのメソッドを呼び出す)
        enabled_tools_list = self.bot._enabled_tools() if hasattr(self.bot, '_enabled_tools') else []  # type: ignore

        # API呼び出しの共通パラメータ
        api_common_kwargs = dict(
            model=model_name,
            stream=True,  # ストリーミング有効
            tools=enabled_tools_list,  # 有効なツール
            tool_choice="auto",  # ツール使用を自動判断
            extra_body=self.cfg.get("extra_api_parameters", {}),  # 追加APIパラメータ (例: temperature)
        )

        max_tool_interaction_loops = self.cfg.get("max_tool_loops", 3)  # 最大ツールコールループ回数
        current_api_messages_for_loop = list(api_messages_to_send)  # ループ内で変更されるAPIメッセージリスト

        for loop_iteration_count in range(max_tool_interaction_loops + 1):  # +1は最終応答用
            # ループ上限に達したらツール使用を強制的にオフ
            if loop_iteration_count == max_tool_interaction_loops:
                api_common_kwargs.pop("tools", None)  # toolsキー自体を削除
                api_common_kwargs.pop("tool_choice", None)  # tool_choiceキー自体を削除
                logger.info("ツールコールループ上限。ツール使用を無効化して最終応答を試みます。")

            api_request_parameters = dict(api_common_kwargs, messages=current_api_messages_for_loop)

            # ツールコール関連データのバッファ
            tool_call_data_buffer_for_iteration: dict[str, dict[str, Any]] = {}  # type: ignore
            assistant_text_content_with_tool_call = ""  # ツールコールと同時に来るテキスト用
            tool_call_detected_in_stream = False  # このストリームでツールコールを検出したか

            try:
                async with origin_discord_message.channel.typing():  # 「入力中...」表示
                    async for stream_chunk in await llm_client.chat.completions.create(**api_request_parameters):
                        choice_in_chunk = stream_chunk.choices[0]  # 通常choiceは1つ

                        # ツールコール情報の処理
                        tool_calls_in_delta = getattr(choice_in_chunk.delta, "tool_calls", None)
                        if tool_calls_in_delta:
                            tool_call_detected_in_stream = True
                            for tc_delta_item in tool_calls_in_delta:
                                if tc_delta_item.id not in tool_call_data_buffer_for_iteration:
                                    tool_call_data_buffer_for_iteration[tc_delta_item.id] = {  # type: ignore
                                        "name": tc_delta_item.function.name or "",
                                        "arguments_chunks": []
                                    }
                                if tc_delta_item.function.name and not \
                                tool_call_data_buffer_for_iteration[tc_delta_item.id]["name"]:  # type: ignore
                                    tool_call_data_buffer_for_iteration[tc_delta_item.id][
                                        "name"] = tc_delta_item.function.name  # type: ignore
                                if tc_delta_item.function.arguments:
                                    tool_call_data_buffer_for_iteration[tc_delta_item.id]["arguments_chunks"].append(
                                        tc_delta_item.function.arguments)  # type: ignore
                            continue  # ツールコールチャンクはテキスト処理スキップ

                        # 通常のテキストコンテンツ処理
                        delta_text_content = choice_in_chunk.delta.content
                        if delta_text_content is not None:
                            if tool_call_detected_in_stream:  # ツールコールと同時にテキストが来た場合
                                assistant_text_content_with_tool_call += delta_text_content
                            else:  # 通常のテキストストリーム
                                current_message_buffer += delta_text_content

                                # 最初のメッセージに警告テキストを付加 (未処理の場合)
                                if not response_discord_messages and initial_warnings_text:
                                    current_message_buffer = initial_warnings_text + " " + current_message_buffer
                                    initial_warnings_text = ""  # 一度追加したらクリア

                                # Discordメッセージ長上限を超えそうな場合の分割送信処理
                                content_part_for_new_message = None
                                if len(current_message_buffer) > max_discord_msg_len:
                                    content_part_for_new_message = current_message_buffer[:max_discord_msg_len]
                                    current_message_buffer = current_message_buffer[max_discord_msg_len:]  # 残りをバッファに

                                if content_part_for_new_message is not None:  # 新しいメッセージとして送信
                                    if edit_task and not edit_task.done(): await edit_task  # 実行中の編集があれば待つ
                                    reply_target_message = origin_discord_message if not response_discord_messages else \
                                    response_discord_messages[-1]
                                    try:
                                        # メッセージが続くことを示すインジケータを付加
                                        new_discord_message_part = await reply_target_message.reply(
                                            content=content_part_for_new_message + STREAMING_INDICATOR,
                                            silent=True  # @mention通知なし
                                        )
                                        # 送信したメッセージを記録、ノードも作成 (編集のため)
                                        self.message_nodes[new_discord_message_part.id] = MessageNode(
                                            text=content_part_for_new_message,  # 元のテキスト
                                            next_message=reply_target_message, role="assistant"
                                        )
                                        await self.message_nodes[new_discord_message_part.id].lock.acquire()  # ノードロック
                                        response_discord_messages.append(new_discord_message_part)
                                        self.bot.last_llm_edit_task_time = dt.now().timestamp()  # type: ignore
                                    except discord.HTTPException as e_send_part:
                                        logger.error(f"Discordメッセージパート送信失敗(新規): {e_send_part}")
                                        # エラーメッセージ送信を試みる (configから取得)
                                        err_msg_send_fail = self.ERROR_MESSAGES.get("send_failed_part",
                                                                                    "⚠️ メッセージ送信中にエラー。")
                                        try:
                                            err_reply_target = response_discord_messages[
                                                -1] if response_discord_messages else origin_discord_message
                                            await err_reply_target.reply(content=err_msg_send_fail, silent=True)
                                        except discord.HTTPException:
                                            pass
                                        return  # 送信失敗時は処理中断

                            # メッセージ編集のタイミング制御
                            can_edit_now = (
                                    response_discord_messages  # 既に送信済みメッセージがある
                                    and current_message_buffer  # バッファに編集内容がある
                                    and (edit_task is None or edit_task.done())  # 前の編集タスクが完了
                                    and (dt.now().timestamp() - (
                                        self.bot.last_llm_edit_task_time or 0) >= EDIT_DELAY_SECONDS)

                            )
                            is_final_stream_chunk = choice_in_chunk.finish_reason is not None  # 最後のチャンクか

                            if (
                                    can_edit_now or is_final_stream_chunk) and not tool_call_detected_in_stream and response_discord_messages:
                                if edit_task and not edit_task.done(): await edit_task  # 念のため待機

                                content_for_message_edit = current_message_buffer
                                if not is_final_stream_chunk:  # 最終チャンクでなければストリーミングインジケータを追加
                                    content_for_message_edit += STREAMING_INDICATOR

                                discord_message_to_edit = response_discord_messages[-1]
                                # 編集タスクを作成して実行 (非同期)
                                edit_task = asyncio.create_task(
                                    self._perform_message_edit(discord_message_to_edit, content_for_message_edit))
                                self.bot.last_llm_edit_task_time = dt.now().timestamp()  # type: ignore

                        # ストリーム終了条件の確認
                        finish_reason = choice_in_chunk.finish_reason
                        if finish_reason:
                            if finish_reason == "tool_calls": break  # ツールコール処理へ
                            if finish_reason == "stop": break  # 通常終了、ループを抜けて最終処理へ
                            # その他の終了理由 (最大長、フィルタリングなど)
                            if finish_reason == "length":
                                user_warnings_from_history.add(self.ERROR_MESSAGES.get("max_tokens_limit",
                                                                                       "⚠️ LLMの最大応答長に達し応答が途切れました。"))
                            else:
                                logger.warning(f"LLMストリームが予期せぬ理由で終了: {finish_reason}")
                                user_warnings_from_history.add(self.ERROR_MESSAGES.get("unexpected_finish",
                                                                                       f"⚠️ LLMの応答が予期せず終了 ({finish_reason})。"))
                            break  # ループを抜ける

                    # ストリームループ終了後の処理分岐
                    if tool_call_detected_in_stream:  # ツールコールが検出された場合
                        # アシスタントからのメッセージ(ツールコール指示)をAPI履歴に追加
                        assistant_message_with_tool_calls: dict = {
                            "role": "assistant",
                            "content": assistant_text_content_with_tool_call.strip() or None,  # テキストがあれば追加
                            "tool_calls": []
                        }
                        for call_id, details in tool_call_data_buffer_for_iteration.items():
                            function_name = details["name"]
                            arguments_str = "".join(details["arguments_chunks"])  # 分割された引数を結合
                            assistant_message_with_tool_calls["tool_calls"].append({  # type: ignore
                                "id": call_id, "type": "function",
                                "function": {"name": function_name, "arguments": arguments_str}
                            })

                        if not assistant_message_with_tool_calls["tool_calls"]:  # type: ignore
                            logger.error("ツールコール検出後、ツール詳細データが空でした。")
                            current_api_messages_for_loop.append({
                                "role": "user",  # ユーザーにエラーを伝えるためのダミーメッセージ
                                "content": "システムエラー: ツール呼び出しが試みられましたが、詳細が欠落していました。"
                            })
                            break  # tool_interaction_loops の次のイテレーションへ (または終了)

                        current_api_messages_for_loop.append(assistant_message_with_tool_calls)
                        assistant_text_content_with_tool_call = ""  # バッファクリア

                        # 各ツールコールを実行し、結果をAPI履歴に追加
                        plugins_on_bot = getattr(self.bot, 'plugins', {})  # メインBotのプラグインリスト
                        for tool_call_info in assistant_message_with_tool_calls["tool_calls"]:  # type: ignore
                            tool_name = tool_call_info["function"]["name"]
                            tool_call_id = tool_call_info["id"]
                            tool_arguments_str = tool_call_info["function"]["arguments"]

                            active_tool_names_cfg = self.cfg.get("active_tools", None)
                            is_tool_enabled_for_run = (
                                    tool_name in plugins_on_bot and
                                    (active_tool_names_cfg is None or tool_name in active_tool_names_cfg)
                            )

                            tool_result_str = ""
                            if not is_tool_enabled_for_run:
                                tool_result_str = f"エラー: ツール '{tool_name}' は現在利用できません。"
                                logger.warning(f"LLMが要求したツール '{tool_name}' は無効化されています。")
                            else:
                                plugin_instance_to_run = plugins_on_bot[tool_name]
                                try:
                                    parsed_tool_args = json.loads(tool_arguments_str)
                                    # プラグイン実行 (botインスタンスやメッセージ情報も渡す)
                                    tool_result_str = await plugin_instance_to_run.run(
                                        arguments=parsed_tool_args, bot=self.bot, source_message=origin_discord_message
                                    )
                                except json.JSONDecodeError:
                                    tool_result_str = f"エラー: ツール '{tool_name}' の引数JSONの解析に失敗。"
                                    logger.error(f"ツール '{tool_name}' 引数JSON解析失敗: {tool_arguments_str}")
                                except Exception as e_tool_run_exc:
                                    tool_result_str = f"エラー: ツール '{tool_name}' の実行中に問題発生。"
                                    logger.error(f"ツール '{tool_name}' 実行エラー: {e_tool_run_exc}", exc_info=True)

                            current_api_messages_for_loop.append({
                                "role": "tool", "tool_call_id": tool_call_id, "name": tool_name,
                                "content": str(tool_result_str)[:10000]  # 長すぎる結果は切り詰め
                            })

                        current_message_buffer = ""  # 通常テキストバッファもクリア
                        continue  # tool_interaction_loops の次のイテレーションへ

                    else:  # 通常のテキストストリームが終了した場合 (ツールコールなし)
                        final_buffered_content = current_message_buffer
                        if final_buffered_content:  # バッファに内容があれば送信/編集
                            if not response_discord_messages:  # まだ一度もメッセージを送信していない場合
                                try:
                                    newly_sent_final_msg = await origin_discord_message.reply(
                                        content=final_buffered_content, silent=True)
                                    self.message_nodes[newly_sent_final_msg.id] = MessageNode(
                                        text=final_buffered_content, next_message=origin_discord_message,
                                        role="assistant")
                                    response_discord_messages.append(newly_sent_final_msg)
                                except discord.HTTPException as e_send_final_new:
                                    logger.error(f"最終メッセージ送信失敗(新規): {e_send_final_new}")
                            else:  # 既に送信済みメッセージがある場合は編集
                                if edit_task and not edit_task.done(): await edit_task
                                await self._perform_message_edit(response_discord_messages[-1], final_buffered_content)

                        break  # 通常応答完了、tool_interaction_loops を抜ける

            except RateLimitError:  # APIレート制限エラー
                logger.warning("OpenAI API レート制限エラー (429) が発生しました。")
                ratelimit_msg_text = self.ERROR_MESSAGES.get("ratelimit_error",
                                                             "⚠️ リクエストが集中しています。しばらくしてから再度お試しください。")
                try:
                    await origin_discord_message.reply(content=ratelimit_msg_text, silent=True)
                except discord.HTTPException as e_rl_reply:
                    logger.error(f"レート制限エラーメッセージ送信失敗: {e_rl_reply}")
                return  # レート制限時は処理終了

            except Exception as e_generate_response_outer:  # レスポンス生成中のその他のエラー
                logger.exception("レスポンス生成中に一般エラーが発生しました。")
                general_error_text = self.ERROR_MESSAGES.get("general_error",
                                                             "⚠️ 予期しないエラーが発生しました。応答を生成できませんでした。")
                error_reply_target_msg = response_discord_messages[
                    -1] if response_discord_messages else origin_discord_message
                try:
                    await error_reply_target_msg.reply(content=general_error_text, silent=True)
                except discord.HTTPException as e_gen_err_reply:
                    logger.error(f"一般エラーメッセージ送信失敗: {e_gen_err_reply}")
                return  # 一般エラー時も処理終了

            # for loop_iteration_count の最後に到達 (通常応答完了か、ループ上限)
            if not tool_call_detected_in_stream:  # ツールコールがなければ応答は完了
                break

        # すべてのループ終了後の最終処理
        if edit_task and not edit_task.done():  # 未完了の編集タスクがあれば待つ
            try:
                await edit_task
            except Exception as e_edit_final:
                logger.error(f"最終編集タスク完了待ちエラー: {e_edit_final}")

        # 警告メッセージがあれば、最後のメッセージに追記または新規送信
        if user_warnings_from_history:
            final_warnings_text = " ".join(sorted(list(user_warnings_from_history)))
            target_for_warning_reply = response_discord_messages[
                -1] if response_discord_messages else origin_discord_message
            try:
                # 既存メッセージに追記できるか試みる (長さに注意)
                if response_discord_messages and len(
                        target_for_warning_reply.content + "\n\n" + final_warnings_text) <= max_discord_msg_len:
                    await self._perform_message_edit(target_for_warning_reply,
                                                     target_for_warning_reply.content + "\n\n" + final_warnings_text)
                else:  # 追記できないか、応答メッセージがなければ新規返信
                    await target_for_warning_reply.reply(content=final_warnings_text, silent=True)
            except discord.HTTPException as e_warn_reply:
                logger.error(f"最終警告メッセージ送信/編集失敗: {e_warn_reply}")

        # 送信した全メッセージのテキストを結合してログ出力
        full_response_text_parts_final = []
        for msg_part_final in response_discord_messages:
            node_final = self.message_nodes.get(msg_part_final.id)
            text_to_append = ""
            if node_final and node_final.text:
                text_to_append = node_final.text
            elif msg_part_final.content:  # ノードがなくてもメッセージコンテンツがあれば使用
                text_to_append = msg_part_final.content.replace(STREAMING_INDICATOR, "")  # インジケーター除去
            if text_to_append:
                full_response_text_parts_final.append(text_to_append)

        full_response_text_logged = "".join(full_response_text_parts_final)
        logger.info(
            "LLMレスポンス完了 (起点メッセージID: %s): %s",
            origin_discord_message.id,
            full_response_text_logged[:300] + ("..." if len(full_response_text_logged) > 300 else ""),  # ログ出力は短めに
        )

        # 使用したメッセージノードの最終テキストを更新し、ロックを解放
        for msg_part_node_cleanup in response_discord_messages:
            node_to_cleanup = self.message_nodes.get(msg_part_node_cleanup.id)
            if node_to_cleanup:
                # STREAMING_INDICATOR を除去した最終テキストをノードに保存
                final_text_for_node_cleanup = msg_part_node_cleanup.content.replace(STREAMING_INDICATOR, "").strip()
                node_to_cleanup.text = final_text_for_node_cleanup

                if node_to_cleanup.lock.locked():
                    node_to_cleanup.lock.release()
            else:
                logger.warning(f"メッセージノード {msg_part_node_cleanup.id} が最終クリーンアップで見つかりません。")

                # メッセージノードキャッシュのプルーニング (メインBotインスタンスの属性を操作)
                # config.yaml からLLM Cog用のメッセージノード最大数を取得するキー名を直接文字列で指定
            max_nodes_config_key_in_yaml = "max_message_nodes_llm"  # config.yaml で定義したキー名
            default_max_nodes = 100  # configにキーがなかった場合のデフォルト値
            max_nodes_for_pruning = self.cfg.get(max_nodes_config_key_in_yaml, default_max_nodes)

            if hasattr(self.bot, 'message_nodes_llm') and isinstance(self.bot.message_nodes_llm, dict) and \
                    len(self.bot.message_nodes_llm) > max_nodes_for_pruning:

                num_to_prune = len(self.bot.message_nodes_llm) - max_nodes_for_pruning
                # メッセージID (キー) の昇順でソートして古いものから削除候補とする
                node_ids_to_prune = sorted(list(self.bot.message_nodes_llm.keys()))[:num_to_prune]  # list()で囲む
                logger.info(f"古いLLMメッセージノードを {num_to_prune} 件削除します...")
                for node_id_to_delete in node_ids_to_prune:
                    # self.bot.message_nodes_llm から直接 pop する
                    node_obj_to_delete = self.bot.message_nodes_llm.pop(node_id_to_delete, None)  # type: ignore
                    if node_obj_to_delete and hasattr(node_obj_to_delete,
                                                      'lock') and node_obj_to_delete.lock.locked():  # type: ignore
                        logger.debug(
                            f"プルーニング中にノード {node_id_to_delete} のロックが取得できませんでした (LLMCog)。ノードを戻します。")
                        # 削除できなかった（ロックされていた）ノードを戻す
                        self.bot.message_nodes_llm[node_id_to_delete] = node_obj_to_delete  # type: ignore

    async def _perform_message_edit(self, message_to_edit: discord.Message, new_content: str) -> None:
        """エラー処理を含めて、Discordメッセージを安全に編集します。"""
        try:
            # 編集するコンテンツが現在のメッセージ内容と異なる場合のみ編集実行
            if new_content != message_to_edit.content:
                await message_to_edit.edit(content=new_content)
        except discord.NotFound:  # メッセージが既に削除されている場合
            logger.warning(f"編集しようとしたメッセージ {message_to_edit.id} は見つかりませんでした (削除済み)。")
        except discord.HTTPException as e_http_edit:  # レート制限などその他のHTTPエラー
            logger.warning(
                f"メッセージ {message_to_edit.id} の編集中にHTTPException: {e_http_edit.status} {e_http_edit.text}")
        except Exception as e_edit_unexpected:  # その他の予期せぬエラー
            logger.error(f"メッセージ {message_to_edit.id} の編集中に予期しないエラー: {e_edit_unexpected}",
                         exc_info=True)


async def setup(bot: commands.Bot):
    """LLMInteractionsCogをボットに登録するための必須関数です。"""
    await bot.add_cog(LLMInteractionsCog(bot))  # Cogをインスタンス化してボットに追加
    logger.info("LLMInteractionsCog (LLM 対話機能 Cog) が正常にロードされました。")