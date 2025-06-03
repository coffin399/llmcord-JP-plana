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
from discord.ext import commands

import httpx
import yaml
import json
import os
import shutil
import sys

from openai import AsyncOpenAI, RateLimitError

from modules.llmcord_plana.plugins import load_plugins
import modules.llmcord_plana.error.PlanaError as error

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

VISION_MODEL_TAGS: Tuple[str, ...] = (
    "gpt-4o",
    "claude-3",
    "gemini",
    "pixtral",
    "llava",
    "vision",
)
PROVIDERS_SUPPORTING_USERNAMES: Tuple[str, ...] = ("openai", "x-ai")

ALLOWED_FILE_TYPES: Tuple[str, ...] = ("image", "text")
INVITE_URL = "https://discord.com/api/oauth2/authorize?client_id=1031673203774464160&permissions=412317273088&scope=bot"
SUPPORT_SERVER_INVITE_LINK = "https://discord.gg/SjuWKtwNAG"
ARONA_REPOSITORY = "https://github.com/coffin399/music-bot-arona"
PLANA_REPOSITORY = "https://github.com/coffin399/llmcord-JP-plana"

ALLOWED_CHANNEL_TYPES: Tuple[discord.ChannelType, ...] = (
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.private,
)

EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()

EDIT_DELAY_SECONDS = 1
MAX_MESSAGE_NODES = 100
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


