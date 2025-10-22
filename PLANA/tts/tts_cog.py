import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio
from typing import Dict, Tuple, Optional, List

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

        self.api_url = self.config.get('api_server_url', 'http://127.0.0.1:5000')
        self.api_key = self.config.get('api_key')  # Style-Bert-VITS2では通常不要

        # Style-Bert-VITS2用の設定
        self.default_model_id = self.config.get('default_model_id', 0)
        self.default_style = self.config.get('default_style', 'Neutral')
        self.default_style_weight = self.config.get('default_style_weight', 5.0)
        self.default_speed = self.config.get('default_speed', 1.0)

        # セッションの作成（APIキーがある場合のみヘッダーに追加）
        headers = {}
        if self.api_key:
            headers["X-API-KEY"] = self.api_key

        self.session = aiohttp.ClientSession(headers=headers)
        self.exception_handler = TTSCogExceptionHandler()

        self.interrupted_states: Dict[int, Tuple[Track, int]] = {}
        self.tts_locks: Dict[int, asyncio.Lock] = {}

        # Style-Bert-VITS2では事前の初期化は不要（モデルは自動ロード）
        self.available_models: List[Dict] = []
        self.models_loaded: bool = False

        print("TTSCog loaded (Style-Bert-VITS2 compatible)")

    # --- Cog Lifecycle Events ---

    async def cog_load(self):
        """Cogがロードされたことを通知し、利用可能なモデルを取得"""
        print("TTSCog loaded. Fetching available models...")
        await self.fetch_available_models()

    async def cog_unload(self):
        """Cogがアンロードされる際にセッションを閉じる"""
        await self.session.close()
        print("TTSCog unloaded and session closed.")

    # --- Helper Functions ---

    async def fetch_available_models(self) -> bool:
        """
        Style-Bert-VITS2サーバーから利用可能なモデル一覧を取得
        """
        try:
            async with self.session.get(f"{self.api_url}/models/info") as response:
                if response.status == 200:
                    data = await response.json()
                    self.available_models = data
                    self.models_loaded = True
                    print(f"✓ [TTSCog] {len(self.available_models)}個のモデルを検出")
                    for model in self.available_models:
                        print(f"  - Model ID {model['id']}: {model['name']}")
                    return True
                else:
                    print(f"✗ [TTSCog] モデル情報取得失敗: {response.status}")
                    return False
        except aiohttp.ClientConnectorError as e:
            print(f"✗ [TTSCog] APIサーバーに接続できません: {self.api_url}")
            print(f"  エラー: {e}")
            return False
        except Exception as e:
            print(f"✗ [TTSCog] モデル情報取得中にエラー: {type(e).__name__}: {e}")
            return False

    def get_model_name(self, model_id: int) -> str:
        """モデルIDから名前を取得"""
        for model in self.available_models:
            if model['id'] == model_id:
                return model['name']
        return f"Model {model_id}"

    # --- Event Listener ---

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
            await self.trigger_tts_from_event(member.guild, text_to_say)

    # --- Slash Commands ---

    @app_commands.command(name="say", description="テキストを音声で読み上げます")
    @app_commands.describe(
        text="読み上げるテキスト",
        model_id="モデルID (省略時はデフォルト)",
        style="スタイル名 (例: Neutral, Happy, Angry)",
        style_weight="スタイルの強さ (0.0-10.0)",
        speed="話速 (0.5-2.0)"
    )
    async def say(
            self,
            interaction: discord.Interaction,
            text: str,
            model_id: Optional[int] = None,
            style: Optional[str] = None,
            style_weight: Optional[float] = None,
            speed: Optional[float] = None
    ):
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

        # パラメータのデフォルト値設定
        final_model_id = model_id if model_id is not None else self.default_model_id
        final_style = style if style is not None else self.default_style
        final_style_weight = style_weight if style_weight is not None else self.default_style_weight
        final_speed = speed if speed is not None else self.default_speed

        async with lock:
            await interaction.response.defer()

            success = await self._handle_say_logic(
                interaction.guild,
                text,
                final_model_id,
                final_style,
                final_style_weight,
                final_speed,
                interaction
            )
            if success:
                model_name = self.get_model_name(final_model_id)
                await interaction.followup.send(
                    f"🔊 読み上げ中: `{text}`\n"
                    f"モデル: {model_name} | スタイル: {final_style} ({final_style_weight}) | 速度: {final_speed}x"
                )

    @app_commands.command(name="tts_models", description="利用可能な音声モデルの一覧を表示")
    async def tts_models(self, interaction: discord.Interaction):
        """利用可能なモデルとスタイルの一覧を表示"""
        await interaction.response.defer(ephemeral=True)

        if not self.models_loaded:
            await self.fetch_available_models()

        if not self.available_models:
            await interaction.followup.send(
                "❌ 利用可能なモデルが見つかりませんでした。",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🎙️ 利用可能な音声モデル",
            description=f"合計 {len(self.available_models)} 個のモデル",
            color=discord.Color.blue()
        )

        for model in self.available_models[:10]:  # 最大10個まで表示
            styles = ", ".join(model.get('styles', ['Neutral']))
            embed.add_field(
                name=f"ID: {model['id']} - {model['name']}",
                value=f"スタイル: {styles}",
                inline=False
            )

        if len(self.available_models) > 10:
            embed.set_footer(text=f"... 他 {len(self.available_models) - 10} 個のモデル")

        await interaction.followup.send(embed=embed, ephemeral=True)

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
            await self._handle_say_logic(
                guild,
                text,
                self.default_model_id,
                self.default_style,
                self.default_style_weight,
                self.default_speed
            )

    async def _handle_say_logic(
            self,
            guild: discord.Guild,
            text: str,
            model_id: int,
            style: str,
            style_weight: float,
            speed: float,
            interaction: Optional[discord.Interaction] = None
    ) -> bool:
        """
        読み上げのコアロジック。Style-Bert-VITS2 API対応版
        """
        voice_client = guild.voice_client
        if not voice_client:
            return False

        music_cog: MusicCog = self.bot.get_cog("music_cog")
        music_state: MusicGuildState = music_cog._get_guild_state(guild.id) if music_cog else None

        # 音楽再生中の場合は一時停止
        if music_state and music_state.is_playing and music_state.current_track:
            print(f"[TTSCog] 音楽を一時中断してTTSを再生します (guild {guild.id}): '{text}'")
            current_position = music_state.get_current_position()
            self.interrupted_states[guild.id] = (music_state.current_track, current_position)

            music_state.is_seeking = True
            voice_client.stop()
            await asyncio.sleep(0.1)
            music_state.is_seeking = False

        # Style-Bert-VITS2 APIのエンドポイント
        endpoint = f"{self.api_url}/voice"

        params = {
            "text": text,
            "model_id": model_id,
            "style": style,
            "style_weight": style_weight,
            "speed": speed,
            "encoding": "wav"  # WAV形式で取得
        }

        try:
            async with self.session.get(endpoint, params=params) as response:
                if response.status == 200:
                    wav_data = await response.read()
                    source = discord.FFmpegPCMAudio(io.BytesIO(wav_data), pipe=True)

                    # 既存の再生が完了するまで待機
                    while voice_client.is_playing():
                        await asyncio.sleep(0.1)

                    voice_client.play(
                        source,
                        after=lambda e: asyncio.run_coroutine_threadsafe(
                            self._tts_after_playback(e, guild.id), self.bot.loop
                        ).result()
                    )
                    return True
                else:
                    error_text = await response.text()
                    print(f"[TTSCog] APIエラー (guild {guild.id}): {response.status}")
                    print(f"  詳細: {error_text}")

                    if interaction:
                        await interaction.followup.send(
                            f"❌ 音声生成エラー: {response.status}\n```{error_text[:200]}```",
                            ephemeral=True
                        )

                    self.interrupted_states.pop(guild.id, None)
                    return False

        except aiohttp.ClientConnectorError:
            print(f"[TTSCog] API接続エラー (guild {guild.id}): {self.api_url}")
            if interaction:
                await interaction.followup.send(
                    f"❌ APIサーバーに接続できません: {self.api_url}",
                    ephemeral=True
                )
            self.interrupted_states.pop(guild.id, None)
            return False
        except Exception as e:
            print(f"[TTSCog] 予期しないエラー (guild {guild.id}): {type(e).__name__}: {e}")
            if interaction:
                await interaction.followup.send(
                    f"❌ エラーが発生しました: {type(e).__name__}",
                    ephemeral=True
                )
            self.interrupted_states.pop(guild.id, None)
            return False

    async def _tts_after_playback(self, error: Exception, guild_id: int):
        """読み上げ再生が完了したときに呼び出されるコールバック"""
        if error:
            print(f"[TTSCog] 再生エラー (guild {guild_id}): {error}")

        if guild_id in self.interrupted_states:
            interrupted_track, position = self.interrupted_states.pop(guild_id)

            music_cog: MusicCog = self.bot.get_cog("music_cog")
            if music_cog:
                print(f"[TTSCog] 音楽を再開します (guild {guild_id}) 位置: {position}秒")
                music_state = music_cog._get_guild_state(guild_id)
                music_state.current_track = interrupted_track
                await music_cog._play_next_song(guild_id, seek_seconds=position)
        else:
            print(f"[TTSCog] TTS再生完了 (guild {guild_id}). 再開する音楽はありません。")


async def setup(bot: commands.Bot):
    if 'tts' not in bot.config:
        print("Warning: 'tts' section not found in config.yaml. TTSCog will not be loaded.")
        return

    if not bot.get_cog("music_cog"):
        print("Warning: MusicCog is not loaded. TTSCog may not function correctly with music.")

    await bot.add_cog(TTSCog(bot))