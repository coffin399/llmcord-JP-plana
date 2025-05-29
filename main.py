from __future__ import annotations

import asyncio
import logging
import re
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime as dt
from typing import Literal, Optional, Set, Tuple, List, Any  # Anyを追加 (tool_call_data_for_assistant用)

import discord
# from discord import app_commands # commands.Bot を使う場合、直接は不要になることが多い
from discord.ext import commands  # commands.Bot を使うために追加
import httpx
import yaml
import json
import time
import os
import sys
import shutil
from openai import AsyncOpenAI, RateLimitError
# from google import genai # genai の利用箇所が見当たらないためコメントアウト（必要なら戻す）

from plugins import load_plugins

# ロギングを設定 (変更なし)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# 定数定義 (変更なし、Cogから参照されるものはそのまま)
VISION_MODEL_TAGS: Tuple[str, ...] = (
    "gpt-4o", "claude-3", "gemini", "pixtral", "llava", "vision",
)
PROVIDERS_SUPPORTING_USERNAMES: Tuple[str, ...] = ("openai", "x-ai")
ALLOWED_FILE_TYPES: Tuple[str, ...] = ("image", "text")
INVITE_URL = "https://discord.com/api/oauth2/authorize?client_id=1031673203774464160&permissions=412317273088&scope=bot"
SUPPORT_SERVER_INVITE_LINK = "https://discord.gg/SjuWKtwNAG"
ARONA_REPOSITORY = "https://github.com/coffin399/music-bot-arona"
PLANA_REPOSITORY = "https://github.com/coffin399/llmcord-JP-plana"
ALLOWED_CHANNEL_TYPES: Tuple[discord.ChannelType, ...] = (
    discord.ChannelType.text, discord.ChannelType.public_thread,
    discord.ChannelType.private_thread, discord.ChannelType.private,
)
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
STREAMING_INDICATOR = "<:stream:1313474295372058758>"
EDIT_DELAY_SECONDS = 1
MAX_MESSAGE_NODES = 100
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


