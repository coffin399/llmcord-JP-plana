import discord
from discord.ext import commands
import yaml
import logging
import asyncio  # setup_hook で async 関数を使う場合
import os  # Cogのパス指定用 (今回は固定文字列を使用)

# --- ロギング設定 ---
# ログレベルは INFO 以上に設定 (デバッグ時は DEBUG に変更)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s] %(message)s')
# discord.py の内部ログを抑制したい場合は WARNING などに設定
logging.getLogger('discord').setLevel(logging.WARNING)
# openaiライブラリのINFOログも多いため、必要に応じてWARNINGに設定
logging.getLogger('openai').setLevel(logging.WARNING)
# google.generativeai のログも同様
logging.getLogger('google.generativeai').setLevel(logging.WARNING)
logging.getLogger('google.ai').setLevel(logging.WARNING)  # google.api_core.retryなど
logging.getLogger('httpx').setLevel(logging.WARNING)  # openaiが内部で使用

# Cogが配置されているディレクトリ名 (プロジェクトルートからの相対パス)
COGS_DIRECTORY_NAME = "cogs"


class MyBot(commands.Bot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.config = None  # Botインスタンスにconfigを持たせる
        # self.loop.set_debug(True) # asyncioのデバッグモード (開発時)

    async def setup_hook(self):
        """Botの初期セットアップ（ログイン後、接続準備完了前）"""
        # 1. config.yaml を読み込む
        try:
            with open('config.yaml', 'r', encoding='utf-8') as f:
                self.config = yaml.safe_load(f)
                if not self.config:
                    logging.critical("config.yaml が空または無効です。ボットを起動できません。")
                    raise RuntimeError("config.yaml が空または無効です。")
            logging.info("config.yaml を正常に読み込みました。")
            # デバッグ用に読み込んだconfigの内容（一部）を表示
            logging.debug(f"Loaded config keys: {list(self.config.keys())}")
            if 'llm' in self.config:
                logging.debug(f"LLM config keys: {list(self.config['llm'].keys())}")

        except FileNotFoundError:
            logging.critical("config.yaml が見つかりません。ボットを起動できません。")
            raise FileNotFoundError("config.yaml が見つかりません。")
        except yaml.YAMLError as e:
            logging.critical(f"config.yaml の解析エラー: {e}。ボットを起動できません。")
            raise yaml.YAMLError(f"config.yaml の解析エラー: {e}")
        except Exception as e:
            logging.critical(f"config.yaml の読み込み中に予期せぬエラー: {e}", exc_info=True)
            raise RuntimeError(f"config.yaml の読み込み中に予期せぬエラー: {e}")

        # 2. Cogのロード (config.yaml の enabled_cogs に基づいて)
        if self.config and 'enabled_cogs' in self.config and isinstance(self.config['enabled_cogs'], list):
            for cog_name in self.config['enabled_cogs']:
                if not isinstance(cog_name, str):
                    logging.warning(
                        f"enabled_cogs 内に不正なCog名が含まれています: {cog_name} (型: {type(cog_name)})。スキップします。")
                    continue

                cog_module_path = f"{COGS_DIRECTORY_NAME}.{cog_name.strip()}"  # 前後の空白を除去
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

    async def on_ready(self):
        """Botが完全に準備完了したときに呼び出される"""
        if not self.user:  # まれにuserがNoneのことがある
            logging.error("on_ready: self.user が None です。処理をスキップします。")
            return

        logging.info(f'{self.user.name} ({self.user.id}) としてDiscordにログインし、準備が完了しました！')
        logging.info(f"現在 {len(self.guilds)} サーバーに参加しています。")

        if not self.config:
            logging.error("on_ready: Botのconfigがロードされていません。ステータスを設定できません。")
            return

        # Botのステータスメッセージ設定
        status_template = self.config.get('status_message', "オンライン | {prefix}help")  # configから取得
        bot_prefix_for_status = self.config.get('prefix', '!!')  # configからプレフィックス取得

        # status_message 内のプレースホルダを置換
        status_text = status_template.format(
            prefix=bot_prefix_for_status,
            guild_count=len(self.guilds)  # 参加サーバー数を動的に取得
        )

        activity_type_str = self.config.get('status_activity_type', 'listening').lower()
        activity_type_map = {
            'playing': discord.ActivityType.playing,
            'streaming': discord.ActivityType.streaming,
            'listening': discord.ActivityType.listening,
            'watching': discord.ActivityType.watching,
            'competing': discord.ActivityType.competing,
        }
        selected_activity_type = activity_type_map.get(activity_type_str, discord.ActivityType.listening)

        activity = discord.Activity(type=selected_activity_type, name=status_text)

        # ストリーミングの場合のURL (オプション)
        if selected_activity_type == discord.ActivityType.streaming:
            stream_url = self.config.get('status_stream_url', 'https://www.twitch.tv/discord')  # 例
            activity = discord.Streaming(name=status_text, url=stream_url)

        try:
            await self.change_presence(activity=activity, status=discord.Status.online)
            logging.info(f"ボットのステータスを「{activity.type.name}: {status_text}」に設定しました。")
        except Exception as e:
            logging.error(f"ステータスの設定中にエラーが発生しました: {e}", exc_info=True)

    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        """コマンド処理中にエラーが発生した際のグローバルエラーハンドラ"""
        if isinstance(error, commands.CommandNotFound):
            # logger.debug(f"コマンドが見つかりません: {ctx.invoked_with}") # 頻繁なのでデバッグレベル
            return  # 不明なコマンドは無視するか、ユーザーに通知
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"引数が不足しています: `{error.param.name}`\n`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.BadArgument):
            await ctx.send(
                f"引数の型が正しくありません。\n`{ctx.prefix}{ctx.command.qualified_name} {ctx.command.signature}`")
        elif isinstance(error, commands.CheckFailure):  # has_permissions などのチェック失敗
            await ctx.send("このコマンドを実行する権限がありません。")
        elif isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"このコマンドはクールダウン中です。あと {error.retry_after:.2f} 秒お待ちください。")
        elif isinstance(error, commands.ExtensionError):  # Cog関連のエラー
            logger.error(
                f"Cog関連のエラーが発生しました ({ctx.command.cog_name if ctx.command else 'UnknownCog'}): {error}",
                exc_info=error)
            await ctx.send("コマンドの処理中にCogエラーが発生しました。管理者に報告してください。")
        else:
            # その他の予期しないエラー
            logger.error(
                f"コマンド '{ctx.command.qualified_name if ctx.command else ctx.invoked_with}' の実行中に予期しないエラーが発生しました:",
                exc_info=error)
            try:
                await ctx.send("コマンドの実行中に予期しないエラーが発生しました。しばらくしてから再試行してください。")
            except discord.errors.Forbidden:  # メッセージ送信権限がない場合
                logger.warning(f"エラーメッセージを送信できませんでした ({ctx.channel.id}): 権限不足")


