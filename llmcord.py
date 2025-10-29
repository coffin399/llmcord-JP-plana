
import asyncio
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
import importlib.util
import sys
import json
from typing import Any, Literal, Optional, Dict, List

import discord
from discord.app_commands import Choice
from discord.ext import commands
from discord.ui import LayoutView, TextDisplay
import httpx
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageToolCall
import yaml

# --- Logging Setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
)

# --- Constants ---
VISION_MODEL_TAGS = ("claude", "gemini", "gemma", "gpt-4", "gpt-5", "grok-4", "llama", "llava", "mistral", "o3", "o4", "vision", "vl")
PROVIDERS_SUPPORTING_USERNAMES = ("openai", "x-ai")
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
STREAMING_INDICATOR = " ⚪"
EDIT_DELAY_SECONDS = 1.2
MAX_MESSAGE_NODES = 500

# --- Configuration ---
def get_config(filename: str = "config.yaml") -> dict[str, Any]:
    with open(filename, encoding="utf-8") as file:
        return yaml.safe_load(file)

# --- Data Classes ---
@dataclass
class MsgNode:
    text: Optional[str] = None
    images: list[dict[str, Any]] = field(default_factory=list)
    role: Literal["user", "assistant", "tool"] = "assistant"
    user_id: Optional[int] = None
    tool_calls: list = field(default_factory=list)
    tool_call_id: Optional[str] = None
    has_bad_attachments: bool = False
    fetch_parent_failed: bool = False
    parent_msg: Optional[discord.Message] = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

# --- Custom Bot Class ---
class CustomBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg = get_config()
        self.curr_model = next(iter(self.cfg["models"]))
        self.plugins: Dict[str, Any] = {}
        self.tools: List[Dict[str, Any]] = []
        self._load_plugins()

    def _load_plugins(self):
        plugins_dir = os.path.join(os.path.dirname(__file__), "plugins")
        if not os.path.isdir(plugins_dir):
            logging.warning(f"Plugins directory not found: {plugins_dir}")
            return

        for filename in os.listdir(plugins_dir):
            if filename.endswith(".py") and not filename.startswith("__"):
                plugin_name = filename[:-3]
                plugin_config = self.cfg.get("plugins", {}).get(plugin_name)

                if plugin_config and plugin_config.get("enabled", False):
                    try:
                        filepath = os.path.join(plugins_dir, filename)
                        spec = importlib.util.spec_from_file_location(plugin_name, filepath)
                        if not (spec and spec.loader):
                            raise ImportError(f"Could not get spec for plugin {plugin_name}")

                        module = importlib.util.module_from_spec(spec)
                        sys.modules[plugin_name] = module
                        spec.loader.exec_module(module)

                        class_name = "".join(s.capitalize() for s in plugin_name.split("_"))
                        plugin_class = getattr(module, class_name, None)

                        if plugin_class:
                            instance = plugin_class(self)
                            self.plugins[instance.name] = instance
                            if hasattr(instance, "tool_spec"):
                                self.tools.append(instance.tool_spec)
                            logging.info(f"Plugin '{instance.name}' loaded successfully.")
                        else:
                            logging.error(f"Plugin class '{class_name}' not found in '{filename}'.")
                    except Exception as e:
                        logging.exception(f"Failed to load plugin '{plugin_name}': {e}")

        if self.tools:
            logging.info(f"Enabled tools: {[tool['function']['name'] for tool in self.tools]}")

# --- Bot Initialization ---
intents = discord.Intents.default()
intents.message_content = True
activity = discord.CustomActivity(name=(get_config().get("status_message") or "github.com/jakobdylanc/llmcord")[:128])
discord_bot = CustomBot(intents=intents, activity=activity, command_prefix=None)

# --- Global State ---
httpx_client = httpx.AsyncClient()
msg_nodes: Dict[int, MsgNode] = {}
last_task_time = 0

# --- Discord Commands ---
@discord_bot.tree.command(name="model", description="View or switch the current model")
async def model_command(interaction: discord.Interaction, model: str) -> None:
    if model == discord_bot.curr_model:
        output = f"Current model: `{discord_bot.curr_model}`"
    else:
        if interaction.user.id in discord_bot.cfg["permissions"]["users"]["admin_ids"]:
            discord_bot.curr_model = model
            output = f"Model switched to: `{model}`"
            logging.info(output)
        else:
            output = "You don't have permission to change the model."
    await interaction.response.send_message(output, ephemeral=(interaction.channel.type == discord.ChannelType.private))

