# PLANA/llm/llmcog.py
from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
from typing import List, Dict, Any, Tuple, Optional

import aiohttp
import discord
import openai
from discord import app_commands
from discord.ext import commands

from PLANA.llm.error.errors import (
    LLMExceptionHandler,
    SearchAgentError,
    SearchAPIRateLimitError,
    SearchAPIServerError
)

try:
    from PLANA.llm.plugins.search_agent import SearchAgent
except ImportError:
    logging.error("Could not import SearchAgent. Search functionality will be disabled.")
    SearchAgent = None

try:
    from PLANA.llm.plugins.bio_manager import BioManager
except ImportError:
    logging.error("Could not import BioManager. Bio functionality will be disabled.")
    BioManager = None

try:
    from PLANA.llm.plugins.memory_manager import MemoryManager
except ImportError:
    logging.error("Could not import MemoryManager. Memory functionality will be disabled.")
    MemoryManager = None

try:
    import aiofiles
except ImportError:
    aiofiles = None
    logging.warning("aiofiles library not found. Channel model settings will be saved synchronously. "
                    "Install with: pip install aiofiles")

logger = logging.getLogger(__name__)

# Constants
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpeg', '.jpg', '.gif', '.webp')
IMAGE_URL_PATTERN = re.compile(
    r'https?://[^\s]+\.(?:' + '|'.join(ext.lstrip('.') for ext in SUPPORTED_IMAGE_EXTENSIONS) + r')(?:\?[^\s]*)?',
    re.IGNORECASE
)
DISCORD_MESSAGE_MAX_LENGTH = 1990


