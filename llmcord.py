import asyncio
import logging
import sqlite3
import json
import re
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime as dt
from typing import Literal, Optional

import discord
import httpx
import yaml
from openai import AsyncOpenAI

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# Constants
VISION_MODEL_TAGS = ("gpt-4o", "claude-3", "gemini", "pixtral", "llava", "vision")
PROVIDERS_SUPPORTING_USERNAMES = ("openai", "x-ai")
ALLOWED_FILE_TYPES = ("image", "text")
ALLOWED_CHANNEL_TYPES = (
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.private,
)
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
#STREAMING_INDICATOR = " ⚪"
STREAMING_INDICATOR = " <:stream:1313474295372058758>"
EDIT_DELAY_SECONDS = 1
MAX_MESSAGE_NODES = 100
MENTION_PATTERN = re.compile(r'<@!?(\d+)>')


def load_config(filename="config.yaml"):
    """Load configuration from a YAML file."""
    with open(filename, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


@dataclass
class MessageNode:
    """Represents a node in the message chain."""
    text: Optional[str] = None
    images: list = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    next_message: Optional[discord.Message] = None
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DiscordLLMBot(discord.Client):
    """Discord bot that interacts with LLMs via OpenAI's API."""

    def __init__(self, cfg):
        intents = discord.Intents.default()
        intents.message_content = True
        activity = discord.CustomActivity(
            name=(cfg.get("status_message") or "github.com/jakobdylanc/llmcord")[:128]
        )
        super().__init__(intents=intents, activity=activity)

        self.cfg = cfg
        self.message_nodes = {}
        self.last_task_time = None
        self.httpx_client = httpx.AsyncClient()
        self.SYSTEM_PROMPT = cfg.get("system_prompt")
        self.STARTER_PROMPT = cfg.get("starter_prompt")
        self.BIO_RECORD_MESSAGE = cfg.get("bio_record.message")
        self.ERROR_MESSAGES = cfg.get("error_msg")

        self.conn = sqlite3.connect('bios.db')
        self.cursor = self.conn.cursor()
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bios (
                user_id INTEGER,
                bio_text TEXT
            )
        ''')
        self.conn.commit()

    async def on_message(self, message):
        """Handle incoming messages."""
        if not self.is_valid_message(message):
            return
        if not self.is_authorized_user(message):
            return

        self.cfg = load_config()

        provider_model = self.cfg.get("model", "")
        if not provider_model:
            logging.error("No model specified in the configuration.")
            return

        provider, model = provider_model.split("/", 1)
        provider_cfg = self.cfg["providers"].get(provider)
        if not provider_cfg:
            logging.error(f"No configuration found for provider '{provider}'.")
            return

        base_url = provider_cfg.get("base_url")
        api_key = provider_cfg.get("api_key", "sk-no-key-required")
        openai_client = AsyncOpenAI(base_url=base_url, api_key=api_key)

        accept_images = any(tag in model for tag in VISION_MODEL_TAGS)
        accept_usernames = provider in PROVIDERS_SUPPORTING_USERNAMES

        max_text = self.cfg.get("max_text", 5000)
        max_images = self.cfg.get("max_images", 0) if accept_images else 0
        max_messages = self.cfg.get("max_messages", 5)
        max_message_length = 2000

        messages, user_warnings, user_ids = await self.build_message_chain(
            message,
            max_messages,
            max_text,
            max_images,
            accept_images,
            accept_usernames,
        )

        user_bios = {}
        for uid in user_ids:
            self.cursor.execute('SELECT bio_text FROM bios WHERE user_id = ?', (uid,))
            bios = self.cursor.fetchall()
            bios_texts = [bio[0] for bio in bios]
            if bios_texts:
                user_bios[uid] = bios_texts

        bios_content = "## User Bios:\n"
        for uid, bios in user_bios.items():
            user = self.get_user(uid) or await self.fetch_user(uid)
            display_name = user.display_name if user else f"User {uid}"
            bios_content += f"- {display_name}:\n"
            for bio in bios:
                bios_content += f"  - {bio}\n"

        logging.info(
            f"Message received (user ID: {message.author.id}, attachments: {len(message.attachments)}, conversation length: {len(messages)}):\n{message.content}"
        )

        full_system_prompt = {"role": "system", "content": self.SYSTEM_PROMPT}
        starter_message = {"role": "assistant", "content": f"{bios_content} \n {self.STARTER_PROMPT}"}
        messages.insert(0, full_system_prompt)
        messages.insert(1, starter_message)


        await self.generate_and_send_response(
            messages,
            message,
            user_warnings,
            openai_client,
            model,
            max_message_length,
        )

    def is_valid_message(self, message):
        """Check if the message should be processed."""
        if message.channel.type not in ALLOWED_CHANNEL_TYPES:
            return False
        if message.channel.type != discord.ChannelType.private and self.user not in message.mentions:
            return False
        if message.author.bot:
            return False
        return True

    def is_authorized_user(self, message):
        """Check if the message author is authorized."""
        allowed_channel_ids = self.cfg.get("allowed_channel_ids", [])
        allowed_role_ids = self.cfg.get("allowed_role_ids", [])
        channel_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)
        if allowed_channel_ids and channel_id not in allowed_channel_ids and parent_id not in allowed_channel_ids:
            return False
        if allowed_role_ids:
            roles = getattr(message.author, "roles", [])
            role_ids = [role.id for role in roles]
            if not any(role_id in allowed_role_ids for role_id in role_ids):
                return False
        return True

    async def build_message_chain(self, new_msg, max_messages, max_text, max_images, accept_images, accept_usernames):
        """Build the message chain for the conversation."""
        messages = []
        user_warnings = set()
        user_ids = set()
        curr_msg = new_msg

        # @TODO 文中の<@8491851898401~~~>(メンション)を実際のユーザー名に置き換える。

        while curr_msg is not None and len(messages) < max_messages:
            curr_node = self.message_nodes.setdefault(curr_msg.id, MessageNode())
            async with curr_node.lock:
                if curr_node.text is None:
                    await self.process_message_node(curr_node, curr_msg, accept_images, max_text)
                content = self.get_message_content(curr_node, max_text, max_images)
                if content != "":
                    message = {"content": content, "role": curr_node.role}
                    if accept_usernames and curr_node.user_id is not None:
                        message["name"] = str(curr_node.user_id)
                    messages.append(message)
                self.update_user_warnings(curr_node, max_text, max_images, user_warnings)
                if curr_node.fetch_next_failed or (curr_node.next_message is not None and len(messages) == max_messages):
                    user_warnings.add(f"⚠️ Only using last {len(messages)} message{'s' if len(messages) != 1 else ''}")
                if curr_node.user_id:
                    user_ids.add(curr_node.user_id)
                curr_msg = curr_node.next_message

        return messages[::-1], user_warnings, user_ids  # Reverse messages to correct order

    async def process_message_node(self, curr_node, curr_msg, accept_images, max_text):
        """Process an individual message node."""
        good_attachments = {
            file_type: [att for att in curr_msg.attachments if att.content_type and file_type in att.content_type]
            for file_type in ALLOWED_FILE_TYPES
        }
        message_content = curr_msg.content or ""
        if curr_msg.author != self.user:
            display_name = curr_msg.author.display_name
            message_content = f"{display_name}: {message_content}" if message_content else display_name
        attachment_texts = [await self.fetch_attachment_text(att) for att in good_attachments["text"]]
        embed_descriptions = [embed.description for embed in curr_msg.embeds if embed.description]

        curr_node.text = "\n".join(
            [message_content] + embed_descriptions + attachment_texts
        )

        if curr_node.text.startswith(self.user.mention):
            curr_node.text = curr_node.text.replace(self.user.mention, "", 1).lstrip()
            if curr_msg.author != self.user:
                curr_node.text = f"{curr_msg.author.display_name}: {curr_node.text}"

        curr_node.images = [
            await self.process_image_attachment(att) for att in good_attachments["image"]
        ] if accept_images else []
        curr_node.role = "assistant" if curr_msg.author == self.user else "user"
        curr_node.user_id = curr_msg.author.id if curr_node.role == "user" else None
        curr_node.has_bad_attachments = len(curr_msg.attachments) > sum(
            len(att_list) for att_list in good_attachments.values())
        await self.set_next_message(curr_node, curr_msg)

    async def fetch_attachment_text(self, attachment):
        """Fetch text from an attachment."""
        response = await self.httpx_client.get(attachment.url)
        return response.text

    async def process_image_attachment(self, attachment):
        """Process an image attachment."""
        response = await self.httpx_client.get(attachment.url)
        base64_content = b64encode(response.content).decode('utf-8')
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:{attachment.content_type};base64,{base64_content}"
            }
        }

    async def set_next_message(self, curr_node, curr_msg):
        """Determine the next message in the conversation chain."""
        try:
            if (
                    curr_msg.reference is None
                    and self.user.mention not in curr_msg.content
                    and (prev_msgs := [msg async for msg in curr_msg.channel.history(before=curr_msg, limit=1)])
                    and prev_msgs[0].type in (discord.MessageType.default, discord.MessageType.reply)
                    and prev_msgs[0].author == (self.user if curr_msg.channel.type == discord.ChannelType.private else curr_msg.author)
            ):
                curr_node.next_message = prev_msgs[0]
            else:
                next_is_thread_parent = curr_msg.reference is None and curr_msg.channel.type == discord.ChannelType.public_thread
                next_msg_id = curr_msg.channel.id if next_is_thread_parent else getattr(curr_msg.reference, "message_id", None)

                if next_msg_id:
                    if next_is_thread_parent:
                        curr_node.next_message = curr_msg.channel.starter_message or await curr_msg.channel.parent.fetch_message(next_msg_id)
                    else:
                        curr_node.next_message = curr_msg.reference.cached_message or await curr_msg.channel.fetch_message(next_msg_id)

        except (discord.NotFound, discord.HTTPException, AttributeError):
            logging.exception("Error fetching next message in the chain")
            curr_node.fetch_next_failed = True

    def get_message_content(self, curr_node, max_text, max_images):
        """Assemble the content for the message."""
        if curr_node.images[:max_images]:
            content = (
                          [{"type": "text", "text": curr_node.text[:max_text]}] if curr_node.text[:max_text] else []
                      ) + curr_node.images[:max_images]
        else:
            content = curr_node.text[:max_text]
        return content

    def update_user_warnings(self, curr_node, max_text, max_images, user_warnings):
        """Update the set of user warnings based on message content."""

        messages = self.ERROR_MESSAGES

        if len(curr_node.text) > max_text:
            user_warnings.add(messages["msg_max_text_size"].format(max_text=max_text))
        if len(curr_node.images) > max_images:
            if max_images > 0:
                user_warnings.add(messages["msg_max_image_size"].format(max_images=max_images))
            else:
                user_warnings.add(messages["msg_error_image"])
        if curr_node.has_bad_attachments:
            user_warnings.add(messages["msg_error_attachment"])

    async def generate_and_send_response(
            self, messages, message, user_warnings, openai_client, model, max_message_length
    ):
        """Generate a response using OpenAI's API and send it to Discord as raw text."""
        response_msgs = []
        response_contents = []
        prev_chunk = None
        edit_task = None
        self.last_task_time = dt.now().timestamp()

        functions = [
            {
                "type": "function",
                "function": {
                    "name": "record_bio",
                    "description": "Record bio information for a user",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bio_text": {
                                "type": "string",
                                "description": "The bio text to record",
                            },
                        },
                        "required": ["bio_text"],
                    },
                }
            },
        ]

        kwargs = dict(
            model=model,
            messages=messages,
            stream=True,
            tools=functions,
            extra_body=self.cfg.get("extra_api_parameters", {}),
        )

        try:
            async with message.channel.typing():
                async for curr_chunk in await openai_client.chat.completions.create(**kwargs):
                    if hasattr(curr_chunk, 'choices') and len(curr_chunk.choices) > 0:
                        choice = curr_chunk.choices[0]
                        if choice.finish_reason == "tool_calls" and choice.delta.tool_calls:
                            function_name = choice.delta.tool_calls[0].function.name
                            function_args = choice.delta.tool_calls[0].function.arguments
                            if function_name == "record_bio":
                                try:
                                    args = json.loads(function_args)
                                    bio_text = args.get("bio_text", "").strip()
                                    user_id = message.author.id

                                    if bio_text:
                                        print(bio_text)
                                        self.cursor.execute(
                                            'INSERT INTO bios (user_id, bio_text) VALUES (?, ?)',
                                            (user_id, bio_text)
                                        )
                                        self.conn.commit()

                                        await message.reply(self.BIO_RECORD_MESSAGE, silent=True)
                                except json.JSONDecodeError:
                                    logging.error("Failed to decode function call arguments.")

                            continue

                        prev_content = (
                            prev_chunk.choices[0].delta.content if prev_chunk and prev_chunk.choices[
                                0].delta.content else ""
                        )
                        curr_content = curr_chunk.choices[0].delta.content or ""
                        if response_contents or prev_content:
                            if not response_contents or len(response_contents[-1] + prev_content) > max_message_length:
                                response_contents.append("")
                                content_to_send = prev_content + " " + " ".join(sorted(user_warnings))
                                response_msg = await (message if not response_msgs else response_msgs[-1]).reply(
                                    content=(content_to_send + "\u2026"), silent=True
                                )
                                self.message_nodes[response_msg.id] = MessageNode(next_message=message)
                                await self.message_nodes[response_msg.id].lock.acquire()
                                response_msgs.append(response_msg)
                                self.last_task_time = dt.now().timestamp()
                            response_contents[-1] += prev_content
                            finish_reason = curr_chunk.choices[0].finish_reason
                            ready_to_edit = (
                                    (edit_task is None or edit_task.done())
                                    and dt.now().timestamp() - self.last_task_time >= EDIT_DELAY_SECONDS
                            )
                            msg_split_incoming = len(response_contents[-1] + curr_content) > max_message_length
                            is_final_edit = finish_reason is not None or msg_split_incoming
                            if ready_to_edit or is_final_edit:
                                if edit_task is not None:
                                    await edit_task
                                new_content = (
                                    response_contents[-1] if is_final_edit else
                                    (response_contents[-1] + "\u2026")
                                )
                                edit_task = asyncio.create_task(response_msgs[-1].edit(content=new_content))
                                self.last_task_time = dt.now().timestamp()
                    prev_chunk = curr_chunk

            for content in response_contents:
                if content:
                    self.message_nodes[response_msg.id] = MessageNode(next_message=message)
                    await self.message_nodes[response_msg.id].lock.acquire()
                    response_msgs.append(response_msg)
        except Exception:
            logging.exception("Error while generating response")

        for msg in response_msgs:
            self.message_nodes[msg.id].text = "".join(response_contents)
            self.message_nodes[msg.id].lock.release()

        if (num_nodes := len(self.message_nodes)) > MAX_MESSAGE_NODES:
            for msg_id in sorted(self.message_nodes.keys())[: num_nodes - MAX_MESSAGE_NODES]:
                async with self.message_nodes.setdefault(msg_id, MessageNode()).lock:
                    self.message_nodes.pop(msg_id, None)


async def main():
    cfg = load_config()
    if client_id := cfg.get("client_id"):
        logging.info(
            f"\n\nBOT INVITE URL:\n"
            f"https://discord.com/api/oauth2/authorize?client_id={client_id}&permissions=412317273088&scope=bot\n"
        )
    bot = DiscordLLMBot(cfg)
    await bot.start(cfg["bot_token"])


if __name__ == "__main__":
    asyncio.run(main())