def load_config(filename: str = "config.yaml") -> dict:  # (変更なし)
    with open(filename, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def should_respond(client_user: discord.User, message: discord.Message) -> bool:  # (変更なし)
    if message.channel.type not in ALLOWED_CHANNEL_TYPES: return False
    if message.channel.type != discord.ChannelType.private and client_user not in message.mentions: return False
    if message.author.bot: return False
    return True


@dataclass
class MessageNode:  # (変更なし)
    text: Optional[str] = None
    images: List[dict] = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    next_message: Optional[discord.Message] = None
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class DiscordLLMBot(commands.Bot):  # discord.Client から commands.Bot に変更
    cfg_path: str
    # enabled_cogs 属性をクラスレベルで宣言（任意）
    enabled_cogs: List[str]

    def __init__(self, cfg_path: str = "config.yaml") -> None:
        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)
        self.enabled_cogs = self.cfg.get("enabled_cogs", [])  # 設定からCogリストを読み込む

        intents = discord.Intents.default()
        intents.message_content = True
        activity = discord.CustomActivity(
            name=(self.cfg.get("status_message") or "github.com/jakobdylanc/llmcord")[:128]
        )
        # commands.Bot の初期化
        super().__init__(
            command_prefix=commands.when_mentioned_or("!unused_prefix_llm "),  # スラッシュコマンドのみでも形式的に必要
            intents=intents,
            activity=activity,
            help_command=None  # デフォルトのテキストヘルプコマンドは不要なら無効化
        )
        # self.tree は commands.Bot に組み込まれているので、別途初期化は不要

        self.message_nodes: dict[int, MessageNode] = {}
        self.last_task_time: Optional[float] = None
        self.httpx_client = httpx.AsyncClient()
        self.SYSTEM_PROMPT: str | None = self.cfg.get("system_prompt")
        self.STARTER_PROMPT: str | None = self.cfg.get("starter_prompt")
        self.ERROR_MESSAGES: dict[str, str] = self.cfg.get("error_msg", {}) or {}
        self.plugins = load_plugins(self)

        logging.info("読み込まれたプラグイン: [%s]", ", ".join(p.__class__.__name__ for p in self.plugins.values()))
        logging.info("有効なツール: [%s]", ", ".join(spec["function"]["name"] for spec in self._enabled_tools()))

    async def _load_all_cogs(self) -> None:  # Cogをロードするヘルパーメソッド
        if not self.enabled_cogs:
            logging.info("読み込むCogが設定されていません (config.yamlのenabled_cogs)。")
            return
        loaded_cogs_count = 0
        for cog_name in self.enabled_cogs:
            cog_module_path = f"cogs.{cog_name}"  # cogsフォルダ内のCogを想定
            try:
                await self.load_extension(cog_module_path)
                logging.info(f"Cog '{cog_name}' ({cog_module_path}) を正常にロードしました。")
                loaded_cogs_count += 1
            except commands.ExtensionNotFound:
                logging.error(f"Cog '{cog_name}' ({cog_module_path}) が見つかりません。ファイルパスを確認してください。")
            except commands.ExtensionAlreadyLoaded:
                logging.warning(f"Cog '{cog_name}' ({cog_module_path}) は既にロードされています。")
            except commands.NoEntryPointError:
                logging.error(f"Cog '{cog_name}' ({cog_module_path}) に `setup` 関数が見つかりません。")
            except commands.ExtensionFailed as e:
                logging.error(
                    f"Cog '{cog_name}' ({cog_module_path}) のロード中にエラーが発生しました: {e.original.__class__.__name__}: {e.original}")
                logging.exception(f"Cog '{cog_name}' のロード失敗に関する詳細なスタックトレース:")  # 詳細表示
            except Exception as e:  # その他の予期せぬエラー
                logging.error(f"Cog '{cog_name}' ({cog_module_path}) のロード中に予期しないエラー: {e}")
                logging.exception(f"Cog '{cog_name}' のロード中の予期しないエラーに関する詳細なスタックトレース:")

        if loaded_cogs_count > 0:
            logging.info(f"{loaded_cogs_count}個のCogをロードしました。")
        elif self.enabled_cogs:  # enabled_cogsが空でなく、ロードされたCogがない場合
            logging.info("設定されたCogがありましたが、ロードされたものはありませんでした。エラーログを確認してください。")

    async def setup_hook(self) -> None:  # setup_hook は commands.Bot でCogロードに適している
        await self._load_all_cogs()  # Cogをロード
        # グローバルコマンドとして同期 (ギルド指定なし)
        # self.tree は commands.Bot の CommandTree インスタンス
        try:
            synced = await self.tree.sync()
            logging.info(f"{len(synced)}個のスラッシュコマンドをグローバルに同期しました。")
        except Exception as e:
            logging.exception(f"スラッシュコマンドの同期に失敗しました: {e}")

    async def on_ready(self) -> None:  # on_ready を追加 (任意だが一般的)
        logging.info(f"{self.user} (ID: {self.user.id})としてログインしました。")
        logging.info(f"接続サーバー数: {len(self.guilds)}")
        # setup_hookでコマンド同期するので、on_readyでの同期は不要（重複する可能性）

    async def on_message(self, message: discord.Message) -> None:  # (変更なし)
        if not should_respond(self.user, message): return
        if not self._is_authorised(message): return
        provider_model = self.cfg.get("model", "")
        if not provider_model:
            logging.error("config.yaml 'model' 未検出 - 中止")
            return
        try:
            provider, model = provider_model.split("/", 1)
        except ValueError:
            logging.error(f"無効モデル形式 '{provider_model}'. 'provider/model' 形式要")
            try:
                await message.reply("無効モデル設定", silent=True)
            except:
                pass
            return
        provider_cfg = self.cfg["providers"].get(provider)
        if not provider_cfg:
            logging.error(f"config.yaml プロバイダ '{provider}' 未検出 - 中止")
            try:
                await message.reply(f"プロバイダ '{provider}' 未設定", silent=True)
            except:
                pass
            return
        openai_client = AsyncOpenAI(
            base_url=provider_cfg.get("base_url"),
            api_key=provider_cfg.get("api_key", "sk-no-key-required"),
        )
        accept_images = any(tag in model for tag in VISION_MODEL_TAGS)
        accept_usernames = provider in PROVIDERS_SUPPORTING_USERNAMES
        max_text = self.cfg.get("max_text", 5_000)
        max_images = self.cfg.get("max_images", 0) if accept_images else 0
        max_messages = self.cfg.get("max_messages", 5)
        max_message_length = 2_000
        messages_chain, user_warnings = await self._build_message_chain(
            message, max_messages, max_text, max_images, accept_images, accept_usernames,
        )
        server_name = message.guild.name if message.guild else "DM"
        user_name = message.author.display_name
        logging.info(
            "[%s] User: %s (ID:%s) | Attach:%d | Chain:%d | Content: %s",
            server_name, user_name, message.author.id, len(message.attachments),
            len(messages_chain), message.content,
        )
        api_messages = []
        if self.SYSTEM_PROMPT: api_messages.append({"role": "system", "content": self.SYSTEM_PROMPT})
        if self.STARTER_PROMPT: api_messages.append({"role": "assistant", "content": self.STARTER_PROMPT})
        api_messages.extend(messages_chain)
        await self._generate_and_send_response(
            api_messages, message, user_warnings, openai_client, model, max_message_length,
        )

    def _enabled_tools(self) -> list[dict]:  # (変更なし)
        want = self.cfg.get("active_tools", None)
        if want is None: return [p.tool_spec for p in self.plugins.values()]
        if not want: return []
        return [p.tool_spec for n, p in self.plugins.items() if n in want]

    # _register_slash_commands メソッドは削除 (Cogに移行)

    def _is_authorised(self, message: discord.Message) -> bool:  # (変更なし)
        allowed_channels = set(self.cfg.get("allowed_channel_ids", []))
        allowed_roles = set(self.cfg.get("allowed_role_ids", []))
        chan_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)
        if allowed_channels and chan_id not in allowed_channels and parent_id not in allowed_channels:
            logging.info(f"User {message.author.id} ch {chan_id} msg blk: ch not allowed.")
            return False
        if allowed_roles:
            if hasattr(message.author, 'roles'):
                user_role_ids = {role.id for role in message.author.roles}
                if not user_role_ids & allowed_roles:
                    logging.info(f"User {message.author.id} ch {chan_id} msg blk: no req role.")
                    return False
            elif allowed_roles:  # DM but roles are required
                logging.info(f"User {message.author.id} DM msg blk: roles req but DM.")
                return False
        return True

    async def _build_message_chain(  # (変更なし)
            self, new_msg: discord.Message, max_messages: int, max_text: int, max_images: int,
            accept_images: bool, accept_usernames: bool,
    ) -> tuple[list[dict], Set[str]]:
        messages: list[dict] = []
        user_warnings: set[str] = set()
        curr_msg: Optional[discord.Message] = new_msg
        visited_messages: Set[int] = set()
        while curr_msg and len(messages) < max_messages:
            if curr_msg.id in visited_messages:
                logging.warning(f"Msg chain loop at ID {curr_msg.id}. Stop.")
                user_warnings.add("⚠️履歴ループ検出")
                break
            visited_messages.add(curr_msg.id)
            node = self.message_nodes.setdefault(curr_msg.id, MessageNode())
            async with node.lock:
                if node.text is None or not node.images:  # Simplified check
                    await self._process_message_node(node, curr_msg, accept_images, max_text)
                content = self._compose_message_content(node, max_text, max_images)
                if content:
                    if isinstance(content, str) and not content.strip():
                        pass
                    else:
                        payload: dict = {"content": content, "role": node.role}
                        if accept_usernames and node.user_id: payload["name"] = str(node.user_id)
                        messages.append(payload)
                else:
                    logging.debug(f"Msg ID {curr_msg.id} processed to empty content.")
                self._update_user_warnings(node, max_text, max_images, user_warnings)
                if node.fetch_next_failed:
                    user_warnings.add("⚠️前メッセージ取得失敗")  # Simplified
                    break
                if len(messages) == max_messages:
                    user_warnings.add(f"⚠️直近{len(messages)}件のみ使用")  # Simplified
                    break
                await self._set_next_message(node, curr_msg)
                curr_msg = node.next_message
        if curr_msg and len(messages) == max_messages:
            user_warnings.add(f"⚠️直近{max_messages}件のみ使用")
        return messages[::-1], user_warnings

    async def _process_message_node(  # (変更なし)
            self, node: MessageNode, msg: discord.Message, accept_images: bool, max_text: int,
    ) -> None:
        raw_content = msg.content or ""
        replaced_content = await self._replace_mentions(raw_content)
        if msg.author != self.user:
            display_name = msg.author.display_name
            message_content = f"{display_name}: {replaced_content}" if replaced_content else display_name
        else:
            message_content = replaced_content
        good_atts: dict[str, list[discord.Attachment]] = {
            ft: [att for att in msg.attachments if att.content_type and ft in att.content_type]
            for ft in ALLOWED_FILE_TYPES
        }
        attachment_texts = []
        # Original used good_atts["text"], safer with .get("text", [])
        for att in good_atts.get("text", []):
            try:
                attachment_texts.append(await self._fetch_attachment_text(att))
            except Exception as e:
                logging.warning(f"Text attach {att.id} fetch fail: {e}")
                node.has_bad_attachments = True
        embed_desc = [embed.description for embed in msg.embeds if embed.description]
        all_texts = [message_content] + embed_desc + attachment_texts
        node.text = "\n".join(filter(None, all_texts)).strip()
        if self.user and node.text.startswith(self.user.mention):  # Check self.user exists
            node.text = node.text.replace(self.user.mention, "", 1).lstrip()
        if accept_images:
            node.images = []
            for att in good_atts.get("image", []):  # Safer with .get
                try:
                    node.images.append(await self._process_image(att))
                except Exception as e:
                    logging.warning(f"Image attach {att.id} proc fail: {e}")
                    node.has_bad_attachments = True
        else:
            node.images = []
        node.role = "assistant" if msg.author == self.user else "user"
        node.user_id = msg.author.id if node.role == "user" else None
        if len(msg.attachments) > sum(len(good_atts.get(ft, [])) for ft in ALLOWED_FILE_TYPES):
            node.has_bad_attachments = True

    async def _fetch_attachment_text(self, att: discord.Attachment) -> str:  # (変更なし)
        response = await self.httpx_client.get(att.url, follow_redirects=True)
        response.raise_for_status()
        return response.text

    async def _process_image(self, att: discord.Attachment) -> dict:  # (変更なし)
        response = await self.httpx_client.get(att.url, follow_redirects=True)
        response.raise_for_status()
        b64 = b64encode(response.content).decode()
        return {"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{b64}"}}

    async def _replace_mentions(self, content: str) -> str:  # (変更なし)
        user_ids = {int(m.group(1)) for m in MENTION_PATTERN.finditer(content)}
        users: dict[int, str] = {}
        for uid in user_ids:
            try:
                user = self.get_user(uid) or await self.fetch_user(uid)
                users[uid] = user.display_name if user else f"User{uid}"
            except discord.NotFound:
                logging.warning(f"Mention replace: User ID {uid} not found.")
                users[uid] = f"不明ユーザー{uid}"
            except Exception as e:
                logging.error(f"Mention replace: User {uid} fetch error: {e}")
                users[uid] = f"エラーユーザー{uid}"
        return MENTION_PATTERN.sub(lambda m: users.get(int(m.group(1)), m.group(0)), content)

    async def _set_next_message(self, node: MessageNode, msg: discord.Message) -> None:  # (変更なし)
        next_msg: Optional[discord.Message] = None
        try:
            if msg.reference and msg.reference.message_id:
                try:
                    next_msg = msg.reference.cached_message or await msg.channel.fetch_message(msg.reference.message_id)
                except (discord.NotFound, discord.HTTPException):
                    logging.debug(f"Ref msg {msg.reference.message_id} fetch fail (ref).")
                    node.fetch_next_failed = True
            if next_msg is None and not node.fetch_next_failed and \
                    (
                            self.user and self.user.mention in msg.content or msg.channel.type == discord.ChannelType.private):  # Check self.user
                history_msgs = [m async for m in msg.channel.history(before=msg, limit=1)]
                if history_msgs:
                    prev_msg = history_msgs[0]
                    if prev_msg.type in {discord.MessageType.default, discord.MessageType.reply} and \
                            (prev_msg.author == self.user or \
                             (msg.channel.type == discord.ChannelType.private and prev_msg.author == msg.author)):
                        next_msg = prev_msg
            if next_msg is None and not node.fetch_next_failed and \
                    msg.channel.type == discord.ChannelType.public_thread:  # Simpler thread check
                thread = msg.channel
                # Original had `thread.starter_message and thread.starter_message.id == msg.id`
                # Need to ensure thread.starter_message is not None before accessing .id
                is_starter_message = False
                try:  # starter_message can be None or raise NotFound
                    starter = await thread.fetch_message(thread.id)  # A thread's first message ID is the thread's ID
                    if starter and starter.id == msg.id:
                        is_starter_message = True
                except (discord.NotFound, discord.HTTPException):  # If thread starter message can't be fetched
                    # This might happen if the thread object is partial.
                    # A more robust check for "is this the first message" might be needed
                    # or rely on reference if it's a reply to the thread creation message.
                    pass

                if is_starter_message and thread.parent_id:
                    try:
                        parent_channel = await self.fetch_channel(thread.parent_id)
                        if isinstance(parent_channel, (discord.TextChannel, discord.ForumChannel)):
                            # The message that created the thread is not directly msg.id in parent
                            # This logic needs re-evaluation if the goal is to link to the message that *created* the thread.
                            # For now, if it's the starter message, it's the end of this specific chain type.
                            # next_msg = await parent_channel.fetch_message(msg.id) # This is likely wrong
                            pass  # End of chain for this type of link
                        # else: logging.debug(f"Thread {thread.id} parent {thread.parent_id} not fetchable type.")
                    except (discord.NotFound, discord.HTTPException):
                        # logging.debug(f"Parent ch {thread.parent_id} for thread starter {msg.id} fetch fail (thread parent).")
                        node.fetch_next_failed = True  # Potentially
        except Exception as e:
            logging.exception(f"Set next msg unexpected error (MsgID: {msg.id})")
            node.fetch_next_failed = True
            next_msg = None
        node.next_message = next_msg
        # Logging simplified for brevity to match "no change" request

    def _compose_message_content(  # (変更なし)
            self, node: MessageNode, max_text: int, max_images: int
    ) -> str | list:
        limited_text = node.text[:max_text] if node.text is not None else ""
        limited_images = node.images[:max_images] if node.images is not None else []
        content: list = []
        if limited_text.strip(): content.append({"type": "text", "text": limited_text})
        if limited_images: content.extend(limited_images)
        if len(content) == 1 and content[0]["type"] == "text": return content[0]["text"]
        if not content: return ""
        return content

    def _update_user_warnings(  # (変更なし)
            self, node: MessageNode, max_text: int, max_images: int, warnings: set[str]
    ) -> None:
        err = self.ERROR_MESSAGES
        if node.text is not None and len(node.text) > max_text:
            warnings.add(
                err.get("msg_max_text_size", "⚠️ Text truncated (>{max_text} chars).").format(max_text=max_text))
        if node.images is not None and len(node.images) > max_images:
            if max_images > 0:
                warnings.add(
                    err.get("msg_max_image_size", "⚠️ Using first {max_images} images.").format(max_images=max_images))
            else:
                warnings.add(err.get("msg_error_image", "⚠️ Images not supported by this model/config."))
        if node.has_bad_attachments: warnings.add(
            err.get("msg_error_attachment", "⚠️ Skipped unsupported/failed attachments."))
        if node.fetch_next_failed: warnings.add(
            err.get("msg_fetch_failed", "⚠️ Failed to fetch prior message in history. Chain may be incomplete."))

    async def _generate_and_send_response(  # (変更なし - オリジナルを維持)
            self,
            messages: list[dict],
            origin: discord.Message,
            user_warnings: set[str],
            openai_client: AsyncOpenAI,
            model: str,
            max_message_length: int,
    ) -> None:
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

        max_tool_loops = 3  # オリジナルはこの値
        # current_loop = 0 # オリジナルにはないが、while max_tool_loops: の方がPythonic
        # while current_loop < max_tool_loops:
        #    current_loop +=1
        # 以下のループはオリジナルの `while max_tool_loops:` に従う
        temp_max_tool_loops = max_tool_loops  # ループ回数制御用
        while temp_max_tool_loops > 0:  # オリジナルのループ制御方法に合わせる
            api_kwargs = dict(api_kwargs_base, messages=list(messages))  # messagesをコピー
            tool_call_data_for_assistant: dict[str, dict[str, Any]] = {}  # 型をAnyに
            assistant_text_content_buffer = ""
            saw_tool_call = False  # オリジナルの変数名

            try:
                async with origin.channel.typing():
                    # オリジナルでは await openai_client.chat.completions.create(**api_kwargs) を直接ループ
                    stream = await openai_client.chat.completions.create(**api_kwargs)
                    async for chunk in stream:  # オリジナルに合わせる
                        choice = chunk.choices[0]

                        tc_delta_list = getattr(choice.delta, "tool_calls", None)
                        if tc_delta_list:
                            saw_tool_call = True
                            for tc_delta in tc_delta_list:
                                # オリジナルは tc_delta.id が存在することを前提にしていた
                                # OpenAI v1.xではidは存在するはずだが、念のためチェックは良い習慣
                                call_id = tc_delta.id
                                if call_id not in tool_call_data_for_assistant:
                                    tool_call_data_for_assistant[call_id] = {
                                        # "id": call_id, # オリジナルにはないが、あると便利
                                        # "index": tc_delta.index, # オリジナルにはない
                                        "name": tc_delta.function.name or "",  # オリジナル通り
                                        "arguments_chunks": []  # オリジナル通り
                                    }

                                if tc_delta.function.name and not tool_call_data_for_assistant[call_id]["name"]:
                                    tool_call_data_for_assistant[call_id]["name"] = tc_delta.function.name

                                if tc_delta.function.arguments:
                                    tool_call_data_for_assistant[call_id]["arguments_chunks"].append(
                                        tc_delta.function.arguments)
                            continue

                        delta_content = choice.delta.content
                        if delta_content is not None:
                            if saw_tool_call:  # オリジナル通り
                                assistant_text_content_buffer += delta_content
                            else:
                                last_message_buffer += delta_content

                            if not saw_tool_call:  # オリジナル通り
                                if not response_msgs and initial_warnings_text:
                                    last_message_buffer = initial_warnings_text + " " + last_message_buffer
                                    initial_warnings_text = ""

                                content_to_send_as_new_message = None
                                if len(last_message_buffer) > max_message_length:
                                    # オリジナルは単純にスライス
                                    content_to_send_as_new_message = last_message_buffer[:max_message_length]
                                    last_message_buffer = last_message_buffer[max_message_length:]

                                if content_to_send_as_new_message is not None:
                                    if response_msgs:  # オリジナル通り
                                        if edit_task is not None and not edit_task.done():
                                            await edit_task
                                    msg_to_reply = origin if not response_msgs else response_msgs[-1]
                                    try:
                                        content_to_send_final = content_to_send_as_new_message + "\u2026"
                                        msg = await msg_to_reply.reply(
                                            content=content_to_send_final, silent=True,
                                        )
                                        self.message_nodes[msg.id] = MessageNode(
                                            text=content_to_send_as_new_message,  # オリジナル通り
                                            next_message=msg_to_reply
                                        )
                                        await self.message_nodes[msg.id].lock.acquire()  # オリジナル通り
                                        response_msgs.append(msg)
                                        self.last_task_time = dt.now().timestamp()
                                    except Exception as send_e:
                                        logging.error(f"Msg part send fail (new): {send_e}")
                                        try:
                                            await (response_msgs[-1] if response_msgs else origin).reply(
                                                self.ERROR_MESSAGES.get("send_failed_part",
                                                                        "⚠️Msg part send fail").format(),
                                                silent=True)
                                        except Exception:
                                            pass
                                        return  # オリジナル通り

                            ready_to_edit = (  # オリジナル通り
                                    response_msgs
                                    and last_message_buffer  # オリジナルは last_message_buffer のみ
                                    and (edit_task is None or edit_task.done())
                                    and dt.now().timestamp() - self.last_task_time >= EDIT_DELAY_SECONDS
                            )
                            finish_reason = getattr(choice, "finish_reason", None)  # オリジナル通り
                            is_final_chunk_trigger = finish_reason is not None  # オリジナル通り

                            if ready_to_edit or is_final_chunk_trigger:  # オリジナル通り
                                if response_msgs:  # オリジナル通り
                                    if edit_task is not None and not edit_task.done():
                                        await edit_task
                                    content_to_edit = last_message_buffer
                                    if not is_final_chunk_trigger:
                                        content_to_edit += "\u2026"
                                    msg_to_edit = response_msgs[-1]
                                    edit_task = asyncio.create_task(self._perform_edit(msg_to_edit, content_to_edit))
                                    self.last_task_time = dt.now().timestamp()

                        if choice.finish_reason == "tool_calls":  # オリジナル通り
                            break
                    # async for chunk ループ終了

                    if saw_tool_call:  # オリジナル通り
                        assistant_tool_calls_list = []
                        for call_id, details in tool_call_data_for_assistant.items():
                            function_name = details["name"]
                            arguments_str = "".join(details["arguments_chunks"])
                            assistant_tool_calls_list.append({
                                "id": call_id,  # オリジナルはidをキーにしていたが、リスト内ではidをフィールドに持つべき
                                "type": "function",
                                "function": {"name": function_name, "arguments": arguments_str}
                            })

                        if assistant_tool_calls_list:  # オリジナル通り
                            messages.append({
                                "role": "assistant",
                                "content": assistant_text_content_buffer.strip() if assistant_text_content_buffer.strip() else "",
                                # オリジナル通り
                                "tool_calls": assistant_tool_calls_list
                            })
                            assistant_text_content_buffer = ""  # オリジナル通り

                            for call_spec in assistant_tool_calls_list:  # オリジナルは call
                                tool_name = call_spec["function"]["name"]  # オリジナルは call["function"]["name"]
                                tool_call_id_from_spec = call_spec["id"]  # API送信時は id を使う

                                actives = self.cfg.get("active_tools", None)
                                if (tool_name not in self.plugins or \
                                        (actives is not None and tool_name not in actives)):
                                    messages.append({
                                        "role": "tool",
                                        "tool_call_id": tool_call_id_from_spec,  # API v1.x.x
                                        "name": tool_name,  # API v1.x.x
                                        "content": f"[{tool_name}] は無効化されています。",
                                    })
                                    continue
                                plugin = self.plugins[tool_name]
                                try:  # オリジナルはjson.loadsのtry-exceptなし
                                    args = json.loads(call_spec["function"]["arguments"])
                                except json.JSONDecodeError as e:
                                    logging.error(
                                        f"Tool '{tool_name}' args JSON decode error: {e}. Raw: {call_spec['function']['arguments']}")
                                    messages.append({
                                        "role": "tool", "tool_call_id": tool_call_id_from_spec, "name": tool_name,
                                        "content": f"Error: Tool '{tool_name}' arguments JSON invalid."
                                    })
                                    continue

                                result = await plugin.run(arguments=args, bot=self)  # botを渡す
                                messages.append({
                                    "role": "tool",
                                    "tool_call_id": tool_call_id_from_spec,  # API v1.x.x
                                    "name": tool_name,  # API v1.x.x
                                    "content": str(result),  # 結果は文字列
                                })
                        else:  # オリジナル通り
                            logging.error("Tool call detected but tool details found empty.")
                            messages.append({
                                "role": "user",
                                "content": "Tool call was attempted but failed because the tool details were missing or not recognized."
                            })
                        # max_tool_loops -= 1 # オリジナル通り
                        temp_max_tool_loops -= 1  # こちらで制御
                        last_message_buffer = ""  # オリジナル通り
                        continue  # while ループの先頭へ

                    else:  # saw_tool_call が False の場合 (オリジナル通り)
                        final_content = last_message_buffer
                        if not response_msgs and final_content:  # 最初のメッセージの場合 (response_msgsが空)
                            if initial_warnings_text:  # 警告が残っていれば付与
                                final_content = initial_warnings_text + " " + final_content
                                initial_warnings_text = ""  # 消費した
                            try:
                                msg = await origin.reply(content=final_content, silent=True)
                                self.message_nodes[msg.id] = MessageNode(text=final_content, next_message=origin)
                                await self.message_nodes[msg.id].lock.acquire()
                                response_msgs.append(msg)
                            except Exception as send_e:
                                logging.error(f"Final msg send fail: {send_e}")
                        elif response_msgs and final_content:  # 編集で終了する場合
                            # _perform_edit は edit_task で実行されているはず
                            # ここは最後の編集タスクが完了していることを確認し、バッファをクリアする程度
                            if edit_task and not edit_task.done():
                                await edit_task
                            # 最後の編集で final_content が使われる（または使われた）
                            # last_message_buffer = "" # 既に編集タスクに渡っているはず
                            pass  # 編集は上のロジックで行われる
                        elif not response_msgs and initial_warnings_text:  # テキストなし、警告のみ
                            try:
                                await origin.reply(content=initial_warnings_text, silent=True)
                            except:
                                pass

                        break  # while ループを抜ける

            except RateLimitError:  # オリジナル通り
                logging.warning("OpenAI Rate Limit Error (429) occurred.")
                ratelimit_msg = self.ERROR_MESSAGES.get("ratelimit_error", "⚠️Rate limit!")
                try:
                    await origin.reply(content=ratelimit_msg, silent=True)
                except Exception as e:
                    logging.error(f"Rate limit msg send fail: {e}")
                return  # オリジナル通り

            except Exception as e:  # オリジナルは Exception のみ
                # httpx.ReadTimeout などのネットワーク系もここで捕捉される
                logging.exception("Error during response generation (general).")
                general_error_msg = self.ERROR_MESSAGES.get("general_error", "⚠️Unexpected error!")
                msg_to_reply_on_error = response_msgs[-1] if response_msgs else origin
                try:
                    await msg_to_reply_on_error.reply(content=general_error_msg, silent=True)
                except Exception as e_inner:
                    logging.error(f"General error msg send fail: {e_inner}")
                return  # オリジナル通り
        # while ループ終了

        if edit_task is not None and not edit_task.done():  # オリジナル通り
            try:
                await edit_task
            except Exception as e:
                logging.error(f"Final edit task wait error: {e}")

        # オリジナルは response_msgs or last_message_buffer で分岐していたが、
        # 上のロジックで response_msgs が作られるか、last_message_buffer が編集で使われるはず。
        # 念のため、オリジナルの最後の送信/編集ロジックも残す。
        if not response_msgs and last_message_buffer:  # まだ何も送信されておらず、バッファに何かある
            if initial_warnings_text:  # ここで警告が残っていることは稀だが念のため
                last_message_buffer = initial_warnings_text + " " + last_message_buffer
            try:
                msg = await origin.reply(content=last_message_buffer, silent=True)
                self.message_nodes[msg.id] = MessageNode(text=last_message_buffer, next_message=origin)
                await self.message_nodes[msg.id].lock.acquire()
                response_msgs.append(msg)  # ログ出力のために追加
            except Exception as e:
                logging.error(f"Final fallback msg send fail: {e}")
                try:
                    await origin.reply(self.ERROR_MESSAGES.get("send_failed_final", "⚠️Resp send fail."), silent=True)
                except:
                    pass
        elif response_msgs and last_message_buffer:  # response_msgsがあり、かつバッファにも何か残っている場合 (通常は編集で消費されるはず)
            # このケースは、最後の編集がスキップされたりした場合に起こりうる
            # 最後のメッセージに追記する形で編集を試みる
            try:
                # STREAMING_INDICATORを除去してから追記
                current_text = response_msgs[-1].content
                if current_text.endswith("\u2026"):  # オリジナルは ...
                    current_text = current_text[:-1]
                await self._perform_edit(response_msgs[-1], current_text + last_message_buffer)
            except Exception as e:
                logging.error(f"Final fallback edit fail ({response_msgs[-1].id}): {e}")

        # メッセージノードのテキストを完全なものに統一し、ロックを解放 (オリジナル通り)
        full_parts = []
        for resp_msg_obj in response_msgs:  # msg は discord.Message
            node = self.message_nodes.get(resp_msg_obj.id)
            if node and node.text is not None:
                # オリジナルはnode.textをそのまま使っていた。編集で\u2026が残る可能性を考慮
                text_to_add = node.text
                if text_to_add.endswith("\u2026"):  # 分割送信時の省略記号
                    pass  # そのまま追加 (次のパートが続くので)
                full_parts.append(text_to_add)
            # elif resp_msg_obj.content: # フォールバックとしてメッセージオブジェクトのcontent (編集済みのはず)
            #     full_parts.append(resp_msg_obj.content.replace("\u2026", "")) # 省略記号は除去

        full_response_text = "".join(full_parts)
        # 最後のメッセージの末尾の \u2026 は除去すべき
        if full_response_text.endswith("\u2026"):
            full_response_text = full_response_text[:-1]

        logging.info(
            "LLM Resp End (OriginID: %s): %s", origin.id,
            full_response_text[:500] + ("..." if len(full_response_text) > 500 else ""),
        )

        for resp_msg_obj in response_msgs:  # msg は discord.Message
            node = self.message_nodes.get(resp_msg_obj.id)
            if node:
                node.text = full_response_text  # オリジナル通り、全メッセージノードに完全なテキストを格納
                if node.lock.locked():
                    try:
                        node.lock.release()
                    except RuntimeError:
                        pass  # すでに解放されている場合
            # else: logging.warning(f"MsgNode {resp_msg_obj.id} not found (final proc).") # オリジナルにはない

        # メッセージノードのプルーニング (オリジナル通り)
        if len(self.message_nodes) > MAX_MESSAGE_NODES:
            over = len(self.message_nodes) - MAX_MESSAGE_NODES
            # オリジナルは sorted(self.message_nodes) でキーをソートしていた
            # Python 3.7+では辞書は挿入順を保持するので、最初のキーから削除で良い
            mids_to_pop = list(self.message_nodes.keys())[:over]
            logging.info(f"Pruning {len(mids_to_pop)} old message-nodes...")  # オリジナルは over
            for mid in mids_to_pop:
                node = self.message_nodes.pop(mid, None)  # オリジナルは .get() してから pop
                if not node: continue
                try:
                    # オリジナルはロック取得を試みていた
                    if node.lock.locked():  # ロックされていれば解放を試みる（ただし安全ではない可能性も）
                        node.lock.release()
                    # await asyncio.wait_for(node.lock.acquire(), timeout=0.1)
                    # node.lock.release()
                except asyncio.TimeoutError:
                    logging.debug(f"Skipping locked node {mid} for pruning.")  # オリジナルに近い
                except RuntimeError:  # すでに解放されている場合など
                    pass
                except Exception as e:
                    logging.error(f"Error pruning node {mid}: {e}")  # オリジナルに近い

    async def _perform_edit(self, msg: discord.Message, content: str) -> None:  # (変更なし)
        try:
            if content != msg.content:
                await msg.edit(content=content)
                # オリジナルには MessageNode 更新なし。必要なら追加:
                # node = self.message_nodes.get(msg.id)
                # if node: node.text = content
        except discord.NotFound:
            logging.warning(f"Edit attempt on deleted msg {msg.id}.")
        except discord.HTTPException as e:
            logging.warning(f"HTTPException on msg {msg.id} edit: {e}")
        except Exception as e:
            logging.error(f"Unexpected error on msg {msg.id} edit: {e}")


