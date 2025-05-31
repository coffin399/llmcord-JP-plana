import discord
from discord.ext import commands
from discord import app_commands  # スラッシュコマンド用にインポート
import yaml
import openai  # OpenAIライブラリを複数のプロバイダーで使用
import json
import logging
import asyncio
import io  # 画像をバイトとして扱うために追加
import base64  # 画像をBase64エンコードするために追加 (モデルやAPIによっては必要)

# search_agent.py から SearchAgent をインポート
try:
    from plugins.search_agent import SearchAgent
except ImportError:
    logging.error(
        "plugins/search_agent.py が見つからないか、SearchAgentクラスをインポートできません。検索機能は無効になります。")
    SearchAgent = None

logger = logging.getLogger(__name__)

# サポートする画像形式 (小文字)
SUPPORTED_IMAGE_EXTENSIONS = ('.png', '.jpeg', '.jpg', '.gif', '.webp')  # gif, webpはモデルによる


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

        if hasattr(self.bot, 'cfg'):
            logger.warning(
                "LLMCog: self.bot.cfg は既に存在します。上書きします。複数のCogがこの属性を使用している場合、問題が発生する可能性があります。")
        self.bot.cfg = self.llm_config  # SearchAgentが self.bot.cfg を参照するため (理想はSearchAgent側で self.bot.config['llm'] を参照)

        self.chat_histories = {}

        self.main_llm_client = None
        main_model_str = self.llm_config.get('model')
        if main_model_str:
            self.main_llm_client = self._initialize_llm_client(
                main_model_str,
                provider_config_section='providers'
            )
        if not self.main_llm_client:
            logger.error("メインLLMクライアントの初期化に失敗しました。主要機能が無効になります。")

        self.search_agent = None
        if 'search' in self.llm_config.get('active_tools', []) and SearchAgent:
            search_agent_settings = self.llm_config.get('search_agent', {})
            if not search_agent_settings.get('api_key') or not search_agent_settings.get('model'):
                logger.error(
                    "SearchAgentの設定 (llm.search_agent.api_key または llm.search_agent.model) が不足しています。検索機能は無効になります。")
            else:
                try:
                    self.search_agent = SearchAgent(self.bot)
                    logger.info("SearchAgentが正常に初期化されました。")
                except Exception as e:
                    logger.error(f"SearchAgentの初期化に失敗しました: {e}", exc_info=True)
                    self.search_agent = None
        elif not SearchAgent:
            logger.info("SearchAgentクラスがインポートできなかったため、検索機能は無効です。")
        else:
            logger.info("llm_configのactive_toolsに'search'が含まれていないため、SearchAgentは初期化されません。")

        if not self.llm_config.get('system_prompt'):
            logger.warning("system_prompt が llm_config にありません。LLMが期待通りに動作しない可能性があります。")

    def _initialize_llm_client(self, model_string: str, provider_config_section: str, api_key_override: str = None):
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
            actual_api_key = api_key_override if api_key_override is not None else api_key_from_provider
            is_local_provider = provider_name.lower() in ['ollama', 'oobabooga', 'jan', 'lmstudio']

            if not actual_api_key:
                if is_local_provider:
                    actual_api_key = "local-dummy-key"
                    logger.info(f"ローカルプロバイダー '{provider_name}' モデル '{model_name}' にダミーAPIキーを使用。")
                else:
                    logger.error(f"リモートプロバイダー '{provider_name}' モデル '{model_name}' のAPIキーがありません。")
                    return None

            if not base_url and provider_name.lower() != "openai":
                logger.warning(
                    f"プロバイダー '{provider_name}' のベースURLがありません。OpenAIデフォルトAPIベースを使用します（ライブラリが上書きしない場合）。")
            elif not base_url and provider_name.lower() == "openai":
                if not (actual_api_key and (actual_api_key.startswith("sk-") or actual_api_key.startswith("gsk_"))):
                    logger.warning(
                        f"OpenAIプロバイダーでAPIキーの形式が標準的でないため、ベースURLがないと問題が発生する可能性があります。")
            elif not base_url:
                logger.error(f"プロバイダー '{provider_name}' のベースURLがありません。クライアントを初期化できません。")
                return None

            client = openai.AsyncOpenAI(
                base_url=base_url,
                api_key=actual_api_key,
            )
            client.model_name_for_api_calls = model_name
            logger.info(
                f"プロバイダー '{provider_name}'、モデル '{model_name}' のLLMクライアント初期化完了 (Base URL: {base_url or 'ライブラリデフォルト'})")
            return client
        except Exception as e:
            logger.error(f"LLMクライアント '{model_string}' の初期化中にエラー: {e}", exc_info=True)
            return None

    def get_tools_definition(self):
        active_tool_names = self.llm_config.get('active_tools', [])
        if not active_tool_names: return None
        tools_definitions = []
        if 'search' in active_tool_names and self.search_agent and hasattr(self.search_agent, 'tool_spec'):
            tools_definitions.append(self.search_agent.tool_spec)
            logger.debug(
                f"SearchAgentのtool_specをツール定義に追加: {json.dumps(self.search_agent.tool_spec, indent=2)}")
        return tools_definitions if tools_definitions else None

    async def _process_attachments(self, message: discord.Message) -> list:
        image_inputs = []
        max_images = self.llm_config.get('max_images', 1)
        processed_image_count = 0
        for attachment in message.attachments:
            if processed_image_count >= max_images: break
            if attachment.filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS):
                image_inputs.append({"type": "image_url", "image_url": {"url": attachment.url}})
                processed_image_count += 1
        return image_inputs

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot: return
        if not self.bot.user.mentioned_in(message): return

        allowed_channel_ids = self.config.get('allowed_channel_ids', [])
        if allowed_channel_ids and message.channel.id not in allowed_channel_ids:
            logger.debug(f"メッセージ無視: 非許可ch {message.channel.id} (User: {message.author.id})")
            return
        allowed_role_ids = self.config.get('allowed_role_ids', [])
        if allowed_role_ids and isinstance(message.author, discord.Member):
            if not any(role.id in allowed_role_ids for role in message.author.roles):
                logger.debug(f"メッセージ無視: 非許可ロール (User: {message.author.id})")
                return

        log_message_parts = [
            f"ユーザー入力受信: User='{message.author.name}({message.author.id})'",
            f"Channel='{message.channel.name}({message.channel.id})'",
            f"Guild='{message.guild.name}({message.guild.id if message.guild else 'DM'})'"
        ]
        user_text_content_for_llm = message.content
        for mention_pattern in [f'<@!{self.bot.user.id}>', f'<@{self.bot.user.id}>']:
            user_text_content_for_llm = user_text_content_for_llm.replace(mention_pattern, '').strip()
        if user_text_content_for_llm: log_message_parts.append(f"テキスト: '{user_text_content_for_llm}'")
        if message.attachments:
            attachment_logs = []
            for att_idx, att in enumerate(message.attachments):
                is_supported_image = att.filename.lower().endswith(SUPPORTED_IMAGE_EXTENSIONS)
                attachment_logs.append(
                    f"  添付[{att_idx + 1}]: {att.filename} (Type: {att.content_type}, URL: {att.url}, SupportedImage: {is_supported_image})")
            if attachment_logs: log_message_parts.append("添付ファイル:\n" + "\n".join(attachment_logs))
        logger.info("\n".join(log_message_parts))

        history_key = message.channel.id
        if history_key not in self.chat_histories: self.chat_histories[history_key] = []
        image_contents_for_llm = await self._process_attachments(message)
        if not user_text_content_for_llm and not image_contents_for_llm:
            await message.channel.send(
                self.llm_config.get('error_msg', {}).get('empty_mention_reply', "はい、ご用件は何でしょうか？"));
            return
        max_text_len = self.llm_config.get('max_text', 100000)
        if len(user_text_content_for_llm) > max_text_len:
            await message.channel.send(self.llm_config.get('error_msg', {}).get('msg_max_text_size',
                                                                                "メッセージ長すぎ。最大 {max_text:,} 字。").format(
                max_text=max_text_len));
            return
        if not self.main_llm_client:
            await message.channel.send(
                self.llm_config.get('error_msg', {}).get('general_error', "LLM未設定。処理不可。"));
            return

        user_input_content_parts = []
        if user_text_content_for_llm: user_input_content_parts.append(
            {"type": "text", "text": user_text_content_for_llm})
        if image_contents_for_llm: user_input_content_parts.extend(image_contents_for_llm)
        user_message_for_api = {"role": "user",
                                "content": user_input_content_parts if image_contents_for_llm else user_text_content_for_llm}

        system_prompt_content = self.llm_config.get('system_prompt', "あなたはアシスタント。")
        messages_for_llm_api = [{"role": "system", "content": system_prompt_content}]
        current_channel_history = list(self.chat_histories[history_key]);
        current_channel_history.append(user_message_for_api)
        max_hist_pairs = self.llm_config.get('max_messages', 10);
        max_hist_entries = max_hist_pairs * 2
        if len(current_channel_history) > max_hist_entries:
            num_to_remove = len(current_channel_history) - max_hist_entries
            current_channel_history = current_channel_history[num_to_remove:]
        messages_for_llm_api.extend(current_channel_history)

        try:
            async with message.channel.typing():
                current_llm_call_messages_api_format = messages_for_llm_api;
                llm_reply_text_content = None
                for i in range(self.llm_config.get('max_tool_iterations', 3)):
                    logger.debug(
                        f"LLM呼び出し (反復 {i + 1}): messages = {json.dumps(current_llm_call_messages_api_format, indent=2, ensure_ascii=False, default=str)}")
                    tools_def = self.get_tools_definition();
                    tool_choice_val = "auto" if tools_def else None
                    extra_params = self.llm_config.get('extra_api_parameters', {})
                    response = await self.main_llm_client.chat.completions.create(
                        model=self.main_llm_client.model_name_for_api_calls,
                        messages=current_llm_call_messages_api_format,
                        tools=tools_def, tool_choice=tool_choice_val,
                        temperature=extra_params.get('temperature', 0.7),
                        max_tokens=extra_params.get('max_tokens', 4096)
                    )
                    response_message = response.choices[0].message
                    current_llm_call_messages_api_format.append(response_message.model_dump(exclude_none=True))
                    if response_message.tool_calls:
                        logger.info(f"LLMツール呼び出し要求: {response_message.tool_calls}")
                        if not self.search_agent:
                            logger.error("SearchAgent未初期化だがツール呼び出し要求あり。")
                            for tool_call in response_message.tool_calls: current_llm_call_messages_api_format.append(
                                {"tool_call_id": tool_call.id, "role": "tool", "name": tool_call.function.name,
                                 "content": f"エラー: ツール '{tool_call.function.name}' 利用不可。"})
                            continue
                        for tool_call in response_message.tool_calls:
                            function_name = tool_call.function.name;
                            function_args_str = tool_call.function.arguments;
                            tool_output_content = ""
                            if function_name == self.search_agent.name:
                                try:
                                    function_args = json.loads(
                                        function_args_str); tool_output_content = await self.search_agent.run(
                                        arguments=function_args, bot=self.bot)
                                except Exception as e_tool:
                                    tool_output_content = f"エラー: ツール '{function_name}' 実行エラー: {e_tool}"; logger.error(
                                        tool_output_content, exc_info=True)
                            else:
                                tool_output_content = f"エラー: 未対応ツール '{function_name}'"
                            current_llm_call_messages_api_format.append(
                                {"tool_call_id": tool_call.id, "role": "tool", "name": function_name,
                                 "content": str(tool_output_content)})
                        continue
                    else:
                        llm_reply_text_content = response_message.content; break
                if not llm_reply_text_content: llm_reply_text_content = self.llm_config.get('error_msg', {}).get(
                    'tool_loop_timeout', "ツール処理複雑すぎ。")

            if llm_reply_text_content:
                logger.info(
                    f"LLM最終応答 (User: {message.author.id}, Channel: {message.channel.id}): {llm_reply_text_content[:200]}...")
                self.chat_histories[history_key].append(user_message_for_api)
                self.chat_histories[history_key].append({"role": "assistant", "content": llm_reply_text_content})
                if len(self.chat_histories[history_key]) > max_hist_entries:
                    num_to_remove = len(self.chat_histories[history_key]) - max_hist_entries
                    self.chat_histories[history_key] = self.chat_histories[history_key][num_to_remove:]
                for chunk in self._split_message(llm_reply_text_content): await message.channel.send(chunk)
            else:
                logger.warning("LLMが空の最終応答。")
                await message.channel.send(self.llm_config.get('error_msg', {}).get('general_error', "AI空応答。"))
        except openai.APIConnectionError as e:
            logger.error(f"LLM API接続エラー: {e}"); await message.channel.send(
                self.llm_config.get('error_msg', {}).get('general_error', "AI接続不可。"))
        except openai.RateLimitError:
            logger.warning(f"LLM APIレート制限超過。"); await message.channel.send(
                self.llm_config.get('error_msg', {}).get('ratelimit_error', "AI混雑中。"))
        except openai.APIStatusError as e:
            response_text = e.response.text if e.response else 'N/A';
            logger.error(f"LLM APIステータスエラー: {e.status_code} - {response_text}");
            error_key = 'ratelimit_error' if e.status_code == 429 else 'general_error';
            error_template = self.llm_config.get('error_msg', {}).get(error_key, "APIエラー({status_code})。");
            detail_msg = ""
            try:
                if e.response and e.response.text: error_body = json.loads(e.response.text)
                if 'error' in error_body:
                    if isinstance(error_body['error'], dict) and 'message' in error_body['error']:
                        detail_msg = f" 詳細: {error_body['error']['message']}"
                    elif isinstance(error_body['error'], str):
                        detail_msg = f" 詳細: {error_body['error']}"
                elif 'message' in error_body:
                    detail_msg = f" 詳細: {error_body['message']}"
            except:
                pass
            await message.channel.send(error_template.format(status_code=e.status_code) + detail_msg)
        except Exception as e:
            logger.error(f"on_messageで予期しないエラー: {e}", exc_info=True); await message.channel.send(
                self.llm_config.get('error_msg', {}).get('general_error', "予期せぬエラー。"))

    def _split_message(self, text_content: str, max_length: int = 1990):
        if not text_content: return [""]
        lines = text_content.splitlines(keepends=True);
        chunks, current_chunk = [], ""
        for line in lines:
            if len(current_chunk) + len(line) > max_length:
                if current_chunk: chunks.append(current_chunk)
                current_chunk = line
                while len(current_chunk) > max_length: chunks.append(
                    current_chunk[:max_length]); current_chunk = current_chunk[max_length:]
            else:
                current_chunk += line
        if current_chunk: chunks.append(current_chunk)
        return chunks if chunks else [""]

    @app_commands.command(name="llm_help", description="LLM (AI対話) 機能に関する詳細なヘルプを表示します。")
    async def llm_help_slash(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        embed = discord.Embed(title="💡 LLM (AI対話) 機能 ヘルプ",
                              description=f"{self.bot.user.name if self.bot.user else '当Bot'} のAI対話機能についての説明です。",
                              color=discord.Color.purple())
        if self.bot.user and self.bot.user.avatar: embed.set_thumbnail(url=self.bot.user.avatar.url)

        embed.add_field(
            name="基本的な使い方",
            value=f"• Botにメンション (`@{self.bot.user.name if self.bot.user else 'Bot'}`) して話しかけると、AIが応答します。\n"
                  f"• メッセージと一緒に画像を添付すると、AIが画像の内容も理解しようとします（対応モデルの場合）。",
            inline=False
        )
        model_name = self.llm_config.get('model', '未設定');
        max_hist = self.llm_config.get('max_messages', '未設定')
        embed.add_field(
            name="現在のAI設定",
            value=f"• **使用モデル:** `{model_name}`\n"
                  f"• **会話履歴の最大保持数:** {max_hist} ペア\n"
                  f"• **最大入力文字数:** {self.llm_config.get('max_text', '未設定'):,} 文字\n"
                  f"• **一度に処理できる最大画像枚数:** {self.llm_config.get('max_images', '未設定')} 枚",
            inline=False
        )
        active_tools_list = self.llm_config.get('active_tools', []);
        tools_description = ""
        if 'search' in active_tools_list and self.search_agent:
            tools_description += f"• **ウェブ検索 (Search):** AIが必要と判断した場合、情報を検索して応答に利用します。\n"
            search_model = self.llm_config.get('search_agent', {}).get('model', '未設定')
            tools_description += f"  *検索エージェントモデル: `{search_model}`*\n"
        if not tools_description: tools_description = "現在、特別な追加機能（ツール）は有効になっていません。"
        embed.add_field(name="AIの追加機能 (ツール)", value=tools_description, inline=False)
        embed.add_field(
            name="注意点",
            value="• AIは常に正しい情報を提供するとは限りません。重要な情報は必ずご自身で確認してください。\n"
                  "• 会話はチャンネルごとに独立して記憶されます。\n"
                  "• あまりにも長い会話や複雑すぎる指示は、AIが混乱する原因になることがあります。\n"
                  "• 個人情報や機密性の高い情報は送信しないでください。",
            inline=False
        )
        embed.set_footer(text="現在開発中です。仕様が変更される可能性があります。")
        await interaction.followup.send(embed=embed, ephemeral=False)
        logger.info(f"/llm_help が実行されました。 (User: {interaction.user.id}, Guild: {interaction.guild_id})")


async def setup(bot: commands.Bot):
    if not hasattr(bot, 'config') or not bot.config:
        logger.error("LLMCog: Botインスタンスに 'config' 属性が見つからないか空です。Cogをロードできません。")
        raise commands.ExtensionFailed("LLMCog", "Botのconfigがロードされていません。")
    try:
        cog_instance = LLMCog(bot)
        await bot.add_cog(cog_instance)
        logger.info("LLMCogが正常にロードされました。")
    except commands.ExtensionFailed as e:
        logger.error(f"LLMCogの初期化中にエラー (ExtensionFailed): {e.name} - {e.original}", exc_info=e.original)
        raise
    except Exception as e:
        logger.error(f"LLMCogのセットアップ中に予期しないエラー: {e}", exc_info=True)
        raise