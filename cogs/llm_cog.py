import discord
from discord.ext import commands
import yaml
import openai  # OpenAIライブラリを複数のプロバイダーで使用
import json
import logging
import asyncio

# search_agent.py から SearchAgent をインポート
try:
    from plugins.search_agent import SearchAgent
except ImportError:
    logging.error(
        "plugins/search_agent.py が見つからないか、SearchAgentクラスをインポートできません。検索機能は無効になります。")
    SearchAgent = None

logger = logging.getLogger(__name__)


class LLMCog(commands.Cog, name="LLM"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        logger.debug(f"LLMCog __init__: self.bot.config のタイプ: {type(self.bot.config)}")
        if isinstance(self.bot.config, dict):
            logger.debug(f"LLMCog __init__: self.bot.config のキー: {list(self.bot.config.keys())}")

        if not hasattr(self.bot, 'config') or not self.bot.config:
            logger.error("LLMCog: Botインスタンスに 'config' 属性が見つからないか空です。設定を読み込めません。")
            raise commands.ExtensionFailed(self.qualified_name, "Botのconfigがロードされていません。")

        self.config = self.bot.config  # Bot全体のconfig

        if 'llm' not in self.config:
            logger.error("config.yamlに 'llm' セクションが見つかりません。LLM Cogを開始できません。")
            raise commands.ExtensionFailed(self.qualified_name, "'llm' 設定セクションがありません。")

        self.llm_config = self.config['llm']  # LLM固有の設定

        if not isinstance(self.llm_config, dict):
            logger.error(
                f"config.yamlの 'llm' セクションが辞書ではありません (タイプ: {type(self.llm_config)})。LLM Cogを開始できません。")
            raise commands.ExtensionFailed(self.qualified_name, "'llm' 設定セクションが辞書形式ではありません。")

        # SearchAgent が self.bot.cfg を期待しているので、llm_config をそこに設定
        # 注意: この方法は、他のCogが self.bot.cfg を上書きする可能性があるため、理想的ではありません。
        # SearchAgent側で self.bot.config['llm'] を参照するように変更するのが望ましいです。
        # 今回はSearchAgentのコードを変更しない前提で、llm_configをcfgに設定します。
        if hasattr(self.bot, 'cfg'):
            logger.warning(
                "LLMCog: self.bot.cfg は既に存在します。上書きします。複数のCogがこの属性を使用している場合、問題が発生する可能性があります。")
        self.bot.cfg = self.llm_config  # SearchAgent用

        self.chat_histories = {}  # チャンネルごとの会話履歴

        # メインLLMクライアントの初期化
        self.main_llm_client = None
        main_model_str = self.llm_config.get('model')
        if main_model_str:
            self.main_llm_client = self._initialize_llm_client(
                main_model_str,
                provider_config_section='providers'  # llm_config内の 'providers' を参照
            )
        if not self.main_llm_client:
            logger.error("メインLLMクライアントの初期化に失敗しました。主要機能が無効になります。")
            # 主要機能が無効になる場合、Cogのロードを中止することも検討
            # raise commands.ExtensionFailed(self.qualified_name, "メインLLMクライアントの初期化失敗。")

        # SearchAgentの初期化
        self.search_agent = None
        # llm_config の active_tools と SearchAgentクラスの存在を確認
        if 'search' in self.llm_config.get('active_tools', []) and SearchAgent:
            # SearchAgentが必要とする設定 (api_key, model) がllm_config.search_agentにあるか確認
            search_agent_settings = self.llm_config.get('search_agent', {})
            if not search_agent_settings.get('api_key') or not search_agent_settings.get('model'):
                logger.error(
                    "SearchAgentの設定 (llm.search_agent.api_key または llm.search_agent.model) が不足しています。検索機能は無効になります。")
            else:
                try:
                    self.search_agent = SearchAgent(self.bot)  # botインスタンスを渡す (SearchAgentはbot.cfgを参照)
                    logger.info("SearchAgentが正常に初期化されました。")
                except Exception as e:
                    logger.error(f"SearchAgentの初期化に失敗しました: {e}", exc_info=True)
                    self.search_agent = None  # 初期化失敗時はNoneに
        elif not SearchAgent:
            logger.info("SearchAgentクラスがインポートできなかったため、検索機能は無効です。")
        else:
            logger.info("llm_configのactive_toolsに'search'が含まれていないため、SearchAgentは初期化されません。")

        if not self.llm_config.get('system_prompt'):
            logger.warning("system_prompt が llm_config にありません。LLMが期待通りに動作しない可能性があります。")

    def _initialize_llm_client(self, model_string: str, provider_config_section: str, api_key_override: str = None):
        """LLMクライアントを初期化するヘルパーメソッド"""
        try:
            if '/' not in model_string:
                logger.error(
                    f"無効なモデル形式: '{model_string}'。'プロバイダー名/モデル名' の形式である必要があります。")
                return None
            provider_name, model_name = model_string.split('/', 1)

            providers_settings = self.llm_config.get(provider_config_section, {})
            provider_specific_config = providers_settings.get(provider_name)

            if not provider_specific_config:
                logger.error(
                    f"LLMプロバイダー '{provider_name}' の設定が llm_config.{provider_config_section} に見つかりません。")
                return None

            base_url = provider_specific_config.get('base_url')
            api_key_from_provider = provider_specific_config.get('api_key')

            # APIキーの優先順位: 引数 > プロバイダー設定
            actual_api_key = api_key_override if api_key_override is not None else api_key_from_provider

            is_local_provider = provider_name.lower() in ['ollama', 'oobabooga', 'jan', 'lmstudio']

            if not actual_api_key:
                if is_local_provider:
                    actual_api_key = "local-dummy-key"
                    logger.info(f"ローカルプロバイダー '{provider_name}' モデル '{model_name}' にダミーAPIキーを使用。")
                else:
                    logger.error(f"リモートプロバイダー '{provider_name}' モデル '{model_name}' のAPIキーがありません。")
                    return None

            # base_urlの検証（OpenAI互換APIでAPIキー形式がsk-やgsk_で始まる場合はbase_urlがなくても許容する）
            if not base_url and provider_name.lower() != "openai":
                # OpenAI以外でbase_urlがない場合は警告（ライブラリデフォルトに依存）
                logger.warning(
                    f"プロバイダー '{provider_name}' のベースURLがありません。OpenAIデフォルトAPIベースを使用します（ライブラリが上書きしない場合）。")
            elif not base_url and provider_name.lower() == "openai":
                # OpenAIの場合、キーが特定の形式ならbase_urlは任意
                if not (actual_api_key and (actual_api_key.startswith("sk-") or actual_api_key.startswith("gsk_"))):
                    logger.warning(
                        f"OpenAIプロバイダーでAPIキーの形式が標準的でないため、ベースURLがないと問題が発生する可能性があります。")
            elif not base_url:  # 上記以外でbase_urlがないのはエラー
                logger.error(f"プロバイダー '{provider_name}' のベースURLがありません。クライアントを初期化できません。")
                return None

            client = openai.AsyncOpenAI(
                base_url=base_url,  # Noneの場合、openaiライブラリのデフォルトが使われる
                api_key=actual_api_key,
            )
            client.model_name_for_api_calls = model_name  # APIコールに使うモデル名
            logger.info(
                f"プロバイダー '{provider_name}'、モデル '{model_name}' のLLMクライアント初期化完了 (Base URL: {base_url or 'ライブラリデフォルト'})")
            return client
        except Exception as e:
            logger.error(f"LLMクライアント '{model_string}' の初期化中にエラー: {e}", exc_info=True)
            return None

    def get_tools_definition(self):
        """アクティブなツールに基づいてツール定義リストを返す"""
        active_tool_names = self.llm_config.get('active_tools', [])
        if not active_tool_names:
            return None

        tools_definitions = []
        if 'search' in active_tool_names and self.search_agent and hasattr(self.search_agent, 'tool_spec'):
            tools_definitions.append(self.search_agent.tool_spec)
            logger.debug(
                f"SearchAgentのtool_specをツール定義に追加: {json.dumps(self.search_agent.tool_spec, indent=2)}")

        return tools_definitions if tools_definitions else None

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        if not self.bot.user.mentioned_in(message): return

        # --- アクセス制御 (Bot全体のconfigから) ---
        allowed_channel_ids = self.config.get('allowed_channel_ids', [])
        if allowed_channel_ids and message.channel.id not in allowed_channel_ids:
            logger.debug(f"メッセージ無視: 許可されていないチャンネル {message.channel.id} (User: {message.author.id})")
            return

        allowed_role_ids = self.config.get('allowed_role_ids', [])
        if allowed_role_ids and isinstance(message.author, discord.Member):
            member_role_ids = {role.id for role in message.author.roles}
            if not any(role_id in member_role_ids for role_id in allowed_role_ids):
                logger.debug(f"メッセージ無視: 許可されていないロール (User: {message.author.id})")
                return

        # --- メッセージ処理 ---
        history_key = message.channel.id
        if history_key not in self.chat_histories:
            self.chat_histories[history_key] = []
            # スタータープロンプトはsystem_promptに含まれているか、別途処理する想定

        user_message_content = message.content
        for mention_pattern in [f'<@!{self.bot.user.id}>', f'<@{self.bot.user.id}>']:
            user_message_content = user_message_content.replace(mention_pattern, '').strip()

        if not user_message_content:
            reply_text = self.llm_config.get('error_msg', {}).get('empty_mention_reply', "はい、ご用件は何でしょうか？")
            await message.channel.send(reply_text)
            return

        max_text_len = self.llm_config.get('max_text', 100000)
        if len(user_message_content) > max_text_len:
            error_template = self.llm_config.get('error_msg', {}).get('msg_max_text_size',
                                                                      "メッセージ長すぎ。最大 {max_text:,} 字。")
            await message.channel.send(error_template.format(max_text=max_text_len))
            return

        if not self.main_llm_client:
            error_msg = self.llm_config.get('error_msg', {}).get('general_error', "LLM未設定。処理不可。")
            await message.channel.send(error_msg)
            return

        # --- LLMへの入力準備 ---
        system_prompt_content = self.llm_config.get('system_prompt', "あなたはアシスタント。")
        messages_for_llm = [{"role": "system", "content": system_prompt_content}]

        current_channel_history = list(self.chat_histories[history_key])
        current_channel_history.append({"role": "user", "content": user_message_content})

        max_hist_messages = self.llm_config.get('max_messages', 10) * 2  # user/assistantペアなので実質2倍
        if len(current_channel_history) > max_hist_messages:
            num_to_remove = len(current_channel_history) - max_hist_messages
            current_channel_history = current_channel_history[num_to_remove:]

        messages_for_llm.extend(current_channel_history)

        # --- LLM呼び出しとツール処理 ---
        try:
            async with message.channel.typing():
                current_llm_call_messages = messages_for_llm  # 最初のLLM呼び出し用
                llm_reply_content = None

                for i in range(self.llm_config.get('max_tool_iterations', 3)):  # ツール呼び出しの最大反復回数
                    logger.debug(
                        f"LLM呼び出し (反復 {i + 1}): messages = {json.dumps(current_llm_call_messages, indent=2, ensure_ascii=False)}")

                    tools_def = self.get_tools_definition()
                    tool_choice_val = "auto" if tools_def else None

                    extra_params = self.llm_config.get('extra_api_parameters', {})

                    response = await self.main_llm_client.chat.completions.create(
                        model=self.main_llm_client.model_name_for_api_calls,
                        messages=current_llm_call_messages,
                        tools=tools_def,
                        tool_choice=tool_choice_val,
                        temperature=extra_params.get('temperature', 0.7),
                        max_tokens=extra_params.get('max_tokens', 4096)
                    )
                    response_message = response.choices[0].message

                    # LLMの応答 (ツール呼び出し含む) を現在の呼び出しメッセージリストに追加
                    current_llm_call_messages.append(response_message.model_dump(exclude_none=True))

                    if response_message.tool_calls:
                        logger.info(f"LLMがツール呼び出しを要求: {response_message.tool_calls}")
                        if not self.search_agent:  # SearchAgentが利用不可
                            logger.error("SearchAgent未初期化だがツール呼び出し要求あり。")
                            # ツールが使えないことを示すメッセージをLLMに返す
                            for tool_call in response_message.tool_calls:
                                current_llm_call_messages.append({
                                    "tool_call_id": tool_call.id, "role": "tool",
                                    "name": tool_call.function.name,
                                    "content": f"エラー: ツール '{tool_call.function.name}' は現在利用できません。",
                                })
                            continue  # 次の反復でLLMに再考させる

                        # SearchAgentが利用可能な場合
                        for tool_call in response_message.tool_calls:
                            function_name = tool_call.function.name
                            function_args_str = tool_call.function.arguments
                            tool_output_content = ""

                            if function_name == self.search_agent.name:
                                try:
                                    function_args = json.loads(function_args_str)
                                    logger.info(f"SearchAgent実行: 引数 {function_args}")
                                    # SearchAgentのrunにbotインスタンスを渡す
                                    tool_output_content = await self.search_agent.run(arguments=function_args,
                                                                                      bot=self.bot)
                                except json.JSONDecodeError:
                                    err_msg = f"ツール '{function_name}' の引数デコード失敗: {function_args_str}"
                                    logger.error(err_msg)
                                    tool_output_content = f"エラー: {err_msg}"
                                except Exception as e_tool:
                                    err_msg = f"ツール '{function_name}' 実行エラー: {e_tool}"
                                    logger.error(err_msg, exc_info=True)
                                    tool_output_content = f"エラー: {err_msg}"
                            else:
                                err_msg = f"未対応のツール呼び出し: {function_name}"
                                logger.warning(err_msg)
                                tool_output_content = f"エラー: {err_msg}"

                            current_llm_call_messages.append({
                                "tool_call_id": tool_call.id, "role": "tool",
                                "name": function_name, "content": str(tool_output_content),
                            })
                        # ツール結果を渡して次の反復へ
                        continue
                    else:  # ツール呼び出しなし => LLMの最終応答
                        llm_reply_content = response_message.content
                        break  # ループ終了

                # ループが最大反復回数に達した場合 (ツール呼び出しが続いた場合)
                if not llm_reply_content:
                    logger.warning(
                        f"LLMが最大反復回数 ({self.llm_config.get('max_tool_iterations', 3)}) 後もテキスト応答を返さず。")
                    # フォールバック応答
                    llm_reply_content = self.llm_config.get('error_msg', {}).get('tool_loop_timeout',
                                                                                 "ツールの処理が複雑すぎたため、応答をまとめられませんでした。")

            # --- 応答送信と履歴保存 ---
            if llm_reply_content:
                logger.info(f"LLM最終応答 (User: {message.author.id}): {llm_reply_content[:200]}...")

                # 実際の会話履歴には、元のユーザーメッセージと最終的なLLMのテキスト応答のみを保存
                self.chat_histories[history_key].append({"role": "user", "content": user_message_content})
                self.chat_histories[history_key].append({"role": "assistant", "content": llm_reply_content})

                # 履歴が最大数を超えたら古いものから削除 (user/assistantペアを維持)
                if len(self.chat_histories[history_key]) > max_hist_messages:
                    num_entries_to_remove = len(self.chat_histories[history_key]) - max_hist_messages
                    self.chat_histories[history_key] = self.chat_histories[history_key][num_entries_to_remove:]

                for chunk in self._split_message(llm_reply_content):
                    await message.channel.send(chunk)
            else:
                logger.warning("LLMが空の最終応答を返しました。")
                await message.channel.send(self.llm_config.get('error_msg', {}).get('general_error', "AI空応答。"))

        # --- エラーハンドリング ---
        except openai.APIConnectionError as e:
            logger.error(f"LLM API接続エラー: {e}")
            await message.channel.send(self.llm_config.get('error_msg', {}).get('general_error', "AI接続不可。"))
        except openai.RateLimitError:
            logger.warning(f"LLM APIレート制限超過。")
            await message.channel.send(self.llm_config.get('error_msg', {}).get('ratelimit_error', "AI混雑中。"))
        except openai.APIStatusError as e:
            logger.error(f"LLM APIステータスエラー: {e.status_code} - {e.response.text if e.response else 'N/A'}")
            error_key = 'ratelimit_error' if e.status_code == 429 else 'general_error'
            error_template = self.llm_config.get('error_msg', {}).get(error_key, "APIエラー({status_code})。")

            detail_msg = ""
            try:
                if e.response and e.response.text:
                    error_body = json.loads(e.response.text)
                    if 'error' in error_body and isinstance(error_body['error'], dict) and 'message' in error_body[
                        'error']:
                        detail_msg = f" 詳細: {error_body['error']['message']}"
                    elif 'error' in error_body and isinstance(error_body['error'], str):
                        detail_msg = f" 詳細: {error_body['error']}"
                    elif 'message' in error_body:
                        detail_msg = f" 詳細: {error_body['message']}"

            except json.JSONDecodeError:
                detail_msg = f" (未解析エラーレスポンス: {e.response.text[:100]}...)" if e.response else ""
            except Exception:  # 他のパースエラー
                pass

            await message.channel.send(error_template.format(status_code=e.status_code) + detail_msg)
        except Exception as e:
            logger.error(f"on_messageで予期しないエラー: {e}", exc_info=True)
            await message.channel.send(self.llm_config.get('error_msg', {}).get('general_error', "予期せぬエラー。"))

    def _split_message(self, text_content: str, max_length: int = 1990):
        """メッセージをDiscordの最大文字数に合わせて分割する"""
        if not text_content: return [""]

        lines = text_content.splitlines(keepends=True)
        chunks = []
        current_chunk = ""
        for line in lines:
            if len(current_chunk) + len(line) > max_length:
                if current_chunk: chunks.append(current_chunk)
                current_chunk = line
                while len(current_chunk) > max_length:  # 1行が長すぎる場合
                    chunks.append(current_chunk[:max_length])
                    current_chunk = current_chunk[max_length:]
            else:
                current_chunk += line
        if current_chunk: chunks.append(current_chunk)
        return chunks if chunks else [""]

    @commands.command(name="planahelp", help="PLANAボットのヘルプ情報を表示します。")
    async def plana_help_command(self, ctx: commands.Context):
        help_msg_from_config = self.llm_config.get('help_message', "ヘルプ未設定。")
        embed = discord.Embed(title="PLANA ボットヘルプ (LLM)", description=help_msg_from_config,
                              color=discord.Color.blue())
        admin_ids_str = ", ".join(map(str, self.config.get('admin_user_ids', [])))  # Bot全体のadmin ID

        embed.add_field(name="LLM設定",
                        value=f"モデル: {self.llm_config.get('model', 'N/A')}\n"
                              f"最大履歴: {self.llm_config.get('max_messages', 'N/A')}ペア",
                        inline=False)
        if admin_ids_str:
            embed.set_footer(text=f"管理者ID: {admin_ids_str}")

        try:
            await ctx.send(embed=embed)
        except discord.errors.Forbidden:
            await ctx.send(help_msg_from_config)


async def setup(bot: commands.Bot):
    try:
        cog_instance = LLMCog(bot)
        await bot.add_cog(cog_instance)
        logger.info("LLMCogが正常にロードされました。")
    except commands.ExtensionFailed as e:
        logger.error(f"LLMCogの初期化に失敗 (ExtensionFailed): {e.name} - {e.original}", exc_info=e.original)
        raise
    except Exception as e:
        logger.error(f"LLMCogのセットアップ中に予期しないエラー: {e}", exc_info=True)
        raise