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
from openai import AsyncOpenAI


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

ALLOWED_CHANNEL_TYPES: Tuple[discord.ChannelType, ...] = (
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.private,
)

EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
STREAMING_INDICATOR = "<:stream:1313474295372058758>"
EDIT_DELAY_SECONDS = 1
MAX_MESSAGE_NODES = 100
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")

def load_config(filename: str = "config.yaml") -> dict:
    """Load (or reload) the YAML configuration file."""
    with open(filename, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def should_respond(client_user: discord.User, message: discord.Message) -> bool:
    """Fast path – decide whether we even parse this message."""
    if message.channel.type not in ALLOWED_CHANNEL_TYPES:
        return False
    if message.channel.type != discord.ChannelType.private and client_user not in message.mentions:
        return False
    if message.author.bot:
        return False
    return True

@dataclass
class MessageNode:
    """Represents one vertex in the message chain."""

    text: Optional[str] = None
    images: List[dict] = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    next_message: Optional[discord.Message] = None
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

class DiscordLLMBot(discord.Client):
    """Discord bot that forwards conversation to an LLM."""

    cfg_path: str

    def __init__(self, cfg_path: str = "config.yaml") -> None:
        self.cfg_path = cfg_path
        self.cfg = load_config(cfg_path)

        intents = discord.Intents.default()
        intents.message_content = True

        activity = discord.CustomActivity(
            name=(self.cfg.get("status_message") or "github.com/jakobdylanc/llmcord")[:128]
        )
        super().__init__(intents=intents, activity=activity)

        self.tree = app_commands.CommandTree(self)
        self._register_slash_commands()

        self.message_nodes: dict[int, MessageNode] = {}
        self.last_task_time: Optional[float] = None
        self.httpx_client = httpx.AsyncClient()

        self.SYSTEM_PROMPT: str | None = self.cfg.get("system_prompt")
        self.STARTER_PROMPT: str | None = self.cfg.get("starter_prompt")
        self.ERROR_MESSAGES: dict[str, str] = self.cfg.get("error_msg", {})

    async def setup_hook(self) -> None:
        """Called once the client is connected; sync application commands."""
        await self.tree.sync()
        logging.info("Slash‑commands registered.")

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming traditional text messages (prefixed with @mention)."""
        if not should_respond(self.user, message):
            return
        if not self._is_authorised(message):
            return

        self.cfg = load_config(self.cfg_path)
        provider_model = self.cfg.get("model", "")
        if not provider_model:
            logging.error("No 'model' key found in config.yaml – aborting.")
            return

        provider, model = provider_model.split("/", 1)
        provider_cfg = self.cfg["providers"].get(provider)
        if not provider_cfg:
            logging.error("Provider '%s' missing in config.yaml – aborting.", provider)
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
        logging.info(
            "Message from %s (attachments=%d, conversation=%d): %s",
            message.author.id,
            len(message.attachments),
            len(messages),
            message.content,
        )

        system_prompt = {"role": "system", "content": self.SYSTEM_PROMPT}
        starter_message = {"role": "assistant", "content": self.STARTER_PROMPT}
        messages.insert(0, system_prompt)
        messages.insert(1, starter_message)

        await self._generate_and_send_response(
            messages,
            message,
            user_warnings,
            openai_client,
            model,
            max_message_length,
        )

    def _register_slash_commands(self) -> None:
        """Register slash commands on the local CommandTree."""

        @self.tree.command(name="help", description="Display the help message")
        async def _help(interaction: discord.Interaction) -> None:  # noqa: WPS430
            help_text = self.cfg.get("help_message", "Help message not configured.")
            await interaction.response.send_message(help_text, ephemeral=True)

    def _is_authorised(self, message: discord.Message) -> bool:
        """Check whether the author or channel is allowed to interact."""
        allowed_channels = set(self.cfg.get("allowed_channel_ids", []))
        allowed_roles = set(self.cfg.get("allowed_role_ids", []))

        chan_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)
        if allowed_channels and chan_id not in allowed_channels and parent_id not in allowed_channels:
            return False
        if allowed_roles:
            user_role_ids = {role.id for role in getattr(message.author, "roles", [])}
            if not user_role_ids & allowed_roles:
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
        """Walk backwards through the thread and gather context."""
        messages: list[dict] = []
        user_warnings: set[str] = set()
        curr_msg: Optional[discord.Message] = new_msg

        while curr_msg and len(messages) < max_messages:
            node = self.message_nodes.setdefault(curr_msg.id, MessageNode())
            async with node.lock:
                if node.text is None:
                    await self._process_message_node(node, curr_msg, accept_images, max_text)

                content = self._compose_message_content(node, max_text, max_images)
                if content:
                    payload: dict = {"content": content, "role": node.role}
                    if accept_usernames and node.user_id:
                        payload["name"] = str(node.user_id)
                    messages.append(payload)

                self._update_user_warnings(node, max_text, max_images, user_warnings)
                if node.fetch_next_failed or (
                    node.next_message is not None and len(messages) == max_messages
                ):
                    user_warnings.add(
                        f"⚠️ Only using last {len(messages)} message{'s' if len(messages)!=1 else ''}"
                    )
                curr_msg = node.next_message

        return messages[::-1], user_warnings

    async def _process_message_node(
        self,
        node: MessageNode,
        msg: discord.Message,
        accept_images: bool,
        max_text: int,
    ) -> None:
        """Parse a Discord message into a MessageNode."""

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
        attachment_texts = [await self._fetch_attachment_text(att) for att in good_atts["text"]]
        embed_desc = [embed.description for embed in msg.embeds if embed.description]

        node.text = "\n".join([message_content] + embed_desc + attachment_texts)

        if node.text.startswith(self.user.mention):
            node.text = node.text.replace(self.user.mention, "", 1).lstrip()
            if msg.author != self.user:
                node.text = f"{msg.author.display_name}: {node.text}"

        node.images = (
            [await self._process_image(att) for att in good_atts["image"]] if accept_images else []
        )
        node.role = "assistant" if msg.author == self.user else "user"
        node.user_id = msg.author.id if node.role == "user" else None
        node.has_bad_attachments = len(msg.attachments) > sum(len(v) for v in good_atts.values())
        await self._set_next_message(node, msg)

    async def _fetch_attachment_text(self, att: discord.Attachment) -> str:
        response = await self.httpx_client.get(att.url)
        return response.text

    async def _process_image(self, att: discord.Attachment) -> dict:
        response = await self.httpx_client.get(att.url)
        b64 = b64encode(response.content).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{att.content_type};base64,{b64}"},
        }

    async def _replace_mentions(self, content: str) -> str:
        user_ids = {int(m.group(1)) for m in MENTION_PATTERN.finditer(content)}
        users: dict[int, str] = {}
        for uid in user_ids:
            user = self.get_user(uid) or await self.fetch_user(uid)
            users[uid] = user.display_name if user else f"User{uid}"
        return MENTION_PATTERN.sub(lambda m: users[int(m.group(1))], content)

    async def _set_next_message(self, node: MessageNode, msg: discord.Message) -> None:
        try:
            if (
                msg.reference is None
                and self.user.mention not in msg.content
                and (
                    prev := [m async for m in msg.channel.history(before=msg, limit=1)]
                )
                and prev[0].type in {discord.MessageType.default, discord.MessageType.reply}
                and prev[0].author == (
                    self.user if msg.channel.type == discord.ChannelType.private else msg.author
                )
            ):
                node.next_message = prev[0]
            else:
                next_is_thread_parent = (
                    msg.reference is None and msg.channel.type == discord.ChannelType.public_thread
                )
                next_msg_id = (
                    msg.channel.id if next_is_thread_parent else getattr(msg.reference, "message_id", None)
                )
                if next_msg_id:
                    if next_is_thread_parent:
                        node.next_message = (
                            msg.channel.starter_message or await msg.channel.parent.fetch_message(next_msg_id)
                        )
                    else:
                        node.next_message = (
                            msg.reference.cached_message or await msg.channel.fetch_message(next_msg_id)
                        )
        except (discord.NotFound, discord.HTTPException, AttributeError):
            logging.exception("Failed to fetch the next message in chain.")
            node.fetch_next_failed = True

    def _compose_message_content(
        self, node: MessageNode, max_text: int, max_images: int
    ) -> str | list:
        if node.images[:max_images]:
            return (
                ([{"type": "text", "text": node.text[:max_text]}] if node.text[:max_text] else [])
                + node.images[:max_images]
            )
        return node.text[:max_text]

    def _update_user_warnings(
        self, node: MessageNode, max_text: int, max_images: int, warnings: set[str]
    ) -> None:
        err = self.ERROR_MESSAGES
        if len(node.text) > max_text:
            warnings.add(err.get("msg_max_text_size", "Text too long").format(max_text=max_text))
        if len(node.images) > max_images:
            if max_images:
                warnings.add(err.get("msg_max_image_size", "Too many images").format(max_images=max_images))
            else:
                warnings.add(err.get("msg_error_image", "Images not allowed"))
        if node.has_bad_attachments:
            warnings.add(err.get("msg_error_attachment", "Unsupported attachment"))

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
        response_chunks: list[str] = []
        prev_chunk = None
        edit_task: Optional[asyncio.Task] = None
        self.last_task_time = dt.now().timestamp()

        api_kwargs = dict(
            model=model,
            messages=messages,
            stream=True,
            extra_body=self.cfg.get("extra_api_parameters", {}),
        )

        try:
            async with origin.channel.typing():
                async for curr_chunk in await openai_client.chat.completions.create(**api_kwargs):
                    if not (hasattr(curr_chunk, "choices") and curr_chunk.choices):
                        continue

                    prev_content = prev_chunk.choices[0].delta.content if prev_chunk else ""
                    curr_content = curr_chunk.choices[0].delta.content or ""

                    if response_chunks or prev_content:
                        if (
                            not response_chunks
                            or len(response_chunks[-1] + prev_content) > max_message_length
                        ):
                            response_chunks.append("")
                            initial = prev_content + " " + " ".join(sorted(user_warnings))
                            msg = await (origin if not response_msgs else response_msgs[-1]).reply(
                                content=initial + "\u2026", silent=True
                            )
                            self.message_nodes[msg.id] = MessageNode(next_message=origin)
                            await self.message_nodes[msg.id].lock.acquire()
                            response_msgs.append(msg)
                            self.last_task_time = dt.now().timestamp()
                        response_chunks[-1] += prev_content

                        finish_reason = curr_chunk.choices[0].finish_reason
                        ready_to_edit = (
                            (edit_task is None or edit_task.done())
                            and dt.now().timestamp() - self.last_task_time >= EDIT_DELAY_SECONDS
                        )
                        msg_split_incoming = len(response_chunks[-1] + curr_content) > max_message_length
                        is_final_edit = finish_reason is not None or msg_split_incoming
                        if ready_to_edit or is_final_edit:
                            if edit_task is not None:
                                await edit_task
                            new_content = response_chunks[-1] if is_final_edit else response_chunks[-1] + "\u2026"
                            edit_task = asyncio.create_task(response_msgs[-1].edit(content=new_content))
                            self.last_task_time = dt.now().timestamp()

                    prev_chunk = curr_chunk
        except Exception:
            logging.exception("Error during response generation.")

        for msg in response_msgs:
            self.message_nodes[msg.id].text = "".join(response_chunks)
            if self.message_nodes[msg.id].lock.locked():
                self.message_nodes[msg.id].lock.release()

        if (n := len(self.message_nodes)) > MAX_MESSAGE_NODES:
            for mid in sorted(self.message_nodes)[: n - MAX_MESSAGE_NODES]:
                async with self.message_nodes.setdefault(mid, MessageNode()).lock:
                    self.message_nodes.pop(mid, None)


aio_run = asyncio.run

async def _main() -> None:
    cfg = load_config()
    if client_id := cfg.get("client_id"):
        logging.info(
            "\n\nBOT INVITE URL:\n"
            "https://discord.com/api/oauth2/authorize?client_id=%s&permissions=412317273088&scope=bot\n",
            client_id,
        )
    bot = DiscordLLMBot("config.yaml")
    await bot.start(cfg["bot_token"])

if __name__ == "__main__":
    aio_run(_main())
