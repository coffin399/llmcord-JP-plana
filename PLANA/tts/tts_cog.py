import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio
from typing import Dict, Tuple, Optional, Set

# MusicCogのクラスやオブジェクトの型ヒントのため
try:
    from .music_cog import MusicCog, GuildState as MusicGuildState, Track
except ImportError:
    MusicCog = commands.Cog
    MusicGuildState = any
    Track = any

# エラーハンドラをインポート
try:
    from ..error.errors import TTSCogExceptionHandler
except ImportError:
    try:
        from error.errors import TTSCogExceptionHandler
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

        self.api_url = self.config.get('api_server_url')
        self.api_key = self.config.get('api_key')

        if not self.api_url or not self.api_key:
            raise ValueError("tts.api_server_url and tts.api_key must be set in the config.yaml file.")

        # 自前APIサーバー用のヘッダー設定
        self.session = aiohttp.ClientSession(headers={"X-API-KEY": self.api_key})
        self.exception_handler = TTSCogExceptionHandler()

        self.interrupted_states: Dict[int, Tuple[Track, int]] = {}
        self.tts_locks: Dict[int, asyncio.Lock] = {}
        self.initialized_models: Set[str] = set()

        print("TTSCog loaded (for local FastAPI server).")

    async def cog_load(self):
        print("TTSCog loaded. Models will be initialized on demand.")

    async def cog_unload(self):
        await self.session.close()
        print("TTSCog unloaded and session closed.")

    async def ensure_model_initialized(self) -> bool:
        if self.api_key in self.initialized_models:
            return True

        print(f"[TTSCog] モデルを初期化しています (APIキー: {self.api_key[:8]}...).")
        try:
            async with self.session.post(f"{self.api_url}/init") as response:
                if response.status == 200:
                    self.initialized_models.add(self.api_key)
                    print(f"✓ [TTSCog] モデル初期化成功")
                    return True
                else:
                    error_text = await response.text()
                    print(f"✗ [TTSCog] モデル初期化失敗 (ステータス: {response.status}): {error_text}")
                    return False
        except aiohttp.ClientConnectorError as e:
            print(f"✗ [TTSCog] APIサーバーに接続できません: {self.api_url}")
            return False
        except Exception as e:
            print(f"✗ [TTSCog] モデル初期化中に予期しないエラーが発生: {e}")
            return False

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if not self.config.get('enable_join_leave_notice', False):
            return
        if member.bot or not member.guild.voice_client:
            return

        text_to_say = None
        bot_channel = member.guild.voice_client.channel

        if before.channel != bot_channel and after.channel == bot_channel:
            template = self.config.get('join_message_template', "{member_name}さんが参加しました。")
            text_to_say = template.format(member_name=member.display_name)
        elif before.channel == bot_channel and after.channel != bot_channel:
            template = self.config.get('leave_message_template', "{member_name}さんが退出しました。")
            text_to_say = template.format(member_name=member.display_name)

        if text_to_say:
            if await self.ensure_model_initialized():
                await self.trigger_tts_from_event(member.guild, text_to_say)
            else:
                print("[TTSCog] モデル初期化失敗のため、入退室通知をスキップします。")

    @app_commands.command(name="say", description="テキストを音声で読み上げます。(音楽再生中でも割り込みます)")
    @app_commands.describe(text="読み上げるテキスト", language="言語 (例: JP, EN)", speaker_id="話者ID")
    async def say(self, interaction: discord.Interaction, text: str, language: Optional[str] = None,
                  speaker_id: Optional[int] = None):
        if not self.config.get('enable_say_command', False):
            await interaction.response.send_message("読み上げコマンドは現在無効化されています。", ephemeral=True)
            return
        if not interaction.guild.voice_client:
            await self.exception_handler.send_message(interaction, "bot_not_in_voice", ephemeral=True)
            return

        lock = self._get_tts_lock(interaction.guild.id)
        if lock.locked():
            await self.exception_handler.send_message(interaction, "tts_in_progress", ephemeral=True)
            return

        final_lang = language if language is not None else self.config.get('default_language', 'JP')
        final_spk_id = speaker_id if speaker_id is not None else self.config.get('default_speaker_id', 0)

        async with lock:
            await interaction.response.defer()
            initialized = await self.ensure_model_initialized()
            if not initialized:
                await interaction.followup.send("モデルの初期化に失敗しました。APIサーバーの状態を確認してください。",
                                                ephemeral=True)
                return

            success = await self._handle_say_logic(interaction.guild, text, final_lang, final_spk_id, interaction)
            if success:
                await self.exception_handler.send_message(interaction, "tts_success", followup=True, text=text)

    def _get_tts_lock(self, guild_id: int) -> asyncio.Lock:
        if guild_id not in self.tts_locks:
            self.tts_locks[guild_id] = asyncio.Lock()
        return self.tts_locks[guild_id]

    async def trigger_tts_from_event(self, guild: discord.Guild, text: str):
        lock = self._get_tts_lock(guild.id)
        async with lock:
            lang = self.config.get('default_language', 'JP')
            spk_id = self.config.get('default_speaker_id', 0)
            await self._handle_say_logic(guild, text, lang, spk_id)

    async def _handle_say_logic(self, guild: discord.Guild, text: str, language: str, speaker_id: int,
                                interaction: Optional[discord.Interaction] = None) -> bool:
        voice_client = guild.voice_client
        if not voice_client: return False

        music_cog: MusicCog = self.bot.get_cog("music_cog")
        music_state: MusicGuildState = music_cog._get_guild_state(guild.id) if music_cog else None

        if music_state and music_state.is_playing and music_state.current_track:
            print(f"[TTSCog] 音楽を一時中断してTTSを再生します (guild {guild.id}): '{text}'")
            current_position = music_state.get_current_position()
            self.interrupted_states[guild.id] = (music_state.current_track, current_position)
            music_state.is_seeking = True
            voice_client.stop()
            await asyncio.sleep(0.1)
            music_state.is_seeking = False

        payload = {
            "text": text,
            "language": language,
            "speaker_id": speaker_id
        }

        endpoint_url = f"{self.api_url}/tts"

        try:
            print(f"[TTSCog] 自前APIにリクエスト送信: {endpoint_url}")
            async with self.session.post(endpoint_url, json=payload) as response:
                if response.status == 200:
                    wav_data = await response.read()
                    source = discord.FFmpegPCMAudio(io.BytesIO(wav_data))

                    while voice_client.is_playing():
                        await asyncio.sleep(0.5)
                    voice_client.play(source, after=lambda e: asyncio.run_coroutine_threadsafe(
                        self._tts_after_playback(e, guild.id), self.bot.loop).result())
                    return True
                else:
                    if interaction:
                        await self.exception_handler.handle_api_error(interaction, response)
                    else:
                        error_text = await response.text()
                        print(f"[TTSCog] APIエラー (guild {guild.id}): {response.status}, {error_text}")
                    self.interrupted_states.pop(guild.id, None)
                    return False
        except aiohttp.ClientConnectorError:
            if interaction:
                await self.exception_handler.handle_connection_error(interaction)
            else:
                print(f"[TTSCog] API接続エラー (guild {guild.id})")
            self.interrupted_states.pop(guild.id, None)
            return False
        except Exception as e:
            if interaction:
                await self.exception_handler.handle_unexpected_error(interaction, e)
            else:
                print(f"[TTSCog] 予期しないエラー (guild {guild.id}): {e}")
            self.interrupted_states.pop(guild.id, None)
            return False

    async def _tts_after_playback(self, error: Exception, guild_id: int):
        if error:
            print(f"[TTSCog] 再生エラー (guild {guild_id}): {error}")
        if guild_id in self.interrupted_states:
            interrupted_track, position = self.interrupted_states.pop(guild_id)
            music_cog: MusicCog = self.bot.get_cog("music_cog")
            if music_cog:
                print(f"[TTSCog] 音楽を再開します (guild {guild.id}) 位置: {position}秒")
                music_state = music_cog._get_guild_state(guild_id)
                music_state.current_track = interrupted_track
                await music_cog._play_next_song(guild_id, seek_seconds=position)
        else:
            print(f"[TTSCog] TTS再生完了 (guild {guild.id}). 再開する音楽はありません。")


async def setup(bot: commands.Bot):
    if 'tts' not in bot.config:
        print("Warning: 'tts' section not found in config.yaml. TTSCog will not be loaded.")
        return
    if not bot.get_cog("music_cog"):
        print("Warning: MusicCog is not loaded. TTSCog may not function correctly with music.")
    await bot.add_cog(TTSCog(bot))