@model_command.autocomplete("model")
async def model_autocomplete(interaction: discord.Interaction, curr_str: str) -> list[Choice[str]]:
    discord_bot.cfg = await asyncio.to_thread(get_config)
    choices = [Choice(name=f"◉ {discord_bot.curr_model} (current)", value=discord_bot.curr_model)] if curr_str.lower() in discord_bot.curr_model.lower() else []
    choices += [
        Choice(name=f"○ {model}", value=model)
        for model in discord_bot.cfg["models"]
        if model != discord_bot.curr_model and curr_str.lower() in model.lower()
    ]
    return choices[:25]

# --- Discord Events ---
@discord_bot.event
async def on_ready() -> None:
    if client_id := discord_bot.cfg.get("client_id"):
        logging.info(f"\n\nBOT INVITE URL:\nhttps://discord.com/oauth2/authorize?client_id={client_id}&permissions=412317191168&scope=bot\n")
    await discord_bot.tree.sync()

@discord_bot.event
async def on_message(new_msg: discord.Message) -> None:
    global last_task_time
    if (not new_msg.channel.type == discord.ChannelType.private and discord_bot.user not in new_msg.mentions) or new_msg.author.bot:
        return

    # --- Permission Checks ---
    discord_bot.cfg = await asyncio.to_thread(get_config) # Hot-reload config
    if not has_permission(new_msg, discord_bot.cfg):
        return

    # --- Message Processing ---
    provider_slash_model = discord_bot.curr_model
    provider, model = provider_slash_model.removesuffix(":vision").split("/", 1)
    provider_config = discord_bot.cfg["providers"][provider]
    api_keys = [v for k, v in provider_config.items() if k.startswith("api_key") and v] or ["sk-no-key-required"]

    async with new_msg.channel.typing():
        try:
            messages, user_warnings = await build_message_chain(new_msg)
            
            last_exception = None
            for api_key in api_keys:
                try:
                    openai_client = AsyncOpenAI(base_url=provider_config["base_url"], api_key=api_key, http_client=httpx_client)
                    await handle_llm_interaction(new_msg, openai_client, model, messages, user_warnings)
                    last_exception = None
                    break # Success
                except Exception as e:
                    last_exception = e
                    logging.warning(f"API key failed for provider {provider}. Trying next key. Error: {e}")
            
            if last_exception:
                raise last_exception

        except Exception as e:
            logging.exception("Error during message processing")
            await new_msg.reply(f"An error occurred: {e}", silent=True)

def has_permission(new_msg: discord.Message, cfg: dict) -> bool:
    is_dm = new_msg.channel.type == discord.ChannelType.private
    permissions = cfg["permissions"]
    user_is_admin = new_msg.author.id in permissions["users"]["admin_ids"]

    if user_is_admin:
        return True

    if is_dm:
        return cfg.get("allow_dms", True) and new_msg.author.id not in permissions["users"]["blocked_ids"]

    role_ids = {role.id for role in getattr(new_msg.author, "roles", [])}
    channel_ids = {new_msg.channel.id, getattr(new_msg.channel, "parent_id", None), getattr(new_msg.channel, "category_id", None)}

    # User checks
    allowed_user_ids = permissions["users"]["allowed_ids"]
    is_good_user = not allowed_user_ids or new_msg.author.id in allowed_user_ids
    if not is_good_user or new_msg.author.id in permissions["users"]["blocked_ids"]:
        return False

    # Role checks
    allowed_role_ids = permissions["roles"]["allowed_ids"]
    is_good_role = not allowed_role_ids or any(id in allowed_role_ids for id in role_ids)
    if not is_good_role or any(id in permissions["roles"]["blocked_ids"] for id in role_ids):
        return False

    # Channel checks
    allowed_channel_ids = permissions["channels"]["allowed_ids"]
    is_good_channel = not allowed_channel_ids or any(id in allowed_channel_ids for id in channel_ids)
    if not is_good_channel or any(id in permissions["channels"]["blocked_ids"] for id in channel_ids):
        return False

    return True