if __name__ == "__main__":
    # --- 起動前の準備 ---
    temp_config = {}
    try:
        with open('config.yaml', 'r', encoding='utf-8') as f_main:
            temp_config = yaml.safe_load(f_main)
            if not temp_config or not isinstance(temp_config, dict):
                logging.critical("メイン実行: config.yaml が空または無効な形式です。ボットを起動できません。")
                exit(1)
    except FileNotFoundError:
        logging.critical("メイン実行: config.yaml が見つかりません。ボットを起動できません。")
        exit(1)
    except yaml.YAMLError as e_main:
        logging.critical(f"メイン実行: config.yaml の解析エラー: {e_main}。ボットを起動できません。")
        exit(1)
    except Exception as e_main_generic:
        logging.critical(f"メイン実行: config.yaml の読み込み中に予期せぬエラー: {e_main_generic}。")
        exit(1)

    # Botのプレフィックスとトークンを取得
    bot_prefix_val = temp_config.get('prefix')  # getの第2引数でデフォルト値を指定できる
    if not bot_prefix_val or not isinstance(bot_prefix_val, str):
        logging.warning(f"config.yamlの 'prefix' が無効です。デフォルト値 '!!' を使用します。")
        bot_prefix_val = '!!'

    bot_token_val = temp_config.get('bot_token')
    if not bot_token_val or not isinstance(bot_token_val, str):
        logging.critical("config.yamlにbot_tokenが設定されていないか、無効な形式です。ボットを起動できません。")
        exit(1)

    # --- Intentsの設定 ---
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    # intents.members = True # 必要に応じて
    # intents.presences = True # 必要に応じて

    # --- Botインスタンスの作成 ---
    # command_prefix には callable, str, list[str], tuple[str] などが指定可能
    allowed_mentions = discord.AllowedMentions(everyone=False, users=True, roles=False, replied_user=True)
    bot_instance = MyBot(
        command_prefix=commands.when_mentioned_or(bot_prefix_val),
        intents=intents,
        help_command=None,  # デフォルトのヘルプコマンドを無効化 (Cogで独自に実装するため)
        allowed_mentions=allowed_mentions  # 全体メンションを抑制
    )

    # --- Botの実行 ---
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