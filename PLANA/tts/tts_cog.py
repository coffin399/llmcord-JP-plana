import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio
import json
import os
from pathlib import Path
from typing import Dict, Tuple, Optional, List, Any
import subprocess
import wave

try:
    from .music_cog import MusicCog, GuildState as MusicGuildState, Track
except ImportError:
    MusicCog = commands.Cog
    MusicGuildState = any
    Track = any

try:
    from PLANA.tts.error.errors import TTSCogExceptionHandler
except ImportError:
    try:
        from PLANA.tts.error.errors import TTSCogExceptionHandler
    except ImportError as e:
        print(f"[CRITICAL] TTSCog: 必須コンポーネントのインポートに失敗しました。エラー: {e}")
        TTSCogExceptionHandler = None


class TTSCog(commands.Cog, name="tts_cog"):
    def __init__(self, bot: commands.Bot):
        if TTSCogExceptionHandler is None:
            raise commands.ExtensionFailed(self.qualified_name,
                                           "必須コンポーネントTTSCogExceptionHandlerのインポート失敗")

        self.bot = bot
        self.config = bot.config.get('tts', {})

        self.api_url = self.config.get('api_server_url', 'http://127.0.0.1:5000')
        self.api_key = self.config.get('api_key')

        self.default_model_id = self.config.get('default_model_id', 0)
        self.default_style = self.config.get('default_style', 'Neutral')
        self.default_style_weight = self.config.get('default_style_weight', 5.0)
        self.default_speed = self.config.get('default_speed', 1.0)

        headers = {}
        if self.api_key:
            headers["X-API-KEY"] = self.api_key

        self.session = aiohttp.ClientSession(headers=headers)
        self.exception_handler = TTSCogExceptionHandler()

        self.interrupted_states: Dict[int, Tuple[Track, int]] = {}
        self.tts_locks: Dict[int, asyncio.Lock] = {}

        self.available_models: List[Dict] = []
        self.models_loaded: bool = False

        self.settings_file = Path("data/tts_settings.json")
        self.channel_settings: Dict[int, Dict] = {}
        self._load_settings()

        self.speech_settings_file = Path("data/speech_settings.json")
        self.speech_settings: Dict[str, Dict[str, Any]] = {}
        self._load_speech_settings()

        self.llm_bot_ids = [1031673203774464160, 1311866016011124736]

        print("TTSCog loaded (Style-Bert-VITS2 compatible)")

    async def cog_load(self):
        print("TTSCog loaded. Fetching available models...")
        await self.fetch_available_models()

    async def cog_unload(self):
        self._save_settings()
        self._save_speech_settings()
        await self.session.close()
        print("TTSCog unloaded and session closed.")

    def _load_settings(self):
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.channel_settings = {int(k): v for k, v in data.items()}
                print(f"✓ [TTSCog] モデル設定を読み込みました: {len(self.channel_settings)}チャンネル")
            else:
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ [TTSCog] モデル設定読み込みエラー: {e}")
            self.channel_settings = {}

    def _save_settings(self):
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                data = {str(k): v for k, v in self.channel_settings.items()}
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"✗ [TTSCog] モデル設定保存エラー: {e}")

    def _load_speech_settings(self):
        try:
            if self.speech_settings_file.exists():
                with open(self.speech_settings_file, 'r', encoding='utf-8') as f:
                    self.speech_settings = json.load(f)
                print(f"✓ [TTSCog] 読み上げ設定を読み込みました: {len(self.speech_settings)}ギルド")
            else:
                self.speech_settings_file.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ [TTSCog] 読み上げ設定読み込みエラー: {e}")
            self.speech_settings = {}

    def _save_speech_settings(self):
        try:
            self.speech_settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.speech_settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.speech_settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"✗ [TTSCog] 読み上げ設定保存エラー: {e}")

    def _get_guild_speech_settings(self, guild_id: int) -> Dict[str, Any]:
        guild_id_str = str(guild_id)
        if guild_id_str not in self.speech_settings:
            self.speech_settings[guild_id_str] = {
                "speech_channel_id": None,
                "auto_join_users": []
            }
        return self.speech_settings[guild_id_str]

    def _get_channel_settings(self, channel_id: int) -> Dict:
        if channel_id not in self.channel_settings:
            return {
                "model_id": self.default_model_id,
                "style": self.default_style,
                "style_weight": self.default_style_weight,
                "speed": self.default_speed
            }
        return self.channel_settings[channel_id]

    def _set_channel_settings(self, channel_id: int, settings: Dict):
        self.channel_settings[channel_id] = settings
        self._save_settings()

    async def fetch_available_models(self) -> bool:
        try:
            async with self.session.get(f"{self.api_url}/models/info") as response:
                if response.status == 200:
                    data = await response.json()
                    if isinstance(data, list):
                        self.available_models = data
                    elif isinstance(data, dict):
                        if "models" in data:
                            self.available_models = data["models"]
                        else:
                            self.available_models = [{"id": k, "name": v if isinstance(v, str) else str(v)} for k, v in
                                                     data.items()]
                    else:
                        return False
                    self.models_loaded = True
                    print(f"✓ [TTSCog] {len(self.available_models)}個のモデルを検出")
                    return True
                else:
                    return False
        except aiohttp.ClientConnectorError:
            return False
        except Exception:
            return False

    def get_model_name(self, model_id: int) -> str:
        for model in self.available_models:
            if isinstance(model, dict) and (model.get('id') == model_id or str(model.get('id')) == str(model_id)):
                return model.get('name', f"Model {model_id}")
        return f"Model {model_id}"

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        if message.embeds:
            return

        guild_settings = self._get_guild_speech_settings(message.guild.id)
        speech_channel_id = guild_settings.get("speech_channel_id")

        if message.channel.id != speech_channel_id:
            return

        voice_client = message.guild.voice_client
        if not voice_client or not voice_client.is_connected():
            return

        text_to_say = message.clean_content
        if not text_to_say:
            return

        lock = self._get_tts_lock(message.guild.id)
        if lock.locked():
            return

        async with lock:
            channel_settings = self._get_channel_settings(voice_client.channel.id)
            await self._handle_say_logic(
                message.guild,
                text_to_say,
                channel_settings["model_id"],
                channel_settings["style"],
                channel_settings["style_weight"],
                channel_settings["speed"]
            )

    @commands.Cog.listener()
    async def on_llm_response_complete(self, response_messages: list, text_to_speak: str):
        # response_messagesはリストなので、空でないか確認し、最初の要素を取得します
        if not response_messages:
            print("[TTSCog] on_llm_response_complete received an empty message list.")
            return

        first_message = response_messages[0]
        guild = first_message.guild
        if not guild:
            return

        guild_settings = self._get_guild_speech_settings(guild.id)
        speech_channel_id = guild_settings.get("speech_channel_id")

        # response_message.channel.id を first_message.channel.id に変更
        if first_message.channel.id != speech_channel_id:
            return

        voice_client = guild.voice_client
        if not voice_client or not voice_client.is_connected():
            return

        if not text_to_speak:
            return

        lock = self._get_tts_lock(guild.id)
        if lock.locked():
            print(f"[TTSCog] LLM response for guild {guild.id} skipped, TTS is busy.")
            return

        async with lock:
            print(f"[TTSCog] Queuing LLM response for guild {guild.id} (length: {len(text_to_speak)})")
            channel_settings = self._get_channel_settings(voice_client.channel.id)
            await self._handle_say_logic(
                guild,
                text_to_speak,
                channel_settings["model_id"],
                channel_settings["style"],
                channel_settings["style_weight"],
                channel_settings["speed"]
            )

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        guild = member.guild
        voice_client = guild.voice_client

        if member.id == self.bot.user.id:
            return

        guild_settings = self._get_guild_speech_settings(guild.id)
        auto_join_users = guild_settings.get("auto_join_users", [])

        if member.id in auto_join_users and not before.channel and after.channel:
            if not voice_client or not voice_client.is_connected():
                try:
                    await after.channel.connect()
                    print(f"[TTSCog] 自動参加: {member.display_name} に追従して {after.channel.name} に参加しました。")
                except Exception as e:
                    print(f"[TTSCog] 自動参加エラー: {e}")

        if not voice_client:
            return

        if before.channel == voice_client.channel and after.channel != voice_client.channel:
            human_members = [m for m in voice_client.channel.members if not m.bot]
            if not human_members:
                await voice_client.disconnect()
                print(f"[TTSCog] 自動退出: {voice_client.channel.name} から退出しました。")
                guild_settings["speech_channel_id"] = None
                self._save_speech_settings()
                return

        if not self.config.get('enable_join_leave_notice', True):
            return

        text_to_say = None
        bot_channel = voice_client.channel

        if before.channel != bot_channel and after.channel == bot_channel:
            template = self.config.get('join_message_template', "{member_name}さんが参加しました。")
            text_to_say = template.format(member_name=member.display_name)
        elif before.channel == bot_channel and after.channel != bot_channel:
            template = self.config.get('leave_message_template', "{member_name}さんが退出しました。")
            text_to_say = template.format(member_name=member.display_name)

        if text_to_say:
            await self.trigger_tts_from_event(member.guild, text_to_say)

    speech_group = app_commands.Group(name="speech", description="テキストチャンネルの読み上げに関するコマンド")

    @speech_group.command(name="enable", description="このチャンネルのメッセージ読み上げを有効にします")
    async def enable_speech(self, interaction: discord.Interaction):
        if not interaction.user.voice or not interaction.user.voice.channel:
            await interaction.response.send_message("❌ ボイスチャンネルに接続してから実行してください。", ephemeral=True)
            return
        voice_channel = interaction.user.voice.channel
        try:
            if interaction.guild.voice_client:
                await interaction.guild.voice_client.move_to(voice_channel)
            else:
                await voice_channel.connect()
        except Exception as e:
            await interaction.response.send_message(f"❌ ボイスチャンネルへの接続に失敗しました: `{e}`", ephemeral=True)
            return
        guild_settings = self._get_guild_speech_settings(interaction.guild.id)
        guild_settings["speech_channel_id"] = interaction.channel.id
        self._save_speech_settings()
        embed = discord.Embed(title="🔊 VC読み上げを開始します",
                              description=f"対象チャンネル: {interaction.channel.mention}\n対象VC: {voice_channel.mention}",
                              color=discord.Color.green())
        await interaction.response.send_message(embed=embed)

    @speech_group.command(name="disable", description="メッセージ読み上げを無効にします")
    async def disable_speech(self, interaction: discord.Interaction):
        guild_settings = self._get_guild_speech_settings(interaction.guild.id)
        if guild_settings.get("speech_channel_id") is None:
            await interaction.response.send_message("ℹ️ メッセージ読み上げは現在無効です。", ephemeral=False)
            return
        guild_settings["speech_channel_id"] = None
        self._save_speech_settings()
        if interaction.guild.voice_client:
            await interaction.guild.voice_client.disconnect()
        await interaction.response.send_message("✅ メッセージ読み上げを無効にしました。", ephemeral=False)

    autojoin_group = app_commands.Group(name="autojoin", description="VCへの自動参加に関するコマンド")

    @autojoin_group.command(name="enable", description="あなたがVCに参加した際、BOTも自動で参加するようにします")
    async def enable_auto_join(self, interaction: discord.Interaction):
        if not interaction.user.voice:
            await interaction.response.send_message("❌ ボイスチャンネルに接続してから実行してください。", ephemeral=True)
            return
        guild_settings = self._get_guild_speech_settings(interaction.guild.id)
        auto_join_users = guild_settings.setdefault("auto_join_users", [])
        if interaction.user.id in auto_join_users:
            await interaction.response.send_message("ℹ️ あなたの自動参加は既に有効です。", ephemeral=False)
            return
        auto_join_users.append(interaction.user.id)
        self._save_speech_settings()
        await interaction.response.send_message("✅ あなたがVCに参加した際、BOTが自動で参加するようになりました。",
                                                ephemeral=False)

    @autojoin_group.command(name="disable", description="BOTの自動参加設定を解除します")
    async def disable_auto_join(self, interaction: discord.Interaction):
        guild_settings = self._get_guild_speech_settings(interaction.guild.id)
        auto_join_users = guild_settings.get("auto_join_users", [])
        if interaction.user.id not in auto_join_users:
            await interaction.response.send_message("ℹ️ あなたの自動参加は設定されていません。", ephemeral=False)
            return
        auto_join_users.remove(interaction.user.id)
        self._save_speech_settings()
        await interaction.response.send_message("✅ 自動参加設定を解除しました。", ephemeral=False)

    @app_commands.command(name="say", description="テキストを音声で読み上げます")
    @app_commands.describe(text="読み上げるテキスト", model_id="モデルID (省略時はチャンネル設定)",
                           style="スタイル名 (例: Neutral, Happy, Angry)", style_weight="スタイルの強さ (0.0-10.0)",
                           speed="話速 (0.5-2.0)")
    async def say(self, interaction: discord.Interaction, text: str, model_id: Optional[int] = None,
                  style: Optional[str] = None, style_weight: Optional[float] = None, speed: Optional[float] = None):
        if not self.config.get('enable_say_command', True):
            await interaction.response.send_message("読み上げコマンドは現在無効化されています。", ephemeral=True)
            return
        if not interaction.guild.voice_client:
            await self.exception_handler.send_message(interaction, "bot_not_in_voice", ephemeral=True)
            return
        lock = self._get_tts_lock(interaction.guild.id)
        if lock.locked():
            await self.exception_handler.send_message(interaction, "tts_in_progress", ephemeral=True)
            return
        voice_channel_id = interaction.guild.voice_client.channel.id
        channel_settings = self._get_channel_settings(voice_channel_id)
        final_model_id = model_id if model_id is not None else channel_settings["model_id"]
        final_style = style if style is not None else channel_settings["style"]
        final_style_weight = style_weight if style_weight is not None else channel_settings["style_weight"]
        final_speed = speed if speed is not None else channel_settings["speed"]
        async with lock:
            await interaction.response.defer()
            success = await self._handle_say_logic(interaction.guild, text, final_model_id, final_style,
                                                   final_style_weight, final_speed, interaction)
            if success:
                model_name = self.get_model_name(final_model_id)
                await interaction.followup.send(
                    f"🔊 読み上げ中: `{text}`\n速度: {final_speed}x")

    @app_commands.command(name="tts_models", description="利用可能な音声モデルの一覧を表示")
    async def tts_models(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not self.models_loaded: await self.fetch_available_models()
        if not self.available_models:
            await interaction.followup.send("❌ 利用可能なモデルが見つかりませんでした。", ephemeral=True)
            return
        embed = discord.Embed(title="🎙️ 利用可能な音声モデル",
                              description=f"合計 {len(self.available_models)} 個のモデル", color=discord.Color.blue())
        for model in self.available_models[:10]:
            if isinstance(model, dict):
                model_id, model_name, styles = model.get('id', 'N/A'), model.get('name', 'Unknown'), model.get('styles',
                                                                                                               ['Neutral'])
                display_name = f"ID: {model_id} - {str(model_name)[:200]}"
                styles_str = ", ".join(str(s) for s in styles[:10]) if isinstance(styles, list) else str(styles)
                if isinstance(styles, list) and len(styles) > 10: styles_str += f" ... (他{len(styles) - 10}個)"
                embed.add_field(name=display_name[:256], value=f"スタイル: {styles_str[:1000]}", inline=False)
            else:
                embed.add_field(name=f"Model: {str(model)[:240]}", value="詳細情報なし", inline=False)
        if len(self.available_models) > 10: embed.set_footer(
            text=f"... 他 {len(self.available_models) - 10} 個のモデル")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="switch-tts-model", description="このチャンネルのTTSモデル設定を変更します")
    @app_commands.describe(model_id="使用するモデルID", style="スタイル名 (省略時は現在の設定を維持)",
                           style_weight="スタイルの強さ (0.0-10.0, 省略時は現在の設定を維持)",
                           speed="話速 (0.5-2.0, 省略時は現在の設定を維持)")
    async def switch_tts_model(self, interaction: discord.Interaction, model_id: int, style: Optional[str] = None,
                               style_weight: Optional[float] = None, speed: Optional[float] = None):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Botがボイスチャンネルに接続していません。", ephemeral=True)
            return
        voice_channel, channel_id = interaction.guild.voice_client.channel, interaction.guild.voice_client.channel.id
        current_settings = self._get_channel_settings(channel_id)
        new_settings = {"model_id": model_id, "style": style if style is not None else current_settings["style"],
                        "style_weight": style_weight if style_weight is not None else current_settings["style_weight"],
                        "speed": speed if speed is not None else current_settings["speed"]}
        self._set_channel_settings(channel_id, new_settings)
        model_name = self.get_model_name(model_id)
        embed = discord.Embed(title="✅ TTS設定を更新しました", description=f"チャンネル: {voice_channel.mention}",
                              color=discord.Color.green())
        embed.add_field(name="モデル", value=f"ID: {model_id} - {model_name}", inline=False)
        embed.add_field(name="スタイル", value=new_settings["style"], inline=True)
        embed.add_field(name="スタイル強度", value=f"{new_settings['style_weight']}", inline=True)
        embed.add_field(name="速度", value=f"{new_settings['speed']}x", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="show-tts-settings", description="現在のチャンネルのTTS設定を表示します")
    async def show_tts_settings(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Botがボイスチャンネルに接続していません。", ephemeral=True)
            return
        voice_channel, channel_id = interaction.guild.voice_client.channel, interaction.guild.voice_client.channel.id
        settings, model_name, is_custom = self._get_channel_settings(channel_id), self.get_model_name(
            self._get_channel_settings(channel_id)["model_id"]), channel_id in self.channel_settings
        embed = discord.Embed(title="🎙️ 現在のTTS設定",
                              description=f"チャンネル: {voice_channel.mention}\n{'(カスタム設定)' if is_custom else '(デフォルト設定)'}",
                              color=discord.Color.blue() if is_custom else discord.Color.greyple())
        embed.add_field(name="モデル", value=f"ID: {settings['model_id']} - {model_name}", inline=False)
        embed.add_field(name="スタイル", value=settings["style"], inline=True)
        embed.add_field(name="スタイル強度", value=f"{settings['style_weight']}", inline=True)
        embed.add_field(name="速度", value=f"{settings['speed']}x", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reset-tts-settings", description="このチャンネルのTTS設定をデフォルトに戻します")
    async def reset_tts_settings(self, interaction: discord.Interaction):
        if not interaction.guild.voice_client:
            await interaction.response.send_message("❌ Botがボイスチャンネルに接続していません。", ephemeral=True)
            return
        voice_channel, channel_id = interaction.guild.voice_client.channel, interaction.guild.voice_client.channel.id
        if channel_id in self.channel_settings:
            del self.channel_settings[channel_id]
            self._save_settings()
            await interaction.response.send_message(f"✅ {voice_channel.mention} のTTS設定をデフォルトに戻しました。",
                                                    ephemeral=True)
        else:
            await interaction.response.send_message(
                f"ℹ️ {voice_channel.mention} はすでにデフォルト設定を使用しています。", ephemeral=True)

    def _get_tts_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.tts_locks: self.tts_locks[guild_id] = asyncio.Lock()
        return self.tts_locks[guild_id]

    async def trigger_tts_from_event(self, guild: discord.Guild, text: str):
        lock = self._get_tts_lock(guild.id)
        async with lock:
            await self._handle_say_logic(guild, text, self.default_model_id, self.default_style,
                                         self.default_style_weight, self.default_speed)

    async def _handle_say_logic(self, guild: discord.Guild, text: str, model_id: int, style: str, style_weight: float,
                                speed: float, interaction: Optional[discord.Interaction] = None) -> bool:
        voice_client = guild.voice_client
        if not voice_client:
            return False

        music_cog: MusicCog = self.bot.get_cog("music_cog")
        music_state: MusicGuildState = music_cog._get_guild_state(guild.id) if music_cog else None

        if music_state and music_state.is_playing and music_state.current_track:
            print(f"[TTSCog-Mixing] Music detected. Starting mix for guild {guild.id}")
            return await self._handle_mixed_say_logic(
                guild, text, model_id, style, style_weight, speed, interaction
            )
        else:
            print(f"[TTSCog-Normal] No music detected. Starting normal TTS for guild {guild.id}")
            if music_state and music_state.is_playing and music_state.current_track:
                current_position = music_state.get_current_position()
                self.interrupted_states[guild.id] = (music_state.current_track, current_position)
                music_state.is_seeking = True
                voice_client.stop()
            return await self._handle_normal_say_logic(
                guild, text, model_id, style, style_weight, speed, interaction
            )

    async def _handle_normal_say_logic(self, guild: discord.Guild, text: str, model_id: int, style: str,
                                       style_weight: float,
                                       speed: float, interaction: Optional[discord.Interaction] = None) -> bool:
        voice_client = guild.voice_client
        if not voice_client: return False

        endpoint, payload = f"{self.api_url}/voice", {"text": text, "model_id": model_id, "style": style,
                                                      "style_weight": style_weight, "speed": speed, "encoding": "wav"}
        try:
            async with self.session.post(endpoint, params=payload) as response:
                if response.status == 200:
                    wav_data = await response.read()
                    source = discord.FFmpegPCMAudio(io.BytesIO(wav_data), pipe=True)
                    while voice_client.is_playing(): await asyncio.sleep(0.1)
                    voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                        self._tts_after_playback(e, guild.id), self.bot.loop).result())
                    return True
                else:
                    error_text = await response.text()
                    if interaction: await interaction.followup.send(
                        f"❌ 音声生成エラー: {response.status}\n```{error_text[:200]}```", ephemeral=True)
                    if guild.id in self.interrupted_states:
                        self.interrupted_states.pop(guild.id, None)
                        music_cog: MusicCog = self.bot.get_cog("music_cog")
                        if music_cog:
                            music_state = music_cog._get_guild_state(guild.id)
                            if music_state:
                                music_state.is_seeking = False
                    return False
        except aiohttp.ClientConnectorError:
            if interaction: await interaction.followup.send(f"❌ APIサーバーに接続できません: {self.api_url}",
                                                            ephemeral=True)
            if guild.id in self.interrupted_states:
                self.interrupted_states.pop(guild.id, None)
                music_cog: MusicCog = self.bot.get_cog("music_cog")
                if music_cog:
                    music_state = music_cog._get_guild_state(guild.id)
                    if music_state:
                        music_state.is_seeking = False
            return False
        except Exception as e:
            if interaction: await interaction.followup.send(f"❌ エラーが発生しました: {type(e).__name__}",
                                                            ephemeral=True)
            if guild.id in self.interrupted_states:
                self.interrupted_states.pop(guild.id, None)
                music_cog: MusicCog = self.bot.get_cog("music_cog")
                if music_cog:
                    music_state = music_cog._get_guild_state(guild.id)
                    if music_state:
                        music_state.is_seeking = False
            return False

    async def _handle_mixed_say_logic(self, guild: discord.Guild, text: str, model_id: int, style: str,
                                      style_weight: float,
                                      speed: float, interaction: Optional[discord.Interaction] = None) -> bool:
        voice_client = guild.voice_client
        music_cog: MusicCog = self.bot.get_cog("music_cog")
        music_state: MusicGuildState = music_cog._get_guild_state(guild.id)

        track = music_state.current_track
        position = music_state.get_current_position()

        music_state.is_seeking = True
        if voice_client.is_playing():
            voice_client.stop()

        endpoint, payload = f"{self.api_url}/voice", {"text": text, "model_id": model_id, "style": style,
                                                      "style_weight": style_weight, "speed": speed, "encoding": "wav"}
        try:
            async with self.session.post(endpoint, params=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    if interaction: await interaction.followup.send(
                        f"❌ 音声生成エラー: {response.status}\n```{error_text[:200]}```", ephemeral=True)
                    music_state.is_seeking = False
                    return False
                tts_wav_data = await response.read()
        except Exception as e:
            if interaction: await interaction.followup.send(f"❌ TTSリクエストエラー: {e}", ephemeral=True)
            music_state.is_seeking = False
            return False

        try:
            with wave.open(io.BytesIO(tts_wav_data), 'rb') as wf:
                tts_duration = wf.getnframes() / float(wf.getframerate())
        except Exception:
            tts_duration = 5.0

        ffmpeg_executable = music_cog.ffmpeg_path
        ffmpeg_options = [
            '-i', track.stream_url,
            '-ss', str(position),
            '-i', 'pipe:0',
            '-filter_complex', '[0:a]volume=0.6[a0];[1:a]volume=1.0[a1];[a0][a1]amix=inputs=2:duration=shortest[out]',
            '-map', '[out]',
            '-f', 's16le',
            '-ar', '48000',
            '-ac', '2',
            '-'
        ]

        try:
            process = subprocess.Popen(
                [ffmpeg_executable] + ffmpeg_options,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE
            )

            try:
                process.stdin.write(tts_wav_data)
                process.stdin.flush()
                process.stdin.close()
            except (BrokenPipeError, OSError):
                print(f"[TTSCog-Mixing] FFmpeg stdin write error for guild {guild.id}")
                music_state.is_seeking = False
                await music_cog._play_next_song(guild.id, seek_seconds=position)
                return False

            source = discord.PCMAudio(process.stdout)
            player = discord.PCMVolumeTransformer(source, volume=music_state.volume)

            async def after_mixing_playback(error):
                if error:
                    print(f"[TTSCog-Mixing] Playback error for guild {guild.id}: {error}")

                process.kill()

                new_position = position + tts_duration
                print(f"[TTSCog-Mixing] Mix finished for guild {guild.id}. Resuming music at {new_position:.2f}s")

                await music_cog._play_next_song(guild.id, seek_seconds=int(new_position))

            voice_client.play(player, after=lambda e: asyncio.run_coroutine_threadsafe(after_mixing_playback(e),
                                                                                       self.bot.loop).result())
            return True

        except Exception as e:
            print(f"[TTSCog-Mixing] FFmpeg process error for guild {guild.id}: {e}")
            if interaction: await interaction.followup.send(f"❌ ミキシング処理エラー: {e}", ephemeral=True)
            music_state.is_seeking = False
            await music_cog._play_next_song(guild.id, seek_seconds=position)
            return False

    async def _tts_after_playback(self, error: Exception, guild_id: int):
        guild = self.bot.get_guild(guild_id)
        if not guild:
            print(f"[TTSCog] After-playback error: Guild {guild_id} not found.")
            self.interrupted_states.pop(guild_id, None)
            return

        if error:
            print(f"[TTSCog] 再生エラー (guild {guild.id}): {error}")

        if guild_id in self.interrupted_states:
            interrupted_track, position = self.interrupted_states.pop(guild_id)
            music_cog: MusicCog = self.bot.get_cog("music_cog")

            print(f"[TTSCog] 音楽を再開します (guild {guild.id}) 位置: {position}秒")

            if music_cog:
                music_state = music_cog._get_guild_state(guild_id)
                if music_state and guild.voice_client and guild.voice_client.is_connected():
                    music_state.current_track = interrupted_track
                    await music_cog._play_next_song(guild_id, seek_seconds=position)
                else:
                    if music_state:
                        music_state.is_seeking = False
                    print(
                        f"[TTSCog] 音楽の再開をスキップしました (guild {guild.id}): MusicStateまたはVCが見つかりません。")
            else:
                print(f"[TTSCog] 音楽の再開をスキップしました (guild {guild.id}): MusicCogが見つかりません。")
        else:
            print(f"[TTSCog] TTS再生完了 (guild {guild.id}). 再開する音楽はありません。")


async def setup(bot: commands.Bot):
    if 'tts' not in bot.config:
        print("Warning: 'tts' section not found in config.yaml. TTSCog will not be loaded.")
        return
    if not bot.get_cog("music_cog"):
        print("Warning: MusicCog is not loaded. TTSCog may not function correctly with music.")
    await bot.add_cog(TTSCog(bot))