async def build_message_chain(new_msg: discord.Message) -> tuple[list, set]:
    messages = []
    user_warnings = set()
    curr_msg = new_msg
    max_messages = discord_bot.cfg.get("max_messages", 25)

    while curr_msg and len(messages) < max_messages:
        node = msg_nodes.setdefault(curr_msg.id, MsgNode())
        async with node.lock:
            if node.text is None:
                await process_message_node(node, curr_msg)

            if message_content := format_message_content(node):
                message = {"role": node.role, "content": message_content}
                if node.role == "tool":
                    message["tool_call_id"] = node.tool_call_id
                if node.tool_calls:
                    message["tool_calls"] = node.tool_calls
                if discord_bot.curr_model.startswith("openai/") and node.role == "user":
                    message["name"] = str(node.user_id)
                messages.append(message)
            
            update_user_warnings(user_warnings, node, len(messages) == max_messages)
            curr_msg = node.parent_msg

    # Add system prompt
    if system_prompt := discord_bot.cfg.get("system_prompt"):
        now = datetime.now().astimezone()
        system_prompt = system_prompt.replace("{date}", now.strftime("%B %d %Y")).replace("{time}", now.strftime("%H:%M:%S %Z%z")).strip()
        if any(discord_bot.curr_model.lower().startswith(x) for x in PROVIDERS_SUPPORTING_USERNAMES):
            system_prompt += "\n\nUser's names are their Discord IDs and should be typed as '<@ID>'."
        messages.append({"role": "system", "content": system_prompt})

    logging.info(f"Message received (user ID: {new_msg.author.id}, attachments: {len(new_msg.attachments)}, conversation length: {len(messages)}):\n{new_msg.content}")
    return messages[::-1], user_warnings

async def process_message_node(node: MsgNode, msg: discord.Message):
    # Set role and user ID
    node.role = "assistant" if msg.author == discord_bot.user else "user"
    if node.role == "user":
        node.user_id = msg.author.id

    # Extract text and images
    cleaned_content = msg.content.removeprefix(discord_bot.user.mention).lstrip()
    good_attachments = [att for att in msg.attachments if att.content_type and ("text" in att.content_type or "image" in att.content_type)]
    node.has_bad_attachments = len(msg.attachments) > len(good_attachments)
    
    attachment_texts = []
    if good_attachments:
        responses = await asyncio.gather(*[httpx_client.get(att.url) for att in good_attachments])
        for att, resp in zip(good_attachments, responses):
            if "text" in att.content_type:
                attachment_texts.append(resp.text)
            elif "image" in att.content_type:
                node.images.append({"type": "image_url", "image_url": {"url": f"data:{att.content_type};base64,{b64encode(resp.content).decode('utf-8')}"}})

    node.text = "\n".join(
        ([cleaned_content] if cleaned_content else [])
        + [f"{embed.title}\n{embed.description}" for embed in msg.embeds if embed.description or embed.title]
        + attachment_texts
    )

    # Find parent message
    try:
        if msg.reference:
            node.parent_msg = msg.reference.cached_message or await msg.channel.fetch_message(msg.reference.message_id)
        elif (prev_msg_in_channel := [m async for m in msg.channel.history(before=msg, limit=1)]):
             if prev_msg_in_channel[0].author in (discord_bot.user, msg.author):
                node.parent_msg = prev_msg_in_channel[0]
    except (discord.NotFound, discord.HTTPException):
        node.fetch_parent_failed = True
        logging.warning(f"Could not fetch parent message for {msg.id}")

def format_message_content(node: MsgNode) -> Any:
    max_text = discord_bot.cfg.get("max_text", 100000)
    max_images = discord_bot.cfg.get("max_images", 5)
    
    text_content = node.text[:max_text] if node.text else ""
    
    if any(discord_bot.curr_model.lower().startswith(tag) for tag in VISION_MODEL_TAGS) and node.images:
        content_list = []
        if text_content:
            content_list.append({"type": "text", "text": text_content})
        content_list.extend(node.images[:max_images])
        return content_list
    return text_content

def update_user_warnings(warnings: set, node: MsgNode, limit_reached: bool):
    max_text = discord_bot.cfg.get("max_text", 100000)
    max_images = discord_bot.cfg.get("max_images", 5)

    if node.text and len(node.text) > max_text:
        warnings.add(f"⚠️ Max {max_text:,} characters per message")
    if len(node.images) > max_images:
        warnings.add(f"⚠️ Max {max_images} images per message")
    if node.has_bad_attachments:
        warnings.add("⚠️ Unsupported attachments")
    if node.fetch_parent_failed or limit_reached:
        warnings.add(f"⚠️ Conversation limit reached")