def load_config(filename: str = "llmcord.config.yaml") -> dict:
    with open(filename, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def should_respond(bot_user: discord.User, message: discord.Message) -> bool:
    if message.channel.type not in ALLOWED_CHANNEL_TYPES:
        return False
    if message.channel.type != discord.ChannelType.private and bot_user not in message.mentions:
        return False
    if message.author.bot:
        return False
    return True


@dataclass
class MessageNode:
    text: Optional[str] = None
    images: List[dict] = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    next_message: Optional[discord.Message] = None
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


class LLMcord(commands.Cog):

    def __init__(self, bot: commands.Bot, cfg_path: str = "plana.config.yaml") -> None:
        self.bot = bot
        self.cfg_path = cfg_path

        self.cfg = load_config(cfg_path)

        self.message_nodes: dict[int, MessageNode] = {}
        self.last_task_time: Optional[float] = None

        self.httpx_client = httpx.AsyncClient()
        self.SYSTEM_PROMPT: str | None = self.cfg.get("system_prompt")
        self.STARTER_PROMPT: str | None = self.cfg.get("starter_prompt")
        self.ERROR_MESSAGES: dict[str, str] = self.cfg.get("error_msg", {}) or {}

        self.plugins = load_plugins(self.bot)
        logging.info("読み込まれたプラグイン: [%s]", ", ".join(p.__class__.__name__ for p in self.plugins.values()))
        logging.info("有効なツール: [%s]", ", ".join(spec["function"]["name"] for spec in self._enabled_tools()))

        self._register_slash_commands()

    @commands.Cog.listener(name="on_message")
    async def _on_message(self, message: discord.Message) -> None:
        if not should_respond(self.bot.user, message):
            return
        if not self._is_authorised(message):
            return

        provider_model = self.cfg.get("model", "")
        if not provider_model:
            logging.error("config.yaml に 'model' キーが見つかりません – 中止します。")
            return
        try:
            provider, model = provider_model.split("/", 1)
        except ValueError:
            logging.error(f"無効なモデル形式 '{provider_model}'")
            await message.reply("無効なモデル設定です。ボットの設定を確認してください。", silent=True)
            return

        provider_cfg = self.cfg["providers"].get(provider)
        if not provider_cfg:
            logging.error("config.yaml にプロバイダー '%s' が見つかりません", provider)
            await message.reply(f"プロバイダー '{provider}' が設定されていません。", silent=True)
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

        messages, user_warnings = await self._build_message_chain(
            message,
            max_messages,
            max_text,
            max_images,
            accept_images,
            accept_usernames,
        )

        server_name = message.guild.name if message.guild else "DM"
        logging.info(
            "[%s] ユーザー: %s (%s) | 添付: %d | 履歴: %d | 内容: %s",
            server_name,
            message.author.display_name,
            message.author.id,
            len(message.attachments),
            len(messages),
            message.content,
        )

        api_messages = []
        if self.SYSTEM_PROMPT:
            api_messages.append({"role": "system", "content": self.SYSTEM_PROMPT})
        if self.STARTER_PROMPT:
            api_messages.append({"role": "assistant", "content": self.STARTER_PROMPT})
        api_messages.extend(messages)

        await self._generate_and_send_response(
            api_messages,
            message,
            user_warnings,
            openai_client,
            model,
            max_message_length,
        )

    def _register_slash_commands(self) -> None:
        tree = self.bot.tree

        @tree.command(name="help", description="ヘルプメッセージを表示します")
        async def _help(inter: discord.Interaction):
            await inter.response.send_message(
                self.cfg.get("help_message", "ヘルプメッセージが設定されていません。"),
                ephemeral=False,
            )
        
    def _enabled_tools(self) -> list[dict]:
        want = self.cfg.get("active_tools", None)
        if want is None:
            return [p.tool_spec for p in self.plugins.values()]
        if not want:
            return []
        return [p.tool_spec for n, p in self.plugins.items() if n in want]

    def _is_authorised(self, message: discord.Message) -> bool:
        allowed_channels = set(self.cfg.get("allowed_channel_ids", []))
        allowed_roles = set(self.cfg.get("allowed_role_ids", []))

        chan_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)
        if allowed_channels and chan_id not in allowed_channels and parent_id not in allowed_channels:
            return False

        if allowed_roles and hasattr(message.author, "roles"):
            if not {r.id for r in message.author.roles} & allowed_roles:
                return False
        elif allowed_roles and message.channel.type == discord.ChannelType.private:
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
        messages: list[dict] = []
        user_warnings: set[str] = set()
        curr_msg: Optional[discord.Message] = new_msg

        visited_messages: Set[int] = set()

        while curr_msg and len(messages) < max_messages:
            if curr_msg.id in visited_messages:
                logging.warning(f"メッセージチェーンのメッセージ ID {curr_msg.id} でループを検出しました。停止します。")
                user_warnings.add("⚠️ 会話履歴にループを検出しました。ここで停止します。")
                break

            visited_messages.add(curr_msg.id)

            node = self.message_nodes.setdefault(curr_msg.id, MessageNode())
            async with node.lock:
                if node.text is None or not node.images:
                    await self._process_message_node(node, curr_msg, accept_images, max_text)

                content = self._compose_message_content(node, max_text, max_images)
                if content:
                    if isinstance(content, str) and not content.strip():
                        logging.debug(f"ID {curr_msg.id} からの空のテキストメッセージをスキップします")
                        pass
                    else:
                        payload: dict = {"content": content, "role": node.role}
                        if accept_usernames and node.user_id:
                            payload["name"] = str(node.user_id)
                        messages.append(payload)
                else:
                    logging.debug(f"メッセージ ID {curr_msg.id} は空のコンテンツとして処理されました。")

                self._update_user_warnings(node, max_text, max_images, user_warnings)

                if node.fetch_next_failed:
                    user_warnings.add(
                        f"⚠️ 会話チェーンの前のメッセージの取得に失敗しました。会話が不完全な可能性があります。"
                    )
                    break

                if len(messages) == max_messages:
                    user_warnings.add(
                        f"⚠️ 直近の {len(messages)} 件のメッセージのみを使用しています。"
                    )
                    break

                await self._set_next_message(node, curr_msg)
                curr_msg = node.next_message

        if curr_msg and len(messages) == max_messages:
            user_warnings.add(f"⚠️ 直近の {max_messages} 件のメッセージのみを使用しています。")

        return messages[::-1], user_warnings


    async def _process_message_node(
            self,
            node: MessageNode,
            msg: discord.Message,
            accept_images: bool,
            max_text: int,
    ) -> None:

        raw_content = msg.content or ""
        replaced_content = await self._replace_mentions(raw_content)

        if msg.author != self.bot.user:
            display_name = msg.author.display_name
            message_content = f"{display_name}: {replaced_content}" if replaced_content else display_name
        else:
            message_content = replaced_content

        good_atts: dict[str, list[discord.Attachment]] = {
            ft: [att for att in msg.attachments if att.content_type and ft in att.content_type]
            for ft in ALLOWED_FILE_TYPES
        }
        attachment_texts = []
        for att in good_atts["text"]:
            try:
                text = await self._fetch_attachment_text(att)
                attachment_texts.append(text)
            except Exception as e:
                logging.warning(f"テキスト添付ファイル {att.id} のフェッチに失敗しました: {e}")
                node.has_bad_attachments = True

        embed_desc = [embed.description for embed in msg.embeds if embed.description]

        all_texts = [message_content] + embed_desc + attachment_texts
        node.text = "\n".join(filter(None, all_texts)).strip()

        if node.text.startswith(self.bot.user.mention):
            node.text = node.text.replace(self.bot.user.mention, "", 1).lstrip()

        if accept_images:
            node.images = []
            for att in good_atts["image"]:
                try:
                    img_data = await self._process_image(att)
                    node.images.append(img_data)
                except Exception as e:
                    logging.warning(f"画像添付ファイル {att.id} の処理に失敗しました: {e}")
                    node.has_bad_attachments = True
        else:
            node.images = []

        node.role = "assistant" if msg.author == self.bot.user else "user"
        node.user_id = msg.author.id if node.role == "user" else None
        if len(msg.attachments) > sum(len(good_atts.get(ft, [])) for ft in ALLOWED_FILE_TYPES):
            node.has_bad_attachments = True

    async def _fetch_attachment_text(self, att: discord.Attachment) -> str:
        response = await self.httpx_client.get(att.url, follow_redirects=True)
        response.raise_for_status()
        return response.text

    async def _process_image(self, att: discord.Attachment) -> dict:
        response = await self.httpx_client.get(att.url, follow_redirects=True)
        response.raise_for_status()
        b64 = b64encode(response.content).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{att.content_type};base64,{b64}"},
        }

    async def _replace_mentions(self, content: str) -> str:
        user_ids = {int(m.group(1)) for m in MENTION_PATTERN.finditer(content)}
        users: dict[int, str] = {}
        for uid in user_ids:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                users[uid] = user.display_name if user else f"User{uid}"
            except discord.NotFound:
                logging.warning(f"メンション置換中にユーザー ID {uid} が見つかりませんでした。")
                users[uid] = f"不明なユーザー{uid}"
            except Exception as e:
                logging.error(f"メンション置換用のユーザー {uid} のフェッチ中にエラー: {e}")
                users[uid] = f"エラーユーザー{uid}"

        return MENTION_PATTERN.sub(lambda m: users.get(int(m.group(1)), m.group(0)), content)

    async def _set_next_message(self, node: MessageNode, msg: discord.Message) -> None:
        next_msg: Optional[discord.Message] = None
        try:
            if msg.reference and msg.reference.message_id:
                try:
                    next_msg = msg.reference.cached_message or await msg.channel.fetch_message(msg.reference.message_id)
                except (discord.NotFound, discord.HTTPException):
                    logging.debug(f"参照されたメッセージ {msg.reference.message_id} のフェッチに失敗しました (参照)。")

                    node.fetch_next_failed = True

            if next_msg is None and not node.fetch_next_failed and (
                    self.bot.user.mention in msg.content or msg.channel.type == discord.ChannelType.private):
                history_msgs = [m async for m in msg.channel.history(before=msg, limit=1)]
                if history_msgs:
                    prev_msg = history_msgs[0]
                    if prev_msg.type in {discord.MessageType.default, discord.MessageType.reply} and (
                            prev_msg.author == self.bot.user or (
                            msg.channel.type == discord.ChannelType.private and prev_msg.author == msg.author)
                    ):
                        next_msg = prev_msg

            if next_msg is None and not node.fetch_next_failed and msg.channel.type == discord.ChannelType.public_thread:
                thread = msg.channel
                if thread.starter_message and thread.starter_message.id == msg.id:
                    if thread.parent_id:
                        try:
                            parent_channel = await self.bot.fetch_channel(thread.parent_id)
                            if isinstance(parent_channel,
                                          (discord.TextChannel, discord.ForumChannel)):
                                next_msg = await parent_channel.fetch_message(msg.id)
                            else:
                                logging.debug(
                                    f"スレッド {thread.id} の親チャンネル {thread.parent_id} はフェッチ可能なタイプではありません。")
                        except (discord.NotFound, discord.HTTPException):
                            logging.debug(
                                f"親チャンネル {thread.parent_id} からスレッドスターターメッセージ ({msg.id}) のフェッチに失敗しました (スレッド親)。")
                            node.fetch_next_failed = True 

        except Exception as e:
            logging.exception(
                f"メッセージチェーンの次のメッセージ設定中に予期しないエラーが発生しました (メッセージID: {msg.id} / エラー: {e})")
            node.fetch_next_failed = True
            next_msg = None  

        node.next_message = next_msg

        if node.next_message:
            logging.debug(f"メッセージ ID {msg.id} は前のメッセージ ID {node.next_message.id} にリンクされました。")
        else:
            logging.debug(
                f"メッセージ ID {msg.id} はチェーンの終端です (参照なし、関連履歴なし、スレッドスターターではない)。")

    def _compose_message_content(
            self, node: MessageNode, max_text: int, max_images: int
    ) -> str | list:
        limited_text = node.text[:max_text] if node.text is not None else ""
        limited_images = node.images[:max_images] if node.images is not None else []

        content: list = []

        if limited_text.strip():
            content.append({"type": "text", "text": limited_text})

        if limited_images:
            content.extend(limited_images)

        if len(content) == 1 and content[0]["type"] == "text":
            return content[0]["text"]
        if not content:
            return ""
        return content

    def _update_user_warnings(
            self, node: MessageNode, max_text: int, max_images: int, warnings: set[str]
    ) -> None:
        err = self.ERROR_MESSAGES
        if node.text is not None and len(node.text) > max_text:
            warnings.add(
                err.get("msg_max_text_size", "⚠️ メッセージテキストが切り詰められました (>{max_text} 文字)。").format(
                    max_text=max_text))

        if node.images is not None and len(node.images) > max_images:
            if max_images > 0:
                warnings.add(
                    err.get("msg_max_image_size", "⚠️ 最初の {max_images} 件の画像のみを使用しています。").format(
                        max_images=max_images))
            else:
                warnings.add(err.get("msg_error_image", "⚠️ このモデルまたは設定では画像はサポートされていません。"))

        if node.has_bad_attachments:
            warnings.add(err.get("msg_error_attachment",
                                 "⚠️ サポートされていない添付ファイルをスキップしました、または添付ファイル (テキスト/画像) の処理に失敗しました。"))

        if node.fetch_next_failed:
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
                                result = await plugin.run(arguments=args, bot=self.bot)

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
        try:
            if content != msg.content:
                await msg.edit(content=content)
        except discord.NotFound:
            logging.warning(f"おそらく削除されたメッセージ {msg.id} を編集しようとしました。")
        except discord.HTTPException as e:
            logging.warning(f"メッセージ {msg.id} の編集中に HTTPException: {e}")
        except Exception as e:
            logging.error(f"メッセージ {msg.id} の編集中に予期しないエラー: {e}")
    
    @staticmethod
    def ensure_config(cfg_path: str = "plana.config.yaml",
                    default_path: str = "plana.config.default.yaml") -> bool:
        if os.path.exists(cfg_path):
            return True
        if not os.path.exists(default_path):
            raise error.PlanaDefaultConfigNotFound(
                f"{default_path} が見つかりません。"
            )
        shutil.copy2(default_path, cfg_path)
        logging.warning(
            f"{cfg_path} が無かったため {default_path} をコピーしました。\n"
            f"必要に応じて編集してから再度起動してください。"
        )
        return False

async def setup(bot: commands.Bot):
    if not LLMcord.ensure_config():
        raise error.PlanaFirstRunWarning("生成されたファイルを編集してから再度起動してください。")
    await bot.add_cog(LLMcord(bot))