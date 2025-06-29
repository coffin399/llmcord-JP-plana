import discord
from discord.ext import commands
import yaml
import logging
import asyncio
import os
import shutil  # shutilモジュールをインポート

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s')
logging.getLogger('discord').setLevel(logging.WARNING)
logging.getLogger('openai').setLevel(logging.WARNING)
logging.getLogger('google.generativeai').setLevel(logging.WARNING)
logging.getLogger('google.ai').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

COGS_DIRECTORY_NAME = "cogs"
CONFIG_FILE = 'config.yaml'
DEFAULT_CONFIG_FILE = 'config.default.yaml'


class Shittim(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = None
        # self.loop.set_debug(True)

    async def setup_hook(self):
        """Botの初期セットアップ（ログイン後、接続準備完了前）"""
        if not os.path.exists(CONFIG_FILE):
            if os.path.exists(DEFAULT_CONFIG_FILE):
                try:
                    shutil.copyfile(DEFAULT_CONFIG_FILE, CONFIG_FILE)
                    logging.info(
                        f"{CONFIG_FILE} が見つからなかったため、{DEFAULT_CONFIG_FILE} をコピーして生成しました。")
                    logging.warning(f"生成された {CONFIG_FILE} を確認し、ボットトークンやAPIキーを設定してください。")
                except Exception as e_copy:
                    logging.critical(
                        f"{DEFAULT_CONFIG_FILE} から {CONFIG_FILE} のコピー中にエラーが発生しました: {e_copy}",
                        exc_info=True)
                    raise RuntimeError(f"{CONFIG_FILE} の生成に失敗しました。")
            else:
                logging.critical(f"{CONFIG_FILE} も {DEFAULT_CONFIG_FILE} も見つかりません。設定ファイルがありません。")
                raise FileNotFoundError(f"{CONFIG_FILE} も {DEFAULT_CONFIG_FILE} も見つかりません。")

        # 2. config.yaml を読み込む
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
                if not self.config:
                    logging.critical(f"{CONFIG_FILE} が空または無効です。ボットを起動できません。")
                    raise RuntimeError(f"{CONFIG_FILE} が空または無効です。")
            logging.info(f"{CONFIG_FILE} を正常に読み込みました。")
            logging.debug(f"Loaded config keys: {list(self.config.keys())}")
            if 'llm' in self.config:
                logging.debug(f"LLM config keys: {list(self.config['llm'].keys())}")

        except FileNotFoundError:
            logging.critical(f"{CONFIG_FILE} が見つかりません。ボットを起動できません。")
            raise FileNotFoundError(f"{CONFIG_FILE} が見つかりません。")
        except yaml.YAMLError as e:
            logging.critical(f"{CONFIG_FILE} の解析エラー: {e}。ボットを起動できません。")
            raise yaml.YAMLError(f"{CONFIG_FILE} の解析エラー: {e}")
        except Exception as e:
            logging.critical(f"{CONFIG_FILE} の読み込み中に予期せぬエラー: {e}", exc_info=True)
            raise RuntimeError(f"{CONFIG_FILE} の読み込み中に予期せぬエラー: {e}")

        if self.config and 'enabled_cogs' in self.config and isinstance(self.config['enabled_cogs'], list):
            for cog_name in self.config['enabled_cogs']:
                if not isinstance(cog_name, str):
                    logging.warning(
                        f"enabled_cogs 内に不正なCog名が含まれています: {cog_name} (型: {type(cog_name)})。スキップします。")
                    continue

                cog_module_path = f"{COGS_DIRECTORY_NAME}.{cog_name.strip()}"
                try:
                    await self.load_extension(cog_module_path)
                    logging.info(f"Cog '{cog_module_path}' のロードに成功しました。")
                except commands.ExtensionNotFound:
                    logging.error(
                        f"Cog '{cog_module_path}' が見つかりません。ファイルが存在し、正しい名前か確認してください。")
                except commands.ExtensionAlreadyLoaded:
                    logging.warning(f"Cog '{cog_module_path}' は既にロードされています。")
                except commands.NoEntryPointError:
                    logging.error(f"Cog '{cog_module_path}' に setup 関数が見つかりません。")
                except commands.ExtensionFailed as e:
                    logging.error(
                        f"Cog '{cog_module_path}' のセットアップ中にエラーが発生しました: {e.name} - {e.original}",
                        exc_info=e.original)
                except Exception as e:
                    logging.error(f"Cog '{cog_module_path}' のロード中に予期しないエラーが発生しました: {e}",
                                  exc_info=True)
        else:
            logging.warning(
                "config.yamlに 'enabled_cogs' が設定されていないか、リスト形式ではありません。Cogはロードされません。")

        if self.config.get('sync_slash_commands', True):
            try:
                test_guild_id = self.config.get('test_guild_id')
                if test_guild_id:
                    guild_obj = discord.Object(id=int(test_guild_id))
                    synced_commands = await self.tree.sync(guild=guild_obj)
                    logging.info(
                        f"{len(synced_commands)}個のスラッシュコマンドをテストギルド {test_guild_id} に同期しました。")
                else:
                    synced_commands = await self.tree.sync()
                    logging.info(f"{len(synced_commands)}個のグローバルスラッシュコマンドを同期しました。")
            except discord.errors.Forbidden as e:
                logging.error(f"スラッシュコマンドの同期に必要な権限がありません: {e}")
            except Exception as e:
                logging.error(f"スラッシュコマンドの同期中にエラーが発生しました: {e}", exc_info=True)
        else:
            logging.info("スラッシュコマンドの同期は設定で無効化されています。")

    async def update_status(self):
        """ボットのステータスを現在の状態で更新する"""
        if not self.is_ready() or not self.config:
            return

        status_template = self.config.get('status_message', "operating on {guild_count} servers")
        bot_prefix_for_status = self.config.get('prefix', '!!')
        status_text = status_template.format(
            prefix=bot_prefix_for_status,
            guild_count=len(self.guilds)
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
            stream_url = self.config.get('status_stream_url', 'https://www.twitch.tv/discord')
            activity = discord.Streaming(name=status_text, url=stream_url)
        else:
            activity = discord.Activity(type=selected_activity_type, name=status_text)

        try:
            await self.change_presence(activity=activity, status=discord.Status.online)
            logging.info(f"ボットのステータスを「{activity.type.name}: {status_text}」に更新しました。")
        except Exception as e:
            logging.error(f"ステータスの更新中にエラーが発生しました: {e}", exc_info=True)

    async def on_ready(self):
        if not self.user:
            logging.error("on_ready: self.user が None です。処理をスキップします。")
            return

        logging.info(f'{self.user.name} ({self.user.id}) としてDiscordにログインし、準備が完了しました！')
        logging.info(f"現在 {len(self.guilds)} サーバーに参加しています。")

        await self.update_status()

    async def on_guild_join(self, guild: discord.Guild):
        """ボットがサーバーに参加したときに呼ばれる"""
        logging.info(f"新しいサーバー '{guild.name}' (ID: {guild.id}) に参加しました。")
        await self.update_status()

    async def on_guild_remove(self, guild: discord.Guild):
        """ボットがサーバーから退出したときに呼ばれる"""
        logging.info(f"サーバー '{guild.name}' (ID: {guild.id}) から退出しました。")
        await self.update_status()

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"引数が不足しています: `{error.param.name}`\n`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(
                f"引数の型が正しくありません。\n`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.CheckFailure):
            await ctx.send("このコマンドを実行する権限がありません。")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"このコマンドはクールダウン中です。あと {error.retry_after:.2f} 秒お待ちください。")
        elif isinstance(error, commands.ExtensionError):
            logging.error(
                f"Cog関連のエラーが発生しました ({ctx.command.cog_name if ctx.command else 'UnknownCog'}): {error}",
                exc_info=error)
            await ctx.send("コマンドの処理中にCogエラーが発生しました。管理者に報告してください。")
        else:
            logging.error(
                f"コマンド '{ctx.command.qualified_name if ctx.command else ctx.invoked_with}' の実行中に予期しないエラーが発生しました:",
                exc_info=error)
            try:
                await ctx.send("コマンドの実行中に予期しないエラーが発生しました。しばらくしてから再試行してください。")
            except discord.errors.Forbidden:
                logging.warning(f"エラーメッセージを送信できませんでした ({ctx.channel.id}): 権限不足")


if __name__ == "__main__":
    initial_config = {}
    try:
        # config.yaml がなくても、デフォルトからコピーされることを期待してまず試行
        if not os.path.exists(CONFIG_FILE) and os.path.exists(DEFAULT_CONFIG_FILE):
            try:
                shutil.copyfile(DEFAULT_CONFIG_FILE, CONFIG_FILE)
                logging.info(f"メイン実行: {CONFIG_FILE} が見つからず、{DEFAULT_CONFIG_FILE} からコピー生成しました。")
            except Exception as e_copy_main:
                logging.critical(
                    f"メイン実行: {DEFAULT_CONFIG_FILE} から {CONFIG_FILE} のコピー中にエラー: {e_copy_main}",
                    exc_info=True)
                exit(1)

        with open(CONFIG_FILE, 'r', encoding='utf-8') as f_main_init:
            initial_config = yaml.safe_load(f_main_init)
            if not initial_config or not isinstance(initial_config, dict):
                logging.critical(f"メイン実行: {CONFIG_FILE} が空または無効な形式です。")
                exit(1)
    except FileNotFoundError:
        logging.critical(
            f"メイン実行: {CONFIG_FILE} が見つかりません。{DEFAULT_CONFIG_FILE} も存在しないかコピーに失敗しました。")
        exit(1)
    except yaml.YAMLError as e_main_yaml:
        logging.critical(f"メイン実行: {CONFIG_FILE} の解析エラー: {e_main_yaml}。")
        exit(1)
    except Exception as e_main_generic:
        logging.critical(f"メイン実行: {CONFIG_FILE} の読み込み中に予期せぬエラー: {e_main_generic}。")
        exit(1)

    bot_prefix_val = initial_config.get('prefix')
    if not bot_prefix_val or not isinstance(bot_prefix_val, str):
        logging.warning(f"{CONFIG_FILE}の 'prefix' が無効。デフォルト値 '!!' を使用。")
        bot_prefix_val = '!!'

    bot_token_val = initial_config.get('bot_token')
    if not bot_token_val or not isinstance(bot_token_val,
                                           str) or bot_token_val == "YOUR_BOT_TOKEN_HERE":
        logging.critical(f"{CONFIG_FILE}にbot_tokenが未設定か無効、またはプレースホルダのままです。")
        if os.path.exists(DEFAULT_CONFIG_FILE) and not os.path.exists(CONFIG_FILE):
            logging.info(f"{CONFIG_FILE} は {DEFAULT_CONFIG_FILE} からコピーされました。トークンを設定してください。")
        exit(1)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    intents.guilds = True  # サーバー参加/退出イベントを受け取るために必要

    allowed_mentions = discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=True)
    bot_instance = Shittim(
        command_prefix=commands.when_mentioned_or(bot_prefix_val),
        intents=intents,
        help_command=None,
        allowed_mentions=allowed_mentions
    )

    try:
        bot_instance.run(bot_token_val)
    except RuntimeError as e_run:
        logging.critical(f"ボットの起動に失敗しました (RuntimeError): {e_run}")
    except discord.errors.LoginFailure:
        logging.critical("Discordへのログインに失敗しました。トークンが正しいか確認してください。")
    except discord.errors.PrivilegedIntentsRequired as e_priv_intent:
        logging.critical(
            f"必要な特権インテントが無効です: {e_priv_intent}。Discord Developer Portalで有効化してください。")
    except Exception as e_run_generic:
        logging.critical(f"ボット実行中に予期せぬエラーが発生し終了しました: {e_run_generic}", exc_info=True)