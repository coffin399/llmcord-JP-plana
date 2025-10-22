# PLANA/tts/tts_cog.py
import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio
from typing import Dict, Tuple, Optional

# MusicCogのクラスやオブジェクトの型ヒントのため
try:
    from .music_cog import MusicCog, GuildState as MusicGuildState, Track
except ImportError:
    MusicCog = commands.Cog
    MusicGuildState = any
    Track = any

# エラーハンドラをインポート
try:
    from PLANA.tts.error.errors import TTSCogExceptionHandler
except ImportError:
    # フォールバック
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
        # config.yamlからttsセクションを読み込む
        self.config = bot.config.get('tts', {})

        # config.yamlからAPI設定を取得
        self.api_url = self.config.get('api_server_url')
        self.api_key = self.config.get('api_key')

        if not self.api_url or not self.api_key:
            raise ValueError("tts.api_server_url and tts.api_key must be set in the config.yaml file.")

        self.session = aiohttp.ClientSession(headers={"X-API-KEY": self.api_key})
        self.exception_handler = TTSCogExceptionHandler()

        self.interrupted_states: Dict[int, Tuple[Track, int]] = {}
        self.tts_locks: Dict[int, asyncio.Lock] = {}

        print("TTSCog loaded (with config.yaml integration, event listener, and music interruption support).")

    # --- Cog Lifecycle Events ---

    async def cog_load(self):
        """Cogがロードされた際にAPIサーバーのモデルを初期化する"""
        print("Initializing TTS model via API...")
        try:
            async with self.session.post(f"{self.api_url}/init") as response:
                if response.status == 200:
                    print("TTS Model initialized successfully.")
                else:
                    error_text = await response.text()
                    print(f"Failed to initialize TTS model. Status: {response.status}, Response: {error_text}")
        except aiohttp.ClientConnectorError:
            print(f"Error: Could not connect to the TTS API server at {self.api_url}. Is it running?")
        except Exception as e:
            print(f"An unexpected error occurred during TTS model initialization: {e}")

    async def cog_unload(self):
        """Cogがアンロードされる際にセッションを閉じる"""
        await self.session.close()
        print("TTSCog unloaded and session closed.")

    # --- Event Listener ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        # config.yamlで機能が無効化されていれば何もしない
        if not self.config.get('enable_join_leave_notice', False):
            return

        # BOT自身のイベントや、BOTがVCにいない場合は無視
        if member.bot or not member.guild.voice_client:
            return

        text_to_say = None
        bot_channel = member.guild.voice_client.channel

        # ユーザーがBOTのいるVCに参加した時
        if before.channel != bot_channel and after.channel == bot_channel:
            template = self.config.get('join_message_template', "{member_name}さんが参加しました。")
            text_to_say = template.format(member_name=member.display_name)

        # ユーザーがBOTのいるVCから退出した時
        elif before.channel == bot_channel and after.channel != bot_channel:
            template = self.config.get('leave_message_template', "{member_name}さんが退出しました。")
            text_to_say = template.format(member_name=member.display_name)

        if text_to_say:
            # 読み上げ処理をトリガー
            await self.trigger_tts_from_event(member.guild, text_to_say)

    # --- Slash Command ---

    @app_commands.command(name="say", description="テキストを音声で読み上げます。(音楽再生中でも割り込みます)")
    @app_commands.describe(text="読み上げるテキスト", language="言語 (例: JP, EN)", speaker_id="話者ID")
    async def say(self, interaction: discord.Interaction, text: str, language: Optional[str] = None,
                  speaker_id: Optional[int] = None):
        # config.yamlで機能が無効化されていればエラーメッセージを返す
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

        # 引数が指定されなかった場合、config.yamlのデフォルト値を使用
        final_lang = language if language is not None else self.config.get('default_language', 'JP')
        final_spk_id = speaker_id if speaker_id is not None else self.config.get('default_speaker_id', 0)

        async with lock:
            await interaction.response.defer()
            success = await self._handle_say_logic(interaction.guild, text, final_lang, final_spk_id, interaction)
            if success:
                await self.exception_handler.send_message(interaction, "tts_success", followup=True, text=text)

    # --- Core Logic ---

    def _get_tts_lock(self, guild_id: int) -> asyncio.Lock:
        """ギルドごとのロックを取得または作成する"""
        if guild_id not in self.tts_locks:
            self.tts_locks[guild_id] = asyncio.Lock()
        return self.tts_locks[guild_id]

    async def trigger_tts_from_event(self, guild: discord.Guild, text: str):
        """イベントからTTSをトリガーするためのヘルパー関数"""
        lock = self._get_tts_lock(guild.id)
        async with lock:
            # イベントからの呼び出しでは、configのデフォルト値を使用
            lang = self.config.get('default_language', 'JP')
            spk_id = self.config.get('default_speaker_id', 0)
            await self._handle_say_logic(guild, text, lang, spk_id)

    async def _handle_say_logic(self, guild: discord.Guild, text: str, language: str, speaker_id: int,
                                interaction: Optional[discord.Interaction] = None) -> bool:
        """
        読み上げのコアロジック。コマンドとイベントの両方から呼び出される。
        interactionが渡された場合、エラーメッセージを送信する。
        """
        voice_client = guild.voice_client
        if not voice_client: return False

        music_cog: MusicCog = self.bot.get_cog("music_cog")
        music_state: MusicGuildState = music_cog._get_guild_state(guild.id) if music_cog else None

        if music_state and music_state.is_playing and music_state.current_track:
            print(f"Interrupting music in guild {guild.id} for TTS: '{text}'")
            current_position = music_state.get_current_position()
            self.interrupted_states[guild.id] = (music_state.current_track, current_position)

            music_state.is_seeking = True
            voice_client.stop()
            await asyncio.sleep(0.1)
            music_state.is_seeking = False

        payload = {"text": text, "language": language, "speaker_id": speaker_id}
        try:
            async with self.session.post(f"{self.api_url}/tts", json=payload) as response:
                if response.status == 200:
                    wav_data = await response.read()
                    source = discord.FFmpegPCMAudio(io.BytesIO(wav_data))

                    while voice_client.is_playing():
                        await asyncio.sleep(0.5)

                    voice_client.play(
                        source,
                        after=lambda e: asyncio.run_coroutine_threadsafe(
                            self._tts_after_playback(e, guild.id), self.bot.loop
                        ).result()
                    )
                    return True
                else:
                    if interaction:
                        await self.exception_handler.handle_api_error(interaction, response)
                    else:
                        print(f"[TTSCog] API Error in guild {guild.id}: {response.status}")
                    self.interrupted_states.pop(guild.id, None)
                    return False

        except aiohttp.ClientConnectorError:
            if interaction:
                await self.exception_handler.handle_connection_error(interaction)
            else:
                print(f"[TTSCog] API Connection Error in guild {guild.id}")
            self.interrupted_states.pop(guild.id, None)
            return False
        except Exception as e:
            if interaction:
                await self.exception_handler.handle_unexpected_error(interaction, e)
            else:
                print(f"[TTSCog] Unexpected Error in guild {guild.id}: {e}")
            self.interrupted_states.pop(guild.id, None)
            return False

    async def _tts_after_playback(self, error: Exception, guild_id: int):
        """読み上げ再生が完了したときに呼び出されるコールバック"""
        if error:
            print(f"[TTSCog] Playback error in guild {guild_id}: {error}")

        if guild_id in self.interrupted_states:
            interrupted_track, position = self.interrupted_states.pop(guild_id)

            music_cog: MusicCog = self.bot.get_cog("music_cog")
            if music_cog:
                print(f"[TTSCog] Resuming music in guild {guild_id} at {position}s.")
                music_state = music_cog._get_guild_state(guild_id)
                music_state.current_track = interrupted_track
                await music_cog._play_next_song(guild_id, seek_seconds=position)
        else:
            print(f"[TTSCog] TTS finished in guild {guild_id}. No music to resume.")


async def setup(bot: commands.Bot):
    # config.yamlに'tts'セクションがあるか確認
    if 'tts' not in bot.config:
        print("Warning: 'tts' section not found in config.yaml. TTSCog will not be loaded.")
        return

    # MusicCogがロードされているか確認
    if not bot.get_cog("music_cog"):
        print("Warning: MusicCog is not loaded. TTSCog may not function correctly with music.")

    await bot.add_cog(TTSCog(bot))