class LLMCog(commands.Cog, name="LLM"):
    """A cog for interacting with Large Language Models, with tool support."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not hasattr(self.bot, 'config') or not self.bot.config:
            raise commands.ExtensionFailed(self.qualified_name, "Bot config not loaded.")
        self.config = self.bot.config
        self.llm_config = self.config.get('llm')
        if not isinstance(self.llm_config, dict):
            raise commands.ExtensionFailed(self.qualified_name, "The 'llm' section in config is missing or invalid.")
        self.http_session = aiohttp.ClientSession()
        self.bot.cfg = self.llm_config
        self.conversation_threads: Dict[int, List[Dict[str, Any]]] = {}
        self.message_to_thread: Dict[int, int] = {}
        self.llm_clients: Dict[str, openai.AsyncOpenAI] = {}

        self.exception_handler = LLMExceptionHandler(self.llm_config)

        self.channel_settings_path = "data/channel_llm_models.json"
        self.channel_models: Dict[str, str] = self._load_json_data(self.channel_settings_path)
        logger.info(
            f"Loaded {len(self.channel_models)} channel-specific model settings from '{self.channel_settings_path}'.")

        # プラグインの初期化
        self.search_agent = self._initialize_search_agent()
        self.bio_manager = self._initialize_bio_manager()
        self.memory_manager = self._initialize_memory_manager()

        default_model_string = self.llm_config.get('model')
        if default_model_string:
            main_llm_client = self._initialize_llm_client(default_model_string)
            if main_llm_client:
                self.llm_clients[default_model_string] = main_llm_client
                logger.info(f"Default LLM client '{default_model_string}' initialized and cached.")
            else:
                logger.error("Failed to initialize main LLM client. Core functionality may be disabled.")
        else:
            logger.error("Default LLM model is not configured in config.yaml.")

    async def cog_unload(self):
        await self.http_session.close()
        logger.info("LLMCog's aiohttp session has been closed.")

    def _load_json_data(self, path: str) -> Dict[str, Any]:
        try:
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return {str(k): v for k, v in data.items()}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load JSON file '{path}': {e}")
        return {}

    async def _save_json_data(self, data: Dict[str, Any], path: str) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if aiofiles:
                async with aiofiles.open(path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(data, indent=4, ensure_ascii=False))
            else:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Failed to save JSON file '{path}': {e}")
            raise

    async def _save_channel_models(self) -> None:
        await self._save_json_data(self.channel_models, self.channel_settings_path)

    def _initialize_llm_client(self, model_string: Optional[str]) -> Optional[openai.AsyncOpenAI]:
        if not model_string or '/' not in model_string:
            logger.error(f"Invalid model format: '{model_string}'. Expected 'provider_name/model_name'.")
            return None
        try:
            provider_name, model_name = model_string.split('/', 1)
            provider_config = self.llm_config.get('providers', {}).get(provider_name)
            if not provider_config:
                logger.error(f"Configuration for LLM provider '{provider_name}' not found.")
                return None
            client = openai.AsyncOpenAI(base_url=provider_config.get('base_url'),
                                        api_key=provider_config.get('api_key') or "local-dummy-key")
            client.model_name_for_api_calls = model_name
            logger.info(f"Initialized LLM client for provider '{provider_name}' with model '{model_name}'.")
            return client
        except Exception as e:
            logger.error(f"Error initializing LLM client for '{model_string}': {e}", exc_info=True)
            return None

    async def _get_llm_client_for_channel(self, channel_id: int) -> Optional[openai.AsyncOpenAI]:
        channel_id_str = str(channel_id)
        model_string = self.channel_models.get(channel_id_str) or self.llm_config.get('model')
        if not model_string:
            logger.error("No default model is configured.")
            return None
        if model_string in self.llm_clients:
            return self.llm_clients[model_string]
        logger.info(f"Initializing a new LLM client for model '{model_string}' for channel {channel_id}")
        client = self._initialize_llm_client(model_string)
        if client:
            self.llm_clients[model_string] = client
        return client

    def _initialize_search_agent(self) -> Optional[SearchAgent]:
        if 'search' not in self.llm_config.get('active_tools', []) or not SearchAgent:
            return None
        search_config = self.llm_config.get('search_agent', {})
        if not search_config.get('api_key'):
            logger.error("SearchAgent config (api_key) is missing. Search will be disabled.")
            return None
        try:
            return SearchAgent(self.bot)
        except Exception as e:
            logger.error(f"Failed to initialize SearchAgent: {e}", exc_info=True)
            return None

    def _initialize_bio_manager(self) -> Optional[BioManager]:
        if not BioManager:
            return None
        try:
            return BioManager(self.bot)
        except Exception as e:
            logger.error(f"Failed to initialize BioManager: {e}", exc_info=True)
            return None

    def _initialize_memory_manager(self) -> Optional[MemoryManager]:
        if not MemoryManager:
            return None
        try:
            return MemoryManager(self.bot)
        except Exception as e:
            logger.error(f"Failed to initialize MemoryManager: {e}", exc_info=True)
            return None

    def get_tools_definition(self) -> Optional[List[Dict[str, Any]]]:
        definitions = []
        active_tools = self.llm_config.get('active_tools', [])

        if 'search' in active_tools and self.search_agent:
            definitions.append(self.search_agent.tool_spec)
        if 'user_bio' in active_tools and self.bio_manager:
            definitions.append(self.bio_manager.tool_spec)
        if 'memory' in active_tools and self.memory_manager:
            definitions.append(self.memory_manager.tool_spec)

        return definitions or None

    async def _get_conversation_thread_id(self, message: discord.Message) -> int:
        if message.id in self.message_to_thread:
            return self.message_to_thread[message.id]
        current_msg = message
        visited_ids = set()
        while current_msg.reference and current_msg.reference.message_id:
            if current_msg.id in visited_ids: break
            visited_ids.add(current_msg.id)
            try:
                parent_msg = current_msg.reference.resolved or await message.channel.fetch_message(
                    current_msg.reference.message_id)
                if parent_msg.author != self.bot.user: break
                current_msg = parent_msg
            except (discord.NotFound, discord.HTTPException):
                break
        thread_id = current_msg.id
        self.message_to_thread[message.id] = thread_id
        return thread_id

    async def _collect_conversation_history(self, message: discord.Message) -> List[Dict[str, Any]]:
        history = []
        current_msg = message
        visited_ids = set()
        while current_msg.reference and current_msg.reference.message_id:
            if current_msg.reference.message_id in visited_ids: break
            visited_ids.add(current_msg.reference.message_id)
            try:
                parent_msg = current_msg.reference.resolved or await message.channel.fetch_message(
                    current_msg.reference.message_id)
                if parent_msg.author != self.bot.user:
                    image_contents, text_content = await self._prepare_multimodal_content(parent_msg)
                    text_content = text_content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>',
                                                                                               '').strip()
                    if text_content or image_contents:
                        user_content_parts = [{"type": "text", "text": text_content}] if text_content else []
                        user_content_parts.extend(image_contents)
                        history.append({"role": "user", "content": user_content_parts})
                else:
                    thread_id = await self._get_conversation_thread_id(parent_msg)
                    if thread_id in self.conversation_threads:
                        for msg in self.conversation_threads[thread_id]:
                            if msg.get("role") == "assistant" and msg.get("message_id") == parent_msg.id:
                                history.append({"role": "assistant", "content": msg["content"]})
                                break
                current_msg = parent_msg
            except (discord.NotFound, discord.HTTPException):
                break
        history.reverse()
        max_history_entries = self.llm_config.get('max_messages', 10) * 2
        return history[-max_history_entries:] if len(history) > max_history_entries else history

    async def _process_image_url(self, url: str) -> Optional[Dict[str, Any]]:
        try:
            async with self.http_session.get(url) as response:
                if response.status == 200:
                    image_bytes = await response.read()
                    encoded_image = base64.b64encode(image_bytes).decode('utf-8')
                    mime_type = response.content_type
                    if not mime_type or not mime_type.startswith('image/'):
                        ext = url.split('.')[-1].lower().split('?')[0]
                        mime_type = f'image/{ext}' if ext in ('png', 'jpeg', 'gif', 'webp') else 'image/jpeg'
                    return {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"}}
                else:
                    logger.warning(f"Failed to download image from {url} (Status: {response.status})")
                    return None
        except Exception as e:
            logger.error(f"Error processing image URL {url}: {e}", exc_info=True)
            return None

    async def _prepare_multimodal_content(self, message: discord.Message) -> Tuple[List[Dict[str, Any]], str]:
        image_inputs, processed_urls = [], set()
        messages_to_scan = [message]
        if message.reference and isinstance(message.reference.resolved, discord.Message):
            messages_to_scan.append(message.reference.resolved)
        source_urls = []
        for msg in messages_to_scan:
            source_urls.extend(url for url in IMAGE_URL_PATTERN.findall(msg.content) if url not in processed_urls)
            processed_urls.update(source_urls)
            source_urls.extend(att.url for att in msg.attachments if att.content_type and att.content_type.startswith(
                'image/') and att.url not in processed_urls)
            processed_urls.update(att.url for att in msg.attachments)
        max_images = self.llm_config.get('max_images', 1)
        for url in source_urls[:max_images]:
            if image_data := await self._process_image_url(url):
                image_inputs.append(image_data)
        if len(source_urls) > max_images:
            logger.info(f"Reached max image limit ({max_images}). Ignoring {len(source_urls) - max_images} images.")
            try:
                error_msg_template = self.llm_config.get('error_msg', {}).get('msg_max_image_size',
                                                                              "⚠️ Max images ({max_images}) reached.")
                await message.channel.send(error_msg_template.format(max_images=max_images), delete_after=10,
                                           silent=True)
            except discord.HTTPException:
                pass
        clean_text = IMAGE_URL_PATTERN.sub('', message.content).strip()
        return image_inputs, clean_text

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return
        is_mentioned = self.bot.user.mentioned_in(message) and not message.mention_everyone
        is_reply_to_bot = (
                message.reference and
                isinstance(message.reference.resolved, discord.Message) and
                message.reference.resolved.author == self.bot.user
        )
        if not (is_mentioned or is_reply_to_bot):
            return
        if (
                allowed_channels := self.config.get('allowed_channel_ids',
                                                    [])) and message.channel.id not in allowed_channels:
            return
        try:
            llm_client = await self._get_llm_client_for_channel(message.channel.id)
            if not llm_client:
                error_msg = self.llm_config.get('error_msg', {}).get('general_error',
                                                                     "LLM client is not available for this channel.")
                await message.reply(error_msg, silent=True)
                return
        except Exception as e:
            logger.error(f"Failed to get LLM client for channel {message.channel.id}: {e}", exc_info=True)
            await message.reply(self.exception_handler.handle_exception(e), silent=True)
            return
        image_contents, text_content = await self._prepare_multimodal_content(message)
        text_content = text_content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()
        if not text_content and not image_contents:
            error_key = 'empty_reply' if is_reply_to_bot and not is_mentioned else 'empty_mention_reply'
            default_msg = "何かお話しください。" if error_key == 'empty_reply' else "はい、何か御用でしょうか？"
            await message.reply(self.llm_config.get('error_msg', {}).get(error_key, default_msg), silent=True)
            return
        guild_log = f"guild='{message.guild.name}({message.guild.id})'" if message.guild else "guild='DM'"
        channel_log = f"channel='{message.channel.name}({message.channel.id})'" if hasattr(message.channel,
                                                                                           'name') and message.channel.name else f"channel(id)={message.channel.id}"
        author_log = f"author='{message.author.name}({message.author.id})'"
        log_context = f"{guild_log}, {channel_log}, {author_log}"

        model_in_use = llm_client.model_name_for_api_calls
        logger.info(
            f"Received LLM request | {log_context} | model='{model_in_use}' | image_count={len(image_contents)} | text='{text_content[:150]}...' | is_reply={is_reply_to_bot}")

        thread_id = await self._get_conversation_thread_id(message)

        if not self.bio_manager or not self.memory_manager:
            await message.reply("必要なプラグインが初期化されていないため、応答できません。", silent=True)
            return

        system_prompt = self.bio_manager.get_system_prompt(
            channel_id=message.channel.id,
            user_id=message.author.id,
            user_display_name=message.author.display_name
        )

        if formatted_memories := self.memory_manager.get_formatted_memories():
            system_prompt += f"\n\n{formatted_memories}"

        messages_for_api: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages_for_api.extend(await self._collect_conversation_history(message))
        user_content_parts = [{"type": "text", "text": text_content}] if text_content else []
        user_content_parts.extend(image_contents)
        user_message_for_api = {"role": "user", "content": user_content_parts}
        messages_for_api.append(user_message_for_api)
        try:
            async with message.channel.typing():
                llm_response = await self._get_llm_response(messages_for_api, log_context, llm_client,
                                                            message.channel.id, message.author.id)
            if llm_response:
                logger.info(
                    f"Sending LLM response | {log_context} | model='{model_in_use}' | response='{llm_response.replace(chr(10), ' ')[:150]}...'")
                if thread_id not in self.conversation_threads: self.conversation_threads[thread_id] = []
                self.conversation_threads[thread_id].append(user_message_for_api)
                sent_message = await self._send_reply_chunks(message, llm_response)
                if sent_message:
                    assistant_message = {"role": "assistant", "content": llm_response, "message_id": sent_message.id}
                    self.conversation_threads[thread_id].append(assistant_message)
                    self.message_to_thread[sent_message.id] = thread_id
                self._cleanup_old_threads()
            else:
                logger.warning(f"Received empty response from LLM | {log_context}")
                await message.reply(self.llm_config.get('error_msg', {}).get('general_error',
                                                                             "Received an empty response from the AI."),
                                    silent=True)
        except Exception as e:
            await message.reply(self.exception_handler.handle_exception(e), silent=True)

    def _cleanup_old_threads(self):
        max_threads = 100
        if len(self.conversation_threads) > max_threads:
            threads_to_remove = list(self.conversation_threads.keys())[:len(self.conversation_threads) - max_threads]
            for thread_id in threads_to_remove:
                del self.conversation_threads[thread_id]
                self.message_to_thread = {k: v for k, v in self.message_to_thread.items() if v != thread_id}

    async def _get_llm_response(self, messages: List[Dict[str, Any]], log_context: str,
                                client: openai.AsyncOpenAI, channel_id: int, user_id: int) -> str:
        current_messages = messages.copy()
        max_iterations = self.llm_config.get('max_tool_iterations', 5)
        extra_params = self.llm_config.get('extra_api_parameters', {})
        for iteration in range(max_iterations):
            tools_def = self.get_tools_definition()
            api_kwargs = {
                "model": client.model_name_for_api_calls,
                "messages": current_messages,
                "temperature": extra_params.get('temperature', 0.7),
                "max_tokens": extra_params.get('max_tokens', 4096)
            }
            if tools_def:
                api_kwargs["tools"] = tools_def
                api_kwargs["tool_choice"] = "auto"
            try:
                response = await client.chat.completions.create(**api_kwargs)
                response_message = response.choices[0].message
                current_messages.append(response_message.model_dump(exclude_none=True))
                if response_message.tool_calls:
                    logger.info(
                        f"Processing {len(response_message.tool_calls)} tool call(s) in iteration {iteration + 1}")
                    await self._process_tool_calls(response_message.tool_calls, current_messages, log_context,
                                                   channel_id, user_id)
                    continue
                else:
                    return response_message.content or ""
            except Exception as e:
                logger.error(f"Error during LLM API call in iteration {iteration + 1}: {e}")
                raise
        logger.warning(f"Tool processing exceeded max iterations ({max_iterations})")
        return self.llm_config.get('error_msg', {}).get('tool_loop_timeout', "Tool processing exceeded max iterations.")

    async def _process_tool_calls(self, tool_calls: List[Any], messages: List[Dict[str, Any]],
                                  log_context: str, channel_id: int, user_id: int) -> None:
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            error_content = None
            tool_response_content = ""

            try:
                function_args = json.loads(tool_call.function.arguments)

                if self.search_agent and function_name == self.search_agent.name:
                    query_text = function_args.get('query', 'N/A')
                    logger.info(f"Executing SearchAgent | {log_context} | query='{query_text}'")
                    tool_response_content = await self.search_agent.run(arguments=function_args, bot=self.bot,
                                                                        channel_id=channel_id)
                    logger.info(
                        f"SearchAgent completed | {log_context} | result_length={len(str(tool_response_content))}")

                elif self.bio_manager and function_name == self.bio_manager.name:
                    tool_response_content = await self.bio_manager.run_tool(arguments=function_args, user_id=user_id)

                elif self.memory_manager and function_name == self.memory_manager.name:
                    tool_response_content = await self.memory_manager.run_tool(arguments=function_args)

                else:
                    logger.warning(f"Received a call for an unsupported tool: {function_name} | {log_context}")
                    error_content = f"Error: Tool '{function_name}' is not available."

            except json.JSONDecodeError as e:
                logger.error(f"Error decoding tool arguments for {function_name}: {e}", exc_info=True)
                error_content = f"Error: Invalid JSON arguments - {str(e)}"
            except SearchAPIRateLimitError as e:
                logger.warning(f"SearchAgent rate limit hit: {e}")
                error_content = "[Google Search Error]\nGoogle検索APIの利用制限に達しました。時間を置いてから再試行するようにユーザーに伝えてください。"
            except SearchAPIServerError as e:
                logger.error(f"SearchAgent server error: {e}")
                error_content = "[Google Search Error]\n検索サービスで一時的なサーバーエラーが発生しました。時間を置いてから再試行するようにユーザーに伝えてください。"
            except SearchAgentError as e:
                logger.error(f"Error during SearchAgent execution for {function_name}: {e}", exc_info=True)
                error_content = f"[Google Search Error]\n検索の実行中にエラーが発生しました: {str(e)}"
            except Exception as e:
                logger.error(f"Unexpected error during tool call for {function_name}: {e}", exc_info=True)
                error_content = f"[Tool Error]\n予期しないエラーが発生しました: {str(e)}"

            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": error_content if error_content else tool_response_content
            })

    async def _send_reply_chunks(self, message: discord.Message, text_content: str) -> Optional[discord.Message]:
        if not text_content: return None
        chunks = self._split_message(text_content, DISCORD_MESSAGE_MAX_LENGTH)
        first_chunk = chunks.pop(0)
        first_message = None
        try:
            first_message = await message.reply(first_chunk, silent=True)
        except discord.HTTPException as e:
            logger.warning(f"Failed to reply, falling back to sending message. Error: {e}")
            first_message = await message.channel.send(first_chunk, silent=True)
        for chunk in chunks:
            await message.channel.send(chunk, silent=True)
        return first_message

    def _split_message(self, text_content: str, max_length: int) -> List[str]:
        if not text_content: return []
        chunks, current_chunk = [], io.StringIO()
        for line in text_content.splitlines(keepends=True):
            if current_chunk.tell() + len(line) > max_length:
                if chunk_val := current_chunk.getvalue(): chunks.append(chunk_val)
                current_chunk = io.StringIO()
                while len(line) > max_length:
                    chunks.append(line[:max_length])
                    line = line[max_length:]
            current_chunk.write(line)
        if final_chunk := current_chunk.getvalue(): chunks.append(final_chunk)
        return chunks if chunks else [""]

    # --- ここからコマンド定義 ---

    # --- AIのbio (チャンネルごと) ---
    @app_commands.command(
        name="set-ai-bio",
        description="このチャンネルのAIの性格や役割(bio)を設定します。/ Set the AI's personality/role (bio) for this channel."
    )
    @app_commands.describe(
        bio="AIに設定したい性格や役割を記述してください。(例: あなたは猫です。語尾に「にゃん」をつけて話します。)"
    )
    async def set_ai_bio_slash(self, interaction: discord.Interaction, bio: str):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            await interaction.followup.send("❌ BioManagerが利用できません。", ephemeral=False)
            return

        if len(bio) > 1024:
            await interaction.followup.send("⚠️ AIのbioが長すぎます。1024文字以内で設定してください。", ephemeral=False)
            return

        try:
            await self.bio_manager.set_channel_bio(interaction.channel_id, bio)
            logger.info(f"AI bio for channel {interaction.channel_id} set by {interaction.user.name}")
            embed = discord.Embed(
                title="✅ AIのbioを設定しました",
                description=f"このチャンネルでのAIの役割が以下のように設定されました。\n\n**新しいAIのbio:**\n```\n{bio}\n```",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save channel AI bio settings: {e}", exc_info=True)
            await interaction.followup.send("❌ AIのbio設定の保存に失敗しました。", ephemeral=False)

    @app_commands.command(
        name="show-ai-bio",
        description="このチャンネルのAIに現在設定されているbioを表示します。/ Show the AI's current bio for this channel."
    )
    async def show_ai_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            await interaction.followup.send("❌ BioManagerが利用できません。", ephemeral=False)
            return

        current_bio = self.bio_manager.get_channel_bio(interaction.channel_id)
        if current_bio:
            title = "現在のAIのbio"
            description = f"このチャンネルでは、AIに以下の役割が設定されています。\n\n**AIのbio:**\n```\n{current_bio}\n```"
            color = discord.Color.blue()
        else:
            default_prompt = self.llm_config.get('system_prompt', "設定されていません。")
            title = "現在のAIのbio"
            description = f"このチャンネルには専用のAI bioが設定されていません。\nサーバーのデフォルト設定が使用されます。\n\n**デフォルト設定:**\n```\n{default_prompt}\n```"
            color = discord.Color.greyple()
        embed = discord.Embed(title=title, description=description, color=color)
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="reset-ai-bio",
        description="このチャンネルのAIのbioをデフォルト設定に戻します。/ Reset the AI's bio to default for this channel."
    )
    async def reset_ai_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            await interaction.followup.send("❌ BioManagerが利用できません。", ephemeral=False)
            return

        try:
            if await self.bio_manager.reset_channel_bio(interaction.channel_id):
                logger.info(f"AI bio for channel {interaction.channel_id} reset by {interaction.user.name}")
                default_prompt = self.llm_config.get('system_prompt', '未設定')
                await interaction.followup.send(
                    f"✅ このチャンネルのAIのbioをデフォルト設定に戻しました。\n> 現在のデフォルト: `{default_prompt}`",
                    ephemeral=False
                )
            else:
                await interaction.followup.send("ℹ️ このチャンネルには専用のAI bioが設定されていません。",
                                                ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save channel AI bio settings after reset: {e}", exc_info=True)
            await interaction.followup.send("❌ AIのbio設定の保存に失敗しました。", ephemeral=False)

    # --- ユーザーのbio (ユーザーごと) ---
    @app_commands.command(
        name="set-user-bio",
        description="AIにあなたの情報を記憶させます。/ Save your information for the AI to remember."
    )
    @app_commands.describe(
        bio="AIに覚えてほしいあなたの情報を記述してください。(例: 私の名前は田中です。趣味は読書です。)",
        mode="保存モードを選択してください。'上書き'または'追記'が可能です。"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="上書き (Overwrite)", value="overwrite"),
        app_commands.Choice(name="追記 (Append)", value="append"),
    ])
    async def set_user_bio_slash(self, interaction: discord.Interaction, bio: str, mode: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            await interaction.followup.send("❌ BioManagerが利用できません。", ephemeral=False)
            return

        if len(bio) > 1024:
            await interaction.followup.send("⚠️ ユーザー情報(bio)が長すぎます。1024文字以内で設定してください。",
                                            ephemeral=False)
            return

        try:
            await self.bio_manager.set_user_bio(interaction.user.id, bio, mode=mode.value)
            logger.info(
                f"User bio for {interaction.user.name} ({interaction.user.id}) was set with mode '{mode.value}'.")

            updated_bio = self.bio_manager.get_user_bio(interaction.user.id)

            embed = discord.Embed(
                title=f"✅ あなたの情報を記憶しました ({mode.name})",
                description=f"AIはあなたの情報を以下のように記憶しました。\n\n**あなたのbio:**\n```\n{updated_bio}\n```",
                color=discord.Color.green()
            )
            await interaction.followup.send(embed=embed, ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save user bio settings: {e}", exc_info=True)
            await interaction.followup.send("❌ あなたの情報の保存に失敗しました。", ephemeral=False)

    @app_commands.command(
        name="show-user-bio",
        description="AIが記憶しているあなたの情報を表示します。/ Show the information the AI has stored about you."
    )
    async def show_user_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            await interaction.followup.send("❌ BioManagerが利用できません。", ephemeral=False)
            return

        current_bio = self.bio_manager.get_user_bio(interaction.user.id)
        if current_bio:
            embed = discord.Embed(
                title=f"💡 {interaction.user.display_name}さんの情報",
                description=f"**bio:**\n```\n{current_bio}\n```",
                color=discord.Color.blue()
            )
        else:
            embed = discord.Embed(
                title=f"💡 {interaction.user.display_name}さんの情報",
                description="現在、あなたに関する情報は何も記憶されていません。\n`/set-user-bio` コマンドか、会話の中でAIに記憶を頼むことで設定できます。",
                color=discord.Color.greyple()
            )
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="reset-user-bio",
        description="AIが記憶しているあなたの情報をすべて削除します。/ Delete all information the AI has stored about you."
    )
    async def reset_user_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            await interaction.followup.send("❌ BioManagerが利用できません。", ephemeral=False)
            return

        try:
            if await self.bio_manager.reset_user_bio(interaction.user.id):
                logger.info(f"User bio for {interaction.user.name} ({interaction.user.id}) was reset.")
                await interaction.followup.send(
                    f"✅ {interaction.user.display_name}さんに関する情報をすべて削除しました。", ephemeral=False)
            else:
                await interaction.followup.send("ℹ️ あなたに関する情報は何も記憶されていません。", ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save user bio settings after reset: {e}", exc_info=True)
            await interaction.followup.send("❌ あなたの情報の削除に失敗しました。", ephemeral=False)

    # --- グローバル共有メモリ関連コマンド ---
    @app_commands.command(
        name="memory-save",
        description="グローバル共有メモリに情報を保存します。/ Save information to the global shared memory."
    )
    @app_commands.describe(
        key="情報のキー（項目名） 例: '開発者からのお知らせ'",
        value="情報の内容 例: '次回のメンテナンスは...'"
    )
    async def memory_save_slash(self, interaction: discord.Interaction, key: str, value: str):
        await interaction.response.defer(ephemeral=False)
        if not self.memory_manager:
            await interaction.followup.send("❌ MemoryManagerが利用できません。", ephemeral=False)
            return

        try:
            await self.memory_manager.save_memory(key, value)
            embed = discord.Embed(
                title="✅ グローバル共有メモリに保存しました",
                color=discord.Color.green()
            )
            embed.add_field(name="キー", value=f"```{key}```", inline=False)
            embed.add_field(name="値", value=f"```{value}```", inline=False)
            await interaction.followup.send(embed=embed, ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save global memory via command: {e}", exc_info=True)
            await interaction.followup.send("❌ グローバル共有メモリへの保存に失敗しました。", ephemeral=False)

    @app_commands.command(
        name="memory-list",
        description="グローバル共有メモリの情報を一覧表示します。/ List all global shared memories."
    )
    async def memory_list_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.memory_manager:
            await interaction.followup.send("❌ MemoryManagerが利用できません。", ephemeral=False)
            return

        memories = self.memory_manager.list_memories()
        if not memories:
            await interaction.followup.send("ℹ️ グローバル共有メモリには何も保存されていません。", ephemeral=False)
            return

        embed = discord.Embed(
            title="🌐 グローバル共有メモリ",
            color=discord.Color.blue()
        )
        description = ""
        for key, value in memories.items():
            field_text = f"**{key}**: {value}\n"
            if len(description) + len(field_text) > 4000:
                description += "\n... (表示制限のため一部省略)"
                break
            description += field_text

        embed.description = description
        await interaction.followup.send(embed=embed, ephemeral=False)

    async def memory_key_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        if not self.memory_manager:
            return []
        keys = self.memory_manager.list_memories().keys()
        return [
            app_commands.Choice(name=key, value=key)
            for key in keys if current.lower() in key.lower()
        ][:25]

    @app_commands.command(
        name="memory-delete",
        description="グローバル共有メモリから情報を削除します。/ Delete a global shared memory."
    )
    @app_commands.describe(key="削除したい情報のキー")
    @app_commands.autocomplete(key=memory_key_autocomplete)
    async def memory_delete_slash(self, interaction: discord.Interaction, key: str):
        await interaction.response.defer(ephemeral=False)
        if not self.memory_manager:
            await interaction.followup.send("❌ MemoryManagerが利用できません。", ephemeral=False)
            return

        try:
            if await self.memory_manager.delete_memory(key):
                await interaction.followup.send(f"✅ グローバル共有メモリからキー '{key}' を削除しました。",
                                                ephemeral=False)
            else:
                await interaction.followup.send(f"⚠️ キー '{key}' はグローバル共有メモリに存在しません。",
                                                ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to delete global memory via command: {e}", exc_info=True)
            await interaction.followup.send("❌ グローバル共有メモリからの削除に失敗しました。", ephemeral=False)

    # --- モデル切り替え関連コマンド ---
    async def model_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        available_models = self.llm_config.get('available_models', [])
        return [
            app_commands.Choice(name=model, value=model)
            for model in available_models if current.lower() in model.lower()
        ][:25]

    @app_commands.command(
        name="switch-models",
        description="このチャンネルで使用するAIモデルを切り替えます。/ Switches the AI model used for this channel."
    )
    @app_commands.describe(
        model="使用したいモデルを選択してください。"
    )
    @app_commands.autocomplete(model=model_autocomplete)
    async def switch_model_slash(self, interaction: discord.Interaction, model: str):
        await interaction.response.defer(ephemeral=False)
        available_models = self.llm_config.get('available_models', [])
        if model not in available_models:
            await interaction.followup.send(f"⚠️ 指定されたモデル '{model}' は利用できません。")
            return

        channel_id_str = str(interaction.channel_id)
        self.channel_models[channel_id_str] = model

        try:
            await self._save_channel_models()
            await self._get_llm_client_for_channel(interaction.channel_id)
            await interaction.followup.send(f"✅ このチャンネルのAIモデルが `{model}` に切り替えられました。",
                                            ephemeral=False)
            logger.info(f"Model for channel {interaction.channel_id} switched to '{model}' by {interaction.user.name}")
        except Exception as e:
            logger.error(f"Failed to save channel model settings: {e}", exc_info=True)
            await interaction.followup.send("❌ 設定の保存に失敗しました。")

    @app_commands.command(
        name="switch-models-default-server",
        description="このチャンネルのAIモデルをサーバーのデフォルト設定に戻します。/ Resets the AI model for this channel to the server default."
    )
    async def reset_model_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        channel_id_str = str(interaction.channel_id)

        if channel_id_str in self.channel_models:
            del self.channel_models[channel_id_str]
            try:
                await self._save_channel_models()
                default_model = self.llm_config.get('model', '未設定')
                await interaction.followup.send(
                    f"✅ このチャンネルのAIモデルをデフォルト (`{default_model}`) に戻しました。", ephemeral=False)
                logger.info(f"Model for channel {interaction.channel_id} reset to default by {interaction.user.name}")
            except Exception as e:
                logger.error(f"Failed to save channel model settings after reset: {e}", exc_info=True)
                await interaction.followup.send("❌ 設定の保存に失敗しました。")
        else:
            await interaction.followup.send("ℹ️ このチャンネルには専用のモデルが設定されていません。", ephemeral=False)

    @switch_model_slash.error
    async def switch_model_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Error in /switch-model command: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(f"予期せぬエラーが発生しました: {error}", ephemeral=False)
        else:
            await interaction.followup.send(f"予期せぬエラーが発生しました: {error}", ephemeral=False)

    # --- ヘルプと履歴クリア ---
    @app_commands.command(name="llm_help",
                          description="LLM (AI対話) 機能のヘルプと利用ガイドラインを表示します。/ Displays help and usage guidelines for LLM (AI Chat) features.")
    async def llm_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user or interaction.client.user
        bot_name = bot_user.name if bot_user else "当Bot"
        embed = discord.Embed(title=f"💡 {bot_name} AI対話機能ヘルプ＆ガイドライン",
                              description=f"{bot_name}のAI対話機能についての説明と利用規約です。",
                              color=discord.Color.purple())
        if bot_user and bot_user.avatar: embed.set_thumbnail(url=bot_user.avatar.url)
        embed.add_field(
            name="基本的な使い方",
            value=(
                f"• Botにメンション (`@{bot_name}`) して話しかけると、AIが応答します。\n"
                f"• **Botのメッセージに返信することでも会話を続けられます（メンション不要）。**\n"
                f"• 「私の名前は〇〇です。覚えておいて」のように話しかけると、AIがあなたの情報を記憶しようとします。\n"
                f"• 画像と一緒に話しかけると、AIが画像の内容も理解しようとします。"
            ),
            inline=False
        )
        embed.add_field(
            name="便利なコマンド",
            value=(
                "**【AIの設定 (チャンネルごと)】**\n"
                "• `/switch-models`: このチャンネルで使うAIモデルを変更します。\n"
                "• `/set-ai-bio`: このチャンネル専用のAIの性格や役割を設定します。\n"
                "• `/show-ai-bio`: 現在のAIのbio設定を確認します。\n"
                "• `/reset-ai-bio`: AIのbio設定をデフォルトに戻します。\n"
                "**【あなたの情報】**\n"
                "• `/set-user-bio`: AIに覚えてほしいあなたの情報を設定します。\n"
                "• `/show-user-bio`: AIが記憶しているあなたの情報を確認します。\n"
                "• `/reset-user-bio`: あなたの情報をAIの記憶から削除します。\n"
                "**【グローバルメモリ】**\n"
                "• `/memory-save`: 全サーバー共通のメモリに情報を保存します。\n"
                "• `/memory-list`: グローバルメモリの情報を一覧表示します。\n"
                "• `/memory-delete`: グローバルメモリから情報を削除します。\n"
                "**【その他】**\n"
                "• `/clear_history`: 会話履歴をリセットします。"
            ),
            inline=False
        )
        channel_model_str = self.channel_models.get(str(interaction.channel_id))
        model_display = f"`{channel_model_str}` (このチャンネル専用)" if channel_model_str else f"`{self.llm_config.get('model', '未設定')}` (デフォルト)"

        ai_bio_display = "N/A"
        user_bio_display = "N/A"
        if self.bio_manager:
            ai_bio_display = "✅ (専用設定あり)" if self.bio_manager.get_channel_bio(interaction.channel_id) else "デフォルト"
            user_bio_display = "✅ (記憶あり)" if self.bio_manager.get_user_bio(interaction.user.id) else "なし"

        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• なし" if not active_tools else "• " + ", ".join(active_tools)
        embed.add_field(name="現在のAI設定",
                        value=f"• **使用モデル:** {model_display}\n"
                              f"• **AIの役割(チャンネル):** {ai_bio_display} (詳細は `/show-ai-bio`)\n"
                              f"• **あなたの情報:** {user_bio_display} (詳細は `/show-user-bio`)\n"
                              f"• **会話履歴の最大保持数:** {self.llm_config.get('max_messages', '未設定')} ペア\n"
                              f"• **一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', '未設定')} 枚\n"
                              f"• **利用可能なツール:** {tools_info}",
                        inline=False)
        embed.add_field(name="--- 📜 AI利用ガイドライン ---",
                        value="AI機能を安全にご利用いただくため、以下の内容を必ずご確認ください。", inline=False)
        embed.add_field(name="⚠️ 2. データ入力時の注意", value=(
            "AIに記憶させる情報には、氏名、連絡先、パスワードなどの**個人情報や秘密情報を絶対に含めないでください。**"),
                        inline=False)
        embed.add_field(name="✅ 3. 生成物利用時の注意", value=(
            "AIの応答には虚偽や偏見が含まれる可能性があります。**必ずファクトチェックを行い、自己の責任で利用してください。**"),
                        inline=False)
        embed.set_footer(text="ガイドラインは予告なく変更される場合があります。")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="llm_help_en",
                          description="Displays help and usage guidelines for LLM (AI Chat) features.")
    async def llm_help_en_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user or interaction.client.user
        bot_name = bot_user.name if bot_user else "This Bot"
        embed = discord.Embed(title=f"💡 {bot_name} AI Chat Help & Guidelines",
                              description=f"Explanation and terms of use for the AI chat features of {bot_name}.",
                              color=discord.Color.purple())
        if bot_user and bot_user.avatar: embed.set_thumbnail(url=bot_user.avatar.url)
        embed.add_field(
            name="Basic Usage",
            value=(
                f"• Mention the bot (`@{bot_name}`) to get a response from the AI.\n"
                f"• **You can also continue the conversation by replying to the bot's messages (no mention needed).**\n"
                f"• If you ask the AI to remember something (e.g., 'My name is John, please remember it'), it will try to store that information.\n"
                f"• Attach images or paste image URLs with your message, and the AI will try to understand them."
            ),
            inline=False
        )
        embed.add_field(
            name="Useful Commands",
            value=(
                "**[AI Settings (Per Channel)]**\n"
                "• `/switch-models`: Change the AI model used in this channel.\n"
                "• `/set-ai-bio`: Set a custom personality/role for the AI in this channel.\n"
                "• `/show-ai-bio`: Check the current AI bio setting.\n"
                "• `/reset-ai-bio`: Reset the AI bio to the default.\n"
                "**[Your Information]**\n"
                "• `/set-user-bio`: Set information about you for the AI to remember.\n"
                "• `/show-user-bio`: Check the information the AI has stored about you.\n"
                "• `/reset-user-bio`: Delete your information from the AI's memory.\n"
                "**[Global Memory]**\n"
                "• `/memory-save`: Save information to the global shared memory.\n"
                "• `/memory-list`: List all information in the global memory.\n"
                "• `/memory-delete`: Delete information from the global memory.\n"
                "**[Other]**\n"
                "• `/clear_history`: Reset the conversation history."
            ),
            inline=False
        )
        channel_model_str = self.channel_models.get(str(interaction.channel_id))
        model_display = f"`{channel_model_str}` (Channel-specific)" if channel_model_str else f"`{self.llm_config.get('model', 'Not set')}` (Default)"

        ai_bio_display = "N/A"
        user_bio_display = "N/A"
        if self.bio_manager:
            ai_bio_display = "✅ (Custom)" if self.bio_manager.get_channel_bio(interaction.channel_id) else "Default"
            user_bio_display = "✅ (Stored)" if self.bio_manager.get_user_bio(interaction.user.id) else "None"

        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• None" if not active_tools else "• " + ", ".join(active_tools)
        embed.add_field(name="Current AI Settings",
                        value=f"• **Model in Use:** {model_display}\n"
                              f"• **AI Role (Channel):** {ai_bio_display} (see `/show-ai-bio`)\n"
                              f"• **Your Info:** {user_bio_display} (see `/show-user-bio`)\n"
                              f"• **Max Conversation History:** {self.llm_config.get('max_messages', 'Not set')} pairs\n"
                              f"• **Max Images Processed at Once:** {self.llm_config.get('max_images', 'Not set')} image(s)\n"
                              f"• **Available Tools:** {tools_info}",
                        inline=False)
        embed.add_field(name="--- 📜 AI Usage Guidelines ---",
                        value="Please review the following to ensure safe use of the AI features.", inline=False)
        embed.add_field(name="⚠️ 2. Precautions for Data Input", value=(
            "**NEVER include personal or confidential information** such as your name, contact details, or passwords in the information you ask the AI to remember."),
                        inline=False)
        embed.add_field(name="✅ 3. Precautions for Using Generated Output", value=(
            "The AI's responses may contain inaccuracies or biases. **Always fact-check and use them at your own risk.**"),
                        inline=False)
        embed.set_footer(text="These guidelines are subject to change without notice.")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="clear_history",
        description="現在の会話スレッドの履歴をクリアします。/ Clears the history of the current conversation thread."
    )
    async def clear_history_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        cleared_count = 0
        threads_to_clear = set()
        try:
            async for msg in interaction.channel.history(limit=200):
                if msg.id in self.message_to_thread:
                    threads_to_clear.add(self.message_to_thread[msg.id])
        except (discord.Forbidden, discord.HTTPException):
            await interaction.followup.send("⚠️ チャンネルのメッセージ履歴を読み取れませんでした。")
            return
        for thread_id in threads_to_clear:
            if thread_id in self.conversation_threads:
                del self.conversation_threads[thread_id]
                self.message_to_thread = {k: v for k, v in self.message_to_thread.items() if v != thread_id}
                cleared_count += 1
        if cleared_count > 0:
            await interaction.followup.send(
                f"✅ このチャンネルに関連する {cleared_count} 個の会話スレッドの履歴をクリアしました。")
        else:
            await interaction.followup.send("ℹ️ クリア対象の会話履歴が見つかりませんでした。")


async def setup(bot: commands.Bot):
    """Sets up the LLMCog."""
    try:
        await bot.add_cog(LLMCog(bot))
        logger.info("LLMCog loaded successfully.")
    except Exception as e:
        logger.critical(f"Failed to set up LLMCog: {e}", exc_info=True)
        raise