# PLANA/llm/plugins/image_generator.py
from __future__ import annotations

import asyncio
import base64
import io
import logging
from typing import Dict, Any, Optional

import aiohttp
import discord

logger = logging.getLogger(__name__)


class ImageGenerator:
    """NVIDIA NIM APIを使用して画像を生成するプラグイン"""

    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config.get('llm', {})

        # NVIDIA NIMの設定を取得
        nvidia_config = self.config.get('providers', {}).get('nvidia_nim', {})
        self.api_key = nvidia_config.get('api_key')

        # 画像生成設定
        self.image_gen_config = self.config.get('image_generator', {})
        self.default_model = self.image_gen_config.get('model', 'stabilityai/stable-diffusion-xl-base-1.0')
        self.default_size = self.image_gen_config.get('default_size', '1024x1024')
        self.timeout = self.image_gen_config.get('timeout', 120.0)

        # ✅ エンドポイント情報を読み込む
        self.endpoints = self.image_gen_config.get('endpoints', {})

        # 🔧 config.yamlから利用可能なモデルリストを取得
        self.available_models = self.config.get('available_image_models', [self.default_model])

        # デフォルトモデルがリストにない場合は追加
        if self.default_model not in self.available_models:
            self.available_models.insert(0, self.default_model)
            logger.warning(f"Default model '{self.default_model}' not in available_image_models, adding it")

        # チャンネルごとのモデル設定を管理
        self.channel_models_path = "data/channel_image_models.json"
        self.channel_models: Dict[str, str] = self._load_channel_models()

        if not self.api_key:
            logger.error("NVIDIA NIM API key not found in config!")

        self.http_session = aiohttp.ClientSession()
        logger.info(f"ImageGenerator initialized with default model: {self.default_model}")
        logger.info(f"Available image models: {', '.join(self.available_models)}")

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

            logger.info(f"Saved channel image model settings to {self.channel_models_path}")
        except Exception as e:
            logger.error(f"Failed to save channel image models: {e}")
            raise

    def get_model_for_channel(self, channel_id: int) -> str:
        """指定されたチャンネルで使用するモデルを取得"""
        channel_id_str = str(channel_id)
        model = self.channel_models.get(channel_id_str, self.default_model)

        # モデルが利用可能なリストにない場合はデフォルトに戻す
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

    def get_available_models(self) -> list:
        """利用可能なモデルのリストを取得（config.yamlから）"""
        return self.available_models.copy()

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
                    "Generate an image based on a text prompt using NVIDIA NIM API. "
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
                                "生成する画像の詳細な説明。具体的で詳細に。スタイル、雰囲気、色などを含める。"
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
                                "Default is 1024x1024. 画像サイズ（例: '1024x1024', '512x768'）。"
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
            channel_id: Discordチャンネルid（画像送信用）

        Returns:
            LLMに返すレスポンスメッセージ
        """
        prompt = arguments.get('prompt', '').strip()
        negative_prompt = arguments.get('negative_prompt', '').strip()
        size = arguments.get('size', self.default_size)

        if not prompt:
            return "❌ Error: Empty prompt provided. / エラー: プロンプトが空です。"

        # チャンネルごとのモデルを取得
        model = self.get_model_for_channel(channel_id)

        logger.info(f"🎨 [IMAGE_GEN] Starting image generation with prompt: {prompt[:100]}...")
        logger.info(f"🎨 [IMAGE_GEN] Using model: {model} for channel {channel_id}")

        try:
            # 画像を生成
            image_data = await self._generate_image(prompt, negative_prompt, size, model)

            if not image_data:
                return "❌ Failed to generate image. / 画像の生成に失敗しました。"

            # Discordに画像を送信
            channel = self.bot.get_channel(channel_id)
            if not channel:
                logger.error(f"Channel {channel_id} not found!")
                return "❌ Error: Could not find channel to send image. / エラー: 画像を送信するチャンネルが見つかりません。"

            # 画像ファイルを作成
            image_file = discord.File(
                fp=io.BytesIO(image_data),
                filename="generated_image.png"
            )

            # 画像情報のembed
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
            embed.add_field(name="Size / サイズ", value=size, inline=True)
            embed.add_field(name="Model / モデル", value=model, inline=True)
            embed.set_footer(text="Generated by NVIDIA NIM")

            # 画像を送信
            await channel.send(embed=embed, file=image_file)

            logger.info(f"✅ [IMAGE_GEN] Successfully generated and sent image")

            return (
                f"✅ Successfully generated image with prompt: '{prompt[:100]}{'...' if len(prompt) > 100 else ''}'\n"
                f"The image has been sent to the channel. / 画像をチャンネルに送信しました。"
            )

        except aiohttp.ClientError as e:
            logger.error(f"❌ [IMAGE_GEN] Network error: {e}", exc_info=True)
            return f"❌ Network error while generating image: {str(e)[:200]}"
        except asyncio.TimeoutError:
            logger.error(f"❌ [IMAGE_GEN] Timeout during image generation")
            return "❌ Image generation timed out. Please try again. / タイムアウトしました。もう一度お試しください。"
        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Unexpected error: {e}", exc_info=True)
            return f"❌ Unexpected error during image generation: {str(e)[:200]}"

    async def _generate_image(
            self,
            prompt: str,
            negative_prompt: str,
            size: str,
            model: str
    ) -> Optional[bytes]:
        """
        NVIDIA NIM APIを使用して画像を生成

        Args:
            prompt: 生成する画像の説明
            negative_prompt: 除外する要素
            size: 画像サイズ (e.g., "1024x1024")
            model: 使用するモデル名

        Returns:
            生成された画像データ（PNG形式）、失敗時はNone
        """
        width, height = map(int, size.split('x'))

        # ✅ モデルに対応するエンドポイント情報を取得
        endpoint_info = self.endpoints.get(model)
        if not endpoint_info:
            logger.error(f"❌ [IMAGE_GEN] No endpoint configuration found for model: {model}")
            logger.error(f"❌ [IMAGE_GEN] Available models in config: {list(self.endpoints.keys())}")
            return None

        url = endpoint_info.get('url')
        if not url:
            logger.error(f"❌ [IMAGE_GEN] No URL found for model: {model}")
            return None

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json"
        }

        # ✅ NVIDIA NIM API用のペイロード形式
        payload = {
            "text_prompts": [
                {
                    "text": prompt,
                    "weight": 1.0
                }
            ],
            "cfg_scale": 5.0,
            "sampler": "K_DPM_2_ANCESTRAL",
            "seed": 0,
            "steps": 25,
            "width": width,
            "height": height
        }

        # ネガティブプロンプトがある場合
        if negative_prompt:
            payload["text_prompts"].append({
                "text": negative_prompt,
                "weight": -1.0
            })

        logger.info(f"🔵 [IMAGE_GEN] Calling NVIDIA NIM API: {url}")
        logger.info(f"🔵 [IMAGE_GEN] Model: {model}, Size: {width}x{height}")
        logger.info(f"🔵 [IMAGE_GEN] Payload keys: {list(payload.keys())}")

        try:
            async with self.http_session.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:
                if response.status == 200:
                    result = await response.json()

                    # ✅ Base64データをデコード（NVIDIA NIM形式）
                    if result.get('artifacts') and len(result['artifacts']) > 0:
                        b64_image = result['artifacts'][0].get('base64')
                        if b64_image:
                            image_bytes = base64.b64decode(b64_image)
                            logger.info(f"✅ [IMAGE_GEN] Successfully received image ({len(image_bytes)} bytes)")
                            return image_bytes

                    logger.error(f"❌ [IMAGE_GEN] No image data in response")
                    logger.error(f"❌ [IMAGE_GEN] Response keys: {list(result.keys())}")
                    return None

                elif response.status == 404:
                    error_text = await response.text()
                    logger.error(f"❌ [IMAGE_GEN] 404 Not Found")
                    logger.error(f"❌ [IMAGE_GEN] Endpoint: {url}")
                    logger.error(f"❌ [IMAGE_GEN] Response: {error_text[:500]}")
                    return None

                elif response.status == 429:
                    logger.warning(f"⚠️ [IMAGE_GEN] Rate limit hit (429)")
                    return None

                elif response.status == 401:
                    logger.error(f"❌ [IMAGE_GEN] Authentication failed (401)")
                    logger.error(f"❌ [IMAGE_GEN] Check your NVIDIA NIM API key")
                    return None

                else:
                    error_text = await response.text()
                    logger.error(f"❌ [IMAGE_GEN] API error {response.status}: {error_text[:500]}")
                    return None

        except asyncio.TimeoutError:
            logger.error(f"❌ [IMAGE_GEN] Request timed out after {self.timeout}s")
            raise
        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Exception during API call: {e}", exc_info=True)
            raise

    async def close(self):
        """HTTPセッションをクローズ"""
        await self.http_session.close()
        logger.info("ImageGenerator HTTP session closed")