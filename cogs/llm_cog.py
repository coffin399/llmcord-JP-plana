import discord
from discord.ext import commands
from discord import app_commands
import yaml
import openai
import json
import logging
import asyncio
import io
import base64
import re
import aiohttp

try:
    from plugins.search_agent import SearchAgent
except ImportError:
    logging.error(
        "plugins/search_agent.py が見つからないか、SearchAgentクラスをインポートできません。検索機能は無効になります。")
    SearchAgent = None

logger = logging.getLogger(__name__)
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpeg', '.jpg', '.gif', '.webp')
IMAGE_URL_PATTERN = re.compile(
    r'https?://[^\s]+\.(?:' + '|'.join(ext.lstrip('.') for ext in SUPPORTED_IMAGE_EXTENSIONS) + r')(?:\?[^\s]*)?',
    re.IGNORECASE)


class LLMCog(commands.Cog, name="LLM"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not hasattr(self.bot, 'config') or not self.bot.config:
            raise commands.ExtensionFailed(self.qualified_name, "Botのconfigがロードされていません。")

        self.config = self.bot.config
        if 'llm' not in self.config:
            raise commands.ExtensionFailed(self.qualified_name, "'llm' 設定セクションがありません。")

        self.llm_config = self.config['llm']
        if not isinstance(self.llm_config, dict):
            raise commands.ExtensionFailed(self.qualified_name, "'llm' 設定セクションが辞書形式ではありません。")

        self.http_session = aiohttp.ClientSession()
        self.bot.cfg = self.llm_config
        self.chat_histories = {}
        self.main_llm_client = None
        main_model_str = self.llm_config.get('model')
        if main_model_str:
            self.main_llm_client = self._initialize_llm_client(main_model_str, provider_config_section='providers')
        if not self.main_llm_client:
            logger.error("メインLLMクライアントの初期化に失敗しました。主要機能が無効になります。")

        self.search_agent = None
        if 'search' in self.llm_config.get('active_tools', []) and SearchAgent:
            search_agent_settings = self.llm_config.get('search_agent', {})
            if not search_agent_settings.get('api_key') or not search_agent_settings.get('model'):
                logger.error(
                    "SearchAgentの設定 (llm.search_agent.api_key または llm.search_agent.model) が不足。検索機能無効。")
            else:
                try:
                    self.search_agent = SearchAgent(self.bot)
                    logger.info("SearchAgentが正常に初期化されました。")
                except Exception as e:
                    logger.error(f"SearchAgentの初期化失敗: {e}", exc_info=True)
                    self.search_agent = None

        if not self.llm_config.get('system_prompt'):
            logger.warning("system_prompt が llm_config になし。")

    async def cog_unload(self):
        await self.http_session.close()
        logger.info("LLMCogのaiohttpセッションを閉じました。")

    def _initialize_llm_client(self, model_string: str, provider_config_section: str, api_key_override: str = None):
        try:
            if '/' not in model_string:
                logger.error(f"無効なモデル形式: '{model_string}'。'プロバイダー名/モデル名' の形式必須。")
                return None
            provider_name, model_name = model_string.split('/', 1)
            providers_settings = self.llm_config.get(provider_config_section, {})
            provider_specific_config = providers_settings.get(provider_name)

            if not provider_specific_config:
                logger.error(f"LLMプロバイダー '{provider_name}' 設定が llm_config.{provider_config_section} になし。")
                return None

            base_url = provider_specific_config.get('base_url')
            api_key_from_provider = provider_specific_config.get('api_key')
            actual_api_key = api_key_override if api_key_override is not None else api_key_from_provider
            is_local_provider = provider_name.lower() in ['ollama', 'oobabooga', 'jan', 'lmstudio']

            if not actual_api_key:
                if is_local_provider:
                    actual_api_key = "local-dummy-key"
                else:
                    logger.error(f"リモートプロバイダー '{provider_name}' モデル '{model_name}' のAPIキーなし。")
                    return None

            client = openai.AsyncOpenAI(base_url=base_url, api_key=actual_api_key)
            client.model_name_for_api_calls = model_name
            logger.info(
                f"プロバイダー '{provider_name}'、モデル '{model_name}' LLMクライアント初期化完了 (Base URL: {base_url or 'ライブラリデフォルト'})")
            return client
        except Exception as e:
            logger.error(f"LLMクライアント '{model_string}' 初期化中エラー: {e}", exc_info=True)
            return None

    def get_tools_definition(self):
        active_tool_names = self.llm_config.get('active_tools', [])
        if not active_tool_names:
            return None
        tools_definitions = []
        if 'search' in active_tool_names and self.search_agent and hasattr(self.search_agent, 'tool_spec'):
            tools_definitions.append(self.search_agent.tool_spec)
        return tools_definitions if tools_definitions else None

    async def _process_image_url(self, url: str) -> dict | None:
        try:
            async with self.http_session.get(url) as response:
                if response.status == 200:
                    image_bytes = await response.read()
                    encoded_image = base64.b64encode(image_bytes).decode('utf-8')
                    mime_type = response.content_type
                    if not mime_type or not mime_type.startswith('image/'):
                        ext = url.split('.')[-1].lower().split('?')[0]
                        if ext == 'jpg': ext = 'jpeg'
                        mime_type = f'image/{ext}' if ext in ('png', 'jpeg', 'gif', 'webp') else 'image/jpeg'

                    logger.debug(f"画像URLを処理: {url} ({len(image_bytes) / 1024:.2f} KB)")
                    return {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{encoded_image}"}
                    }
                else:
                    logger.warning(f"画像URLのダウンロード失敗: {url} (ステータスコード: {response.status})")
                    return None
        except Exception as e:
            logger.error(f"画像URLの処理中にエラー: {url} - {e}", exc_info=True)
            return None

    async def _prepare_multimodal_content(self, primary_message: discord.Message) -> (list, str):
        image_inputs = []

        messages_to_scan = [primary_message]
        if primary_message.reference and isinstance(primary_message.reference.resolved, discord.Message):
            replied_message = primary_message.reference.resolved
            messages_to_scan.append(replied_message)
            logger.debug(f"引用メッセージ (ID: {replied_message.id}) を画像スキャン対象に追加。")

        max_images = self.llm_config.get('max_images', 1)
        processed_urls = set()
        user_informed_about_max_images = False

        async def inform_max_images():
            nonlocal user_informed_about_max_images
            if not user_informed_about_max_images:
                try:
                    error_msg = self.llm_config.get('error_msg', {}).get('msg_max_image_size',
                                                                         f"⚠️ 最大画像数は {max_images} 枚です。超過分は無視されます。")
                    await primary_message.channel.send(error_msg.format(max_images=max_images), silent=False)
                    user_informed_about_max_images = True
                except Exception as e_send:
                    logger.warning(f"最大画像数超過の通知メッセージ送信失敗: {e_send}")

        source_urls = []
        for msg in messages_to_scan:
            found_urls = IMAGE_URL_PATTERN.findall(msg.content)
            for url in found_urls:
                if url not in processed_urls:
                    source_urls.append(url)
                    processed_urls.add(url)
            for attachment in msg.attachments:
                if attachment.content_type and attachment.content_type.startswith(
                        'image/') and attachment.url not in processed_urls:
                    source_urls.append(attachment.url)
                    processed_urls.add(attachment.url)

        for url in source_urls:
            if len(image_inputs) >= max_images:
                await inform_max_images()
                logger.info(f"最大画像数 ({max_images}枚) に達したため、残りのURL ({url}) は無視します。")
                break

            image_data = await self._process_image_url(url)
            if image_data:
                image_inputs.append(image_data)

        clean_text = IMAGE_URL_PATTERN.sub('', primary_message.content).strip()
        return image_inputs, clean_text

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        if not self.bot.user.mentioned_in(message): return
        if message.mention_everyone: return

        allowed_channel_ids = self.config.get('allowed_channel_ids', [])
        if allowed_channel_ids and message.channel.id not in allowed_channel_ids: return

        allowed_role_ids = self.config.get('allowed_role_ids', [])
        if allowed_role_ids and isinstance(message.author, discord.Member):
            if not any(role.id in allowed_role_ids for role in message.author.roles): return

        image_contents_for_llm, user_text_content = await self._prepare_multimodal_content(message)

        for mention_pattern in [f'<@!{self.bot.user.id}>', f'<@{self.bot.user.id}>']:
            user_text_content = user_text_content.replace(mention_pattern, '').strip()

        # ログ用の情報を生成
        guild_log_str = f"Guild='{message.guild.name}({message.guild.id})'" if message.guild else "Guild='DM'"
        channel_log_str = f"Channel='{message.channel.name}({message.channel.id})'" if hasattr(message.channel,
                                                                                               'name') and message.channel.name else f"Channel(ID)='{message.channel.id}'"

        logger.info(
            f"ユーザー入力受信: {guild_log_str}, {channel_log_str}, User='{message.author.name}({message.author.id})', Text='{user_text_content}', Images={len(image_contents_for_llm)}")

        if not user_text_content and not image_contents_for_llm:
            reply_text = self.llm_config.get('error_msg', {}).get('empty_mention_reply', "はい、ご用件は何でしょうか？")
            await message.reply(reply_text, silent=True)
            return

        if not self.main_llm_client:
            await message.reply(self.llm_config.get('error_msg', {}).get('general_error', "LLM未設定。処理不可。"),
                                silent=True)
            return

        user_input_content_parts = []
        if user_text_content:
            user_input_content_parts.append({"type": "text", "text": user_text_content})
        if image_contents_for_llm:
            user_input_content_parts.extend(image_contents_for_llm)

        user_message_for_api = {"role": "user", "content": user_input_content_parts}

        history_key = message.channel.id
        if history_key not in self.chat_histories: self.chat_histories[history_key] = []

        system_prompt_content = self.llm_config.get('system_prompt', "あなたはアシスタント。")
        messages_for_llm_api = [{"role": "system", "content": system_prompt_content}]

        current_channel_history = list(self.chat_histories[history_key])
        max_hist_pairs = self.llm_config.get('max_messages', 10)
        max_hist_entries = max_hist_pairs * 2
        if len(current_channel_history) > max_hist_entries:
            current_channel_history = current_channel_history[-max_hist_entries:]

        messages_for_llm_api.extend(current_channel_history)
        messages_for_llm_api.append(user_message_for_api)

        try:
            async with message.channel.typing():
                llm_reply_text_content = await self._get_llm_response(messages_for_llm_api)

            if llm_reply_text_content:
                # LLMの応答をログに出力
                log_response = llm_reply_text_content.replace('\n', ' ').strip()[:200]
                logger.info(
                    f"LLM応答: {guild_log_str}, {channel_log_str}, To='{message.author.name}', Response='{log_response}...'")

                self.chat_histories[history_key].append(user_message_for_api)
                self.chat_histories[history_key].append({"role": "assistant", "content": llm_reply_text_content})
                # 履歴が最大保持数を超えたら古いものから削除
                if len(self.chat_histories[history_key]) > max_hist_entries:
                    self.chat_histories[history_key] = self.chat_histories[history_key][-max_hist_entries:]

                await self._send_reply_chunks(message, llm_reply_text_content)
            else:
                await message.reply(
                    self.llm_config.get('error_msg', {}).get('general_error', "AIから空の応答がありました。"),
                    silent=True)
        except Exception as e:
            error_message = self._handle_llm_exception(e)
            await message.reply(error_message, silent=True)

    async def _get_llm_response(self, messages: list) -> str:
        current_llm_call_messages = messages
        for i in range(self.llm_config.get('max_tool_iterations', 3)):
            tools_def = self.get_tools_definition()
            tool_choice_val = "auto" if tools_def else None
            extra_params = self.llm_config.get('extra_api_parameters', {})

            response = await self.main_llm_client.chat.completions.create(
                model=self.main_llm_client.model_name_for_api_calls, messages=current_llm_call_messages,
                tools=tools_def, tool_choice=tool_choice_val,
                temperature=extra_params.get('temperature', 0.7), max_tokens=extra_params.get('max_tokens', 4096)
            )
            response_message = response.choices[0].message
            current_llm_call_messages.append(response_message.model_dump(exclude_none=True))

            if response_message.tool_calls:
                await self._process_tool_calls(response_message.tool_calls, current_llm_call_messages)
                continue
            else:
                return response_message.content

        return self.llm_config.get('error_msg', {}).get('tool_loop_timeout',
                                                        "ツール処理が複雑すぎたため、応答を中断しました。")

    async def _process_tool_calls(self, tool_calls, messages: list):
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            tool_output_content = ""
            if self.search_agent and function_name == self.search_agent.name:
                try:
                    function_args = json.loads(tool_call.function.arguments)
                    tool_output_content = await self.search_agent.run(arguments=function_args, bot=self.bot)
                except Exception as e:
                    tool_output_content = f"エラー: ツール '{function_name}' 実行エラー: {e}"
            else:
                tool_output_content = f"エラー: 未対応または利用不可能なツール '{function_name}'"

            messages.append({"tool_call_id": tool_call.id, "role": "tool", "name": function_name,
                             "content": str(tool_output_content)})

    def _handle_llm_exception(self, e: Exception) -> str:
        if isinstance(e, openai.APIConnectionError):
            logger.error(f"LLM API接続エラー: {e}")
            return self.llm_config.get('error_msg', {}).get('general_error', "AIとの接続に失敗しました。")
        elif isinstance(e, openai.RateLimitError):
            logger.warning(f"LLM APIレート制限超過。")
            return self.llm_config.get('error_msg', {}).get('ratelimit_error',
                                                            "AIが混み合っています。しばらくしてから再度お試しください。")
        elif isinstance(e, openai.APIStatusError):
            logger.error(f"LLM APIステータスエラー: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            return self.llm_config.get('error_msg', {}).get('general_error',
                                                            f"APIでエラーが発生しました (コード: {e.status_code})。")
        else:
            logger.error(f"予期しないエラー: {e}", exc_info=True)
            return self.llm_config.get('error_msg', {}).get('general_error', "予期せぬエラーが発生しました。")

    async def _send_reply_chunks(self, message: discord.Message, text_content: str):
        response_chunks = self._split_message(text_content)
        if not response_chunks: return

        try:
            await message.reply(response_chunks[0], silent=True)
        except discord.HTTPException as e:
            logger.warning(f"リプライでの送信に失敗。通常の送信にフォールバックします。エラー: {e}")
            await message.channel.send(response_chunks[0], silent=True)

        if len(response_chunks) > 1:
            for chunk in response_chunks[1:]:
                await message.channel.send(chunk, silent=True)

    def _split_message(self, text_content: str, max_length: int = 1990) -> list[str]:
        if not text_content: return []
        chunks = []
        current_chunk = ""
        for line in text_content.splitlines(keepends=True):
            if len(current_chunk) + len(line) > max_length:
                if current_chunk: chunks.append(current_chunk)
                current_chunk = line
                while len(current_chunk) > max_length:
                    chunks.append(current_chunk[:max_length])
                    current_chunk = current_chunk[max_length:]
            else:
                current_chunk += line
        if current_chunk: chunks.append(current_chunk)
        return chunks if chunks else []

    @app_commands.command(name="llm_help", description="LLM (AI対話) 機能に関する詳細なヘルプを表示します。")
    async def llm_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        embed = discord.Embed(title="💡 LLM (AI対話) 機能 ヘルプ",
                              description=f"{self.bot.user.name if self.bot.user else '当Bot'} のAI対話機能についての説明です。",
                              color=discord.Color.purple())
        if self.bot.user and self.bot.user.avatar: embed.set_thumbnail(url=self.bot.user.avatar.url)
        embed.add_field(
            name="基本的な使い方",
            value=f"• Botにメンション (`@{self.bot.user.name if self.bot.user else 'Bot'}`) して話しかけると、AIが応答します。\n"
                  f"• メッセージと一緒に画像を添付、または画像URLを貼り付けると、AIが画像の内容も理解しようとします。\n"
                  f"• 他の人のメッセージに返信する形でメンションすると、引用元の画像も認識します。",
            inline=False
        )
        model_name = self.llm_config.get('model', '未設定');
        max_hist = self.llm_config.get('max_messages', '未設定')
        embed.add_field(
            name="現在のAI設定",
            value=f"• **使用モデル:** `{model_name}`\n"
                  f"• **会話履歴の最大保持数:** {max_hist} ペア\n"
                  f"• **一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', '未設定')} 枚",
            inline=False
        )
        await interaction.followup.send(embed=embed, ephemeral=False)

    @app_commands.command(name="llm_help_en",
                          description="Displays detailed help for LLM (AI Chat) features in English.")
    async def llm_help_en_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_name = self.bot.user.name if self.bot.user else "This Bot"
        embed = discord.Embed(title="💡 LLM (AI Chat) Feature Help",
                              description=f"This is an explanation of the AI chat features for {bot_name}.",
                              color=discord.Color.purple())
        if self.bot.user and self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        embed.add_field(
            name="Basic Usage",
            value=f"• Mention the bot (`@{bot_name}`) to get a response from the AI.\n"
                  f"• Attach images or paste image URLs with your message, and the AI will try to understand them.\n"
                  f"• If you reply to another message while mentioning the bot, it will also see the images in the replied-to message.",
            inline=False
        )
        model_name_en = self.llm_config.get('model', 'Not set')
        max_hist_en = self.llm_config.get('max_messages', 'Not set')
        max_images_en = self.llm_config.get('max_images', 'Not set')
        settings_value = (f"• **Model in Use:** `{model_name_en}`\n"
                          f"• **Max Conversation History:** {max_hist_en} pairs\n"
                          f"• **Max Images Processed at Once:** {max_images_en} image(s)")
        embed.add_field(name="Current AI Settings", value=settings_value, inline=False)

        active_tools_list_en = self.llm_config.get('active_tools', [])
        tools_description_en = ""
        if 'search' in active_tools_list_en and self.search_agent:
            tools_description_en += f"• **Web Search (Search):** If the AI deems it necessary, it will search the internet for information to use in its response.\n"
            search_model_en = self.llm_config.get('search_agent', {}).get('model', 'Not set')
            tools_description_en += f"  *Search Agent Model: `{search_model_en}`*\n"
        if not tools_description_en:
            tools_description_en = "Currently, no special additional features (tools) are enabled."
        embed.add_field(name="AI's Additional Features (Tools)", value=tools_description_en, inline=False)

        embed.add_field(
            name="Tips & Important Notes",
            value="• The AI does not always provide correct information. Always verify important information yourself.\n"
                  "• Conversations are remembered separately for each channel.\n"
                  "• Excessively long conversations or overly complex instructions can confuse the AI.\n"
                  "• Do not send personal or sensitive information.",
            inline=False
        )
        embed.set_footer(text="This feature is under development and specifications may change.")

        await interaction.followup.send(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot):
    try:
        cog_instance = LLMCog(bot)
        await bot.add_cog(cog_instance)
        logger.info("LLMCogが正常にロードされました。")
    except Exception as e:
        logger.error(f"LLMCogのセットアップ中にエラーが発生しました: {e}", exc_info=True)
        if 'cog_instance' in locals() and hasattr(cog_instance,
                                                  'http_session') and not cog_instance.http_session.closed:
            await cog_instance.http_session.close()
        raise