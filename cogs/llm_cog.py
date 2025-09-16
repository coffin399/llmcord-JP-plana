from __future__ import annotations
import discord
from discord.ext import commands
from discord import app_commands
import openai
import json
import logging
import asyncio
import io
import base64
import re
import aiohttp
import yaml
import os
from typing import List, Dict, Any, Tuple, Optional
from collections import deque

try:
    import aiofiles
except ImportError:
    aiofiles = None
    logging.warning("aiofiles library not found. Channel model settings will be saved synchronously. "
                    "Install with: pip install aiofiles")

try:
    from plugins.search_agent import SearchAgent
except ImportError:
    logging.error("Could not import SearchAgent. Search functionality will be disabled.")
    SearchAgent = None

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

        self.channel_settings_path = "data/channel_llm_models.json"
        self.channel_models: Dict[str, str] = self._load_channel_models()
        logger.info(
            f"Loaded {len(self.channel_models)} channel-specific model settings from '{self.channel_settings_path}'.")

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

        self.search_agent = self._initialize_search_agent()

    async def cog_unload(self):
        await self.http_session.close()
        logger.info("LLMCog's aiohttp session has been closed.")

    def _load_channel_models(self) -> Dict[str, str]:
        """チャンネル設定ファイルを読み込む"""
        try:
            if os.path.exists(self.channel_settings_path):
                with open(self.channel_settings_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    return {str(k): v for k, v in data.items()}
        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load channel models file '{self.channel_settings_path}': {e}")
        return {}

    async def _save_channel_models(self) -> None:
        """チャンネル設定をファイルに保存する"""
        try:
            os.makedirs(os.path.dirname(self.channel_settings_path), exist_ok=True)
            if aiofiles:
                async with aiofiles.open(self.channel_settings_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(self.channel_models, indent=4))
            else:
                with open(self.channel_settings_path, 'w', encoding='utf-8') as f:
                    json.dump(self.channel_models, f, indent=4)
        except IOError as e:
            logger.error(f"Failed to save channel models file '{self.channel_settings_path}': {e}")

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
            agent = SearchAgent(self.bot)
            logger.info("SearchAgent initialized successfully.")
            return agent
        except Exception as e:
            logger.error(f"Failed to initialize SearchAgent: {e}", exc_info=True)
            return None

    def get_tools_definition(self) -> Optional[List[Dict[str, Any]]]:
        definitions = []
        active_tools = self.llm_config.get('active_tools', [])
        if 'search' in active_tools and self.search_agent and hasattr(self.search_agent, 'tool_spec'):
            definitions.append(self.search_agent.tool_spec)
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
            await message.reply(self._handle_llm_exception(e), silent=True)
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
        system_prompt = self.llm_config.get('system_prompt', "You are a helpful assistant.")
        messages_for_api: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages_for_api.extend(await self._collect_conversation_history(message))
        user_content_parts = [{"type": "text", "text": text_content}] if text_content else []
        user_content_parts.extend(image_contents)
        user_message_for_api = {"role": "user", "content": user_content_parts}
        messages_for_api.append(user_message_for_api)
        try:
            async with message.channel.typing():
                llm_response = await self._get_llm_response(messages_for_api, log_context, llm_client,
                                                            message.channel.id)
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
            await message.reply(self._handle_llm_exception(e), silent=True)

    def _cleanup_old_threads(self):
        max_threads = 100
        if len(self.conversation_threads) > max_threads:
            threads_to_remove = list(self.conversation_threads.keys())[:len(self.conversation_threads) - max_threads]
            for thread_id in threads_to_remove:
                del self.conversation_threads[thread_id]
                self.message_to_thread = {k: v for k, v in self.message_to_thread.items() if v != thread_id}

    async def _get_llm_response(self, messages: List[Dict[str, Any]], log_context: str,
                                client: openai.AsyncOpenAI, channel_id: int) -> str:
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
                                                   channel_id)
                    continue
                else:
                    return response_message.content or ""
            except Exception as e:
                logger.error(f"Error during LLM API call in iteration {iteration + 1}: {e}")
                raise
        logger.warning(f"Tool processing exceeded max iterations ({max_iterations})")
        return self.llm_config.get('error_msg', {}).get('tool_loop_timeout', "Tool processing exceeded max iterations.")

    async def _process_tool_calls(self, tool_calls: List[Any], messages: List[Dict[str, Any]],
                                  log_context: str, channel_id: int) -> None:
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            if self.search_agent and function_name == self.search_agent.name:
                try:
                    function_args = json.loads(tool_call.function.arguments)
                    query_text = function_args.get('query', 'N/A')
                    logger.info(f"Executing SearchAgent | {log_context} | query='{query_text}'")
                    search_results = await self.search_agent.run(arguments=function_args, bot=self.bot,
                                                                 channel_id=channel_id)
                    tool_response = {"tool_call_id": tool_call.id, "role": "tool", "name": function_name,
                                     "content": str(search_results)}
                    messages.append(tool_response)
                    logger.info(f"SearchAgent completed | {log_context} | result_length={len(str(search_results))}")
                except Exception as e:
                    logger.error(f"Error during tool call for {function_name}: {e}", exc_info=True)
                    error_content = f"Error: Invalid JSON arguments - {str(e)}" if isinstance(e,
                                                                                              json.JSONDecodeError) else f"Error executing search: {str(e)}"
                    messages.append(
                        {"tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": error_content})
            else:
                logger.warning(f"Received a call for an unsupported tool: {function_name} | {log_context}")
                messages.append({"tool_call_id": tool_call.id, "role": "tool", "name": function_name,
                                 "content": f"Error: Tool '{function_name}' is not available."})

    def _handle_llm_exception(self, e: Exception) -> str:
        error_detail = ""
        if isinstance(e, openai.RateLimitError):
            logger.warning(f"LLM API rate limit exceeded: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            base_msg_key, default_msg = 'ratelimit_error', "⚠️ 生成AIが現在非常に混雑しています。(Code: {status_code})"
        elif isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
            logger.error(f"LLM API connection error: {e}")
            return self.llm_config.get('error_msg', {}).get('general_error', "Failed to connect to the AI service.")
        elif isinstance(e, openai.APIStatusError):
            logger.error(f"LLM API status error: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            base_msg_key, default_msg = 'api_status_error', "AIとの通信でエラーが発生しました。(Code: {status_code})"
        else:
            logger.error(f"An unexpected error occurred during LLM interaction: {e}", exc_info=True)
            return self.llm_config.get('error_msg', {}).get('general_error', "An unexpected error occurred.")

        if hasattr(e, 'response') and e.response:
            try:
                error_data = e.response.json()
                detail = error_data.get('detail') or error_data.get('message') or error_data.get('title')
                error_detail = f"\n> **Details**: {detail}" if detail else f"\n> **Response**: `{str(error_data)[:500]}`"
            except json.JSONDecodeError:
                error_detail = f"\n> **Raw Response**: `{e.response.text[:500]}`"

        base_message = self.llm_config.get('error_msg', {}).get(base_msg_key, default_msg).format(
            status_code=e.status_code)
        return f"{base_message}{error_detail}"[:DISCORD_MESSAGE_MAX_LENGTH]

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

    async def model_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        available_models = self.llm_config.get('available_models', [])
        return [
                   app_commands.Choice(name=model, value=model)
                   for model in available_models if current.lower() in model.lower()
               ][:25]

    @app_commands.command(
        name="switch-models",
        description="このチャンネルで使用するAIモデルを切り替えます。/Switches the AI model used for this channel."
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

    # --- 新規追加: /switch-models-default コマンド ---
    @app_commands.command(
        name="switch-models-default",
        description="このチャンネルのAIモデルをデフォルトに切り替えます。Switch to default"
    )
    async def switch_model_default_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        model_to_set = "mistral/mistral-medium-latest"
        available_models = self.llm_config.get('available_models', [])

        # 推奨モデルが利用可能リストに含まれているかチェック
        if model_to_set not in available_models:
            await interaction.followup.send(
                f"⚠️ 推奨モデル `{model_to_set}` が設定ファイルで利用可能になっていません。\n"
                f"管理者に `config.yaml` の `available_models` リストに追加するよう依頼してください。",
                ephemeral=True
            )
            return

        channel_id_str = str(interaction.channel_id)
        self.channel_models[channel_id_str] = model_to_set

        try:
            await self._save_channel_models()
            await self._get_llm_client_for_channel(interaction.channel_id)
            await interaction.followup.send(
                f"✅ このチャンネルのAIモデルが推奨設定の `{model_to_set}` に切り替えられました。",
                ephemeral=False)
            logger.info(
                f"Model for channel {interaction.channel_id} switched to default '{model_to_set}' by {interaction.user.name}")
        except Exception as e:
            logger.error(f"Failed to save channel model settings for default model: {e}", exc_info=True)
            await interaction.followup.send("❌ 設定の保存に失敗しました。")

    # --- 新規追加ここまで ---

    @app_commands.command(
        name="switch-models-default-server",
        description="このチャンネルのAIモデルをサーバーのデフォルト設定に戻します。"
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
            await interaction.followup.send("ℹ️ このチャンネルには専用のモデルが設定されていません。", ephemeral=True)

    @switch_model_slash.error
    async def switch_model_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Error in /switch-model command: {error}", exc_info=True)
        if not interaction.response.is_done():
            await interaction.response.send_message(f"予期せぬエラーが発生しました: {error}", ephemeral=True)
        else:
            await interaction.followup.send(f"予期せぬエラーが発生しました: {error}", ephemeral=True)

    @app_commands.command(name="llm_help", description="LLM (AI対話) 機能のヘルプと利用ガイドラインを表示します。")
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
                f"• メッセージと一緒に画像を添付、または画像URLを貼り付けると、AIが画像の内容も理解しようとします。"
            ),
            inline=False
        )
        channel_model_str = self.channel_models.get(str(interaction.channel_id))
        model_display = f"`{channel_model_str}` (このチャンネル専用)" if channel_model_str else f"`{self.llm_config.get('model', '未設定')}` (デフォルト)"
        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• なし" if not active_tools else "• " + ", ".join(active_tools)
        embed.add_field(name="現在のAI設定",
                        value=f"• **使用モデル:** {model_display}\n• **会話履歴の最大保持数:** {self.llm_config.get('max_messages', '未設定')} ペア\n• **一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', '未設定')} 枚\n• **利用可能なツール:** {tools_info}",
                        inline=False)
        embed.add_field(name="--- 📜 AI利用ガイドライン ---",
                        value="AI機能を安全にご利用いただくため、以下の内容を必ずご確認ください。", inline=False)
        embed.add_field(name="1. 目的と対象AI", value=(
            "**【目的】** 本ガイドラインは、BotのAI機能を安全にご利用いただくために、技術的・法的リスクを低減させることを目的とします。\n" "**【対象AI】** 本Botは、内部的にMistral AIやGoogle Geminiなどのサードパーティ製生成AIモデルを利用しています。"),
                        inline=False)
        embed.add_field(name="⚠️ 2. データ入力時の注意", value=(
            "以下の情報は、AIの学習や意図しない漏洩に繋がる危険性があるため、**絶対に入力しないでください。**\n" "1. **個人情報・秘密情報:** 氏名、連絡先、NDA対象情報、自組織の機密情報など\n" "2. **第三者の知的財産:** 許可のない著作物(文章,コード等)、登録商標、意匠(ロゴ,デザイン)など"),
                        inline=False)
        embed.add_field(name="✅ 3. 生成物利用時の注意", value=(
            "1. **内容の不正確さ:** 生成物には虚偽や偏見が含まれる可能性があります。**必ずファクトチェックを行い、自己の責任で利用してください。**\n" "2. **権利侵害リスク:** 生成物が意図せず既存の著作物等と類似し、第三者の権利を侵害する可能性があります。\n" "3. **著作権の不発生:** AIによる生成物に著作権は発生しない、または権利が限定的となる可能性があります。\n" "4. **AIポリシーの遵守:** 基盤となるAI（Mistral AI, Gemini等）の利用規約やポリシーも適用されます。"),
                        inline=False)
        embed.add_field(name="🚫 4. 禁止事項と同意", value=(
            "法令や公序良俗に反する利用、他者の権利を侵害する利用、差別的・暴力的・性的なコンテンツの生成は固く禁じます。\n\n" "**本Botの利用をもって、本ガイドラインに同意したものとみなします。**"),
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
                f"• Attach images or paste image URLs with your message, and the AI will try to understand them."
            ),
            inline=False
        )
        channel_model_str = self.channel_models.get(str(interaction.channel_id))
        model_display = f"`{channel_model_str}` (Channel-specific)" if channel_model_str else f"`{self.llm_config.get('model', 'Not set')}` (Default)"
        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• None" if not active_tools else "• " + ", ".join(active_tools)
        settings_value = (
            f"• **Model in Use:** {model_display}\n" f"• **Max Conversation History:** {self.llm_config.get('max_messages', 'Not set')} pairs\n" f"• **Max Images Processed at Once:** {self.llm_config.get('max_images', 'Not set')} image(s)\n" f"• **Available Tools:** {tools_info}")
        embed.add_field(name="Current AI Settings", value=settings_value, inline=False)
        embed.add_field(name="--- 📜 AI Usage Guidelines ---",
                        value="Please review the following to ensure safe use of the AI features.", inline=False)
        embed.add_field(name="1. Purpose & Target AI", value=(
            "**Purpose:** This guideline aims to reduce technical and legal risks to ensure the safe use of the bot's AI features.\n" "**Target AI:** This bot internally uses third-party generative AI models such as Mistral AI and Google Gemini."),
                        inline=False)
        embed.add_field(name="⚠️ 2. Precautions for Data Input", value=(
            "**NEVER input the following information**, as it poses a risk of being used for AI training or unintentional leakage.\n" "1. **Personal/Confidential Info:** Name, contact details, NDA-protected info, your organization's sensitive data, etc.\n" "2. **Third-Party IP:** Copyrighted works (text, code), trademarks, or designs without permission."),
                        inline=False)
        embed.add_field(name="✅ 3. Precautions for Using Generated Output", value=(
            "1. **Inaccuracy:** The output may contain falsehoods. **Always fact-check and use it at your own risk.**\n" "2. **Rights Infringement Risk:** The output may unintentionally resemble existing works, potentially infringing on third-party rights.\n" "3. **No Copyright:** Copyright may not apply to AI-generated output, or rights may be limited.\n" "4. **Adherence to Policies:** The terms of the underlying AI (e.g., Mistral AI, Gemini) also apply."),
                        inline=False)
        embed.add_field(name="🚫 4. Prohibited Uses & Agreement", value=(
            "Use that violates laws, infringes on rights, or generates discriminatory, violent, or explicit content is strictly prohibited.\n\n" "**By using this bot, you are deemed to have agreed to these guidelines.**"),
                        inline=False)
        embed.set_footer(text="These guidelines are subject to change without notice.")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(
        name="clear_history",
        description="現在の会話スレッドの履歴をクリアします。"
    )
    async def clear_history_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
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