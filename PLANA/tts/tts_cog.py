# PLANA/tts/tts_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio
import json
from pathlib import Path
from typing import Dict, Optional, List, Any
import time

try:
    from PLANA.music.music_cog import MusicCog
    from PLANA.music.plugins.audio_mixer import TTSAudioSource, MusicAudioSource
except ImportError:
    MusicCog = None
    TTSAudioSource = None
    MusicAudioSource = None

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
        if TTSCogExceptionHandler is None:
            raise commands.ExtensionFailed(self.qualified_name,
                                           "必須コンポーネントTTSCogExceptionHandlerのインポート失敗")

            # TTSAudioSourceのチェックを追加
        if TTSAudioSource is None:
            raise commands.ExtensionFailed(self.qualified_name,
                                           "必須コンポーネントTTSAudioSourceのインポート失敗")
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

        self.tts_locks: Dict[int, asyncio.Lock] = {}

        self.available_models: List[Dict] = []
        self.models_loaded: bool = False

        self.settings_file = Path("data/tts_settings.json")
        self.channel_settings: Dict[int, Dict] = {}
        self._load_settings()

        self.speech_settings_file = Path("data/speech_settings.json")
        self.speech_settings: Dict[str, Dict[str, Any]] = {}
        self._load_speech_settings()

        # 辞書システムの初期化を追加（self.llm_bot_idsの前に）
        self.dictionary_file = Path("data/speech_dictionary.json")
        self.speech_dictionary: Dict[str, str] = {}
        self._load_dictionary()

        self.llm_bot_ids = [1031673203774464160, 1311866016011124736]

        print("TTSCog loaded (Style-Bert-VITS2 compatible, AudioMixer enabled)")

    async def cog_load(self):
        print("TTSCog loaded. Fetching available models...")
        await self.fetch_available_models()

    async def cog_unload(self):
        self._save_settings()
        self._save_speech_settings()
        self._save_dictionary()
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
                "auto_join_users": [],
                "enable_notifications": True
            }
        # 以前のバージョンから設定ファイルを引き継いだ場合でもキーが存在するようにする
        self.speech_settings[guild_id_str].setdefault("enable_notifications", True)
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
        if not response_messages:
            print("[TTSCog] on_llm_response_complete received an empty message list.")
            return

        first_message = response_messages[0]
        guild = first_message.guild
        if not guild:
            return

        guild_settings = self._get_guild_speech_settings(guild.id)
        speech_channel_id = guild_settings.get("speech_channel_id")

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

        # グローバル設定の代わりにギルドごとの設定を確認
        if not guild_settings.get("enable_notifications", True):
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

    @speech_group.command(name="skip", description="現在の読み上げをスキップします")
    async def skip_speech(self, interaction: discord.Interaction):
        """現在の読み上げをスキップします"""
        voice_client = interaction.guild.voice_client
        if not voice_client:
            await interaction.response.send_message("❌ Botがボイスチャンネルに接続していません。", ephemeral=True)
            return

        music_cog: Optional[MusicCog] = self.bot.get_cog("music_cog")
        music_state = music_cog._get_guild_state(interaction.guild.id) if music_cog else None

        skipped = False
        # ミキサーが動作している場合
        if music_state and music_state.mixer:
            # 'tts_' プレフィックスを持つソースをすべて削除
            tts_sources = [name for name in music_state.mixer.sources.keys() if name.startswith("tts_")]
            if tts_sources:
                for name in tts_sources:
                    await music_state.mixer.remove_source(name)
                skipped = True
                print(f"[TTSCog] Skipped {len(tts_sources)} TTS source(s) from mixer.")

        # 通常の再生の場合、またはミキサーにTTSソースがなかった場合
        if voice_client.is_playing():
            # 再生中のソースがTTSAudioSourceか確認
            if isinstance(voice_client.source, TTSAudioSource):
                voice_client.stop()
                skipped = True
                print("[TTSCog] Skipped direct TTS playback.")

        if skipped:
            await interaction.response.send_message("✅ 読み上げをスキップしました。", ephemeral=False)
        else:
            await interaction.response.send_message("❌ スキップ対象の読み上げがありません。", ephemeral=True)

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

    notification_group = app_commands.Group(
        name="join-leave-notification",
        description="VCへの入退室通知に関するコマンド / Commands for voice channel join/leave notifications"
    )

    @notification_group.command(
        name="enable",
        description="VCへの入退室を音声で通知するようにします / Enables voice notifications for members joining/leaving the VC"
    )
    async def enable_notification(self, interaction: discord.Interaction):
        guild_settings = self._get_guild_speech_settings(interaction.guild.id)
        if guild_settings.get("enable_notifications", True):
            await interaction.response.send_message("ℹ️ 入退室通知は既に有効です。 / Join/leave notifications are already enabled.", ephemeral=True)
            return

        guild_settings["enable_notifications"] = True
        self._save_speech_settings()
        await interaction.response.send_message("✅ VCへの入退室通知を有効にしました。 / Enabled join/leave notifications.", ephemeral=False)

    @notification_group.command(
        name="disable",
        description="VCへの入退室通知を無効にします / Disables voice notifications for members joining/leaving the VC"
    )
    async def disable_notification(self, interaction: discord.Interaction):
        guild_settings = self._get_guild_speech_settings(interaction.guild.id)
        if not guild_settings.get("enable_notifications", True):
            await interaction.response.send_message("ℹ️ 入退室通知は既に無効です。 / Join/leave notifications are already disabled.", ephemeral=True)
            return

        guild_settings["enable_notifications"] = False
        self._save_speech_settings()
        await interaction.response.send_message("✅ VCへの入退室通知を無効にしました。 / Disabled join/leave notifications.", ephemeral=False)

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

        await interaction.response.defer()
        async with lock:
            success = await self._handle_say_logic(interaction.guild, text, final_model_id, final_style,
                                                   final_style_weight, final_speed, interaction)
            if success:
                await interaction.followup.send(
                    f"🔊 読み上げ中: `{text}`\n速度: {final_speed}x")

    def _get_tts_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.tts_locks: self.tts_locks[guild_id] = asyncio.Lock()
        return self.tts_locks[guild_id]

    async def trigger_tts_from_event(self, guild: discord.Guild, text: str):
        lock = self._get_tts_lock(guild.id)
        async with lock:
            await self._handle_say_logic(guild, text, self.default_model_id, self.default_style,
                                         self.default_style_weight, self.default_speed)

    def _load_dictionary(self):
        """辞書ファイルを読み込む"""
        try:
            if self.dictionary_file.exists():
                with open(self.dictionary_file, 'r', encoding='utf-8') as f:
                    self.speech_dictionary = json.load(f)
                print(f"✓ [TTSCog] 読み上げ辞書を読み込みました: {len(self.speech_dictionary)}単語")
            else:
                self.dictionary_file.parent.mkdir(parents=True, exist_ok=True)
                self._save_dictionary()
                print(f"✓ [TTSCog] 新しい辞書ファイルを作成しました")
        except Exception as e:
            print(f"✗ [TTSCog] 辞書読み込みエラー: {e}")
            self.speech_dictionary = {}

    def _save_dictionary(self):
        """辞書ファイルを保存"""
        try:
            self.dictionary_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.dictionary_file, 'w', encoding='utf-8') as f:
                json.dump(self.speech_dictionary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"✗ [TTSCog] 辞書保存エラー: {e}")

    def _apply_dictionary(self, text: str) -> str:
        """
        辞書を適用してテキストを変換
        長い単語から順に置換することで、部分一致の問題を回避
        """
        if not self.speech_dictionary:
            return text

        # 辞書のキーを長い順にソート（長い単語を優先的に置換）
        sorted_words = sorted(self.speech_dictionary.keys(), key=len, reverse=True)

        result = text
        for word in sorted_words:
            reading = self.speech_dictionary[word]
            result = result.replace(word, reading)

        return result

    dictionary_group = app_commands.Group(
        name="dictionary",
        description="読み上げ辞書の管理 / Manage speech dictionary"
    )

    @dictionary_group.command(
        name="add",
        description="読み上げ辞書に単語を追加します / Add a word to the speech dictionary"
    )
    @app_commands.describe(
        word="登録する単語 / Word to register",
        reading="読み方（ひらがな・カタカナ推奨） / Reading (hiragana/katakana recommended)"
    )
    async def add_dictionary(self, interaction: discord.Interaction, word: str, reading: str):
        """辞書に単語を追加"""
        if not word or not reading:
            await interaction.response.send_message(
                "❌ 単語と読み方の両方を指定してください。 / Please specify both word and reading.",
                ephemeral=True
            )
            return

        # 既存の単語かチェック
        is_update = word in self.speech_dictionary
        old_reading = self.speech_dictionary.get(word)

        # 辞書に追加
        self.speech_dictionary[word] = reading
        self._save_dictionary()

        if is_update:
            embed = discord.Embed(
                title="📖 辞書を更新しました / Dictionary Updated",
                color=discord.Color.blue()
            )
            embed.add_field(name="単語 / Word", value=f"`{word}`", inline=False)
            embed.add_field(name="変更前 / Before", value=f"`{old_reading}`", inline=True)
            embed.add_field(name="変更後 / After", value=f"`{reading}`", inline=True)
        else:
            embed = discord.Embed(
                title="📖 辞書に追加しました / Added to Dictionary",
                color=discord.Color.green()
            )
            embed.add_field(name="単語 / Word", value=f"`{word}`", inline=True)
            embed.add_field(name="読み方 / Reading", value=f"`{reading}`", inline=True)

        embed.set_footer(text=f"辞書登録数: {len(self.speech_dictionary)}語")
        await interaction.response.send_message(embed=embed)

    @dictionary_group.command(
        name="remove",
        description="読み上げ辞書から単語を削除します / Remove a word from the speech dictionary"
    )
    @app_commands.describe(word="削除する単語 / Word to remove")
    async def remove_dictionary(self, interaction: discord.Interaction, word: str):
        """辞書から単語を削除"""
        if word not in self.speech_dictionary:
            await interaction.response.send_message(
                f"❌ `{word}` は辞書に登録されていません。 / `{word}` is not in the dictionary.",
                ephemeral=True
            )
            return

        reading = self.speech_dictionary.pop(word)
        self._save_dictionary()

        embed = discord.Embed(
            title="📖 辞書から削除しました / Removed from Dictionary",
            color=discord.Color.orange()
        )
        embed.add_field(name="単語 / Word", value=f"`{word}`", inline=True)
        embed.add_field(name="読み方 / Reading", value=f"`{reading}`", inline=True)
        embed.set_footer(text=f"辞書登録数: {len(self.speech_dictionary)}語")

        await interaction.response.send_message(embed=embed)

    @dictionary_group.command(
        name="list",
        description="登録されている辞書の一覧を表示します / Display the list of registered words"
    )
    async def list_dictionary(self, interaction: discord.Interaction):
        """辞書の一覧を表示"""
        if not self.speech_dictionary:
            await interaction.response.send_message(
                "📖 辞書に登録されている単語はありません。 / No words in the dictionary.",
                ephemeral=True
            )
            return

        items_per_page = 20
        dictionary_items = sorted(self.speech_dictionary.items(), key=lambda x: x[0])
        total_items = len(dictionary_items)
        total_pages = (total_items + items_per_page - 1) // items_per_page

        def get_page_embed(page_num: int):
            embed = discord.Embed(
                title="📖 読み上げ辞書 / Speech Dictionary",
                description=f"登録単語数: {total_items}語 / Total: {total_items} words",
                color=discord.Color.blue()
            )

            start = (page_num - 1) * items_per_page
            end = start + items_per_page

            for word, reading in dictionary_items[start:end]:
                embed.add_field(
                    name=word,
                    value=f"→ {reading}",
                    inline=True
                )

            if total_pages > 1:
                embed.set_footer(text=f"ページ {page_num}/{total_pages} / Page {page_num}/{total_pages}")

            return embed

        def get_view(current_page: int, user_id: int):
            view = discord.ui.View(timeout=120.0)

            prev_button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                emoji="◀️",
                label="Previous",
                disabled=(current_page == 1)
            )

            async def prev_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message(
                        "このボタンは使用できません。 / You cannot use this button.",
                        ephemeral=True
                    )
                    return
                nonlocal current_page
                current_page = max(1, current_page - 1)
                await button_interaction.response.edit_message(
                    embed=get_page_embed(current_page),
                    view=get_view(current_page, user_id)
                )

            prev_button.callback = prev_callback
            view.add_item(prev_button)

            close_button = discord.ui.Button(
                style=discord.ButtonStyle.danger,
                emoji="⏹️",
                label="Close"
            )

            async def close_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message(
                        "このボタンは使用できません。 / You cannot use this button.",
                        ephemeral=True
                    )
                    return
                await button_interaction.response.edit_message(view=None)

            close_button.callback = close_callback
            view.add_item(close_button)

            next_button = discord.ui.Button(
                style=discord.ButtonStyle.primary,
                emoji="▶️",
                label="Next",
                disabled=(current_page == total_pages)
            )

            async def next_callback(button_interaction: discord.Interaction):
                if button_interaction.user.id != user_id:
                    await button_interaction.response.send_message(
                        "このボタンは使用できません。 / You cannot use this button.",
                        ephemeral=True
                    )
                    return
                nonlocal current_page
                current_page = min(total_pages, current_page + 1)
                await button_interaction.response.edit_message(
                    embed=get_page_embed(current_page),
                    view=get_view(current_page, user_id)
                )

            next_button.callback = next_callback
            view.add_item(next_button)

            return view

        current_page = 1
        if total_pages <= 1:
            await interaction.response.send_message(embed=get_page_embed(current_page))
        else:
            await interaction.response.send_message(
                embed=get_page_embed(current_page),
                view=get_view(current_page, interaction.user.id)
            )

    @dictionary_group.command(
        name="search",
        description="辞書から単語を検索します / Search for a word in the dictionary"
    )
    @app_commands.describe(query="検索する単語（部分一致） / Search query (partial match)")
    async def search_dictionary(self, interaction: discord.Interaction, query: str):
        """辞書から単語を検索"""
        if not self.speech_dictionary:
            await interaction.response.send_message(
                "📖 辞書に登録されている単語はありません。 / No words in the dictionary.",
                ephemeral=True
            )
            return

        # 部分一致で検索
        results = {word: reading for word, reading in self.speech_dictionary.items() if query.lower() in word.lower()}

        if not results:
            await interaction.response.send_message(
                f"❌ `{query}` に一致する単語が見つかりませんでした。 / No words found matching `{query}`.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🔍 検索結果: {query}",
            description=f"{len(results)}件見つかりました / Found {len(results)} result(s)",
            color=discord.Color.blue()
        )

        for word, reading in sorted(results.items())[:25]:  # 最大25件まで表示
            embed.add_field(
                name=word,
                value=f"→ {reading}",
                inline=True
            )

        if len(results) > 25:
            embed.set_footer(text=f"... 他 {len(results) - 25} 件 / ... and {len(results) - 25} more")

        await interaction.response.send_message(embed=embed)

    async def _handle_say_logic(self, guild: discord.Guild, text: str, model_id: int, style: str,
                                style_weight: float, speed: float,
                                interaction: Optional[discord.Interaction] = None) -> bool:
        voice_client = guild.voice_client
        if not voice_client:
            return False

        # 辞書を適用してテキストを変換
        converted_text = self._apply_dictionary(text)
        print(f"[TTSCog] Original: {text}")
        print(f"[TTSCog] Converted: {converted_text}")

        # 200文字を超える場合は切り詰めて「以下省略」を追加
        if len(converted_text) > 200:
            converted_text = converted_text[:200] + " 以下省略"

        music_cog: Optional[MusicCog] = self.bot.get_cog("music_cog")
        music_state = music_cog._get_guild_state(guild.id) if music_cog else None

        if music_state and music_state.mixer and music_state.is_playing:
            return await self._overlay_tts_with_mixer(guild, converted_text, model_id, style, style_weight, speed, interaction)
        else:
            return await self._play_tts_directly(guild, converted_text, model_id, style, style_weight, speed, interaction)

    async def _overlay_tts_with_mixer(self, guild: discord.Guild, text: str, model_id: int, style: str,
                                      style_weight: float, speed: float,
                                      interaction: Optional[discord.Interaction] = None) -> bool:
        music_cog: Optional[MusicCog] = self.bot.get_cog("music_cog")
        music_state = music_cog._get_guild_state(guild.id)

        endpoint = f"{self.api_url}/voice"
        payload = {"text": text, "model_id": model_id, "style": style,
                   "style_weight": style_weight, "speed": speed, "encoding": "wav"}
        try:
            async with self.session.post(endpoint, params=payload) as response:
                if response.status != 200:
                    error_text = await response.text()
                    if interaction: await interaction.followup.send(
                        f"❌ 音声生成エラー: {response.status}\n```{error_text[:200]}```", ephemeral=True)
                    return False

                wav_data = await response.read()
                print(f"[DEBUG] Received {len(wav_data)} bytes from TTS API")

                # BytesIOを作成してシーク位置を先頭に
                wav_buffer = io.BytesIO(wav_data)
                wav_buffer.seek(0)

                tts_source = TTSAudioSource(
                    wav_buffer,
                    text=text,
                    guild_id=guild.id,
                    pipe=True  # pipe=True に戻す（BytesIOの場合は必須）
                )

                source_name = f"tts_{int(time.time() * 1000)}"
                await music_state.mixer.add_source(source_name, tts_source, volume=1.0)

                print(f"[TTSCog] Added TTS source '{source_name}' to the mixer for guild {guild.id}")
                return True

        except Exception as e:
            if interaction: await interaction.followup.send(f"❌ TTS再生中にエラーが発生しました: {type(e).__name__}",
                                                            ephemeral=True)
            print(f"Error during TTS overlay: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _play_tts_directly(self, guild: discord.Guild, text: str, model_id: int, style: str,
                                 style_weight: float, speed: float,
                                 interaction: Optional[discord.Interaction] = None) -> bool:
        voice_client = guild.voice_client
        if not voice_client or voice_client.is_playing():
            return False

        endpoint = f"{self.api_url}/voice"
        payload = {"text": text, "model_id": model_id, "style": style,
                   "style_weight": style_weight, "speed": speed, "encoding": "wav"}
        try:
            async with self.session.post(endpoint, params=payload) as response:
                if response.status == 200:
                    wav_data = await response.read()
                    print(f"[DEBUG] Received {len(wav_data)} bytes from TTS API")

                    # BytesIOを作成してシーク位置を先頭に
                    wav_buffer = io.BytesIO(wav_data)
                    wav_buffer.seek(0)

                    source = TTSAudioSource(
                        wav_buffer,
                        text=text,
                        guild_id=guild.id,
                        pipe=True  # pipe=True に戻す（BytesIOの場合は必須）
                    )

                    voice_client.play(source)
                    return True
                else:
                    error_text = await response.text()
                    if interaction: await interaction.followup.send(
                        f"❌ 音声生成エラー: {response.status}\n```{error_text[:200]}```", ephemeral=True)
                    return False
        except Exception as e:
            if interaction: await interaction.followup.send(f"❌ エラーが発生しました: {type(e).__name__}",
                                                            ephemeral=True)
            print(f"Error during TTS playback: {e}")
            import traceback
            traceback.print_exc()
            return False


async def setup(bot: commands.Bot):
    if 'tts' not in bot.config:
        print("Warning: 'tts' section not found in config.yaml. TTSCog will not be loaded.")
        return
    if not bot.get_cog("music_cog"):
        print("Warning: MusicCog is not loaded. TTSCog may not function correctly with music.")
    
    tts_cog = TTSCog(bot)
    await bot.add_cog(tts_cog)
