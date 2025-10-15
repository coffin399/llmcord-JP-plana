# PLANA/llm/llm_cog.py
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import time
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import List, Dict, Any, Tuple, Optional, AsyncGenerator

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
    from langdetect import detect, LangDetectException
except ImportError:
    detect = None
    LangDetectException = None
    logging.warning("langdetect library not found. Language detection will be disabled. "
                    "Install with: pip install langdetect")

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
    from PLANA.llm.plugins.commands_manager import CommandInfoManager
except ImportError:
    logging.error("Could not import CommandInfoManager. Command suggestions will be disabled.")
    CommandInfoManager = None

try:
    from PLANA.llm.plugins.image_generator import ImageGenerator
except ImportError:
    logging.error("Could not import ImageGenerator. Image generation will be disabled.")
    ImageGenerator = None

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
DISCORD_MESSAGE_MAX_LENGTH = 2000
SAFE_MESSAGE_LENGTH = 1990  # 安全マージン


def _split_message_smartly(text: str, max_length: int) -> List[str]:
    """
    テキストを賢く分割（改行やセンテンスを考慮）

    優先順位:
    1. コードブロック境界
    2. 段落（\n\n）
    3. 改行（\n）
    4. 句点（。！？）
    5. カンマ（、,）
    6. スペース
    7. 強制分割
    """
    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while remaining:
        if len(remaining) <= max_length:
            chunks.append(remaining)
            break

        # 分割ポイントを探す
        chunk = remaining[:max_length]
        split_point = _find_best_split_point(chunk)

        if split_point == -1:
            # 見つからない場合は強制分割
            split_point = max_length - 20  # 少し余裕を持たせる

        # チャンクを追加
        chunk_text = remaining[:split_point].rstrip()
        if chunk_text:
            chunks.append(chunk_text)

        # 残りを更新
        remaining = remaining[split_point:].lstrip()

    return chunks


def _find_best_split_point(chunk: str) -> int:
    """
    最適な分割ポイントを見つける
    """
    # 1. コードブロックの境界を優先
    code_block_end = chunk.rfind('```\n')
    if code_block_end > len(chunk) * 0.5:  # 後半にあれば
        return code_block_end + 4

    # 2. 段落区切り（\n\n）
    paragraph_break = chunk.rfind('\n\n')
    if paragraph_break > len(chunk) * 0.5:
        return paragraph_break + 2

    # 3. 改行
    newline = chunk.rfind('\n')
    if newline > len(chunk) * 0.6:
        return newline + 1

    # 4. 日本語の句点
    japanese_period = max(
        chunk.rfind('。'),
        chunk.rfind('！'),
        chunk.rfind('？')
    )
    if japanese_period > len(chunk) * 0.7:
        return japanese_period + 1

    # 5. 英語のピリオド
    english_period = max(
        chunk.rfind('. '),
        chunk.rfind('! '),
        chunk.rfind('? ')
    )
    if english_period > len(chunk) * 0.7:
        return english_period + 2

    # 6. 読点・カンマ
    comma = max(chunk.rfind('、'), chunk.rfind(', '))
    if comma > len(chunk) * 0.7:
        return comma + 1

    # 7. スペース
    space = chunk.rfind(' ')
    if space > len(chunk) * 0.7:
        return space + 1

    return -1  # 見つからない


