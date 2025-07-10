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
from typing import List, Dict, Any, Tuple, Optional

# Attempt to import the SearchAgent plugin
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
        self.chat_histories: Dict[int, List[Dict[str, Any]]] = {}
        self.main_llm_client = self._initialize_llm_client(self.llm_config.get('model'))
        if not self.main_llm_client:
            logger.error("Failed to initialize main LLM client. Core functionality is disabled.")
        self.search_agent = self._initialize_search_agent()

    async def cog_unload(self):
        await self.http_session.close()
        logger.info("LLMCog's aiohttp session has been closed.")

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

    def _initialize_search_agent(self) -> Optional[SearchAgent]:
        if 'search' not in self.llm_config.get('active_tools', []) or not SearchAgent: return None
        search_config = self.llm_config.get('search_agent', {})
        if not search_config.get('api_key') or not search_config.get('model'):
            logger.error("SearchAgent config (api_key or model) is missing. Search will be disabled.")
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
            found_urls = IMAGE_URL_PATTERN.findall(msg.content)
            for url in found_urls:
                if url not in processed_urls: source_urls.append(url); processed_urls.add(url)
            for attachment in msg.attachments:
                if attachment.content_type and attachment.content_type.startswith(
                        'image/') and attachment.url not in processed_urls:
                    source_urls.append(attachment.url);
                    processed_urls.add(attachment.url)
        max_images = self.llm_config.get('max_images', 1)
        for url in source_urls[:max_images]:
            if image_data := await self._process_image_url(url): image_inputs.append(image_data)
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
        if message.author.bot or not self.bot.user.mentioned_in(message) or message.mention_everyone: return
        if (allowed_channels := self.config.get('allowed_channel_ids',
                                                [])) and message.channel.id not in allowed_channels: return
        if (allowed_roles := self.config.get('allowed_role_ids', [])) and isinstance(message.author,
                                                                                     discord.Member) and not any(
            role.id in allowed_roles for role in message.author.roles): return
        if not self.main_llm_client:
            await message.reply(self.llm_config.get('error_msg', {}).get('general_error', "LLM client not configured."),
                                silent=True)
            return

        image_contents, text_content = await self._prepare_multimodal_content(message)
        text_content = text_content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()

        if not text_content and not image_contents:
            await message.reply(
                self.llm_config.get('error_msg', {}).get('empty_mention_reply', "Yes? How can I help you?"),
                silent=True)
            return

        guild_log = f"guild='{message.guild.name}({message.guild.id})'" if message.guild else "guild='DM'"
        channel_log = f"channel='{message.channel.name}({message.channel.id})'" if hasattr(message.channel,
                                                                                           'name') and message.channel.name else f"channel(id)={message.channel.id}"
        author_log = f"author='{message.author.name}({message.author.id})'"
        log_context = f"{guild_log}, {channel_log}, {author_log}"

        log_text_summary = text_content.replace('\n', ' ')[:150]
        logger.info(
            f"Received LLM request | {log_context} | image_count={len(image_contents)} | text='{log_text_summary}...'"
            )

        history_key = message.channel.id
        self.chat_histories.setdefault(history_key, [])
        system_prompt = self.llm_config.get('system_prompt', "You are a helpful assistant.")
        messages_for_api: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        max_history_entries = self.llm_config.get('max_messages', 10) * 2
        channel_history = self.chat_histories[history_key]
        if len(channel_history) > max_history_entries: channel_history = channel_history[-max_history_entries:]
        messages_for_api.extend(channel_history)
        user_content_parts = [{"type": "text", "text": text_content}] if text_content else []
        user_content_parts.extend(image_contents)
        user_message_for_api = {"role": "user", "content": user_content_parts}
        messages_for_api.append(user_message_for_api)

        try:
            async with message.channel.typing():
                llm_response = await self._get_llm_response(messages_for_api, log_context)
            if llm_response:
                log_response_summary = llm_response.replace('\n', ' ')[:150]
                logger.info(f"Sending LLM response | {log_context} | response='{log_response_summary}...'"
                            )
                self.chat_histories[history_key].append(user_message_for_api)
                self.chat_histories[history_key].append({"role": "assistant", "content": llm_response})
                if len(self.chat_histories[history_key]) > max_history_entries: self.chat_histories[history_key] = \
                self.chat_histories[history_key][-max_history_entries:]
                await self._send_reply_chunks(message, llm_response)
            else:
                logger.warning(f"Received empty response from LLM | {log_context}")
                await message.reply(self.llm_config.get('error_msg', {}).get('general_error',
                                                                             "Received an empty response from the AI."),
                                    silent=True)
        except Exception as e:
            await message.reply(self._handle_llm_exception(e), silent=True)

    async def _get_llm_response(self, messages: List[Dict[str, Any]], log_context: str) -> str:
        current_messages = messages
        max_iterations = self.llm_config.get('max_tool_iterations', 3)
        extra_params = self.llm_config.get('extra_api_parameters', {})
        for _ in range(max_iterations):
            tools_def = self.get_tools_definition()
            api_kwargs = {"model": self.main_llm_client.model_name_for_api_calls, "messages": current_messages,
                          "temperature": extra_params.get('temperature', 0.7),
                          "max_tokens": extra_params.get('max_tokens', 4096)}
            if tools_def:
                api_kwargs["tools"] = tools_def
                api_kwargs["tool_choice"] = "auto"
            response = await self.main_llm_client.chat.completions.create(**api_kwargs)
            response_message = response.choices[0].message
            current_messages.append(response_message.model_dump(exclude_none=True))
            if response_message.tool_calls:
                if (final_answer := await self._process_tool_calls(response_message.tool_calls, current_messages,
                                                                   log_context)) is not None:
                    return final_answer
                continue
            else:
                return response_message.content
        return self.llm_config.get('error_msg', {}).get('tool_loop_timeout', "Tool processing exceeded max iterations.")

    async def _process_tool_calls(self, tool_calls: List[Any], messages: List[Dict[str, Any]], log_context: str) -> \
    Optional[str]:
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            if self.search_agent and function_name == self.search_agent.name:
                try:
                    function_args = json.loads(tool_call.function.arguments)
                    query_text = function_args.get('query', 'N/A')
                    logger.info(f"Executing SearchAgent | {log_context} | query='{query_text}'")
                    return await self.search_agent.run(arguments=function_args, bot=self.bot)
                except Exception as e:
                    logger.error(f"Error executing SearchAgent: {e}", exc_info=True)
                    tool_output = f"[Tool Execution Error] Failed to run '{function_name}': {e}"
            else:
                logger.warning(f"Received a call for an unsupported tool: {function_name} | {log_context}")
                tool_output = f"[Tool Execution Error] Tool '{function_name}' is not available."
            messages.append(
                {"tool_call_id": tool_call.id, "role": "tool", "name": function_name, "content": str(tool_output)})
        return None

    def _handle_llm_exception(self, e: Exception) -> str:
        if isinstance(e, openai.RateLimitError):
            logger.warning(f"LLM API rate limit exceeded: {e}")
            return self.llm_config.get('error_msg', {}).get('ratelimit_error',
                                                            "The AI is busy. Please try again later.")
        elif isinstance(e, (openai.APIConnectionError, openai.APITimeoutError)):
            logger.error(f"LLM API connection error: {e}")
            return self.llm_config.get('error_msg', {}).get('general_error', "Failed to connect to the AI service.")
        elif isinstance(e, openai.APIStatusError):
            logger.error(f"LLM API status error: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            return self.llm_config.get('error_msg', {}).get('general_error',
                                                            f"An API error occurred (Code: {e.status_code}).")
        else:
            logger.error(f"An unexpected error occurred during LLM interaction: {e}", exc_info=True)
            return self.llm_config.get('error_msg', {}).get('general_error', "An unexpected error occurred.")

    async def _send_reply_chunks(self, message: discord.Message, text_content: str):
        if not text_content: return
        chunks = self._split_message(text_content, DISCORD_MESSAGE_MAX_LENGTH)
        first_chunk = chunks.pop(0) if chunks else ""
        try:
            await message.reply(first_chunk, silent=True)
        except discord.HTTPException as e:
            logger.warning(f"Failed to reply, falling back to sending message. Error: {e}")
            await message.channel.send(first_chunk, silent=True)
        for chunk in chunks:
            await message.channel.send(chunk, silent=True)

    def _split_message(self, text_content: str, max_length: int) -> List[str]:
        if not text_content: return []
        chunks, current_chunk = [], io.StringIO()
        for line in text_content.splitlines(keepends=True):
            if current_chunk.tell() + len(line) > max_length:
                if chunk_val := current_chunk.getvalue(): chunks.append(chunk_val)
                current_chunk = io.StringIO()
                while len(line) > max_length:
                    chunks.append(line[:max_length]);
                    line = line[max_length:]
            current_chunk.write(line)
        if final_chunk := current_chunk.getvalue(): chunks.append(final_chunk)
        return chunks

    @app_commands.command(name="llm_help", description="LLM (AI対話) 機能に関する詳細なヘルプを表示します。")
    async def llm_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user or interaction.client.user
        bot_name = bot_user.name if bot_user else "当Bot"
        embed = discord.Embed(title="💡 LLM (AI対話) 機能 ヘルプ",
                              description=f"{bot_name}のAI対話機能についての説明です。",
                              color=discord.Color.purple())
        if bot_user and bot_user.avatar:
            embed.set_thumbnail(url=bot_user.avatar.url)

        embed.add_field(
            name="基本的な使い方",
            value=f"• Botにメンション (`@{bot_name}`) して話しかけると、AIが応答します。\n"
                  f"• メッセージと一緒に画像を添付、または画像URLを貼り付けると、AIが画像の内容も理解しようとします。\n"
                  f"• 他の人のメッセージに返信する形でメンションすると、引用元の画像も認識します。",
            inline=False
        )
        embed.add_field(
            name="現在のAI設定",
            value=f"• **使用モデル:** `{self.llm_config.get('model', '未設定')}`\n"
                  f"• **会話履歴の最大保持数:** {self.llm_config.get('max_messages', '未設定')} ペア\n"
                  f"• **一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', '未設定')} 枚",
            inline=False
        )
        embed.add_field(
            name="免責事項と注意点",
            value="• AIが生成した内容は不正確、または不適切な場合があります。生成された情報を利用する際は、必ずご自身で内容を確認し、その責任を負うものとします。\n"
                  "• 会話はチャンネルごとに記憶されますが、永続的なものではありません。",
            inline=False
        )
        embed.add_field(
            name="ユーザーの責任と禁止事項",
            value="• **個人情報や機密情報を入力しないでください。** (例: 本名、住所、電話番号、パスワード、APIキー等)\n"
                  "• 法令に違反する、または他者の権利を侵害するコンテンツの生成を指示する行為を禁止します。\n"
                  "• 嫌がらせ、差別的、暴力的な目的での利用を禁止します。\n"
                  "• Botの脆弱性を突く試みや、スパムなど意図的に高負荷をかける行為を禁止します。",
            inline=False
        )
        embed.set_footer(text="本機能の利用は、これらの事項に同意したものとみなします。")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="llm_help_en",
                          description="Displays detailed help for LLM (AI Chat) features in English.")
    async def llm_help_en_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user or interaction.client.user
        bot_name = bot_user.name if bot_user else "This Bot"

        embed = discord.Embed(title="💡 LLM (AI Chat) Feature Help",
                              description=f"This is an explanation of the AI chat features for {bot_name}.",
                              color=discord.Color.purple())
        if bot_user and bot_user.avatar:
            embed.set_thumbnail(url=bot_user.avatar.url)

        embed.add_field(
            name="Basic Usage",
            value=f"• Mention the bot (`@{bot_name}`) to get a response from the AI.\n"
                  f"• Attach images or paste image URLs with your message, and the AI will try to understand them.\n"
                  f"• If you reply to another message while mentioning the bot, it will also see the images in the replied-to message.",
            inline=False
        )
        settings_value = (
            f"• **Model in Use:** `{self.llm_config.get('model', 'Not set')}`\n"
            f"• **Max Conversation History:** {self.llm_config.get('max_messages', 'Not set')} pairs\n"
            f"• **Max Images Processed at Once:** {self.llm_config.get('max_images', 'Not set')} image(s)"
        )
        embed.add_field(name="Current AI Settings", value=settings_value, inline=False)
        embed.add_field(
            name="Disclaimer & Important Notes",
            value="• Content generated by the AI may be inaccurate or inappropriate. You are solely responsible for verifying and using the generated information.\n"
                  "• Conversations are remembered per channel but are not permanent.",
            inline=False
        )
        embed.add_field(
            name="User Responsibilities & Prohibited Conduct",
            value="• **Do not input sensitive or personal information** (e.g., real name, address, phone number, passwords, API keys).\n"
                  "• Do not instruct the bot to generate content that violates laws or infringes on the rights of others.\n"
                  "• Do not use this feature for harassment, discrimination, or violent purposes.\n"
                  "• Do not attempt to exploit vulnerabilities or intentionally cause high load (e.g., spamming).",
            inline=False
        )
        embed.set_footer(text="By using this feature, you agree to these terms.")
        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot):
    """Sets up the LLMCog."""
    try:
        await bot.add_cog(LLMCog(bot))
        logger.info("LLMCog loaded successfully.")
    except Exception as e:
        logger.critical(f"Failed to set up LLMCog: {e}", exc_info=True)
        raise