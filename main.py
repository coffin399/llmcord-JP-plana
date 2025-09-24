import discord
from discord.ext import commands, tasks
import yaml
import logging
import os
import shutil
from discord.gateway import DiscordWebSocket # モバイルステータス用にインポート
import sys                                  # モバイルステータス用にインポート

# --- ロギング設定の初期化 ---
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('google.generativeai').setLevel(logging.WARNING)
logging.getLogger('google.ai').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# --- カスタムDiscordロギングハンドラをインポート ---
from PLANA.utilities.discord_handler import DiscordLogHandler

COGS_DIRECTORY_NAME = "cogs"
CONFIG_FILE = 'config.yaml'
DEFAULT_CONFIG_FILE = 'config.default.yaml'


# --- モバイルステータス用のカスタムWebSocketクラス ---
class MobileWebSocket(DiscordWebSocket):
    """
    Botをモバイルステータスにするための、より強力なカスタムWebSocketクラス
    """
    async def send_as_json(self, data):
        if data.get('op') == self.IDENTIFY:
            data['d']['properties'] = {
                '$os': sys.platform,
                '$browser': 'Discord Android',
                '$device': 'Discord Android',
            }
        await super().send_as_json(data)


class Shittim(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = None
        self.status_templates = []
        self.status_index = 0

    async def setup_hook(self):
        """Botの初期セットアップ（ログイン後、接続準備完了前）"""
        # --- config.yaml の存在確認とコピー ---
        if not os.path.exists(CONFIG_FILE):
            if os.path.exists(DEFAULT_CONFIG_FILE):
                try:
                    shutil.copyfile(DEFAULT_CONFIG_FILE, CONFIG_FILE)
                    logging.info(f"{CONFIG_FILE} が見つからなかったため、{DEFAULT_CONFIG_FILE} をコピーして生成しました。")
                    logging.warning(f"生成された {CONFIG_FILE} を確認し、ボットトークンやAPIキーを設定してください。")
                except Exception as e_copy:
                    print(f"CRITICAL: {DEFAULT_CONFIG_FILE} から {CONFIG_FILE} のコピー中にエラーが発生しました: {e_copy}")
                    raise RuntimeError(f"{CONFIG_FILE} の生成に失敗しました。")
            else:
                print(f"CRITICAL: {CONFIG_FILE} も {DEFAULT_CONFIG_FILE} も見つかりません。設定ファイルがありません。")
                raise FileNotFoundError(f"{CONFIG_FILE} も {DEFAULT_CONFIG_FILE} も見つかりません。")

        # --- config.yaml を読み込む ---
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
                if not self.config:
                    print(f"CRITICAL: {CONFIG_FILE} が空または無効です。ボットを起動できません。")
                    raise RuntimeError(f"{CONFIG_FILE} が空または無効です。")
            logging.info(f"{CONFIG_FILE} を正常に読み込みました。")
        except Exception as e:
            print(f"CRITICAL: {CONFIG_FILE} の読み込みまたは解析中にエラーが発生しました: {e}")
            raise

        # ================================================================
        # ===== ロギング設定 =============================================
        # ================================================================
        log_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s')
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        root_logger.handlers = []

        # 1. コンソールへのハンドラ
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(log_format)
        root_logger.addHandler(console_handler)

        # 2. Discordへのカスタムハンドラ (複数チャンネル対応)
        log_channel_ids = self.config.get('log_channel_ids')
        if not log_channel_ids:
            single_channel_id = self.config.get('log_channel_id')
            if single_channel_id:
                log_channel_ids = [single_channel_id]
                logging.warning("設定 'log_channel_id' は非推奨です。今後は 'log_channel_ids' (リスト形式) を使用してください。")

        if log_channel_ids and isinstance(log_channel_ids, list):
            try:
                valid_ids = [int(cid) for cid in log_channel_ids if cid]
                if valid_ids:
                    discord_handler = DiscordLogHandler(bot=self, channel_ids=valid_ids)
                    discord_handler.setLevel(logging.INFO)
                    discord_handler.setFormatter(log_format)
                    root_logger.addHandler(discord_handler)
                    logging.info(f"DiscordへのロギングをチャンネルID {valid_ids} で有効化しました。")
                else:
                    logging.warning("log_channel_ids に有効なチャンネルIDが指定されていません。")
            except (ValueError, TypeError) as e:
                logging.error(f"config.yamlの log_channel_ids の値が不正です: {e}")
        else:
            logging.warning("config.yamlに log_channel_ids が設定されていないため、Discordへのロギングは無効です。")
        # ================================================================
        # ===== ロギング設定ここまで =====================================
        # ================================================================

        # --- Cogのロード ---
        if self.config.get('enabled_cogs') and isinstance(self.config['enabled_cogs'], list):
            for cog_name in self.config['enabled_cogs']:
                cog_module_path = f"{COGS_DIRECTORY_NAME}.{str(cog_name).strip()}"
                try:
                    await self.load_extension(cog_module_path)
                    logging.info(f"Cog '{cog_module_path}' のロードに成功しました。")
                except Exception as e:
                    logging.error(f"Cog '{cog_module_path}' のロード中にエラーが発生しました: {e}", exc_info=True)
        else:
            logging.warning("config.yamlに 'enabled_cogs' が設定されていないか、リスト形式ではありません。Cogはロードされません。")

        # --- スラッシュコマンドの同期 ---
        if self.config.get('sync_slash_commands', True):
            try:
                test_guild_id = self.config.get('test_guild_id')
                if test_guild_id:
                    guild_obj = discord.Object(id=int(test_guild_id))
                    synced_commands = await self.tree.sync(guild=guild_obj)
                    logging.info(f"{len(synced_commands)}個のスラッシュコマンドをテストギルド {test_guild_id} に同期しました。")
                else:
                    synced_commands = await self.tree.sync()
                    logging.info(f"{len(synced_commands)}個のグローバルスラッシュコマンドを同期しました。")
            except Exception as e:
                logging.error(f"スラッシュコマンドの同期中にエラーが発生しました: {e}", exc_info=True)
        else:
            logging.info("スラッシュコマンドの同期は設定で無効化されています。")

    @tasks.loop(seconds=10)
    async def rotate_status(self):
        """10秒ごとにボットのステータスをローテーションさせるタスク"""
        if not self.status_templates:
            return
        current_template = self.status_templates[self.status_index]
        status_text = current_template.format(
            guild_count=len(self.guilds),
            prefix=self.config.get('prefix', '!!')
        )
        activity_type_str = self.config.get('status_activity_type', 'streaming').lower()
        activity_type_map = {
            'playing': discord.ActivityType.playing,
            'streaming': discord.ActivityType.streaming,
            'listening': discord.ActivityType.listening,
            'watching': discord.ActivityType.watching,
            'competing': discord.ActivityType.competing,
        }
        selected_activity_type = activity_type_map.get(activity_type_str, discord.ActivityType.streaming)
        if selected_activity_type == discord.ActivityType.streaming:
            stream_url = self.config.get('status_stream_url', 'https://www.twitch.tv/coffinnoob299')
            activity = discord.Streaming(name=status_text, url=stream_url)
        else:
            activity = discord.Activity(type=selected_activity_type, name=status_text)
        try:
            await self.change_presence(activity=activity, status=discord.Status.online)
        except Exception as e:
            logging.error(f"ステータスの更新中にエラーが発生しました: {e}", exc_info=True)
        self.status_index = (self.status_index + 1) % len(self.status_templates)

    @rotate_status.before_loop
    async def before_rotate_status(self):
        await self.wait_until_ready()

    async def on_ready(self):
        if not self.user:
            logging.error("on_ready: self.user が None です。処理をスキップします。")
            return
        logging.info(f'{self.user.name} ({self.user.id}) としてDiscordにログインし、準備が完了しました！')
        logging.info(f"現在 {len(self.guilds)} サーバーに参加しています。")
        self.status_templates = self.config.get('status_rotation', [
            "plz type /help",
            "operating on {guild_count} servers",
            "operating on {guild_count} servers",
            "Version20250916",
            "SwitchModelsAvailable！",
            "type /switch-model"
        ])
        self.rotate_status.start()

    async def on_guild_join(self, guild: discord.Guild):
        logging.info(f"新しいサーバー '{guild.name}' (ID: {guild.id}) に参加しました。現在のサーバー数: {len(self.guilds)}")

    async def on_guild_remove(self, guild: discord.Guild):
        logging.info(f"サーバー '{guild.name}' (ID: {guild.id}) から退出しました。現在のサーバー数: {len(self.guilds)}")

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"引数が不足しています: `{error.param.name}`\n`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(f"引数の型が正しくありません。\n`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("このコマンドを実行する権限がありません。")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"このコマンドはクールダウン中です。あと {error.retry_after:.2f} 秒お待ちください。")
        elif isinstance(error, commands.ExtensionError):
            logging.error(f"Cog関連のエラーが発生しました ({ctx.command.cog_name if ctx.command else 'UnknownCog'}): {error}", exc_info=error)
            await ctx.send("コマンドの処理中にCogエラーが発生しました。管理者に報告してください。")
        else:
            logging.error(f"コマンド '{ctx.command.qualified_name if ctx.command else ctx.invoked_with}' の実行中に予期しないエラーが発生しました:", exc_info=error)
            try:
                await ctx.send("コマンドの実行中に予期しないエラーが発生しました。")
            except discord.errors.Forbidden:
                logging.warning(f"エラーメッセージを送信できませんでした ({ctx.channel.id}): 権限不足")

# (ASCIIアート消しちゃえ)

if __name__ == "__main__":
    plana_art = r"""
██████╗ ██╗      █████╗ ███╗   ██╗ █████╗ 
██╔══██╗██║     ██╔══██╗████╗  ██║██╔══██╗
██████╔╝██║     ███████║██╔██╗ ██║███████║
██╔═══╝ ██║     ██╔══██║██║╚██╗██║██╔══██║
██║     ███████╗██║  ██║██║ ╚████║██║  ██║
╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝
    """
    print(plana_art)

    initial_config = {}
    try:
        if not os.path.exists(CONFIG_FILE) and os.path.exists(DEFAULT_CONFIG_FILE):
            try:
                shutil.copyfile(DEFAULT_CONFIG_FILE, CONFIG_FILE)
                print(f"INFO: メイン実行: {CONFIG_FILE} が見つからず、{DEFAULT_CONFIG_FILE} からコピー生成しました。")
            except Exception as e_copy_main:
                print(f"CRITICAL: メイン実行: {DEFAULT_CONFIG_FILE} から {CONFIG_FILE} のコピー中にエラー: {e_copy_main}")
                exit(1)
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f_main_init:
            initial_config = yaml.safe_load(f_main_init)
            if not initial_config or not isinstance(initial_config, dict):
                print(f"CRITICAL: メイン実行: {CONFIG_FILE} が空または無効な形式です。")
                exit(1)
    except Exception as e_main:
        print(f"CRITICAL: メイン実行: {CONFIG_FILE} の読み込みまたは解析中にエラー: {e_main}。")
        exit(1)

    bot_token_val = initial_config.get('bot_token')
    if not bot_token_val or bot_token_val == "YOUR_BOT_TOKEN_HERE":
        print(f"CRITICAL: {CONFIG_FILE}にbot_tokenが未設定か無効、またはプレースホルダのままです。")
        exit(1)

    intents = discord.Intents.default()
    intents.guilds = True
    intents.voice_states = True
    intents.message_content = False
    allowed_mentions = discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=True)

    bot_instance = Shittim(
        command_prefix=commands.when_mentioned,
        intents=intents,
        help_command=None,
        allowed_mentions=allowed_mentions,
        ws_options={'gateway': MobileWebSocket}
    )

    try:
        bot_instance.run(bot_token_val)
    except Exception as e:
        logging.critical(f"ボットの実行中に致命的なエラーが発生しました: {e}", exc_info=True)
        print(f"CRITICAL: ボットの実行中に致命的なエラーが発生しました: {e}")