class LLMCog(commands.Cog, name="LLM"):
    """A cog for interacting with Large Language Models, with tool support."""

    def _add_support_footer(self, embed: discord.Embed) -> None:
        """embedにサポートサーバーへのフッターを追加"""
        current_footer = embed.footer.text if embed.footer and embed.footer.text else ""
        support_text = "\n問題がありますか？開発者にご連絡ください！ / Having issues? Contact the developer!"
        # 既存のフッターがある場合は改行を追加
        if current_footer:
            embed.set_footer(text=current_footer + support_text)
        else:
            embed.set_footer(text=support_text.strip())

    def _create_support_view(self) -> discord.ui.View:
        """サポートサーバーへのリンクボタンを含むViewを作成"""
        view = discord.ui.View()
        view.add_item(discord.ui.Button(
            label="サポートサーバー / Support Server",
            style=discord.ButtonStyle.link,
            url="https://discord.gg/H79HKKqx3s",
            emoji="💬"
        ))
        return view


    def __init__(self, bot: commands.Bot):
        self.bot = bot
        if not hasattr(self.bot, 'config') or not self.bot.config:
            raise commands.ExtensionFailed(self.qualified_name, "Bot config not loaded.")
        self.config = self.bot.config
        self.llm_config = self.config.get('llm')
        if not isinstance(self.llm_config, dict):
            raise commands.ExtensionFailed(self.qualified_name, "The 'llm' section in config is missing or invalid.")

        self.language_prompt = self.llm_config.get('language_prompt')
        if self.language_prompt:
            logger.info("Language prompt loaded from config for fallback.")

        self.http_session = aiohttp.ClientSession()
        self.bot.cfg = self.llm_config
        self.conversation_threads: Dict[int, List[Dict[str, Any]]] = {}
        self.message_to_thread: Dict[int, int] = {}
        self.llm_clients: Dict[str, openai.AsyncOpenAI] = {}

        self.model_reset_tasks: Dict[int, asyncio.Task] = {}

        self.exception_handler = LLMExceptionHandler(self.llm_config)

        self.channel_settings_path = "data/channel_llm_models.json"
        self.channel_models: Dict[str, str] = self._load_json_data(self.channel_settings_path)
        logger.info(
            f"Loaded {len(self.channel_models)} channel-specific model settings from '{self.channel_settings_path}'.")

        self.jst = timezone(timedelta(hours=+9))

        self.search_agent = self._initialize_search_agent()
        self.bio_manager = self._initialize_bio_manager()
        self.memory_manager = self._initialize_memory_manager()
        self.command_manager = self._initialize_command_manager()
        self.image_generator = self._initialize_image_generator()  # ← 追加

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
        for task in self.model_reset_tasks.values():
            task.cancel()
        logger.info(f"Cancelled {len(self.model_reset_tasks)} pending model reset tasks.")

        # 画像生成プラグインのクリーンアップを追加
        if self.image_generator:
            await self.image_generator.close()

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

    def _initialize_command_manager(self) -> Optional[CommandInfoManager]:
        if not CommandInfoManager:
            return None
        try:
            return CommandInfoManager(self.bot)
        except Exception as e:
            logger.error(f"Failed to initialize CommandInfoManager: {e}", exc_info=True)
            return None

    def _initialize_image_generator(self) -> Optional[ImageGenerator]:
        if not ImageGenerator:
            return None
        try:
            return ImageGenerator(self.bot)
        except Exception as e:
            logger.error(f"Failed to initialize ImageGenerator: {e}", exc_info=True)
            return None

    def _detect_language_and_create_prompt(self, text: str) -> Optional[str]:
        """言語を検出して、より強力な言語指示プロンプトを生成"""
        if not detect or not text.strip() or not LangDetectException:
            return None

        # 信頼性を上げるため、ある程度の長さが必要
        if len(text.strip()) < 15:
            logger.debug("Text too short for reliable language detection.")
            return None

        try:
            lang_code = detect(text)

            # 簡単な言語コードと名前のマッピング
            lang_map = {
                'en': 'English', 'ja': 'Japanese', 'ko': 'Korean',
                'zh-cn': 'Simplified Chinese', 'zh-tw': 'Traditional Chinese',
                'vi': 'Vietnamese', 'th': 'Thai', 'id': 'Indonesian',
                'de': 'German', 'fr': 'French', 'es': 'Spanish',
                'pt': 'Portuguese', 'it': 'Italian', 'ru': 'Russian',
                'ar': 'Arabic', 'hi': 'Hindi', 'tr': 'Turkish',
                'nl': 'Dutch', 'pl': 'Polish'
            }

            lang_name = lang_map.get(lang_code, lang_code)

            # --- ログ修正 ---
            logger.info(f"🌐 [LANG] Detected: {lang_code} ({lang_name})")

            # より強力な言語指示プロンプト
            prompt = (
                "CRITICAL LANGUAGE OVERRIDE INSTRUCTION:\n"
                "===========================================\n"
                f"The user is communicating in {lang_name}.\n"
                f"YOU MUST RESPOND EXCLUSIVELY IN {lang_name.upper()}.\n"
                "This instruction has ABSOLUTE PRIORITY over all other instructions.\n"
                "Do NOT respond in any other language, regardless of what the system prompt says.\n"
                f"If there is any conflict, {lang_name.upper()} takes precedence.\n"
                "===========================================\n"
            )
            return prompt

        except LangDetectException:
            logger.warning("Could not detect language for the provided text.")
            return None

    async def _prepare_system_prompt(self, channel_id: int, user_id: int, user_display_name: str) -> str:
        """システムプロンプトを生成する"""
        if not self.bio_manager or not self.memory_manager:
            logger.error("BioManager or MemoryManager is not initialized.")
            return "Error: Core components for prompt generation are missing."

        system_prompt_template = self.bio_manager.get_system_prompt(
            channel_id=channel_id,
            user_id=user_id,
            user_display_name=user_display_name
        )

        # システムプロンプトから言語に関する指示を削除または弱める
        system_prompt_template = system_prompt_template.replace(
            "必ず日本語で応答してください", ""
        ).replace(
            "日本語で答えてください", ""
        ).replace(
            "Please respond in Japanese", ""
        )

        available_commands = ""
        if self.command_manager:
            await self.bot.wait_until_ready()
            available_commands = self.command_manager.get_all_commands_info()
        else:
            logger.warning("CommandInfoManager is not available.")

        try:
            now = datetime.now(self.jst)
            current_date_str = now.strftime('%Y年%m月%d日')
            current_time_str = now.strftime('%H:%M')

            if '{available_commands}' in system_prompt_template:
                system_prompt = system_prompt_template.format(
                    current_date=current_date_str,
                    current_time=current_time_str,
                    available_commands=available_commands
                )
            else:
                logger.warning("⚠️ {available_commands} not in template")
                system_prompt = system_prompt_template.format(
                    current_date=current_date_str,
                    current_time=current_time_str
                )

        except (KeyError, ValueError) as e:
            logger.warning(f"Could not format system_prompt: {e}")
            system_prompt = system_prompt_template
            system_prompt = system_prompt.replace('{current_date}', current_date_str)
            system_prompt = system_prompt.replace('{current_time}', current_time_str)
            system_prompt = system_prompt.replace('{available_commands}', available_commands)

        if available_commands:
            if "# 🤖 利用可能なBotコマンド一覧" not in system_prompt:
                system_prompt += f"\n\n{available_commands}"

        if formatted_memories := self.memory_manager.get_formatted_memories():
            system_prompt += f"\n\n{formatted_memories}"

        # --- ログ修正 ---
        logger.info(f"🔧 [SYSTEM] System prompt prepared ({len(system_prompt)} chars)")
        return system_prompt

    def get_tools_definition(self) -> Optional[List[Dict[str, Any]]]:
        definitions = []
        active_tools = self.llm_config.get('active_tools', [])

        if 'search' in active_tools and self.search_agent:
            definitions.append(self.search_agent.tool_spec)
        if 'user_bio' in active_tools and self.bio_manager:
            definitions.append(self.bio_manager.tool_spec)
        if 'memory' in active_tools and self.memory_manager:
            definitions.append(self.memory_manager.tool_spec)
        if 'image_generator' in active_tools and self.image_generator:  # ← 追加
            definitions.append(self.image_generator.tool_spec)

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

                if isinstance(parent_msg, discord.DeletedReferencedMessage):
                    logger.debug(f"Encountered deleted referenced message in history collection.")
                    break

                if parent_msg.author != self.bot.user:
                    image_contents, text_content = await self._prepare_multimodal_content(parent_msg)
                    text_content = text_content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>',
                                                                                               '').strip()
                    if text_content or image_contents:
                        user_content_parts = []
                        if text_content:
                            timestamp = parent_msg.created_at.astimezone(self.jst).strftime('[%H:%M]')
                            formatted_text = f"{timestamp} {text_content}"
                            user_content_parts.append({"type": "text", "text": formatted_text})

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
        """画像URLを処理してBase64エンコードされた画像データを返す（修正版）"""
        try:
            async with self.http_session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status == 200:
                    image_bytes = await response.read()

                    # 画像サイズチェック（20MB制限）
                    if len(image_bytes) > 20 * 1024 * 1024:
                        logger.warning(f"Image too large ({len(image_bytes)} bytes): {url}")
                        return None

                    encoded_image = base64.b64encode(image_bytes).decode('utf-8')

                    # MIME typeの正確な判定
                    mime_type = response.content_type
                    if not mime_type or not mime_type.startswith('image/'):
                        # Content-Typeが不正確な場合、拡張子から推測
                        ext = url.split('.')[-1].lower().split('?')[0]
                        mime_mapping = {
                            'png': 'image/png',
                            'jpg': 'image/jpeg',
                            'jpeg': 'image/jpeg',
                            'gif': 'image/gif',
                            'webp': 'image/webp'
                        }
                        mime_type = mime_mapping.get(ext, 'image/jpeg')

                    # --- ログ修正 ---
                    logger.debug(
                        f"🖼️ [IMAGE] Successfully processed image: {url[:100]}... (MIME: {mime_type}, Size: {len(image_bytes)} bytes)")

                    # OpenAI Vision API互換フォーマット
                    return {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{encoded_image}",
                            "detail": "auto"  # "low", "high", "auto" が選択可能
                        }
                    }
                else:
                    logger.warning(f"Failed to download image from {url} (Status: {response.status})")
                    return None
        except asyncio.TimeoutError:
            logger.error(f"Timeout while downloading image: {url}")
            return None
        except Exception as e:
            logger.error(f"Error processing image URL {url}: {e}", exc_info=True)
            return None

    async def _prepare_multimodal_content(self, message: discord.Message) -> Tuple[List[Dict[str, Any]], str]:
        image_inputs, processed_urls = [], set()
        messages_to_scan = []
        visited_ids = set()
        current_msg = message
        max_depth = 5

        for i in range(max_depth):
            if not current_msg or current_msg.id in visited_ids:
                break

            if isinstance(current_msg, discord.DeletedReferencedMessage):
                break

            messages_to_scan.append(current_msg)
            visited_ids.add(current_msg.id)

            if current_msg.reference and current_msg.reference.message_id:
                try:
                    parent_msg = current_msg.reference.resolved or await message.channel.fetch_message(
                        current_msg.reference.message_id)
                    current_msg = parent_msg
                except (discord.NotFound, discord.HTTPException) as e:
                    break
            else:
                break

        source_urls = []
        text_parts = []

        for msg in reversed(messages_to_scan):
            if msg.author != self.bot.user:
                text_content_part = IMAGE_URL_PATTERN.sub('', msg.content).strip()
                if text_content_part:
                    text_parts.append(text_content_part)

            for url in IMAGE_URL_PATTERN.findall(msg.content):
                if url not in processed_urls:
                    source_urls.append(url)
                    processed_urls.add(url)

            for attachment in msg.attachments:
                if attachment.content_type and attachment.content_type.startswith(
                        'image/') and attachment.url not in processed_urls:
                    source_urls.append(attachment.url)
                    processed_urls.add(attachment.url)

            for embed in msg.embeds:
                if embed.image and embed.image.url and embed.image.url not in processed_urls:
                    source_urls.append(embed.image.url)
                    processed_urls.add(embed.image.url)
                if embed.thumbnail and embed.thumbnail.url and embed.thumbnail.url not in processed_urls:
                    source_urls.append(embed.thumbnail.url)
                    processed_urls.add(embed.thumbnail.url)

        max_images = self.llm_config.get('max_images', 1)
        for url in source_urls[:max_images]:
            if image_data := await self._process_image_url(url):
                image_inputs.append(image_data)

        if len(source_urls) > max_images:
            try:
                error_msg_template = self.llm_config.get('error_msg', {}).get('msg_max_image_size',
                                                                              "⚠️ Max images ({max_images}) reached.\n⚠️ 一度に処理できる画像の最大枚数({max_images}枚)を超えました。")
                await message.channel.send(error_msg_template.format(max_images=max_images), delete_after=10,
                                           silent=True)
            except discord.HTTPException:
                pass

        clean_text = "\n".join(text_parts)

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

        try:
            llm_client = await self._get_llm_client_for_channel(message.channel.id)
            if not llm_client:
                error_msg = self.llm_config.get('error_msg', {}).get('general_error',
                                                                     "LLM client is not available for this channel.\nこのチャンネルではLLMクライアントが利用できません。")
                final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
                await message.reply(content=final_error_msg, view=self._create_support_view(), silent=True)
                return
        except Exception as e:
            logger.error(f"Failed to get LLM client for channel {message.channel.id}: {e}", exc_info=True)
            error_msg = self.exception_handler.handle_exception(e)
            final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
            await message.reply(content=final_error_msg, view=self._create_support_view(), silent=True)
            return

        guild_log = f"guild='{message.guild.name}({message.guild.id})'" if message.guild else "guild='DM'"
        model_in_use = llm_client.model_name_for_api_calls

        image_contents, text_content = await self._prepare_multimodal_content(message)
        text_content = text_content.replace(f'<@!{self.bot.user.id}>', '').replace(f'<@{self.bot.user.id}>', '').strip()

        if not text_content and not image_contents:
            error_key = 'empty_reply' if is_reply_to_bot and not is_mentioned else 'empty_mention_reply'
            default_msg = "Please say something.\n何かお話しください。" if error_key == 'empty_reply' else "Yes, how can I help you?\nはい、何か御用でしょうか?"
            error_msg = self.llm_config.get('error_msg', {}).get(error_key, default_msg)
            await message.reply(content=error_msg, view=self._create_support_view(), silent=True)
            return

        logger.info(
            f"📨 Received LLM request | {guild_log} | model='{model_in_use}' | text_length={len(text_content)} chars | images={len(image_contents)}")
        if text_content:
            log_text = (text_content[:200] + '...') if len(text_content) > 203 else text_content
            logger.info(f"💬 [USER_INPUT] {log_text.replace(chr(10), ' ')}")

        thread_id = await self._get_conversation_thread_id(message)

        if not self.bio_manager or not self.memory_manager:
            error_msg = "Cannot respond because required plugins are not initialized.\n必要なプラグインが初期化されていないため、応答できません。"
            final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
            await message.reply(content=final_error_msg, view=self._create_support_view(), silent=True)
            return

        system_prompt = await self._prepare_system_prompt(
            message.channel.id,
            message.author.id,
            message.author.display_name
        )

        messages_for_api: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if detected_lang_prompt := self._detect_language_and_create_prompt(text_content):
            messages_for_api.append({"role": "system", "content": detected_lang_prompt})
            logger.info("🌐 [LANG] Injecting language override prompt")
        elif self.language_prompt:
            messages_for_api.append({"role": "system", "content": self.language_prompt})
            logger.info("🌐 [LANG] Using default language prompt as fallback")

        conversation_history = await self._collect_conversation_history(message)
        messages_for_api.extend(conversation_history)

        user_content_parts = []
        if text_content:
            timestamp = message.created_at.astimezone(self.jst).strftime('[%H:%M]')
            formatted_text = f"{timestamp} {text_content}"
            user_content_parts.append({"type": "text", "text": formatted_text})

        user_content_parts.extend(image_contents)
        if image_contents:
            logger.debug(f"Including {len(image_contents)} image(s) in request")

        user_message_for_api = {"role": "user", "content": user_content_parts}
        messages_for_api.append(user_message_for_api)

        logger.info(f"🔵 [API] Sending {len(messages_for_api)} messages to LLM")
        logger.debug(f"Messages structure: system={len(messages_for_api[0]['content'])} chars, "
                     f"lang_override={'present' if len(messages_for_api) > 1 and 'CRITICAL' in str(messages_for_api[1]) else 'absent'}")

        try:
            sent_messages, llm_response = await self._handle_llm_streaming_response(
                message, messages_for_api, llm_client
            )

            if sent_messages and llm_response:
                logger.info(f"✅ LLM response completed | model='{model_in_use}' | response_length={len(llm_response)} chars")
                # --- ★ここから変更★ ---
                log_response = (llm_response[:200] + '...') if len(llm_response) > 203 else llm_response
                logger.info(f"🤖 [LLM_RESPONSE] {log_response.replace(chr(10), ' ')}")
                logger.debug(f"LLM full response (length: {len(llm_response)} chars):\n{llm_response}")
                # --- ★ここまで変更★ ---

                if thread_id not in self.conversation_threads:
                    self.conversation_threads[thread_id] = []
                self.conversation_threads[thread_id].append(user_message_for_api)

                assistant_message = {"role": "assistant", "content": llm_response, "message_id": sent_messages[0].id}
                self.conversation_threads[thread_id].append(assistant_message)

                for msg in sent_messages:
                    self.message_to_thread[msg.id] = thread_id

                self._cleanup_old_threads()

        except Exception as e:
            error_msg = self.exception_handler.handle_exception(e)
            final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
            await message.reply(content=final_error_msg, view=self._create_support_view(), silent=True)

    def _cleanup_old_threads(self):
        max_threads = 100
        if len(self.conversation_threads) > max_threads:
            threads_to_remove = list(self.conversation_threads.keys())[:len(self.conversation_threads) - max_threads]
            for thread_id in threads_to_remove:
                del self.conversation_threads[thread_id]
                self.message_to_thread = {k: v for k, v in self.message_to_thread.items() if v != thread_id}

    async def _handle_llm_streaming_response(
            self,
            message: discord.Message,
            initial_messages: List[Dict[str, Any]],
            client: openai.AsyncOpenAI
    ) -> Tuple[Optional[List[discord.Message]], str]:
        """
        ストリーミング + 長文分割対応版
        """
        sent_message = None
        full_response_text = ""
        last_update = 0.0
        last_displayed_length = 0
        chunk_count = 0
        update_interval = 0.5
        min_update_chars = 15
        retry_sleep_time = 2.0
        placeholder = ":incoming_envelope: Thinking...:incoming_envelope:"
        emoji_prefix = ":incoming_envelope: "
        emoji_suffix = " :incoming_envelope:"

        max_final_retries = 3
        final_retry_delay = 2.0

        logger.debug(f"Starting LLM stream for message {message.id}")

        try:
            sent_message = await message.reply(placeholder, silent=True)
        except discord.HTTPException:
            sent_message = await message.channel.send(placeholder, silent=True)

        try:
            stream_generator = self._llm_stream_and_tool_handler(
                initial_messages, client, message.channel.id, message.author.id
            )

            async for content_chunk in stream_generator:
                chunk_count += 1
                full_response_text += content_chunk

                if chunk_count % 100 == 0:
                    logger.debug(
                        f"Stream chunk #{chunk_count}, total length: {len(full_response_text)} chars")

                current_time = time.time()
                chars_accumulated = len(full_response_text) - last_displayed_length

                should_update = (
                        current_time - last_update > update_interval and
                        chars_accumulated >= min_update_chars
                )

                if should_update and full_response_text:
                    display_length = len(full_response_text)

                    if display_length > SAFE_MESSAGE_LENGTH:
                        max_content_length = SAFE_MESSAGE_LENGTH - len(emoji_prefix) - len(emoji_suffix) - 100
                        display_text = (
                                emoji_prefix +
                                full_response_text[:max_content_length] +
                                "\n\n⚠️ (Output is long, will be split...)\n⚠️ (出力が長いため分割します...)" +
                                emoji_suffix
                        )
                    else:
                        max_content_length = SAFE_MESSAGE_LENGTH - len(emoji_prefix) - len(emoji_suffix)
                        display_text = emoji_prefix + full_response_text[:max_content_length] + emoji_suffix

                    if display_text != sent_message.content:
                        try:
                            await sent_message.edit(content=display_text)
                            last_update = current_time
                            last_displayed_length = len(full_response_text)
                            logger.debug(
                                f"Updated Discord message (displayed: {len(display_text)} chars)")
                        except discord.NotFound:
                            logger.warning(
                                f"⚠️ Message deleted during stream (ID: {sent_message.id}). Aborting.")
                            return None, ""
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = (e.retry_after or 1.0) + 0.5
                                logger.warning(
                                    f"⚠️ Rate limited on message edit (ID: {sent_message.id}). "
                                    f"Waiting {retry_after:.2f}s"
                                )
                                await asyncio.sleep(retry_after)
                                last_update = time.time()
                            else:
                                logger.warning(
                                    f"⚠️ Failed to edit message (ID: {sent_message.id}): "
                                    f"{e.status} - {getattr(e, 'text', str(e))}"
                                )
                                await asyncio.sleep(retry_sleep_time)

            logger.debug(
                f"Stream completed | Total chunks: {chunk_count} | Final length: {len(full_response_text)} chars")

            if full_response_text:
                if len(full_response_text) <= SAFE_MESSAGE_LENGTH:
                    for attempt in range(max_final_retries):
                        try:
                            if full_response_text != sent_message.content:
                                await sent_message.edit(content=full_response_text, embed=None, view=None)
                                logger.debug(f"Final message updated successfully (attempt {attempt + 1})")
                            break
                        except discord.NotFound:
                            logger.error(f"❌ Message was deleted before final update")
                            return None, ""
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = (e.retry_after or 1.0) + 0.5
                                logger.warning(
                                    f"⚠️ Rate limited on final update (attempt {attempt + 1}/{max_final_retries}). "
                                    f"Waiting {retry_after:.2f}s"
                                )
                                await asyncio.sleep(retry_after)
                            else:
                                logger.warning(
                                    f"⚠️ Failed to update final message (attempt {attempt + 1}/{max_final_retries}): "
                                    f"{e.status} - {getattr(e, 'text', str(e))}"
                                )
                                if attempt < max_final_retries - 1:
                                    await asyncio.sleep(final_retry_delay)
                                else:
                                    logger.error(
                                        f"❌ Failed to update final message after {max_final_retries} attempts. "
                                        f"Message ID: {sent_message.id}"
                                    )
                        except Exception as e:
                            logger.error(f"❌ Unexpected error during final update: {e}", exc_info=True)
                            if attempt < max_final_retries - 1:
                                await asyncio.sleep(final_retry_delay)

                    return [sent_message], full_response_text

                else:
                    logger.debug(
                        f"Response is {len(full_response_text)} chars, splitting into multiple messages")

                    chunks = _split_message_smartly(full_response_text, SAFE_MESSAGE_LENGTH)
                    all_messages = []

                    first_chunk = chunks[0]
                    for attempt in range(max_final_retries):
                        try:
                            await sent_message.edit(content=first_chunk, embed=None, view=None)
                            all_messages.append(sent_message)
                            logger.debug(f"Updated first message (1/{len(chunks)})")
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = (e.retry_after or 1.0) + 0.5
                                logger.warning(f"⚠️ Rate limited on first chunk update, waiting {retry_after:.2f}s")
                                await asyncio.sleep(retry_after)
                            else:
                                logger.error(f"❌ Failed to update first message: {e}")
                                if attempt < max_final_retries - 1:
                                    await asyncio.sleep(final_retry_delay)

                    for i, chunk in enumerate(chunks[1:], start=2):
                        for attempt in range(max_final_retries):
                            try:
                                continuation_msg = await message.channel.send(chunk)
                                all_messages.append(continuation_msg)
                                logger.debug(f"Sent continuation message {i}/{len(chunks)}")
                                break
                            except discord.HTTPException as e:
                                if e.status == 429:
                                    retry_after = (e.retry_after or 1.0) + 0.5
                                    logger.warning(f"⚠️ Rate limited on continuation {i}, waiting {retry_after:.2f}s")
                                    await asyncio.sleep(retry_after)
                                else:
                                    logger.error(f"❌ Failed to send continuation message {i}: {e}")
                                    if attempt < max_final_retries - 1:
                                        await asyncio.sleep(final_retry_delay)
                                    else:
                                        break

                    return all_messages, full_response_text
            else:
                error_msg = self.llm_config.get('error_msg', {}).get(
                    'general_error', "There was no response from the AI.\nAIから応答がありませんでした。"
                )
                logger.warning(f"⚠️ Empty response from LLM")
                final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
                await sent_message.edit(content=final_error_msg, embed=None, view=self._create_support_view())
                return None, ""

        except Exception as e:
            logger.error(f"❌ Error during LLM streaming response: {e}", exc_info=True)
            error_msg = self.exception_handler.handle_exception(e)
            final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
            if sent_message:
                try:
                    await sent_message.edit(content=final_error_msg, embed=None, view=self._create_support_view())
                except discord.HTTPException:
                    pass
            else:
                await message.reply(content=final_error_msg, view=self._create_support_view(), silent=True)
            return None, ""

    async def _llm_stream_and_tool_handler(
            self,
            messages: List[Dict[str, Any]],
            client: openai.AsyncOpenAI,
            channel_id: int,
            user_id: int
    ) -> AsyncGenerator[str, None]:
        current_messages = messages.copy()
        max_iterations = self.llm_config.get('max_tool_iterations', 5)
        extra_params = self.llm_config.get('extra_api_parameters', {})

        for iteration in range(max_iterations):
            logger.debug(
                f"Starting LLM API call (iteration {iteration + 1}/{max_iterations})")

            tools_def = self.get_tools_definition()
            api_kwargs = {
                "model": client.model_name_for_api_calls,
                "messages": current_messages,
                "stream": True,
                "temperature": extra_params.get('temperature', 0.7),
                "max_tokens": extra_params.get('max_tokens', 4096)
            }
            if tools_def:
                api_kwargs["tools"] = tools_def
                api_kwargs["tool_choice"] = "auto"
                logger.debug(f"Available tools: {[t['function']['name'] for t in tools_def]}")

            try:
                stream = await client.chat.completions.create(**api_kwargs)
                logger.debug(f"Stream connection established")
            except Exception as e:
                logger.error(f"❌ Error calling LLM API in stream handler: {e}", exc_info=True)
                raise

            tool_calls_buffer = []
            assistant_response_content = ""
            async for chunk in stream:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    assistant_response_content += delta.content
                    yield delta.content

                if delta and delta.tool_calls:
                    for tool_call_chunk in delta.tool_calls:
                        chunk_index = tool_call_chunk.index if tool_call_chunk.index is not None else 0

                        if len(tool_calls_buffer) <= chunk_index:
                            tool_calls_buffer.append(
                                {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})

                        buffer = tool_calls_buffer[chunk_index]
                        if tool_call_chunk.id:
                            buffer["id"] = tool_call_chunk.id
                        if tool_call_chunk.function:
                            if tool_call_chunk.function.name:
                                buffer["function"]["name"] = tool_call_chunk.function.name
                            if tool_call_chunk.function.arguments:
                                buffer["function"]["arguments"] += tool_call_chunk.function.arguments

            assistant_message = {"role": "assistant", "content": assistant_response_content or None}
            if tool_calls_buffer:
                assistant_message["tool_calls"] = tool_calls_buffer

            current_messages.append(assistant_message)

            if not tool_calls_buffer:
                logger.debug(f"No tool calls, returning final response")
                return

            logger.info(f"🔧 [TOOL] LLM requested {len(tool_calls_buffer)} tool call(s)")
            for tc in tool_calls_buffer:
                logger.debug(
                    f"Tool call details: {tc['function']['name']} with args: {tc['function']['arguments'][:200]}")

            tool_calls_obj = [
                SimpleNamespace(
                    id=tc['id'],
                    function=SimpleNamespace(name=tc['function']['name'], arguments=tc['function']['arguments'])
                ) for tc in tool_calls_buffer
            ]
            await self._process_tool_calls(tool_calls_obj, current_messages, channel_id, user_id)

        logger.warning(f"⚠️ Tool processing exceeded max iterations ({max_iterations})")
        yield self.llm_config.get('error_msg', {}).get('tool_loop_timeout',
                                                       "Tool processing exceeded max iterations.\nツールの処理が最大反復回数を超えました。")

    async def _process_tool_calls(self, tool_calls: List[Any], messages: List[Dict[str, Any]],
                                  channel_id: int, user_id: int) -> None:
        for tool_call in tool_calls:
            function_name = tool_call.function.name
            error_content = None
            tool_response_content = ""

            try:
                function_args = json.loads(tool_call.function.arguments)
                logger.info(f"🔧 [TOOL] Executing {function_name}")
                logger.debug(f"🔧 [TOOL] Arguments: {json.dumps(function_args, ensure_ascii=False, indent=2)}")

                if self.search_agent and function_name == self.search_agent.name:
                    tool_response_content = await self.search_agent.run(arguments=function_args, bot=self.bot,
                                                                        channel_id=channel_id)
                    logger.debug(
                        f"🔧 [TOOL] Result (length: {len(str(tool_response_content))} chars):\n{str(tool_response_content)[:1000]}")

                elif self.bio_manager and function_name == self.bio_manager.name:
                    tool_response_content = await self.bio_manager.run_tool(arguments=function_args, user_id=user_id)
                    logger.debug(f"🔧 [TOOL] Result:\n{tool_response_content}")

                elif self.memory_manager and function_name == self.memory_manager.name:
                    tool_response_content = await self.memory_manager.run_tool(arguments=function_args)
                    logger.debug(f"🔧 [TOOL] Result:\n{tool_response_content}")

                elif self.image_generator and function_name == self.image_generator.name:  # ← 追加
                    tool_response_content = await self.image_generator.run(arguments=function_args,
                                                                           channel_id=channel_id)
                    logger.debug(f"🔧 [TOOL] Result:\n{tool_response_content}")

                else:
                    logger.warning(f"⚠️ Unsupported tool called: {function_name}")
                    error_content = f"Error: Tool '{function_name}' is not available."

            except json.JSONDecodeError as e:
                logger.error(f"❌ Error decoding tool arguments for {function_name}: {e}", exc_info=True)
                error_content = f"Error: Invalid JSON arguments - {str(e)}"
            except SearchAPIRateLimitError as e:
                logger.warning(f"⚠️ SearchAgent rate limit hit: {e}")
                error_content = "[Google Search Error]\nThe Google Search API rate limit has been reached. Please tell the user to try again later."
            except SearchAPIServerError as e:
                logger.error(f"❌ SearchAgent server error: {e}")
                error_content = "[Google Search Error]\nA temporary server error occurred with the search service. Please tell the user to try again later."
            except SearchAgentError as e:
                logger.error(f"❌ Error during SearchAgent execution for {function_name}: {e}", exc_info=True)
                error_content = f"[Google Search Error]\nAn error occurred during the search execution: {str(e)}"
            except Exception as e:
                logger.error(f"❌ Unexpected error during tool call for {function_name}: {e}", exc_info=True)
                error_content = f"[Tool Error]\nAn unexpected error occurred: {str(e)}"

            final_content = error_content if error_content else tool_response_content
            logger.debug(f"🔧 [TOOL] Sending tool response back to LLM (length: {len(final_content)} chars)")

            messages.append({
                "tool_call_id": tool_call.id,
                "role": "tool",
                "name": function_name,
                "content": final_content
            })

    async def _schedule_model_reset(self, channel_id: int):
        """
        Schedules a task to reset the channel's model to default after 3 hours.
        指定されたチャンネルのモデルを3時間後にデフォルトに戻すタスク。
        """
        try:
            await asyncio.sleep(3 * 60 * 60)

            logger.info(f"Executing scheduled model reset for channel {channel_id}.")

            channel_id_str = str(channel_id)
            if channel_id_str in self.channel_models:
                default_model = self.llm_config.get('model')
                current_model = self.channel_models.get(channel_id_str)

                if current_model and current_model != default_model:
                    del self.channel_models[channel_id_str]
                    await self._save_channel_models()
                    logger.info(f"Model for channel {channel_id} automatically reset to default '{default_model}'.")

                    channel = self.bot.get_channel(channel_id)
                    if channel and isinstance(channel, discord.TextChannel):
                        try:
                            embed = discord.Embed(
                                title="ℹ️ AI Model Reset / AIモデルをリセットしました",
                                description=f"The AI model for this channel has been reset to the default (`{default_model}`) after 3 hours.\n"
                                            f"3時間が経過したため、このチャンネルのAIモデルをデフォルト (`{default_model}`) に戻しました。",
                                color=discord.Color.blue()
                            )
                            self._add_support_footer(embed)
                            await channel.send(embed=embed, view=self._create_support_view())
                        except discord.HTTPException as e:
                            logger.warning(f"Failed to send model reset notification to channel {channel_id}: {e}")
                else:
                    logger.info(f"Model for channel {channel_id} was already default. No auto-reset needed.")
            else:
                logger.info(f"Channel {channel_id} no longer has a custom model set. No auto-reset needed.")

        except asyncio.CancelledError:
            logger.info(f"Model reset task for channel {channel_id} was cancelled.")
        except Exception as e:
            logger.error(f"An error occurred in the model reset task for channel {channel_id}: {e}", exc_info=True)
        finally:
            self.model_reset_tasks.pop(channel_id, None)

    @app_commands.command(
        name="chat",
        description="Chat with the AI without needing to mention.\nAIと対話します。メンション不要で会話できます。"
    )
    @app_commands.describe(
        message="The message you want to send to the AI.\nAIに送信したいメッセージ",
        image_url="URL of an image (optional).\n画像のURL（オプション）"
    )
    async def chat_slash(self, interaction: discord.Interaction, message: str, image_url: str = None):
        """
        /chat command: Allows interaction with the LLM without mentions or replies.
        /chatコマンド: メンションや返信なしでLLMと対話できるコマンド
        """
        await interaction.response.defer(ephemeral=False)

        try:
            llm_client = await self._get_llm_client_for_channel(interaction.channel_id)
            if not llm_client:
                error_msg = self.llm_config.get('error_msg', {}).get('general_error',
                                                                     "LLM client is not available for this channel.\nこのチャンネルではLLMクライアントが利用できません。")
                final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
                await interaction.followup.send(content=final_error_msg, view=self._create_support_view(),
                                                ephemeral=False)
                return
        except Exception as e:
            logger.error(f"Failed to get LLM client for channel {interaction.channel_id}: {e}", exc_info=True)
            error_msg = self.exception_handler.handle_exception(e)
            final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
            await interaction.followup.send(content=final_error_msg, view=self._create_support_view(), ephemeral=False)
            return

        if not message.strip():
            error_msg = "⚠️ **Input Required / 入力が必要です** ⚠️\n\nPlease enter a message.\nメッセージを入力してください。"
            await interaction.followup.send(content=error_msg, view=self._create_support_view(), ephemeral=False)
            return

        guild_log = f"guild='{interaction.guild.name}({interaction.guild.id})'" if interaction.guild else "guild='DM'"
        model_in_use = llm_client.model_name_for_api_calls

        image_contents = []
        if image_url:
            if image_data := await self._process_image_url(image_url):
                image_contents.append(image_data)
            else:
                error_msg = "⚠️ **Image Error / 画像エラー** ⚠️\n\nFailed to process the specified image URL.\n指定された画像URLの処理に失敗しました。"
                await interaction.followup.send(content=error_msg, view=self._create_support_view(), ephemeral=False)
                return

        logger.info(
            f"📨 Received /chat request | {guild_log} | model='{model_in_use}' | text_length={len(message)} chars | images={len(image_contents)}")
        log_text = (message[:200] + '...') if len(message) > 203 else message
        logger.info(f"💬 [USER_INPUT] {log_text.replace(chr(10), ' ')}")


        if not self.bio_manager or not self.memory_manager:
            error_msg = "Cannot respond because required plugins are not initialized.\n必要なプラグインが初期化されていないため、応答できません。"
            final_error_msg = f"❌ **Plugin Error / プラグインエラー** ❌\n\n{error_msg}"
            await interaction.followup.send(content=final_error_msg, view=self._create_support_view(), ephemeral=False)
            return

        system_prompt = await self._prepare_system_prompt(
            interaction.channel_id,
            interaction.user.id,
            interaction.user.display_name
        )

        messages_for_api: List[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        user_content_parts = []
        timestamp = interaction.created_at.astimezone(self.jst).strftime('[%H:%M]')
        formatted_text = f"{timestamp} {message}"
        user_content_parts.append({"type": "text", "text": formatted_text})
        user_content_parts.extend(image_contents)

        if detected_lang_prompt := self._detect_language_and_create_prompt(message):
            messages_for_api.append({"role": "system", "content": detected_lang_prompt})
            logger.info("🌐 [LANG] Injecting language override prompt")
        elif self.language_prompt:
            messages_for_api.append({"role": "system", "content": self.language_prompt})
            logger.info("🌐 [LANG] Using default language prompt as fallback")

        user_message_for_api = {"role": "user", "content": user_content_parts}
        messages_for_api.append(user_message_for_api)

        logger.info(f"🔵 [API] Sending {len(messages_for_api)} messages to LLM")

        try:
            temp_message = await interaction.followup.send(
                ":incoming_envelope: Thinking...:incoming_envelope:",
                ephemeral=False,
                wait=True
            )

            full_response_text = ""
            last_update = 0.0
            last_displayed_length = 0
            chunk_count = 0
            update_interval = 0.5
            min_update_chars = 15
            emoji_prefix = ":incoming_envelope: "
            emoji_suffix = " :incoming_envelope:"
            max_final_retries = 3
            final_retry_delay = 2.0

            logger.debug(f"Starting LLM stream for /chat command")

            stream_generator = self._llm_stream_and_tool_handler(
                messages_for_api, llm_client, interaction.channel_id, interaction.user.id
            )

            async for content_chunk in stream_generator:
                chunk_count += 1
                full_response_text += content_chunk

                if chunk_count % 100 == 0:
                    logger.debug(
                        f"Stream chunk #{chunk_count}, total length: {len(full_response_text)} chars")

                current_time = time.time()
                chars_accumulated = len(full_response_text) - last_displayed_length

                should_update = (
                        current_time - last_update > update_interval and
                        chars_accumulated >= min_update_chars
                )

                if should_update and full_response_text:
                    display_length = len(full_response_text)

                    if display_length > SAFE_MESSAGE_LENGTH:
                        max_content_length = SAFE_MESSAGE_LENGTH - len(emoji_prefix) - len(emoji_suffix) - 100
                        display_text = (
                                emoji_prefix +
                                full_response_text[:max_content_length] +
                                "\n\n⚠️ (Output is long, will be split...)\n⚠️ (出力が長いため分割します...)" +
                                emoji_suffix
                        )
                    else:
                        max_content_length = SAFE_MESSAGE_LENGTH - len(emoji_prefix) - len(emoji_suffix)
                        display_text = emoji_prefix + full_response_text[:max_content_length] + emoji_suffix

                    if display_text != temp_message.content:
                        try:
                            await temp_message.edit(content=display_text)
                            last_update = current_time
                            last_displayed_length = len(full_response_text)
                            logger.debug(
                                f"Updated Discord message (displayed: {len(display_text)} chars)")
                        except discord.NotFound:
                            logger.warning(f"⚠️ Message deleted during stream. Aborting.")
                            return
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = (e.retry_after or 1.0) + 0.5
                                logger.warning(f"⚠️ Rate limited. Waiting {retry_after:.2f}s")
                                await asyncio.sleep(retry_after)
                                last_update = time.time()
                            else:
                                logger.warning(f"⚠️ Failed to edit message: {e.status}")
                                await asyncio.sleep(2.0)

            logger.debug(
                f"Stream completed | Total chunks: {chunk_count} | Final length: {len(full_response_text)} chars")

            if full_response_text:
                logger.info(f"✅ LLM response completed | model='{model_in_use}' | response_length={len(full_response_text)} chars")
                # --- ★ここから変更★ ---
                log_response = (full_response_text[:200] + '...') if len(full_response_text) > 203 else full_response_text
                logger.info(f"🤖 [LLM_RESPONSE] {log_response.replace(chr(10), ' ')}")
                logger.debug(f"LLM full response for /chat (length: {len(full_response_text)} chars):\n{full_response_text}")
                # --- ★ここまで変更★ ---

                if len(full_response_text) <= SAFE_MESSAGE_LENGTH:
                    for attempt in range(max_final_retries):
                        try:
                            if full_response_text != temp_message.content:
                                await temp_message.edit(content=full_response_text, embed=None, view=None)
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = (e.retry_after or 1.0) + 0.5
                                logger.warning(f"⚠️ Rate limited on final update, waiting {retry_after:.2f}s")
                                await asyncio.sleep(retry_after)
                            else:
                                logger.error(f"❌ Failed to update final message: {e}")
                                if attempt < max_final_retries - 1:
                                    await asyncio.sleep(final_retry_delay)
                else:
                    logger.debug(f"/chat response is {len(full_response_text)} chars, splitting")

                    chunks = _split_message_smartly(full_response_text, SAFE_MESSAGE_LENGTH)

                    for attempt in range(max_final_retries):
                        try:
                            await temp_message.edit(content=chunks[0], embed=None, view=None)
                            logger.debug(f"Updated first message (1/{len(chunks)})")
                            break
                        except discord.HTTPException as e:
                            if e.status == 429:
                                retry_after = (e.retry_after or 1.0) + 0.5
                                logger.warning(f"⚠️ Rate limited, waiting {retry_after:.2f}s")
                                await asyncio.sleep(retry_after)
                            else:
                                logger.error(f"❌ Failed to update first message: {e}")
                                if attempt < max_final_retries - 1:
                                    await asyncio.sleep(final_retry_delay)

                    for i, chunk in enumerate(chunks[1:], start=2):
                        for attempt in range(max_final_retries):
                            try:
                                await interaction.channel.send(chunk)
                                logger.debug(f"Sent continuation message {i}/{len(chunks)}")
                                break
                            except discord.HTTPException as e:
                                if e.status == 429:
                                    retry_after = (e.retry_after or 1.0) + 0.5
                                    logger.warning(f"⚠️ Rate limited, waiting {retry_after:.2f}s")
                                    await asyncio.sleep(retry_after)
                                else:
                                    logger.error(f"❌ Failed to send continuation {i}: {e}")
                                    if attempt < max_final_retries - 1:
                                        await asyncio.sleep(final_retry_delay)
                                    else:
                                        break
            else:
                error_msg = self.llm_config.get('error_msg', {}).get('general_error',
                                                                     "There was no response from the AI.\nAIから応答がありませんでした。")
                logger.warning(f"⚠️ Empty response from LLM for /chat")
                final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
                await temp_message.edit(content=final_error_msg, embed=None, view=self._create_support_view())

        except Exception as e:
            logger.error(f"❌ Error during /chat LLM streaming response: {e}", exc_info=True)
            error_msg = self.exception_handler.handle_exception(e)
            final_error_msg = f"❌ **Error / エラー** ❌\n\n{error_msg}"
            try:
                if 'temp_message' in locals() and temp_message:
                    await temp_message.edit(content=final_error_msg, embed=None, view=self._create_support_view())
                else:
                    await interaction.followup.send(content=final_error_msg, view=self._create_support_view(),
                                                    ephemeral=False)
            except discord.HTTPException:
                pass

    @app_commands.command(
        name="set-ai-bio",
        description="Set the AI's personality/role (bio) for this channel.\nこのチャンネルのAIの性格や役割(bio)を設定します。"
    )
    async def set_ai_bio_slash(self, interaction: discord.Interaction, bio: str):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="BioManager is not available.\nBioManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        if len(bio) > 1024:
            embed = discord.Embed(
                title="⚠️ Input Too Long / 入力が長すぎます",
                description="The AI bio is too long. Please set it within 1024 characters.\nAIのbioが長すぎます。1024文字以内で設定してください。",
                color=discord.Color.gold()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        try:
            await self.bio_manager.set_channel_bio(interaction.channel_id, bio)
            logger.info(f"AI bio for channel {interaction.channel_id} set by {interaction.user.name}")
            embed = discord.Embed(
                title="✅ AI Bio Set / AIのbioを設定しました",
                description=f"The AI's role in this channel has been set as follows.\nこのチャンネルでのAIの役割が以下のように設定されました。\n\n**New AI Bio / 新しいAIのbio:**\n```\n{bio}\n```",
                color=discord.Color.green()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save channel AI bio settings: {e}", exc_info=True)
            embed = discord.Embed(title="❌ Save Error / 保存エラー",
                                  description="Failed to save AI bio settings.\nAIのbio設定の保存に失敗しました。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="show-ai-bio",
        description="Show the AI's current bio for this channel.\nこのチャンネルのAIに現在設定されているbioを表示します。"
    )
    async def show_ai_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="BioManager is not available.\nBioManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        current_bio = self.bio_manager.get_channel_bio(interaction.channel_id)
        if current_bio:
            title = "Current AI Bio / 現在のAIのbio"
            description = f"In this channel, the AI has the following role set.\nこのチャンネルでは、AIに以下の役割が設定されています。\n\n**AI Bio / AIのbio:**\n```\n{current_bio}\n```"
            color = discord.Color.blue()
        else:
            default_prompt = self.llm_config.get('system_prompt', "Not set. / 設定されていません。")
            try:
                now = datetime.now(self.jst)
                current_date_str = now.strftime('%Y年%m月%d日')
                current_time_str = now.strftime('%H:%M')
                formatted_prompt = default_prompt.format(current_date=current_date_str, current_time=current_time_str)
            except (KeyError, ValueError):
                formatted_prompt = default_prompt

            title = "Current AI Bio / 現在のAIのbio"
            description = f"No specific AI bio is set for this channel. The server's default setting is used.\nこのチャンネルには専用のAI bioが設定されていません。サーバーのデフォルト設定が使用されます。\n\n**Default Setting / デフォルト設定:**\n```\n{formatted_prompt}\n```"
            color = discord.Color.greyple()
        embed = discord.Embed(title=title, description=description, color=color)
        self._add_support_footer(embed)
        await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="reset-ai-bio",
        description="Reset the AI's bio to default for this channel.\nこのチャンネルのAIのbioをデフォルト設定に戻します。"
    )
    async def reset_ai_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="BioManager is not available.\nBioManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        try:
            if await self.bio_manager.reset_channel_bio(interaction.channel_id):
                logger.info(f"AI bio for channel {interaction.channel_id} reset by {interaction.user.name}")
                default_prompt = self.llm_config.get('system_prompt', 'Not set / 未設定')
                try:
                    now = datetime.now(self.jst)
                    current_date_str = now.strftime('%Y年%m月%d日')
                    current_time_str = now.strftime('%H:%M')
                    formatted_prompt = default_prompt.format(current_date=current_date_str,
                                                             current_time=current_time_str)
                except (KeyError, ValueError):
                    formatted_prompt = default_prompt

                display_prompt = (formatted_prompt[:100] + '...') if len(
                    formatted_prompt) > 103 else formatted_prompt

                embed = discord.Embed(
                    title="✅ AI Bio Reset / AIのbioをリセットしました",
                    description=f"The AI bio for this channel has been reset to the default.\nこのチャンネルのAIのbioをデフォルト設定に戻しました。\n> Current Default / 現在のデフォルト: `{display_prompt}`",
                    color=discord.Color.green()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            else:
                embed = discord.Embed(
                    title="ℹ️ No Custom AI Bio / 専用のAI bioはありません",
                    description="No custom AI bio is set for this channel.\nこのチャンネルには専用のAI bioが設定されていません。",
                    color=discord.Color.blue()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save channel AI bio settings after reset: {e}", exc_info=True)
            embed = discord.Embed(title="❌ Save Error / 保存エラー",
                                  description="Failed to save AI bio settings.\nAIのbio設定の保存に失敗しました。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="set-user-bio",
        description="Save your information for the AI to remember.\nAIにあなたの情報を記憶させます。"
    )
    @app_commands.describe(
        bio="Information about you for the AI to remember (e.g., My name is Tanaka. My hobby is reading.).\nAIに覚えてほしいあなたの情報を記述してください。(例: 私の名前は田中です。趣味は読書です。)",
        mode="Select save mode. 'Overwrite' or 'Append' is available.\n保存モードを選択してください。'上書き'または'追記'が可能です。"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="Overwrite / 上書き", value="overwrite"),
        app_commands.Choice(name="Append / 追記", value="append"),
    ])
    async def set_user_bio_slash(self, interaction: discord.Interaction, bio: str, mode: app_commands.Choice[str]):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="BioManager is not available.\nBioManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        if len(bio) > 1024:
            embed = discord.Embed(
                title="⚠️ Input Too Long / 入力が長すぎます",
                description="User bio is too long. Please set it within 1024 characters.\nユーザー情報(bio)が長すぎます。1024文字以内で設定してください。",
                color=discord.Color.gold()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        try:
            await self.bio_manager.set_user_bio(interaction.user.id, bio, mode=mode.value)
            logger.info(
                f"User bio for {interaction.user.name} ({interaction.user.id}) was set with mode '{mode.value}'.")

            updated_bio = self.bio_manager.get_user_bio(interaction.user.id)

            embed = discord.Embed(
                title=f"✅ Your information has been saved ({mode.name}).\n✅ あなたの情報を記憶しました ({mode.name})",
                description=f"The AI has stored your information as follows.\nAIはあなたの情報を以下のように記憶しました。\n\n**Your Bio / あなたのbio:**\n```\n{updated_bio}\n```",
                color=discord.Color.green()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save user bio settings: {e}", exc_info=True)
            embed = discord.Embed(title="❌ Save Error / 保存エラー",
                                  description="Failed to save your information.\nあなたの情報の保存に失敗しました。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="show-user-bio",
        description="Show the information the AI has stored about you.\nAIが記憶しているあなたの情報を表示します。"
    )
    async def show_user_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="BioManager is not available.\nBioManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        current_bio = self.bio_manager.get_user_bio(interaction.user.id)
        if current_bio:
            embed = discord.Embed(
                title=f"💡 {interaction.user.display_name}'s Information / {interaction.user.display_name}さんの情報",
                description=f"**Bio:**\n```\n{current_bio}\n```",
                color=discord.Color.blue()
            )
        else:
            embed = discord.Embed(
                title=f"💡 {interaction.user.display_name}'s Information / {interaction.user.display_name}さんの情報",
                description="Currently, no information is stored about you.\nYou can set it using the `/set-user-bio` command or by asking the AI to remember it in conversation.\n現在、あなたに関する情報は何も記憶されていません。\n`/set-user-bio` コマンドか、会話の中でAIに記憶を頼むことで設定できます。",
                color=discord.Color.greyple()
            )
        self._add_support_footer(embed)
        await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="reset-user-bio",
        description="Delete all information the AI has stored about you.\nAIが記憶しているあなたの情報をすべて削除します。"
    )
    async def reset_user_bio_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.bio_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="BioManager is not available.\nBioManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        try:
            if await self.bio_manager.reset_user_bio(interaction.user.id):
                logger.info(f"User bio for {interaction.user.name} ({interaction.user.id}) was reset.")
                embed = discord.Embed(
                    title="✅ Information Deleted / 情報を削除しました",
                    description=f"All information about {interaction.user.display_name} has been deleted.\n{interaction.user.display_name}さんに関する情報をすべて削除しました。",
                    color=discord.Color.green()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            else:
                embed = discord.Embed(
                    title="ℹ️ No Information Stored / 情報はありません",
                    description="No information is stored about you.\nあなたに関する情報は何も記憶されていません。",
                    color=discord.Color.blue()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save user bio settings after reset: {e}", exc_info=True)
            embed = discord.Embed(title="❌ Deletion Error / 削除エラー",
                                  description="Failed to delete your information.\nあなたの情報の削除に失敗しました。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="memory-save",
        description="Save information to the global shared memory.\nグローバル共有メモリに情報を保存します。"
    )
    @app_commands.describe(
        key="The key for the information (e.g., 'Developer Announcement').\n情報のキー（項目名） 例: '開発者からのお知らせ'",
        value="The content of the information (e.g., 'Next maintenance is...').\n情報の内容 例: '次回のメンテナンスは...'"
    )
    async def memory_save_slash(self, interaction: discord.Interaction, key: str, value: str):
        await interaction.response.defer(ephemeral=False)
        if not self.memory_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="MemoryManager is not available.\nMemoryManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        try:
            await self.memory_manager.save_memory(key, value)
            embed = discord.Embed(
                title="✅ Saved to Global Shared Memory / グローバル共有メモリに保存しました",
                color=discord.Color.green()
            )
            embed.add_field(name="Key / キー", value=f"```{key}```", inline=False)
            embed.add_field(name="Value / 値", value=f"```{value}```", inline=False)
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save global memory via command: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Save Error / 保存エラー",
                description="Failed to save to global shared memory.\nグローバル共有メモリへの保存に失敗しました。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="memory-list",
        description="List all global shared memories.\nグローバル共有メモリの情報を一覧表示します。"
    )
    async def memory_list_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        if not self.memory_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="MemoryManager is not available.\nMemoryManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        memories = self.memory_manager.list_memories()
        if not memories:
            embed = discord.Embed(
                title="ℹ️ No Memories / メモリに情報はありません",
                description="Nothing is saved in the global shared memory.\nグローバル共有メモリには何も保存されていません。",
                color=discord.Color.blue()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        embed = discord.Embed(
            title="🌐 Global Shared Memory / グローバル共有メモリ",
            color=discord.Color.blue()
        )
        description = ""
        for key, value in memories.items():
            field_text = f"**{key}**: {value}\n"
            if len(description) + len(field_text) > 4000:
                description += "\n... (partially omitted due to display limit / 表示制限のため一部省略)"
                break
            description += field_text

        embed.description = description
        self._add_support_footer(embed)
        await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

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
        description="Delete a global shared memory.\nグローバル共有メモリから情報を削除します。"
    )
    @app_commands.describe(key="The key of the memory to delete.\n削除したい情報のキー")
    @app_commands.autocomplete(key=memory_key_autocomplete)
    async def memory_delete_slash(self, interaction: discord.Interaction, key: str):
        await interaction.response.defer(ephemeral=False)
        if not self.memory_manager:
            embed = discord.Embed(title="❌ Plugin Error / プラグインエラー",
                                  description="MemoryManager is not available.\nMemoryManagerが利用できません。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            return

        try:
            if await self.memory_manager.delete_memory(key):
                embed = discord.Embed(
                    title="✅ Memory Deleted / メモリを削除しました",
                    description=f"Deleted key '{key}' from global shared memory.\nグローバル共有メモリからキー '{key}' を削除しました。",
                    color=discord.Color.green()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            else:
                embed = discord.Embed(
                    title="⚠️ Key Not Found / キーが見つかりません",
                    description=f"Key '{key}' does not exist in global shared memory.\nキー '{key}' はグローバル共有メモリに存在しません。",
                    color=discord.Color.gold()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to delete global memory via command: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Deletion Error / 削除エラー",
                description="Failed to delete from global shared memory.\nグローバル共有メモリからの削除に失敗しました。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    async def model_autocomplete(self, interaction: discord.Interaction, current: str) -> List[
        app_commands.Choice[str]]:
        available_models = self.llm_config.get('available_models', [])
        return [
            app_commands.Choice(name=model, value=model)
            for model in available_models if current.lower() in model.lower()
        ][:25]

    @app_commands.command(
        name="switch-models",
        description="Switches the AI model used for this channel.\nこのチャンネルで使用するAIモデルを切り替えます。"
    )
    @app_commands.describe(
        model="Select the model you want to use.\n使用したいモデルを選択してください。"
    )
    @app_commands.autocomplete(model=model_autocomplete)
    async def switch_model_slash(self, interaction: discord.Interaction, model: str):
        await interaction.response.defer(ephemeral=False)
        available_models = self.llm_config.get('available_models', [])
        if model not in available_models:
            embed = discord.Embed(
                title="⚠️ Invalid Model / 無効なモデル",
                description=f"The specified model '{model}' is not available.\n指定されたモデル '{model}' は利用できません。",
                color=discord.Color.gold()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return

        channel_id = interaction.channel_id
        channel_id_str = str(channel_id)
        default_model = self.llm_config.get('model')

        if channel_id in self.model_reset_tasks:
            self.model_reset_tasks[channel_id].cancel()
            self.model_reset_tasks.pop(channel_id, None)
            logger.info(f"Cancelled previous model reset task for channel {channel_id}.")

        self.channel_models[channel_id_str] = model

        try:
            await self._save_channel_models()
            await self._get_llm_client_for_channel(interaction.channel_id)

            if model != default_model:
                task = asyncio.create_task(self._schedule_model_reset(channel_id))
                self.model_reset_tasks[channel_id] = task

                embed = discord.Embed(
                    title="✅ Model Switched / モデルを切り替えました",
                    description=f"The AI model for this channel has been switched to `{model}`.\n"
                                f"It will automatically revert to the default model (`{default_model}`) **after 3 hours**.\n"
                                f"このチャンネルのAIモデルが `{model}` に切り替えられました。\n"
                                f"**3時間後**にデフォルトモデル (`{default_model}`) に自動的に戻ります。",
                    color=discord.Color.green()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
                logger.info(f"Model for channel {channel_id} switched to '{model}' by {interaction.user.name}. "
                            f"Reset scheduled in 3 hours.")
            else:
                embed = discord.Embed(
                    title="✅ Model Reset to Default / モデルをデフォルトに戻しました",
                    description=f"The AI model for this channel has been reset to the default `{model}`.\nこのチャンネルのAIモデルがデフォルトの `{model}` に戻されました。",
                    color=discord.Color.green()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
                logger.info(f"Model for channel {channel_id} switched to default '{model}' by {interaction.user.name}.")

        except Exception as e:
            logger.error(f"Failed to save channel model settings: {e}", exc_info=True)
            embed = discord.Embed(title="❌ Save Error / 保存エラー",
                                  description="Failed to save settings.\n設定の保存に失敗しました。",
                                  color=discord.Color.red())
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())

    @app_commands.command(
        name="switch-models-default-server",
        description="Resets the AI model for this channel to the server default.\nこのチャンネルのAIモデルをサーバーのデフォルト設定に戻します。"
    )
    async def reset_model_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        channel_id = interaction.channel_id
        channel_id_str = str(channel_id)

        if channel_id in self.model_reset_tasks:
            self.model_reset_tasks[channel_id].cancel()
            self.model_reset_tasks.pop(channel_id, None)
            logger.info(f"Cancelled scheduled model reset for channel {channel_id} due to manual reset.")

        if channel_id_str in self.channel_models:
            del self.channel_models[channel_id_str]
            try:
                await self._save_channel_models()
                default_model = self.llm_config.get('model', 'Not set / 未設定')
                embed = discord.Embed(
                    title="✅ Model Reset to Default / モデルをデフォルトに戻しました",
                    description=f"The AI model for this channel has been reset to the default (`{default_model}`).\nこのチャンネルのAIモデルをデフォルト (`{default_model}`) に戻しました。",
                    color=discord.Color.green()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
                logger.info(f"Model for channel {interaction.channel_id} reset to default by {interaction.user.name}")
            except Exception as e:
                logger.error(f"Failed to save channel model settings after reset: {e}", exc_info=True)
                embed = discord.Embed(title="❌ Save Error / 保存エラー",
                                      description="Failed to save settings.\n設定の保存に失敗しました。",
                                      color=discord.Color.red())
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view())
        else:
            embed = discord.Embed(
                title="ℹ️ No Custom Model Set / 専用モデルはありません",
                description="No custom model is set for this channel.\nこのチャンネルには専用のモデルが設定されていません。",
                color=discord.Color.blue()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @switch_model_slash.error
    async def switch_model_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Error in /switch-model command: {error}", exc_info=True)
        error_message = f"An unexpected error occurred: {error}\n予期せぬエラーが発生しました: {error}"
        embed = discord.Embed(title="❌ Unexpected Error / 予期せぬエラー", description=error_message,
                              color=discord.Color.red())
        self._add_support_footer(embed)
        view = self._create_support_view()
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)

    # ============================================================================
    # Image Model Switching Commands
    # ============================================================================

    async def image_model_autocomplete(
            self,
            interaction: discord.Interaction,
            current: str
    ) -> List[app_commands.Choice[str]]:
        """画像生成モデルのオートコンプリート - プロバイダー名も検索対象"""
        if not self.image_generator:
            return []

        available_models = self.image_generator.get_available_models()

        # 検索文字列を小文字化
        current_lower = current.lower()

        # フィルタリング: モデル名全体 or プロバイダー名が一致
        filtered = [
            model for model in available_models
            if current_lower in model.lower()
        ]

        # 結果が多い場合は、プロバイダーごとにグループ化して表示
        if len(filtered) > 25:
            # プロバイダーで絞り込む
            models_by_provider = self.image_generator.get_models_by_provider()
            choices = []

            for provider, models in sorted(models_by_provider.items()):
                if current_lower in provider.lower():
                    # プロバイダー名が一致する場合は、そのプロバイダーのモデルを優先
                    for model in models[:5]:  # 各プロバイダー最大5個
                        if len(choices) >= 25:
                            break
                        choices.append(app_commands.Choice(name=model, value=model))
                    if len(choices) >= 25:
                        break

            return choices[:25]

        # 通常のフィルタリング結果を返す
        return [
            app_commands.Choice(name=model, value=model)
            for model in filtered
        ][:25]

    @app_commands.command(
        name="switch-image-model",
        description="Switch the image generation model for this channel. / このチャンネルの画像生成モデルを切り替えます。"
    )
    @app_commands.describe(
        model="Select the image generation model you want to use. / 使用したい画像生成モデルを選択してください。"
    )
    @app_commands.autocomplete(model=image_model_autocomplete)
    async def switch_image_model_slash(self, interaction: discord.Interaction, model: str):
        await interaction.response.defer(ephemeral=False)

        if not self.image_generator:
            embed = discord.Embed(
                title="❌ Plugin Error / プラグインエラー",
                description="ImageGenerator is not available.\nImageGeneratorが利用できません。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return

        available_models = self.image_generator.get_available_models()
        if model not in available_models:
            embed = discord.Embed(
                title="⚠️ Invalid Model / 無効なモデル",
                description=f"The specified model `{model}` is not available.\n指定されたモデル `{model}` は利用できません。",
                color=discord.Color.gold()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return

        try:
            await self.image_generator.set_model_for_channel(interaction.channel_id, model)

            default_model = self.image_generator.default_model

            # モデル情報を解析
            try:
                provider, model_name = model.split('/', 1)
            except ValueError:
                provider, model_name = "unknown", model

            if model != default_model:
                embed = discord.Embed(
                    title="✅ Image Model Switched / 画像生成モデルを切り替えました",
                    description=(
                        f"The image generation model for this channel has been switched.\n"
                        f"このチャンネルの画像生成モデルを切り替えました。"
                    ),
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="New Model / 新しいモデル",
                    value=f"```\n{model}\n```",
                    inline=False
                )
                embed.add_field(name="Provider / プロバイダー", value=f"`{provider}`", inline=True)
                embed.add_field(name="Model Name / モデル名", value=f"`{model_name}`", inline=True)
                embed.add_field(
                    name="💡 Tip / ヒント",
                    value=f"To reset to default (`{default_model}`), use `/reset-image-model`\nデフォルト (`{default_model}`) に戻すには `/reset-image-model`",
                    inline=False
                )
            else:
                embed = discord.Embed(
                    title="✅ Image Model Set to Default / 画像生成モデルをデフォルトに設定しました",
                    description=f"The image generation model for this channel is now the default.\nこのチャンネルの画像生成モデルがデフォルトになりました。",
                    color=discord.Color.green()
                )
                embed.add_field(name="Model / モデル", value=f"```\n{model}\n```", inline=False)

            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
            logger.info(
                f"Image model for channel {interaction.channel_id} switched to '{model}' by {interaction.user.name}"
            )

        except Exception as e:
            logger.error(f"Failed to save channel image model settings: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Save Error / 保存エラー",
                description="Failed to save settings.\n設定の保存に失敗しました。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())

    @app_commands.command(
        name="reset-image-model",
        description="Reset the image generation model to default for this channel. / このチャンネルの画像生成モデルをデフォルトに戻します。"
    )
    async def reset_image_model_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        if not self.image_generator:
            embed = discord.Embed(
                title="❌ Plugin Error / プラグインエラー",
                description="ImageGenerator is not available.\nImageGeneratorが利用できません。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return

        try:
            if await self.image_generator.reset_model_for_channel(interaction.channel_id):
                default_model = self.image_generator.default_model
                embed = discord.Embed(
                    title="✅ Image Model Reset to Default / 画像生成モデルをデフォルトに戻しました",
                    description=f"The image generation model for this channel has been reset to the default.\nこのチャンネルの画像生成モデルをデフォルトに戻しました。",
                    color=discord.Color.green()
                )
                embed.add_field(
                    name="Default Model / デフォルトモデル",
                    value=f"```\n{default_model}\n```",
                    inline=False
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
                logger.info(
                    f"Image model for channel {interaction.channel_id} reset to default by {interaction.user.name}"
                )
            else:
                embed = discord.Embed(
                    title="ℹ️ No Custom Model Set / 専用モデルはありません",
                    description="No custom image generation model is set for this channel.\nこのチャンネルには専用の画像生成モデルが設定されていません。",
                    color=discord.Color.blue()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)
        except Exception as e:
            logger.error(f"Failed to save channel image model settings after reset: {e}", exc_info=True)
            embed = discord.Embed(
                title="❌ Save Error / 保存エラー",
                description="Failed to save settings.\n設定の保存に失敗しました。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())

    @app_commands.command(
        name="show-image-model",
        description="Show the current image generation model for this channel. / このチャンネルの現在の画像生成モデルを表示します。"
    )
    async def show_image_model_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)

        if not self.image_generator:
            embed = discord.Embed(
                title="❌ Plugin Error / プラグインエラー",
                description="ImageGenerator is not available.\nImageGeneratorが利用できません。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return

        current_model = self.image_generator.get_model_for_channel(interaction.channel_id)
        default_model = self.image_generator.default_model
        is_default = current_model == default_model

        # モデル情報を解析
        try:
            provider, model_name = current_model.split('/', 1)
        except ValueError:
            provider, model_name = "unknown", current_model

        embed = discord.Embed(
            title="🎨 Current Image Generation Model / 現在の画像生成モデル",
            color=discord.Color.blue() if is_default else discord.Color.purple()
        )

        embed.add_field(
            name="Current Model / 現在のモデル",
            value=f"```\n{current_model}\n```",
            inline=False
        )
        embed.add_field(name="Provider / プロバイダー", value=f"`{provider}`", inline=True)
        embed.add_field(name="Status / 状態", value='`Default / デフォルト`' if is_default else '`Custom / カスタム`',
                        inline=True)

        # 利用可能なモデル一覧をプロバイダーごとに表示
        models_by_provider = self.image_generator.get_models_by_provider()

        for provider_name, models in sorted(models_by_provider.items()):
            model_list = "\n".join([f"• `{m.split('/', 1)[1]}`" for m in models[:5]])  # 各プロバイダー最大5個表示

            if len(models) > 5:
                model_list += f"\n• ... and {len(models) - 5} more"

            embed.add_field(
                name=f"📦 {provider_name.title()} Models",
                value=model_list or "None",
                inline=True
            )

        embed.add_field(
            name="💡 Commands / コマンド",
            value=(
                "• `/switch-image-model` - Change model / モデル変更\n"
                "• `/reset-image-model` - Reset to default / デフォルトに戻す"
            ),
            inline=False
        )

        self._add_support_footer(embed)
        await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="list-image-models",
        description="List all available image generation models. / 利用可能な画像生成モデルの一覧を表示します。"
    )
    @app_commands.describe(
        provider="Filter by provider (optional). / プロバイダーで絞り込み（オプション）"
    )
    async def list_image_models_slash(self, interaction: discord.Interaction, provider: str = None):
        await interaction.response.defer(ephemeral=False)

        if not self.image_generator:
            embed = discord.Embed(
                title="❌ Plugin Error / プラグインエラー",
                description="ImageGenerator is not available.\nImageGeneratorが利用できません。",
                color=discord.Color.red()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return

        models_by_provider = self.image_generator.get_models_by_provider()

        # プロバイダーフィルター
        if provider:
            provider_lower = provider.lower()
            models_by_provider = {
                k: v for k, v in models_by_provider.items()
                if provider_lower in k.lower()
            }

            if not models_by_provider:
                embed = discord.Embed(
                    title="⚠️ No Models Found / モデルが見つかりません",
                    description=f"No models found for provider: `{provider}`\nプロバイダー `{provider}` のモデルが見つかりません。",
                    color=discord.Color.gold()
                )
                self._add_support_footer(embed)
                await interaction.followup.send(embed=embed, view=self._create_support_view())
                return

        embed = discord.Embed(
            title="🎨 Available Image Generation Models / 利用可能な画像生成モデル",
            description=f"Total: {sum(len(models) for models in models_by_provider.values())} models across {len(models_by_provider)} provider(s)\n合計: {len(models_by_provider)}プロバイダー、{sum(len(models) for models in models_by_provider.values())}モデル",
            color=discord.Color.blue()
        )

        for provider_name, models in sorted(models_by_provider.items()):
            # モデル名からプロバイダープレフィックスを除去して表示
            model_names = [m.split('/', 1)[1] for m in models]

            # 長すぎる場合は分割
            if len(model_names) > 10:
                model_text = "\n".join([f"{i + 1}. `{m}`" for i, m in enumerate(model_names[:10])])
                model_text += f"\n... and {len(model_names) - 10} more"
            else:
                model_text = "\n".join([f"{i + 1}. `{m}`" for i, m in enumerate(model_names)])

            embed.add_field(
                name=f"📦 {provider_name.title()} ({len(models)} models)",
                value=model_text or "None",
                inline=False
            )

        embed.add_field(
            name="💡 How to Use / 使い方",
            value="Use `/switch-image-model` to change the model for this channel.\n`/switch-image-model` でこのチャンネルのモデルを変更できます。",
            inline=False
        )

        self._add_support_footer(embed)
        await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @switch_image_model_slash.error
    async def switch_image_model_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        logger.error(f"Error in /switch-image-model command: {error}", exc_info=True)
        error_message = f"An unexpected error occurred: {error}\n予期せぬエラーが発生しました: {error}"
        embed = discord.Embed(
            title="❌ Unexpected Error / 予期せぬエラー",
            description=error_message,
            color=discord.Color.red()
        )
        self._add_support_footer(embed)
        view = self._create_support_view()
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, view=view, ephemeral=False)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=False)

    @app_commands.command(name="llm_help",
                          description="Displays help and usage guidelines for LLM (AI Chat) features.\nLLM (AI対話) 機能のヘルプと利用ガイドラインを表示します。")
    async def llm_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        bot_user = self.bot.user or interaction.client.user
        bot_name = bot_user.name if bot_user else "This Bot / 当Bot"
        embed = discord.Embed(title=f"💡 {bot_name} AI Chat Help & Guidelines / AI対話機能ヘルプ＆ガイドライン",
                              description=f"Explanation and terms of use for the AI chat features.\n{bot_name}のAI対話機能についての説明と利用規約です。",
                              color=discord.Color.purple())
        if bot_user and bot_user.avatar: embed.set_thumbnail(url=bot_user.avatar.url)
        embed.add_field(
            name="Basic Usage / 基本的な使い方",
            value=(
                f"• Mention the bot (`@{bot_name}`) to get a response from the AI.\n  Botにメンション (`@{bot_name}`) して話しかけると、AIが応答します。\n"
                f"• **You can also continue the conversation by replying to the bot's messages (no mention needed).**\n  **Botのメッセージに返信することでも会話を続けられます（メンション不要）。**\n"
                f"• If you ask the AI to remember something, it will try to store that information.\n  「私の名前は〇〇です。覚えておいて」のように話しかけると、AIがあなたの情報を記憶しようとします。\n"
                f"• Attach images or paste image URLs with your message, and the AI will try to understand them.\n  画像と一緒に話しかけると、AIが画像の内容も理解しようとします。"
            ),
            inline=False
        )
        embed.add_field(
            name="Useful Commands / 便利なコマンド",
            value=(
                "**[AI Settings (Per Channel) / AIの設定 (チャンネルごと)]**\n"
                "• `/switch-models`: Change the AI model used in this channel. / このチャンネルで使うAIモデルを変更します。\n"
                "• `/set-ai-bio`: Set a custom personality/role for the AI in this channel. / このチャンネル専用のAIの性格や役割を設定します。\n"
                "• `/show-ai-bio`: Check the current AI bio setting. / 現在のAIのbio設定を確認します。\n"
                "• `/reset-ai-bio`: Reset the AI bio to the default. / AIのbio設定をデフォルトに戻します。\n"
                "**[Your Information / あなたの情報]**\n"
                "• `/set-user-bio`: Set information about you for the AI to remember. / AIに覚えてほしいあなたの情報を設定します。\n"
                "• `/show-user-bio`: Check the information the AI has stored about you. / AIが記憶しているあなたの情報を確認します。\n"
                "• `/reset-user-bio`: Delete your information from the AI's memory. / あなたの情報をAIの記憶から削除します。\n"
                "**[Global Memory / グローバルメモリ]**\n"
                "• `/memory-save`: Save information to the global shared memory. / 全サーバー共通のメモリに情報を保存します。\n"
                "• `/memory-list`: List all information in the global memory. / グローバルメモリの情報を一覧表示します。\n"
                "• `/memory-delete`: Delete information from the global memory. / グローバルメモリから情報を削除します。\n"
                "**[Other / その他]**\n"
                "• `/clear_history`: Reset the conversation history. / 会話履歴をリセットします。"
            ),
            inline=False
        )
        channel_model_str = self.channel_models.get(str(interaction.channel_id))
        model_display = f"`{channel_model_str}` (Channel-specific / このチャンネル専用)" if channel_model_str else f"`{self.llm_config.get('model', 'Not set / 未設定')}` (Default / デフォルト)"

        ai_bio_display = "N/A"
        user_bio_display = "N/A"
        if self.bio_manager:
            ai_bio_display = "✅ (Custom / 専用設定あり)" if self.bio_manager.get_channel_bio(
                interaction.channel_id) else "Default / デフォルト"
            user_bio_display = "✅ (Stored / 記憶あり)" if self.bio_manager.get_user_bio(
                interaction.user.id) else "None / なし"

        active_tools = self.llm_config.get('active_tools', [])
        tools_info = "• None / なし" if not active_tools else "• " + ", ".join(active_tools)
        embed.add_field(name="Current AI Settings / 現在のAI設定",
                        value=f"• **Model in Use / 使用モデル:** {model_display}\n"
                              f"• **AI Role (Channel) / AIの役割(チャンネル):** {ai_bio_display} (see `/show-ai-bio`)\n"
                              f"• **Your Info / あなたの情報:** {user_bio_display} (see `/show-user-bio`)\n"
                              f"• **Max Conversation History / 会話履歴の最大保持数:** {self.llm_config.get('max_messages', 'Not set / 未設定')} pairs\n"
                              f"• **Max Images at Once / 一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', 'Not set / 未設定')} image(s)\n"
                              f"• **Available Tools / 利用可能なツール:** {tools_info}",
                        inline=False)
        embed.add_field(name="--- 📜 AI Usage Guidelines / AI利用ガイドライン ---",
                        value="Please review the following to ensure safe use of the AI features.\nAI機能を安全にご利用いただくため、以下の内容を必ずご確認ください。",
                        inline=False)
        embed.add_field(name="⚠️ 1. Data Input Precautions / データ入力時の注意", value=(
            "**NEVER include personal or confidential information** such as your name, contact details, or passwords.\n"
            "AIに記憶させる情報には、氏名、連絡先、パスワードなどの**個人情報や秘密情報を絶対に含めないでください。**"),
                        inline=False)
        embed.add_field(name="✅ 2. Precautions for Using Generated Output / 生成物利用時の注意", value=(
            "The AI's responses may contain inaccuracies or biases. **Always fact-check and use them at your own risk.**\n"
            "AIの応答には虚偽や偏見が含まれる可能性があります。**必ずファクトチェックを行い、自己の責任で利用してください。**"),
                        inline=False)
        embed.set_footer(
            text="These guidelines are subject to change without notice.\nガイドラインは予告なく変更される場合があります。")
        self._add_support_footer(embed)
        await interaction.followup.send(embed=embed, view=self._create_support_view(), ephemeral=False)

    @app_commands.command(
        name="clear_history",
        description="Clears the history of the current conversation thread.\n現在の会話スレッドの履歴をクリアします。"
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
            embed = discord.Embed(
                title="⚠️ Permission Error / 権限エラー",
                description="Could not read the channel's message history.\nチャンネルのメッセージ履歴を読み取れませんでした。",
                color=discord.Color.gold()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
            return
        for thread_id in threads_to_clear:
            if thread_id in self.conversation_threads:
                del self.conversation_threads[thread_id]
                self.message_to_thread = {k: v for k, v in self.message_to_thread.items() if v != thread_id}
                cleared_count += 1
        if cleared_count > 0:
            embed = discord.Embed(
                title="✅ History Cleared / 履歴をクリアしました",
                description=f"Cleared the history of {cleared_count} conversation thread(s) related to this channel.\nこのチャンネルに関連する {cleared_count} 個の会話スレッドの履歴をクリアしました。",
                color=discord.Color.green()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())
        else:
            embed = discord.Embed(
                title="ℹ️ No History Found / 履歴がありません",
                description="No conversation history to clear was found.\nクリア対象の会話履歴が見つかりませんでした。",
                color=discord.Color.blue()
            )
            self._add_support_footer(embed)
            await interaction.followup.send(embed=embed, view=self._create_support_view())


async def setup(bot: commands.Bot):
    """Sets up the LLMCog."""
    try:
        await bot.add_cog(LLMCog(bot))
        logger.info("LLMCog loaded successfully.")
    except Exception as e:
        logger.critical(f"Failed to set up LLMCog: {e}", exc_info=True)
        raise