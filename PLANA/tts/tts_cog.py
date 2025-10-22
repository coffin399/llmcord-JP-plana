import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import io
import asyncio
import json
import os
from pathlib import Path
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

        # チャンネルごとのモデル設定を保存
        self.settings_file = Path("data/tts_settings.json")
        self.channel_settings: Dict[int, Dict] = {}
        self._load_settings()

        print("TTSCog loaded (Style-Bert-VITS2 compatible)")

    # --- Cog Lifecycle Events ---

    async def cog_load(self):
        """Cogがロードされたことを通知し、利用可能なモデルを取得"""
        print("TTSCog loaded. Fetching available models...")
        await self.fetch_available_models()

    async def cog_unload(self):
        """Cogがアンロードされる際にセッションを閉じる"""
        self._save_settings()
        await self.session.close()
        print("TTSCog unloaded and session closed.")

    # --- Settings Management ---

    def _load_settings(self):
        """チャンネル設定をファイルから読み込む"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    # キーを整数に変換
                    data = json.load(f)
                    self.channel_settings = {int(k): v for k, v in data.items()}
                print(f"✓ [TTSCog] 設定を読み込みました: {len(self.channel_settings)}チャンネル")
            else:
                # dataディレクトリが存在しない場合は作成
                self.settings_file.parent.mkdir(parents=True, exist_ok=True)
                print("[TTSCog] 設定ファイルが見つかりません。新規作成します。")
        except Exception as e:
            print(f"✗ [TTSCog] 設定読み込みエラー: {e}")
            self.channel_settings = {}

    def _save_settings(self):
        """チャンネル設定をファイルに保存"""
        try:
            self.settings_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                # キーを文字列に変換してJSON保存
                data = {str(k): v for k, v in self.channel_settings.items()}
                json.dump(data, f, ensure_ascii=False, indent=2)
            print(f"✓ [TTSCog] 設定を保存しました: {len(self.channel_settings)}チャンネル")
        except Exception as e:
            print(f"✗ [TTSCog] 設定保存エラー: {e}")

    def _get_channel_settings(self, channel_id: int) -> Dict:
        """チャンネルの設定を取得（なければデフォルト値を返す）"""
        if channel_id not in self.channel_settings:
            return {
                "model_id": self.default_model_id,
                "style": self.default_style,
                "style_weight": self.default_style_weight,
                "speed": self.default_speed
            }
        return self.channel_settings[channel_id]

    def _set_channel_settings(self, channel_id: int, settings: Dict):
        """チャンネルの設定を保存"""
        self.channel_settings[channel_id] = settings
        self._save_settings()

    # --- Helper Functions ---

    async def fetch_available_models(self) -> bool:
        """
        Style-Bert-VITS2サーバーから利用可能なモデル一覧を取得
        """
        try:
            async with self.session.get(f"{self.api_url}/models/info") as response:
                if response.status == 200:
                    data = await response.json()

                    # データ構造を確認してログ出力
                    print(f"[TTSCog Debug] API Response type: {type(data)}")

                    # レスポンスがリストの場合とオブジェクトの場合で分岐
                    if isinstance(data, list):
                        self.available_models = data
                    elif isinstance(data, dict):
                        # {"models": [...]} のような形式の場合
                        if "models" in data:
                            self.available_models = data["models"]
                        else:
                            # キーバリューのペアをリストに変換
                            self.available_models = [
                                {"id": k, "name": v if isinstance(v, str) else str(v)}
                                for k, v in data.items()
                            ]
                    else:
                        print(f"✗ [TTSCog] 予期しないデータ形式: {data}")
                        return False

                    self.models_loaded = True
                    print(f"✓ [TTSCog] {len(self.available_models)}個のモデルを検出")

                    # モデル情報を表示
                    for model in self.available_models:
                        if isinstance(model, dict):
                            model_id = model.get('id', 'unknown')
                            model_name = model.get('name', 'unknown')
                            print(f"  - Model ID {model_id}: {model_name}")
                        else:
                            print(f"  - Model: {model}")

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
            import traceback
            traceback.print_exc()
            return False

    def get_model_name(self, model_id: int) -> str:
        """モデルIDから名前を取得"""
        for model in self.available_models:
            if isinstance(model, dict):
                # 辞書形式の場合
                if model.get('id') == model_id or str(model.get('id')) == str(model_id):
                    return model.get('name', f"Model {model_id}")
            elif isinstance(model, str):
                # 文字列形式の場合（モデル名のみ）
                return model
        return f"Model {model_id}"

    # --- Event Listener ---

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState,
                                    after: discord.VoiceState):
        if not self.config.get('enable_join_leave_notice', True):
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
        model_id="モデルID (省略時はチャンネル設定)",
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

        # チャンネル設定を取得
        voice_channel_id = interaction.guild.voice_client.channel.id
        channel_settings = self._get_channel_settings(voice_channel_id)

        # パラメータのデフォルト値設定（チャンネル設定を優先）
        final_model_id = model_id if model_id is not None else channel_settings["model_id"]
        final_style = style if style is not None else channel_settings["style"]
        final_style_weight = style_weight if style_weight is not None else channel_settings["style_weight"]
        final_speed = speed if speed is not None else channel_settings["speed"]

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
            if isinstance(model, dict):
                model_id = model.get('id', 'N/A')
                model_name = model.get('name', 'Unknown')
                styles = model.get('styles', ['Neutral'])

                # 名前を256文字以内に制限
                display_name = f"ID: {model_id}"
                if len(str(model_name)) > 200:
                    display_name += f" - {str(model_name)[:200]}..."
                else:
                    display_name += f" - {model_name}"

                # スタイルの文字列化
                if isinstance(styles, list):
                    styles_str = ", ".join(str(s) for s in styles[:10])
                    if len(styles) > 10:
                        styles_str += f" ... (他{len(styles) - 10}個)"
                else:
                    styles_str = str(styles)

                # valueも1024文字制限があるので念のため制限
                if len(styles_str) > 1000:
                    styles_str = styles_str[:1000] + "..."

                embed.add_field(
                    name=display_name[:256],  # 256文字制限
                    value=f"スタイル: {styles_str}",
                    inline=False
                )
            else:
                # 文字列や単純な形式の場合
                model_str = str(model)[:240]  # 余裕を持って240文字
                embed.add_field(
                    name=f"Model: {model_str}",
                    value="詳細情報なし",
                    inline=False
                )

        if len(self.available_models) > 10:
            embed.set_footer(text=f"... 他 {len(self.available_models) - 10} 個のモデル")

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="change-tts-model", description="このチャンネルのTTSモデル設定を変更します")
    @app_commands.describe(
        model_id="使用するモデルID",
        style="スタイル名 (省略時は現在の設定を維持)",
        style_weight="スタイルの強さ (0.0-10.0, 省略時は現在の設定を維持)",
        speed="話速 (0.5-2.0, 省略時は現在の設定を維持)"
    )
    async def change_tts_model(
            self,
            interaction: discord.Interaction,
            model_id: int,
            style: Optional[str] = None,
            style_weight: Optional[float] = None,
            speed: Optional[float] = None
    ):
        """チャンネルごとのTTSモデル設定を変更"""
        # ボイスチャンネルに接続しているか確認
        if not interaction.guild.voice_client:
            await interaction.response.send_message(
                "❌ Botがボイスチャンネルに接続していません。",
                ephemeral=True
            )
            return

        voice_channel = interaction.guild.voice_client.channel
        channel_id = voice_channel.id

        # 現在の設定を取得
        current_settings = self._get_channel_settings(channel_id)

        # 新しい設定を作成（指定されたパラメータのみ更新）
        new_settings = {
            "model_id": model_id,
            "style": style if style is not None else current_settings["style"],
            "style_weight": style_weight if style_weight is not None else current_settings["style_weight"],
            "speed": speed if speed is not None else current_settings["speed"]
        }

        # 設定を保存
        self._set_channel_settings(channel_id, new_settings)

        # モデル名を取得
        model_name = self.get_model_name(model_id)

        # 確認メッセージを送信
        embed = discord.Embed(
            title="✅ TTS設定を更新しました",
            description=f"チャンネル: {voice_channel.mention}",
            color=discord.Color.green()
        )
        embed.add_field(name="モデル", value=f"ID: {model_id} - {model_name}", inline=False)
        embed.add_field(name="スタイル", value=new_settings["style"], inline=True)
        embed.add_field(name="スタイル強度", value=f"{new_settings['style_weight']}", inline=True)
        embed.add_field(name="速度", value=f"{new_settings['speed']}x", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="show-tts-settings", description="現在のチャンネルのTTS設定を表示します")
    async def show_tts_settings(self, interaction: discord.Interaction):
        """現在のチャンネルのTTS設定を表示"""
        if not interaction.guild.voice_client:
            await interaction.response.send_message(
                "❌ Botがボイスチャンネルに接続していません。",
                ephemeral=True
            )
            return

        voice_channel = interaction.guild.voice_client.channel
        channel_id = voice_channel.id

        # 現在の設定を取得
        settings = self._get_channel_settings(channel_id)
        model_name = self.get_model_name(settings["model_id"])

        # 設定が保存されているかチェック
        is_custom = channel_id in self.channel_settings

        embed = discord.Embed(
            title="🎙️ 現在のTTS設定",
            description=f"チャンネル: {voice_channel.mention}\n"
                        f"{'(カスタム設定)' if is_custom else '(デフォルト設定)'}",
            color=discord.Color.blue() if is_custom else discord.Color.greyple()
        )
        embed.add_field(name="モデル", value=f"ID: {settings['model_id']} - {model_name}", inline=False)
        embed.add_field(name="スタイル", value=settings["style"], inline=True)
        embed.add_field(name="スタイル強度", value=f"{settings['style_weight']}", inline=True)
        embed.add_field(name="速度", value=f"{settings['speed']}x", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reset-tts-settings", description="このチャンネルのTTS設定をデフォルトに戻します")
    async def reset_tts_settings(self, interaction: discord.Interaction):
        """チャンネルのTTS設定をリセット"""
        if not interaction.guild.voice_client:
            await interaction.response.send_message(
                "❌ Botがボイスチャンネルに接続していません。",
                ephemeral=True
            )
            return

        voice_channel = interaction.guild.voice_client.channel
        channel_id = voice_channel.id

        # 設定が存在する場合のみ削除
        if channel_id in self.channel_settings:
            del self.channel_settings[channel_id]
            self._save_settings()
            await interaction.response.send_message(
                f"✅ {voice_channel.mention} のTTS設定をデフォルトに戻しました。",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"ℹ️ {voice_channel.mention} はすでにデフォルト設定を使用しています。",
                ephemeral=True
            )

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

        # Style-Bert-VITS2 APIのエンドポイント (POSTメソッドを使用)
        endpoint = f"{self.api_url}/voice"

        payload = {
            "text": text,
            "model_id": model_id,
            "style": style,
            "style_weight": style_weight,
            "speed": speed,
            "encoding": "wav"  # WAV形式で取得
        }

        try:
            async with self.session.post(endpoint, params=payload) as response:
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
                print(f"[TTSCog] 音楽を再開します (guild {guild.id}) 位置: {position}秒")
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