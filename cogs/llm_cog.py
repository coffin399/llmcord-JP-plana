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
from collections import deque

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
    """A cog for interacting with Large Language Models without requiring MESSAGE_CONTENT privileged intent."""

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
        self.thread_conversations: Dict[int, List[Dict[str, Any]]] = {}
        self.active_threads: set[int] = set()
        self.message_to_thread: Dict[int, int] = {}
        self.thread_close_tasks: Dict[int, asyncio.Task] = {}
        self.closing_keywords = ['ありがとう', 'ありがとうございます', 'ありがとうございました',
                                 '終了', '終わり', 'おわり', 'さようなら', 'さよなら', 'バイバイ',
                                 'thank you', 'thanks', 'bye', 'goodbye', 'end', 'close']
        self.auto_close_delay = self.llm_config.get('auto_close_delay', 60)
        self.main_llm_client = self._initialize_llm_client(self.llm_config.get('model'))
        if not self.main_llm_client:
            logger.error("Failed to initialize main LLM client. Core functionality is disabled.")
        self.search_agent = self._initialize_search_agent()

    async def cog_unload(self):
        for task in self.thread_close_tasks.values():
            if not task.done():
                task.cancel()
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
        if 'search' not in self.llm_config.get('active_tools', []) or not SearchAgent:
            return None
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

    async def _collect_thread_history(self, thread: discord.Thread) -> List[Dict[str, Any]]:
        if thread.id not in self.thread_conversations:
            return []

        history = []
        for msg in self.thread_conversations[thread.id]:
            history.append(msg)

        max_history_entries = self.llm_config.get('max_messages', 10) * 2
        if len(history) > max_history_entries:
            history = history[-max_history_entries:]

        return history

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
        source_urls = []

        found_urls = IMAGE_URL_PATTERN.findall(message.content)
        for url in found_urls:
            if url not in processed_urls:
                source_urls.append(url)
                processed_urls.add(url)

        for attachment in message.attachments:
            if attachment.content_type and attachment.content_type.startswith(
                    'image/') and attachment.url not in processed_urls:
                source_urls.append(attachment.url)
                processed_urls.add(attachment.url)

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

        clean_text = IMAGE_URL_PATTERN.sub('', message.content)
        clean_text = clean_text.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()

        return image_inputs, clean_text

    async def _schedule_thread_close(self, thread: discord.Thread, delay: int = None):
        if thread.id in self.thread_close_tasks:
            existing_task = self.thread_close_tasks[thread.id]
            if not existing_task.done():
                existing_task.cancel()

        delay = delay or self.auto_close_delay
        task = asyncio.create_task(self._auto_close_thread(thread, delay))
        self.thread_close_tasks[thread.id] = task

    async def _auto_close_thread(self, thread: discord.Thread, delay: int):
        try:
            await asyncio.sleep(delay)

            if thread.id not in self.active_threads:
                return

            try:
                await thread.send("⏰ このスレッドは非アクティブのため、まもなくクローズされます。")
                await asyncio.sleep(10)
            except discord.HTTPException:
                pass

            try:
                await thread.edit(archived=True, reason="Auto-closed due to inactivity")
                logger.info(f"Thread '{thread.name}' (ID: {thread.id}) auto-closed after {delay} seconds of inactivity")
            except discord.HTTPException as e:
                logger.error(f"Failed to archive thread {thread.id}: {e}")

            self._cleanup_thread(thread.id)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in auto-close thread task: {e}")

    def _cleanup_thread(self, thread_id: int):
        self.active_threads.discard(thread_id)
        if thread_id in self.thread_conversations:
            del self.thread_conversations[thread_id]
        if thread_id in self.thread_close_tasks:
            del self.thread_close_tasks[thread_id]
        self.message_to_thread = {k: v for k, v in self.message_to_thread.items() if v != thread_id}

    def _check_closing_keywords(self, text: str) -> bool:
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.closing_keywords)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if not (self.bot.user.mentioned_in(message) and not message.mention_everyone):
            return

        if isinstance(message.channel, discord.Thread):
            if message.channel.id in self.active_threads:
                await self._handle_thread_message(message)
            else:
                await self._handle_thread_message(message, new_conversation=True)
            return

        if (allowed_channels := self.config.get('allowed_channel_ids', [])) and message.channel.id not in allowed_channels:
            return

        if (allowed_roles := self.config.get('allowed_role_ids', [])) and isinstance(message.author,
                                                                                     discord.Member) and not any(
                role.id in allowed_roles for role in message.author.roles):
            return

        if not self.main_llm_client:
            await message.reply(self.llm_config.get('error_msg', {}).get('general_error', "LLM client not configured."),
                                silent=True)
            return

        await self._create_thread_and_respond(message)

    async def _create_thread_and_respond(self, message: discord.Message):
        image_contents, text_content = await self._prepare_multimodal_content(message)

        if not text_content and not image_contents:
            await message.reply(
                self.llm_config.get('error_msg', {}).get('empty_mention_reply', "Yes? How can I help you?"),
                silent=True)
            return

        try:
            thread_name = text_content[:30] + "..." if len(
                text_content) > 30 else text_content if text_content else "AI Chat"
            thread_name = thread_name.replace('\n', ' ').strip() or "AI Chat"

            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=60
            )

            self.active_threads.add(thread.id)
            self.message_to_thread[message.id] = thread.id
            self.thread_conversations[thread.id] = []
            logger.info(f"Created thread '{thread_name}' (ID: {thread.id}) for user {message.author.name}")

            user_content_parts = [{"type": "text", "text": text_content}] if text_content else []
            user_content_parts.extend(image_contents)
            user_message = {"role": "user", "content": user_content_parts}
            self.thread_conversations[thread.id].append(user_message)

            if self._check_closing_keywords(text_content):
                await self._schedule_thread_close(thread, delay=30)
            else:
                await self._schedule_thread_close(thread)

            await self._generate_response_in_thread(thread, message.author)

        except discord.HTTPException as e:
            logger.error(f"Failed to create thread: {e}")
            await message.reply(
                self.llm_config.get('error_msg', {}).get('general_error', "Failed to create conversation thread."),
                silent=True)

    async def _handle_thread_message(self, message: discord.Message, new_conversation: bool = False):
        thread = message.channel

        if new_conversation:
            self.active_threads.add(thread.id)
            self.thread_conversations[thread.id] = []

        image_contents, text_content = await self._prepare_multimodal_content(message)

        if not text_content and not image_contents:
            await message.reply("何かメッセージを入力してください。", silent=True)
            return

        user_content_parts = [{"type": "text", "text": text_content}] if text_content else []
        user_content_parts.extend(image_contents)
        user_message = {"role": "user", "content": user_content_parts}

        if thread.id not in self.thread_conversations:
            self.thread_conversations[thread.id] = []

        self.thread_conversations[thread.id].append(user_message)

        if self._check_closing_keywords(text_content):
            await self._schedule_thread_close(thread, delay=30)
        else:
            await self._schedule_thread_close(thread)

        await self._generate_response_in_thread(thread, message.author)

    async def _generate_response_in_thread(self, thread: discord.Thread, author: discord.User):
        guild_log = f"guild='{thread.guild.name}({thread.guild.id})'" if thread.guild else "guild='DM'"
        thread_log = f"thread='{thread.name}({thread.id})'"
        author_log = f"author='{author.name}({author.id})'"
        log_context = f"{guild_log}, {thread_log}, {author_log}"

        system_prompt = self.llm_config.get('system_prompt', "You are a helpful assistant.")
        messages_for_api: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        conversation_history = await self._collect_thread_history(thread)

        allowed_keys = {'role', 'content', 'name', 'tool_calls', 'tool_call_id'}
        cleaned_history = [
            {key: value for key, value in msg.items() if key in allowed_keys}
            for msg in conversation_history
        ]
        messages_for_api.extend(cleaned_history)

        try:
            async with thread.typing():
                llm_response = await self._get_llm_response(messages_for_api, log_context)

            if llm_response:
                log_response_summary = llm_response.replace('\n', ' ')[:150]
                logger.info(f"Sending LLM response in thread | {log_context} | response='{log_response_summary}...'")

                sent_message = await self._send_thread_chunks(thread, llm_response)

                if sent_message:
                    assistant_message = {
                        "role": "assistant",
                        "content": llm_response,
                        "message_id": sent_message.id
                    }
                    self.thread_conversations[thread.id].append(assistant_message)

                if "さようなら" in llm_response or "goodbye" in llm_response.lower() or "会話を終了" in llm_response:
                    await self._schedule_thread_close(thread, delay=30)
                else:
                    await self._schedule_thread_close(thread)

                self._cleanup_old_threads()

            else:
                logger.warning(f"Received empty response from LLM | {log_context}")
                await thread.send(
                    self.llm_config.get('error_msg', {}).get('general_error',
                                                             "Received an empty response from the AI."),
                    silent=True)

        except Exception as e:
            logger.error(f"Error during LLM interaction in thread: {e}", exc_info=True)
            await thread.send(self._handle_llm_exception(e), silent=True)

    def _cleanup_old_threads(self):
        max_threads = 100
        if len(self.thread_conversations) > max_threads:
            threads_to_remove = list(self.thread_conversations.keys())[:len(self.thread_conversations) - max_threads]
            for thread_id in threads_to_remove:
                self._cleanup_thread(thread_id)

    async def _get_llm_response(self, messages: List[Dict[str, Any]], log_context: str) -> str:
        current_messages = messages.copy()
        max_iterations = self.llm_config.get('max_tool_iterations', 5)
        extra_params = self.llm_config.get('extra_api_parameters', {})

        for iteration in range(max_iterations):
            tools_def = self.get_tools_definition()
            api_kwargs = {
                "model": self.main_llm_client.model_name_for_api_calls,
                "messages": current_messages,
                "temperature": extra_params.get('temperature', 0.7),
                "max_tokens": extra_params.get('max_tokens', 4096)
            }

            if tools_def:
                api_kwargs["tools"] = tools_def
                api_kwargs["tool_choice"] = "auto"

            try:
                response = await self.main_llm_client.chat.completions.create(**api_kwargs)
                response_message = response.choices[0].message

                current_messages.append(response_message.model_dump(exclude_none=True))

                if response_message.tool_calls:
                    logger.info(
                        f"Processing {len(response_message.tool_calls)} tool call(s) in iteration {iteration + 1}")

                    await self._process_tool_calls(response_message.tool_calls, current_messages, log_context)

                    continue
                else:
                    return response_message.content or ""

            except Exception as e:
                logger.error(f"Error during LLM API call in iteration {iteration + 1}: {e}", exc_info=True)
                raise

        logger.warning(f"Tool processing exceeded max iterations ({max_iterations})")
        return self.llm_config.get('error_msg', {}).get('tool_loop_timeout', "Tool processing exceeded max iterations.")

    async def _process_tool_calls(self, tool_calls: List[Any], messages: List[Dict[str, Any]],
                                  log_context: str) -> None:
        for tool_call in tool_calls:
            function_name = tool_call.function.name

            if self.search_agent and function_name == self.search_agent.name:
                try:
                    function_args = json.loads(tool_call.function.arguments)
                    query_text = function_args.get('query', 'N/A')
                    logger.info(f"Executing SearchAgent | {log_context} | query='{query_text}'")

                    search_results = await self.search_agent.run(arguments=function_args, bot=self.bot)

                    tool_response = {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": str(search_results)
                    }
                    messages.append(tool_response)

                    logger.info(f"SearchAgent completed | {log_context} | result_length={len(str(search_results))}")

                except json.JSONDecodeError as e:
                    logger.error(f"Error parsing tool arguments: {e}")
                    error_response = {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": f"Error: Invalid JSON arguments - {str(e)}"
                    }
                    messages.append(error_response)

                except Exception as e:
                    logger.error(f"Error executing SearchAgent: {e}", exc_info=True)
                    error_response = {
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": function_name,
                        "content": f"Error executing search: {str(e)}"
                    }
                    messages.append(error_response)
            else:
                logger.warning(f"Received a call for an unsupported tool: {function_name} | {log_context}")
                error_response = {
                    "tool_call_id": tool_call.id,
                    "role": "tool",
                    "name": function_name,
                    "content": f"Error: Tool '{function_name}' is not available."
                }
                messages.append(error_response)

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

    async def _send_thread_chunks(self, thread: discord.Thread, text_content: str) -> Optional[discord.Message]:
        if not text_content:
            return None
        chunks = self._split_message(text_content, DISCORD_MESSAGE_MAX_LENGTH)
        first_message = None
        for i, chunk in enumerate(chunks):
            if i == 0:
                first_message = await thread.send(chunk, silent=True)
            else:
                await thread.send(chunk, silent=True)
        return first_message

    def _split_message(self, text_content: str, max_length: int) -> List[str]:
        if not text_content:
            return []
        chunks, current_chunk = [], io.StringIO()
        for line in text_content.splitlines(keepends=True):
            if current_chunk.tell() + len(line) > max_length:
                if chunk_val := current_chunk.getvalue():
                    chunks.append(chunk_val)
                current_chunk = io.StringIO()
                while len(line) > max_length:
                    chunks.append(line[:max_length])
                    line = line[max_length:]
            current_chunk.write(line)
        if final_chunk := current_chunk.getvalue():
            chunks.append(final_chunk)
        return chunks

    @app_commands.command(name="llm_help", description="LLM (AI対話) 機能のヘルプと利用ガイドラインを表示します。")
    async def llm_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user or interaction.client.user
        bot_name = bot_user.name if bot_user else "当Bot"
        embed = discord.Embed(title=f"💡 {bot_name} AI対話機能ヘルプ＆ガイドライン",
                              description=f"{bot_name}のAI対話機能についての説明と利用規約です。",
                              color=discord.Color.purple())
        if bot_user and bot_user.avatar:
            embed.set_thumbnail(url=bot_user.avatar.url)

        embed.add_field(
            name="基本的な使い方",
            value=f"• **Botにメンション (`@{bot_name}`) して話しかけると、自動的にスレッドが作成されAIが応答します。**\n"
                  f"• **作成されたスレッド内でも、Botにメンションすることで会話を続けられます。**\n"
                  f"• メッセージと一緒に画像を添付、または画像URLを貼り付けると、AIが画像の内容も理解しようとします。\n"
                  f"• **スレッドは{self.auto_close_delay // 60}分間操作がないと自動的にクローズされます。**\n"
                  f"• 「ありがとう」などの終了キーワードを含むメッセージ後は30秒でクローズされます。\n"
                  f"• `/close_thread`コマンドで手動でスレッドをクローズできます。\n"
                  f"• **注意: メンションなしのメッセージはBotには読めません。**",
            inline=False
        )

        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• なし" if not active_tools else "• " + ", ".join(active_tools)
        embed.add_field(
            name="現在のAI設定",
            value=f"• **使用モデル:** `{self.llm_config.get('model', '未設定')}`\n"
                  f"• **会話履歴の最大保持数:** {self.llm_config.get('max_messages', '未設定')} ペア\n"
                  f"• **一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', '未設定')} 枚\n"
                  f"• **利用可能なツール:** {tools_info}",
            inline=False
        )

        embed.add_field(
            name="--- 📜 AI利用ガイドライン ---",
            value="AI機能を安全にご利用いただくため、以下の内容を必ずご確認ください。",
            inline=False
        )

        embed.add_field(
            name="1. 目的と対象AI",
            value=(
                "**【目的】** 本ガイドラインは、BotのAI機能を安全にご利用いただくために、技術的・法的リスクを低減させることを目的とします。\n"
                "**【対象AI】** 本Botは、内部的にMistral AIやGoogle Geminiなどのサードパーティ製生成AIモデルを利用しています。"
            ),
            inline=False
        )

        embed.add_field(
            name="⚠️ 2. データ入力時の注意",
            value=(
                "以下の情報は、AIの学習や意図しない漏洩に繋がる危険性があるため、**絶対に入力しないでください。**\n"
                "1. **個人情報・秘密情報:** 氏名、連絡先、NDA対象情報、自組織の機密情報など\n"
                "2. **第三者の知的財産:** 許可のない著作物(文章,コード等)、登録商標、意匠(ロゴ,デザイン)など"
            ),
            inline=False
        )

        embed.add_field(
            name="✅ 3. 生成物利用時の注意",
            value=(
                "1. **内容の不正確さ:** 生成物には虚偽や偏見が含まれる可能性があります。**必ずファクトチェックを行い、自己の責任で利用してください。**\n"
                "2. **権利侵害リスク:** 生成物が意図せず既存の著作物等と類似し、第三者の権利を侵害する可能性があります。\n"
                "3. **著作権の不発生:** AIによる生成物に著作権は発生しない、または権利が限定的となる可能性があります。\n"
                "4. **AIポリシーの遵守:** 基盤となるAI（Mistral AI, Gemini等）の利用規約やポリシーも適用されます。"
            ),
            inline=False
        )

        embed.add_field(
            name="🚫 4. 禁止事項と同意",
            value=(
                "法令や公序良俗に反する利用、他者の権利を侵害する利用、差別的・暴力的・性的なコンテンツの生成は固く禁じます。\n\n"
                "**本Botの利用をもって、本ガイドラインに同意したものとみなします。**"
            ),
            inline=False
        )
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
        if bot_user and bot_user.avatar:
            embed.set_thumbnail(url=bot_user.avatar.url)

        embed.add_field(
            name="Basic Usage",
            value=f"• **Mention the bot (`@{bot_name}`) to automatically create a thread and get a response from the AI.**\n"
                  f"• **Within the created thread, mention the bot to continue the conversation.**\n"
                  f"• Attach images or paste image URLs with your message, and the AI will try to understand them.\n"
                  f"• **Threads will be automatically closed after {self.auto_close_delay // 60} minutes of inactivity.**\n"
                  f"• Threads close 30 seconds after messages with closing keywords like 'thank you'.\n"
                  f"• Use `/close_thread` command to manually close a thread.\n"
                  f"• **Note: Messages without mentions cannot be read by the bot.**",
            inline=False
        )

        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• None" if not active_tools else "• " + ", ".join(active_tools)
        settings_value = (
            f"• **Model in Use:** `{self.llm_config.get('model', 'Not set')}`\n"
            f"• **Max Conversation History:** {self.llm_config.get('max_messages', 'Not set')} pairs\n"
            f"• **Max Images Processed at Once:** {self.llm_config.get('max_images', 'Not set')} image(s)\n"
            f"• **Available Tools:** {tools_info}"
        )
        embed.add_field(name="Current AI Settings", value=settings_value, inline=False)

        embed.add_field(
            name="--- 📜 AI Usage Guidelines ---",
            value="Please review the following to ensure safe use of the AI features.",
            inline=False
        )

        embed.add_field(
            name="1. Purpose & Target AI",
            value=(
                "**Purpose:** This guideline aims to reduce technical and legal risks to ensure the safe use of the bot's AI features.\n"
                "**Target AI:** This bot internally uses third-party generative AI models such as Mistral AI and Google Gemini."
            ),
            inline=False
        )

        embed.add_field(
            name="⚠️ 2. Precautions for Data Input",
            value=(
                "**NEVER input the following information**, as it poses a risk of being used for AI training or unintentional leakage.\n"
                "1. **Personal/Confidential Info:** Name, contact details, NDA-protected info, your organization's sensitive data, etc.\n"
                "2. **Third-Party IP:** Copyrighted works (text, code), trademarks, or designs without permission."
            ),
            inline=False
        )

        embed.add_field(
            name="✅ 3. Precautions for Using Generated Output",
            value=(
                "1. **Inaccuracy:** The output may contain falsehoods. **Always fact-check and use it at your own risk.**\n"
                "2. **Rights Infringement Risk:** The output may unintentionally resemble existing works, potentially infringing on third-party rights.\n"
                "3. **No Copyright:** Copyright may not apply to AI-generated output, or rights may be limited.\n"
                "4. **Adherence to Policies:** The terms of the underlying AI (e.g., Mistral AI, Gemini) also apply."
            ),
            inline=False
        )

        embed.add_field(
            name="🚫 4. Prohibited Uses & Agreement",
            value=(
                "Use that violates laws, infringes on rights, or generates discriminatory, violent, or explicit content is strictly prohibited.\n\n"
                "**By using this bot, you are deemed to have agreed to these guidelines.**"
            ),
            inline=False
        )

        embed.set_footer(text="These guidelines are subject to change without notice.")
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="clear_history", description="現在のスレッドの会話履歴をクリアします。")
    async def clear_history_slash(self, interaction: discord.Interaction):
        if isinstance(interaction.channel, discord.Thread):
            thread_id = interaction.channel.id
            if thread_id in self.thread_conversations:
                self._cleanup_thread(thread_id)
                await interaction.response.send_message("✅ このスレッドの会話履歴をクリアしました。", ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ このスレッドには会話履歴がありません。", ephemeral=True)
        else:
            cleared_count = len(self.thread_conversations)

            for task in self.thread_close_tasks.values():
                if not task.done():
                    task.cancel()

            self.thread_conversations.clear()
            self.active_threads.clear()
            self.message_to_thread.clear()
            self.thread_close_tasks.clear()

            if cleared_count > 0:
                await interaction.response.send_message(f"✅ {cleared_count}個のスレッドの会話履歴をクリアしました。",
                                                        ephemeral=True)
            else:
                await interaction.response.send_message("ℹ️ 会話履歴がありません。", ephemeral=True)

    @app_commands.command(name="close_thread", description="現在のAI会話スレッドをクローズします。")
    async def close_thread_slash(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "このコマンドはAI会話スレッド内でのみ使用できます。",
                ephemeral=True
            )
            return

        thread = interaction.channel

        if thread.id not in self.active_threads:
            await interaction.response.send_message(
                "このスレッドはAI会話スレッドではありません。",
                ephemeral=True
            )
            return

        try:
            await interaction.response.send_message("👋 会話を終了します。このスレッドをクローズします。")

            self._cleanup_thread(thread.id)

            await asyncio.sleep(2)
            await thread.edit(archived=True, reason="Manually closed by user")

            logger.info(f"Thread '{thread.name}' (ID: {thread.id}) manually closed by {interaction.user.name}")

        except discord.HTTPException as e:
            logger.error(f"Failed to close thread {thread.id}: {e}")
            await interaction.followup.send(
                "スレッドのクローズに失敗しました。",
                ephemeral=True
            )


async def setup(bot: commands.Bot):
    """Sets up the LLMCog."""
    try:
        await bot.add_cog(LLMCog(bot))
        logger.info("LLMCog loaded successfully.")
    except Exception as e:
        logger.critical(f"Failed to set up LLMCog: {e}", exc_info=True)
        raise