# PLANA/llm/plugins/image_generator.py
from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Dict, Any, Optional, List

import aiohttp
import discord

logger = logging.getLogger(__name__)


class ImageGenerator:
    """画像生成プラグイン - Hugging Face / NVIDIA NIM対応"""

    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config.get('llm', {})
        self.image_gen_config = self.config.get('image_generator', {})

        # デフォルト設定
        self.default_model = self.image_gen_config.get('model', 'huggingface/stabilityai/stable-diffusion-xl-base-1.0')
        self.default_size = self.image_gen_config.get('default_size', '1024x1024')
        self.timeout = self.image_gen_config.get('timeout', 120.0)

        # 利用可能なモデルリスト
        self.available_models = self.image_gen_config.get('available_models', [self.default_model])
        if self.default_model not in self.available_models:
            self.available_models.insert(0, self.default_model)
            logger.warning(f"Default model '{self.default_model}' not in available_models, adding it")

        # プロバイダー設定
        self.image_providers = self.image_gen_config.get('image_providers', {})
        self.llm_providers = self.config.get('providers', {})

        # チャンネルごとのモデル設定
        self.channel_models_path = "data/channel_image_models.json"
        self.channel_models: Dict[str, str] = self._load_channel_models()

        self.http_session = aiohttp.ClientSession()

        logger.info(f"ImageGenerator initialized with default model: {self.default_model}")
        logger.info(f"Available image models: {len(self.available_models)} models")
        logger.info(f"Configured providers: {list(self.image_providers.keys())}")

    def _load_channel_models(self) -> Dict[str, str]:
        """チャンネルごとのモデル設定を読み込む"""
        import os
        import json

        if os.path.exists(self.channel_models_path):
            try:
                with open(self.channel_models_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"Loaded {len(data)} channel-specific image model settings")
                    return {str(k): v for k, v in data.items()}
            except Exception as e:
                logger.error(f"Failed to load channel image models: {e}")
        return {}

    async def _save_channel_models(self) -> None:
        """チャンネルごとのモデル設定を保存"""
        import os
        import json

        try:
            os.makedirs(os.path.dirname(self.channel_models_path), exist_ok=True)

            try:
                import aiofiles
                async with aiofiles.open(self.channel_models_path, 'w', encoding='utf-8') as f:
                    await f.write(json.dumps(self.channel_models, indent=4, ensure_ascii=False))
            except ImportError:
                with open(self.channel_models_path, 'w', encoding='utf-8') as f:
                    json.dump(self.channel_models, f, indent=4, ensure_ascii=False)

            logger.info(f"Saved channel image model settings")
        except Exception as e:
            logger.error(f"Failed to save channel image models: {e}")
            raise

    def _parse_model_string(self, model_string: str) -> tuple[str, str]:
        """
        モデル文字列をパースして (provider, model_name) を返す
        例: "huggingface/stabilityai/stable-diffusion-xl" -> ("huggingface", "stabilityai/stable-diffusion-xl")
        """
        if '/' not in model_string:
            raise ValueError(f"Invalid model format: {model_string}. Expected 'provider/model_name'")

        parts = model_string.split('/', 1)
        return parts[0], parts[1]

    def get_model_for_channel(self, channel_id: int) -> str:
        """指定されたチャンネルで使用するモデルを取得"""
        channel_id_str = str(channel_id)
        model = self.channel_models.get(channel_id_str, self.default_model)

        if model not in self.available_models:
            logger.warning(f"Model '{model}' for channel {channel_id} not in available models, using default")
            return self.default_model

        return model

    async def set_model_for_channel(self, channel_id: int, model: str) -> None:
        """指定されたチャンネルのモデルを設定"""
        if model not in self.available_models:
            raise ValueError(f"Model '{model}' is not in available models list")

        channel_id_str = str(channel_id)
        self.channel_models[channel_id_str] = model
        await self._save_channel_models()
        logger.info(f"Set image model for channel {channel_id} to {model}")

    async def reset_model_for_channel(self, channel_id: int) -> bool:
        """指定されたチャンネルのモデルをデフォルトに戻す"""
        channel_id_str = str(channel_id)
        if channel_id_str in self.channel_models:
            del self.channel_models[channel_id_str]
            await self._save_channel_models()
            logger.info(f"Reset image model for channel {channel_id} to default")
            return True
        return False

    def get_available_models(self) -> List[str]:
        """利用可能なモデルのリストを取得"""
        return self.available_models.copy()

    def get_models_by_provider(self) -> Dict[str, List[str]]:
        """プロバイダーごとにモデルを分類して返す"""
        models_by_provider = {}
        for model in self.available_models:
            try:
                provider, _ = self._parse_model_string(model)
                if provider not in models_by_provider:
                    models_by_provider[provider] = []
                models_by_provider[provider].append(model)
            except ValueError:
                continue
        return models_by_provider

    @property
    def name(self) -> str:
        return "generate_image"

    @property
    def tool_spec(self) -> Dict[str, Any]:
        """LLMに渡すツール定義"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": (
                    "Generate an image based on a text prompt using AI image generation. "
                    "Use this when the user asks you to create, generate, or draw an image. "
                    "ユーザーが画像の生成、作成、描画を依頼した時にこのツールを使用してください。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": (
                                "A detailed description of the image to generate. "
                                "Be specific and descriptive. Include style, mood, colors, etc. "
                                "生成する画像の詳細な説明。具体的で詳細に。"
                            )
                        },
                        "negative_prompt": {
                            "type": "string",
                            "description": (
                                "Things to avoid in the image (optional). "
                                "画像に含めたくない要素（オプション）。"
                            )
                        },
                        "size": {
                            "type": "string",
                            "description": (
                                "Image size in format WIDTHxHEIGHT (e.g., '1024x1024', '512x768'). "
                                "Default is 1024x1024."
                            ),
                            "enum": ["512x512", "768x768", "1024x1024", "512x768", "768x512"]
                        }
                    },
                    "required": ["prompt"]
                }
            }
        }

    async def run(self, arguments: Dict[str, Any], channel_id: int) -> str:
        """
        画像生成を実行し、結果を返す

        Args:
            arguments: ツール呼び出しの引数
            channel_id: Discordチャンネルid

        Returns:
            LLMに返すレスポンスメッセージ
        """
        prompt = arguments.get('prompt', '').strip()
        negative_prompt = arguments.get('negative_prompt', '').strip()
        size = arguments.get('size', self.default_size)

        if not prompt:
            return "❌ Error: Empty prompt provided. / エラー: プロンプトが空です。"

        model = self.get_model_for_channel(channel_id)

        logger.info(f"🎨 [IMAGE_GEN] Starting image generation")
        logger.info(f"🎨 [IMAGE_GEN] Model: {model}, Size: {size}")
        logger.info(f"🎨 [IMAGE_GEN] Prompt: {prompt[:100]}...")

        try:
            image_data = await self._generate_image(prompt, negative_prompt, size, model)

            if not image_data:
                return "❌ Failed to generate image. / 画像の生成に失敗しました。"

            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Channel {channel_id} not found!")
                return "❌ Error: Could not find channel to send image."

            image_file = discord.File(fp=io.BytesIO(image_data), filename="generated_image.png")

            embed = discord.Embed(
                title="🎨 Generated Image / 生成された画像",
                description=f"**Prompt:** {prompt[:200]}{'...' if len(prompt) > 200 else ''}",
                color=discord.Color.blue()
            )
            if negative_prompt:
                embed.add_field(
                    name="Negative Prompt",
                    value=negative_prompt[:100] + ('...' if len(negative_prompt) > 100 else ''),
                    inline=False
                )
            embed.add_field(name="Size", value=size, inline=True)
            embed.add_field(name="Model", value=model, inline=True)

            try:
                provider, _ = self._parse_model_string(model)
                embed.set_footer(text=f"Provider: {provider}")
            except ValueError:
                pass

            await channel.send(embed=embed, file=image_file)

            logger.info(f"✅ [IMAGE_GEN] Successfully generated and sent image")

            return (
                f"✅ Successfully generated image with prompt: '{prompt[:100]}{'...' if len(prompt) > 100 else ''}'\n"
                f"The image has been sent to the channel. / 画像をチャンネルに送信しました。"
            )

        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Error: {e}", exc_info=True)
            return f"❌ Error during image generation: {str(e)[:200]}"

    async def _generate_image(
            self,
            prompt: str,
            negative_prompt: str,
            size: str,
            model: str
    ) -> Optional[bytes]:
        """
        画像を生成（プロバイダーごとの処理分岐）

        Args:
            prompt: 生成する画像の説明
            negative_prompt: 除外する要素
            size: 画像サイズ
            model: 使用するモデル（provider/model_name形式）

        Returns:
            生成された画像データ（PNG形式）
        """
        try:
            provider_name, model_name = self._parse_model_string(model)
        except ValueError as e:
            logger.error(f"❌ [IMAGE_GEN] {e}")
            return None

        provider_config = self.image_providers.get(provider_name)
        if not provider_config:
            logger.error(f"❌ [IMAGE_GEN] No configuration found for provider: {provider_name}")
            return None

        # APIキーを取得
        api_key_source = provider_config.get('api_key_source')
        if api_key_source:
            llm_provider = self.llm_providers.get(api_key_source, {})
            api_key = llm_provider.get('api_key')
        else:
            api_key = provider_config.get('api_key')

        if not api_key:
            logger.error(f"❌ [IMAGE_GEN] No API key found for provider: {provider_name}")
            return None

        # プロバイダーごとに処理を分岐
        if provider_name == "huggingface":
            return await self._generate_image_huggingface(
                api_key, provider_config, model_name, prompt, negative_prompt, size
            )
        elif provider_name == "nvidia_nim":
            return await self._generate_image_nvidia(
                api_key, provider_config, model_name, prompt, negative_prompt, size
            )
        else:
            logger.error(f"❌ [IMAGE_GEN] Unsupported provider: {provider_name}")
            return None

    async def _generate_image_huggingface(
            self,
            api_key: str,
            provider_config: Dict,
            model_name: str,
            prompt: str,
            negative_prompt: str,
            size: str
    ) -> Optional[bytes]:
        """Hugging Face APIで画像を生成"""
        width, height = map(int, size.split('x'))
        base_url = provider_config.get('base_url', 'https://api-inference.huggingface.co/models')
        url = f"{base_url}/{model_name}"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        payload = {
            "inputs": prompt,
            "parameters": {
                "width": width,
                "height": height,
                "num_inference_steps": 25
            }
        }

        if negative_prompt:
            payload["parameters"]["negative_prompt"] = negative_prompt

        logger.info(f"🔵 [IMAGE_GEN] Calling Hugging Face API: {url}")

        try:
            async with self.http_session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status == 200:
                    # Hugging Faceはバイナリデータを直接返す
                    image_bytes = await response.read()
                    logger.info(f"✅ [IMAGE_GEN] Successfully received image ({len(image_bytes)} bytes)")
                    return image_bytes
                else:
                    error_text = await response.text()
                    logger.error(f"❌ [IMAGE_GEN] API error {response.status}: {error_text[:500]}")
                    return None

        except asyncio.TimeoutError:
            logger.error(f"❌ [IMAGE_GEN] Request timed out after {self.timeout}s")
            return None
        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Exception: {e}", exc_info=True)
            return None

    async def _generate_image_nvidia(
            self,
            api_key: str,
            provider_config: Dict,
            model_name: str,
            prompt: str,
            negative_prompt: str,
            size: str
    ) -> Optional[bytes]:
        """NVIDIA NIM APIで画像を生成"""
        width, height = map(int, size.split('x'))
        base_url = provider_config.get('base_url', 'https://integrate.api.nvidia.com/v1')
        url = f"{base_url}/images/generations"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        payload = {
            "text_prompts": [{"text": prompt, "weight": 1.0}],
            "cfg_scale": 5.0,
            "sampler": "K_DPM_2_ANCESTRAL",
            "seed": 0,
            "steps": 25,
            "width": width,
            "height": height
        }

        if negative_prompt:
            payload["text_prompts"].append({"text": negative_prompt, "weight": -1.0})

        logger.info(f"🔵 [IMAGE_GEN] Calling NVIDIA NIM API: {url}")

        try:
            async with self.http_session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    if result.get('artifacts') and len(result['artifacts']) > 0:
                        b64_image = result['artifacts'][0].get('base64')
                        if b64_image:
                            image_bytes = base64.b64decode(b64_image)
                            logger.info(f"✅ [IMAGE_GEN] Successfully received image ({len(image_bytes)} bytes)")
                            return image_bytes

                    logger.error(f"❌ [IMAGE_GEN] No image data in response")
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"❌ [IMAGE_GEN] API error {response.status}: {error_text[:500]}")
                    return None

        except asyncio.TimeoutError:
            logger.error(f"❌ [IMAGE_GEN] Request timed out after {self.timeout}s")
            return None
        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Exception: {e}", exc_info=True)
            return None

    async def close(self):
        """HTTPセッションをクローズ"""
        await self.http_session.close()
        logger.info("ImageGenerator HTTP session closed")