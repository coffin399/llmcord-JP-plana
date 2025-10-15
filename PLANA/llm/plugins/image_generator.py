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
    """画像生成プラグイン - Stable Diffusion WebUI Forge対応"""

    def __init__(self, bot):
        self.bot = bot
        self.config = bot.config.get('llm', {})
        self.image_gen_config = self.config.get('image_generator', {})

        # Forge WebUI設定
        self.forge_url = self.image_gen_config.get('forge_url', 'http://127.0.0.1:7860')
        self.default_model = self.image_gen_config.get('model', 'sd_xl_base_1.0.safetensors')
        self.default_size = self.image_gen_config.get('default_size', '1024x1024')
        self.timeout = self.image_gen_config.get('timeout', 180.0)

        # プログレスバー設定
        self.show_progress = self.image_gen_config.get('show_progress', True)
        self.progress_update_interval = self.image_gen_config.get('progress_update_interval', 2.0)

        # 生成パラメータ
        self.default_params = self.image_gen_config.get('default_params', {})

        # 利用可能なモデルリスト
        self.available_models = self.image_gen_config.get('available_models', [self.default_model])
        if self.default_model not in self.available_models:
            self.available_models.insert(0, self.default_model)
            logger.warning(f"Default model '{self.default_model}' not in available_models, adding it")

        # チャンネルごとのモデル設定
        self.channel_models_path = "data/channel_image_models.json"
        self.channel_models: Dict[str, str] = self._load_channel_models()

        self.http_session = aiohttp.ClientSession()

        logger.info(f"ImageGenerator initialized with Forge WebUI at: {self.forge_url}")
        logger.info(f"Default model: {self.default_model}")
        logger.info(f"Available models: {len(self.available_models)} models")

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

    def _create_progress_bar(self, current: int, total: int, width: int = 20) -> str:
        """プログレスバーの文字列を生成"""
        if total == 0:
            percentage = 0
        else:
            percentage = int((current / total) * 100)

        filled = int((current / total) * width) if total > 0 else 0
        bar = '█' * filled + '░' * (width - filled)

        return f"{bar} {percentage}% ({current}/{total})"

    async def _update_progress_message(
            self,
            message: discord.Message,
            current: int,
            total: int,
            prompt: str,
            model: str,
            elapsed_time: float = 0.0
    ):
        """プログレスメッセージを更新"""
        progress_bar = self._create_progress_bar(current, total)

        embed = discord.Embed(
            title="🎨 Generating Image... / 画像生成中...",
            description=f"**Prompt:** {prompt[:150]}{'...' if len(prompt) > 150 else ''}",
            color=discord.Color.orange()
        )
        embed.add_field(
            name="Progress / 進捗",
            value=f"```\n{progress_bar}\n```",
            inline=False
        )
        embed.add_field(name="Model", value=model, inline=True)

        if elapsed_time > 0:
            embed.add_field(
                name="Elapsed Time / 経過時間",
                value=f"{elapsed_time:.1f}s",
                inline=True
            )

        if current < total:
            embed.set_footer(text="⏳ Please wait... / お待ちください...")
        else:
            embed.set_footer(text="✅ Finalizing... / 最終処理中...")

        try:
            await message.edit(embed=embed)
        except discord.HTTPException as e:
            logger.warning(f"Failed to update progress message: {e}")

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
                                "画像に含めたくない要素(オプション)。"
                            )
                        },
                        "size": {
                            "type": "string",
                            "description": (
                                "Image size in format WIDTHxHEIGHT (e.g., '1024x1024', '512x768'). "
                                "Default is 1024x1024."
                            ),
                            "enum": ["512x512", "768x768", "1024x1024", "512x768", "768x512",
                                     "1024x768", "768x1024", "1280x720", "720x1280"]
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
            image_data = await self._generate_image_forge(prompt, negative_prompt, size, model, channel_id)

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
            embed.set_footer(text="Powered by Stable Diffusion WebUI Forge")

            await channel.send(embed=embed, file=image_file)

            logger.info(f"✅ [IMAGE_GEN] Successfully generated and sent image")

            return (
                f"✅ Successfully generated image with prompt: '{prompt[:100]}{'...' if len(prompt) > 100 else ''}'\n"
                f"The image has been sent to the channel. / 画像をチャンネルに送信しました。"
            )

        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Error: {e}", exc_info=True)
            return f"❌ Error during image generation: {str(e)[:200]}"

    async def _generate_image_forge(
            self,
            prompt: str,
            negative_prompt: str,
            size: str,
            model: str,
            channel_id: int
    ) -> Optional[bytes]:
        """
        Stable Diffusion WebUI Forge APIで画像を生成

        Args:
            prompt: 生成する画像の説明
            negative_prompt: 除外する要素
            size: 画像サイズ
            model: 使用するモデル名
            channel_id: Discordチャンネルid (プログレス表示用)

        Returns:
            生成された画像データ(PNG形式)
        """
        width, height = map(int, size.split('x'))

        # Forge WebUI API エンドポイント
        url = f"{self.forge_url.rstrip('/')}/sdapi/v1/txt2img"

        # デフォルトパラメータとマージ
        steps = self.default_params.get('steps', 20)
        payload = {
            "prompt": prompt,
            "negative_prompt": negative_prompt or self.default_params.get('negative_prompt', ''),
            "width": width,
            "height": height,
            "steps": steps,
            "cfg_scale": self.default_params.get('cfg_scale', 7.0),
            "sampler_name": self.default_params.get('sampler_name', 'DPM++ 2M Karras'),
            "batch_size": 1,
            "n_iter": 1,
            "seed": self.default_params.get('seed', -1),
            "restore_faces": self.default_params.get('restore_faces', False),
            "tiling": self.default_params.get('tiling', False),
            "override_settings": {
                "sd_model_checkpoint": model
            },
            "override_settings_restore_afterwards": True
        }

        # 追加パラメータがあればマージ
        extra_params = self.default_params.get('extra_params')
        if extra_params and isinstance(extra_params, dict):
            payload.update(extra_params)

        logger.info(f"🟢 [IMAGE_GEN] Calling Forge WebUI API")
        logger.info(f"🟢 [IMAGE_GEN] URL: {url}")
        logger.info(f"🟢 [IMAGE_GEN] Model: {model}")
        logger.info(f"🟢 [IMAGE_GEN] Size: {width}x{height}")
        logger.info(f"🟢 [IMAGE_GEN] Steps: {payload['steps']}, CFG: {payload['cfg_scale']}")
        logger.info(f"🟢 [IMAGE_GEN] Sampler: {payload['sampler_name']}")

        # プログレスメッセージを投稿
        progress_message = None
        if self.show_progress:
            channel = self.bot.get_channel(channel_id)
            if channel:
                try:
                    embed = discord.Embed(
                        title="🎨 Starting Image Generation... / 画像生成を開始...",
                        description=f"**Prompt:** {prompt[:150]}{'...' if len(prompt) > 150 else ''}",
                        color=discord.Color.orange()
                    )
                    embed.add_field(name="Model", value=model, inline=True)
                    embed.add_field(name="Size", value=size, inline=True)
                    embed.set_footer(text="⏳ Initializing... / 初期化中...")
                    progress_message = await channel.send(embed=embed)
                except Exception as e:
                    logger.warning(f"Failed to send progress message: {e}")

        try:
            # 画像生成リクエストを送信
            import time
            start_time = time.time()

            async with self.http_session.post(
                    url,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=self.timeout)
            ) as response:

                # プログレス監視タスクを起動
                progress_task = None
                if self.show_progress and progress_message:
                    progress_task = asyncio.create_task(
                        self._monitor_progress(progress_message, steps, prompt, model, start_time)
                    )

                logger.info(f"🟢 [IMAGE_GEN] Response status: {response.status}")

                if response.status == 200:
                    result = await response.json()

                    # プログレス監視を停止
                    if progress_task:
                        progress_task.cancel()
                        try:
                            await progress_task
                        except asyncio.CancelledError:
                            pass

                    if result.get('images') and len(result['images']) > 0:
                        # Base64エンコードされた画像をデコード
                        b64_image = result['images'][0]
                        image_bytes = base64.b64decode(b64_image)

                        elapsed_time = time.time() - start_time
                        logger.info(f"✅ [IMAGE_GEN] Successfully received image ({len(image_bytes)} bytes)")
                        logger.info(f"✅ [IMAGE_GEN] Total generation time: {elapsed_time:.1f}s")

                        # 生成情報をログ出力
                        if 'info' in result:
                            logger.info(f"🟢 [IMAGE_GEN] Generation info: {result['info'][:200]}...")

                        # 完了メッセージを表示
                        if progress_message:
                            try:
                                final_embed = discord.Embed(
                                    title="✅ Image Generation Complete! / 画像生成完了!",
                                    description=f"**Prompt:** {prompt[:150]}{'...' if len(prompt) > 150 else ''}",
                                    color=discord.Color.green()
                                )
                                final_embed.add_field(
                                    name="Generation Time / 生成時間",
                                    value=f"{elapsed_time:.1f}s",
                                    inline=True
                                )
                                final_embed.set_footer(text="🎉 Sending image... / 画像を送信中...")
                                await progress_message.edit(embed=final_embed)

                                # 少し待ってから削除
                                await asyncio.sleep(2)
                                await progress_message.delete()
                            except Exception as e:
                                logger.warning(f"Failed to update final progress: {e}")

                        return image_bytes

                    logger.error(f"❌ [IMAGE_GEN] No image data in response")
                    if progress_message:
                        try:
                            await progress_message.delete()
                        except:
                            pass
                    return None
                else:
                    error_text = await response.text()
                    logger.error(f"❌ [IMAGE_GEN] API error {response.status}: {error_text[:500]}")
                    if progress_message:
                        try:
                            await progress_message.delete()
                        except:
                            pass
                    return None

        except asyncio.TimeoutError:
            logger.error(f"❌ [IMAGE_GEN] Request timed out after {self.timeout}s")
            if progress_message:
                try:
                    await progress_message.delete()
                except:
                    pass
            return None
        except aiohttp.ClientConnectorError as e:
            logger.error(f"❌ [IMAGE_GEN] Connection error: {e}")
            logger.error(f"❌ [IMAGE_GEN] Make sure Forge WebUI is running at {self.forge_url}")
            if progress_message:
                try:
                    await progress_message.delete()
                except:
                    pass
            return None
        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Exception during API call: {e}", exc_info=True)
            if progress_message:
                try:
                    await progress_message.delete()
                except:
                    pass
            return None

    async def _monitor_progress(
            self,
            message: discord.Message,
            total_steps: int,
            prompt: str,
            model: str,
            start_time: float
    ):
        """プログレスを監視してメッセージを更新"""
        progress_url = f"{self.forge_url.rstrip('/')}/sdapi/v1/progress"

        try:
            while True:
                await asyncio.sleep(self.progress_update_interval)

                try:
                    async with self.http_session.get(
                            progress_url,
                            timeout=aiohttp.ClientTimeout(total=5.0)
                    ) as response:
                        if response.status == 200:
                            import time
                            data = await response.json()
                            progress = data.get('progress', 0.0)
                            current_step = int(progress * total_steps)
                            elapsed_time = time.time() - start_time

                            await self._update_progress_message(
                                message,
                                current_step,
                                total_steps,
                                prompt,
                                model,
                                elapsed_time
                            )
                except Exception as e:
                    logger.debug(f"Progress check error: {e}")
                    continue

        except asyncio.CancelledError:
            # タスクがキャンセルされた場合は正常終了
            pass

    async def get_available_models_from_forge(self) -> Optional[List[str]]:
        """Forge WebUIから利用可能なモデルリストを取得"""
        url = f"{self.forge_url.rstrip('/')}/sdapi/v1/sd-models"

        try:
            async with self.http_session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10.0)
            ) as response:
                if response.status == 200:
                    models = await response.json()
                    model_names = [model['title'] for model in models]
                    logger.info(f"📋 [IMAGE_GEN] Found {len(model_names)} models in Forge WebUI")
                    return model_names
                else:
                    logger.error(f"❌ [IMAGE_GEN] Failed to fetch models: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"❌ [IMAGE_GEN] Error fetching models: {e}")
            return None

    async def close(self):
        """HTTPセッションをクローズ"""
        await self.http_session.close()
        logger.info("ImageGenerator HTTP session closed")