async def handle_llm_interaction(new_msg: discord.Message, openai_client: AsyncOpenAI, model: str, messages: list, user_warnings: set):
    global last_task_time
    response_msgs = []
    response_contents = []
    
    use_plain_responses = discord_bot.cfg.get("use_plain_responses", False)
    max_message_length = 2000 if use_plain_responses else 4096 - len(STREAMING_INDICATOR)
    
    embed = discord.Embed.from_dict(dict(fields=[dict(name=warning, value="", inline=False) for warning in sorted(user_warnings)]))

    async def reply_helper(**kwargs):
        nonlocal response_msgs
        target = new_msg if not response_msgs else response_msgs[-1]
        msg = await target.reply(**kwargs, silent=True)
        response_msgs.append(msg)
        msg_nodes[msg.id] = MsgNode(parent_msg=new_msg)
        await msg_nodes[msg.id].lock.acquire()

    # Main loop for tool calls and responses
    while True:
        stream = await openai_client.chat.completions.create(
            model=model,
            messages=messages,
            tools=discord_bot.tools or None,
            tool_choice="auto" if discord_bot.tools else None,
            stream=True,
        )

        # --- Stream Processing ---
        full_delta_content = ""
        collected_tool_calls: List[ChatCompletionMessageToolCall] = []
        
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta: continue

            if delta.content:
                full_delta_content += delta.content
                
                if use_plain_responses: continue # Plain responses are sent at the end

                # Start a new message if needed
                if not response_contents or len(response_contents[-1]) + len(delta.content) > max_message_length:
                    response_contents.append("")

                response_contents[-1] += delta.content
                
                time_delta = datetime.now().timestamp() - last_task_time
                if time_delta >= EDIT_DELAY_SECONDS:
                    embed.description = response_contents[-1] + STREAMING_INDICATOR
                    embed.color = EMBED_COLOR_INCOMPLETE
                    if len(response_contents) > len(response_msgs):
                        await reply_helper(embed=embed)
                    else:
                        await response_msgs[-1].edit(embed=embed)
                    last_task_time = datetime.now().timestamp()

            if delta.tool_calls:
                # Reconstruct tool calls from chunks
                for tc_chunk in delta.tool_calls:
                    if tc_chunk.index >= len(collected_tool_calls):
                        collected_tool_calls.append(ChatCompletionMessageToolCall(id="", function={"arguments": "", "name": ""}, type="function"))
                    
                    if tc_chunk.id: collected_tool_calls[tc_chunk.index].id = tc_chunk.id
                    if tc_chunk.function.name: collected_tool_calls[tc_chunk.index].function.name = tc_chunk.function.name
                    if tc_chunk.function.arguments: collected_tool_calls[tc_chunk.index].function.arguments += tc_chunk.function.arguments

        # --- Post-Stream Processing ---
        if not use_plain_responses and response_msgs:
            final_description = "".join(response_contents)
            embed.description = final_description
            embed.color = EMBED_COLOR_COMPLETE
            await response_msgs[-1].edit(embed=embed)
            msg_nodes[response_msgs[-1].id].text = final_description

        if collected_tool_calls:
            # Add assistant's tool call request to history
            messages.append({"role": "assistant", "content": None, "tool_calls": [tc.model_dump() for tc in collected_tool_calls]})
            
            # Execute tools
            for tool_call in collected_tool_calls:
                function_name = tool_call.function.name
                try:
                    arguments = json.loads(tool_call.function.arguments)
                    logging.info(f"Executing tool '{function_name}' with args: {arguments}")
                    plugin = discord_bot.plugins.get(function_name)
                    if plugin:
                        output = await plugin.run(arguments=arguments, bot=discord_bot, channel_id=new_msg.channel.id)
                    else:
                        output = f"Error: Tool '{function_name}' not found."
                    
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": function_name, "content": str(output)})
                except Exception as e:
                    logging.exception(f"Error executing tool {function_name}")
                    messages.append({"role": "tool", "tool_call_id": tool_call.id, "name": function_name, "content": f"Error: {e}"})
            continue # Go back to the LLM with tool results
        
        # If no tool calls, we're done
        if use_plain_responses:
            for content in response_contents:
                await reply_helper(view=LayoutView().add_item(TextDisplay(content=content)))
        
        for msg in response_msgs:
            if msg.id in msg_nodes:
                msg_nodes[msg.id].lock.release()
        break

# --- Main Execution ---
async def main():
    try:
        await discord_bot.start(discord_bot.cfg["bot_token"])
    except (discord.LoginFailure, httpx.ConnectError) as e:
        logging.critical(f"Failed to start bot: {e}")
    finally:
        await httpx_client.aclose()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot shutdown requested.")