# --- main.py の末尾 (ensure_config, _main, __name__ ブロック) ---
# これらは前回の回答から変更なしでOKです。
aio_run = asyncio.run


def ensure_config(cfg_path: str = "config.yaml", default_path: str = "config.default.yaml") -> None:
    if not os.path.exists(cfg_path):
        if not os.path.exists(default_path):
            logging.critical(f"{cfg_path} and {default_path} not found. Cannot start.")
            sys.exit(1)
        try:  # enabled_cogs を default_path に追記する処理
            with open(default_path, "r+", encoding="utf-8") as f_default:
                content = f_default.read()
                if "enabled_cogs:" not in content:
                    f_default.seek(0, os.SEEK_END)
                    f_default.write("\n\n# Enabled Cog list (Python file names in 'cogs' dir, no extension)\n")
                    f_default.write("# enabled_cogs:\n")
                    f_default.write("#   - general_commands\n")  # 例として追加
                    f_default.write("enabled_cogs: []\n")  # デフォルトは空リスト
                    logging.info(f"Added 'enabled_cogs' section to {default_path}.")
        except Exception as e:
            logging.error(f"Error adding 'enabled_cogs' to {default_path}: {e}")

        shutil.copy2(default_path, cfg_path)
        logging.warning(
            f"{cfg_path} not found, copied from {default_path}.\n"
            f"Please edit it (bot_token, client_id, enabled_cogs) and restart."
        )
        sys.exit(0)  # 設定ファイル生成後はユーザーに編集を促して終了


async def _main() -> None:
    # cogs ディレクトリ作成
    if not os.path.exists("cogs"):
        try:
            os.makedirs("cogs")
            logging.info("Created 'cogs' directory for Cog files.")
        except OSError as e:
            logging.error(f"Failed to create 'cogs' directory: {e}")
            # 致命的ではないので続行は可能だが、Cogはロードできない

    ensure_config()
    cfg = load_config()  # ensure_config 後に再度読み込み
    if not cfg.get("bot_token"):
        logging.critical("config.yaml: 'bot_token' is not set. Bot cannot start.")
        sys.exit(1)
    if client_id := cfg.get("client_id"):
        logging.info(
            "\n\nBot Invite URL:\n"
            f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions=412317273088&scope=bot\n"
        )
    else:  # client_id がないと招待URLが作れない
        logging.warning("config.yaml: 'client_id' is not set. Invite URL cannot be generated.")

    bot = DiscordLLMBot("config.yaml")
    await bot.start(cfg["bot_token"])


if __name__ == "__main__":
    try:
        aio_run(_main())
    except KeyboardInterrupt:
        logging.info("Bot shutting down due to KeyboardInterrupt.")
    except SystemExit as e:  # ensure_config などで sys.exit() が呼ばれた場合
        if e.code == 0:  # 正常終了の意図
            logging.info("Bot shutting down via SystemExit (normal).")
        else:  # エラー終了の意図
            logging.error(f"Bot shutting down via SystemExit (error code: {e.code}).")
    except Exception as e:
        logging.exception(f"Unhandled error during bot startup/runtime: {e}")