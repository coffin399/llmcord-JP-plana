from __future__ import annotations

import asyncio
import logging
import re
from base64 import b64encode
from dataclasses import dataclass, field
from datetime import datetime as dt
from typing import Literal, Optional, Set, Tuple, List, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
# httpxはbotインスタンス経由で使うので直接インポートは不要な場合があるが、型ヒント用に残すのもあり
# import httpx
import json
# openai から RateLimitError をインポート
from openai import AsyncOpenAI, RateLimitError

# from google import genai # genaiの利用箇所が見当たらないためコメントアウト

# main.py から DiscordLLMBot をインポート (型チェック用)
if TYPE_CHECKING:
    from main import DiscordLLMBot, MAX_MESSAGE_NODES, load_config

# --- Cog固有の定数 ---
VISION_MODEL_TAGS: Tuple[str, ...] = (
    "gpt-4o",
    "claude-3",
    "gemini",
    "pixtral",
    "llava",
    "vision",
)
PROVIDERS_SUPPORTING_USERNAMES: Tuple[str, ...] = ("openai", "x-ai")
ALLOWED_FILE_TYPES: Tuple[str, ...] = ("image", "text")
INVITE_URL = "https://discord.com/api/oauth2/authorize?client_id=1031673203774464160&permissions=412317273088&scope=bot"  # この値はconfigから取る方が良いかも
SUPPORT_SERVER_INVITE_LINK = "https://discord.gg/SjuWKtwNAG"
ARONA_REPOSITORY = "https://github.com/coffin399/music-bot-arona"
PLANA_REPOSITORY = "https://github.com/coffin399/llmcord-JP-plana"
ALLOWED_CHANNEL_TYPES: Tuple[discord.ChannelType, ...] = (
    discord.ChannelType.text,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.private,
)
EMBED_COLOR_COMPLETE = discord.Color.dark_green()
EMBED_COLOR_INCOMPLETE = discord.Color.orange()
STREAMING_INDICATOR = "<:stream:1313474295372058758>"  # Discordサーバーで利用可能なカスタム絵文字ID
EDIT_DELAY_SECONDS = 1
MENTION_PATTERN = re.compile(r"<@!?(\d+)>")


# MAX_MESSAGE_NODES は main.py から参照する


@dataclass
class MessageNode:
    """メッセージチェーンにおける1つの頂点を表現します。"""
    text: Optional[str] = None
    images: List[dict] = field(default_factory=list)
    role: Literal["user", "assistant"] = "assistant"
    user_id: Optional[int] = None
    next_message: Optional[discord.Message] = None
    has_bad_attachments: bool = False
    fetch_next_failed: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)


def should_respond(client_user: discord.User, message: discord.Message) -> bool:
    """高速パス – このメッセージに応答すべきかどうかを判断します。"""
    if message.channel.type not in ALLOWED_CHANNEL_TYPES:
        return False
    if message.channel.type != discord.ChannelType.private and client_user not in message.mentions:
        return False
    if message.author.bot:
        return False
    return True


class LLMCog(commands.Cog):
    def __init__(self, bot: DiscordLLMBot):
        self.bot: DiscordLLMBot = bot

    def _is_authorised(self, message: discord.Message) -> bool:
        """投稿者またはチャンネルが対話することを許可されているか確認します。"""
        allowed_channels = set(self.bot.cfg.get("allowed_channel_ids", []))
        allowed_roles = set(self.bot.cfg.get("allowed_role_ids", []))

        chan_id = message.channel.id
        parent_id = getattr(message.channel, "parent_id", None)

        if allowed_channels and chan_id not in allowed_channels and parent_id not in allowed_channels:
            logging.info(
                f"ユーザー {message.author.id} からのチャンネル {chan_id} でのメッセージはブロックされました: チャンネルが許可されていません。")
            return False

        if allowed_roles:
            if hasattr(message.author, 'roles'):
                user_role_ids = {role.id for role in message.author.roles}
                if not user_role_ids & allowed_roles:
                    logging.info(
                        f"ユーザー {message.author.id} からのチャンネル {chan_id} でのメッセージはブロックされました: ユーザーが必要なロールを持っていません。")
                    return False
            elif allowed_roles:  # DMでロールが設定されている場合
                logging.info(
                    f"ユーザー {message.author.id} からの DM チャンネルでのメッセージはブロックされました: ロールが必要ですが DM では利用できません。")
                return False
        return True

    async def _build_message_chain(
            self,
            new_msg: discord.Message,
            max_messages: int,
            max_text: int,
            max_images: int,
            accept_images: bool,
            accept_usernames: bool,
    ) -> tuple[list[dict], Set[str]]:
        messages: list[dict] = []
        user_warnings: set[str] = set()
        curr_msg: Optional[discord.Message] = new_msg
        visited_messages: Set[int] = set()

        while curr_msg and len(messages) < max_messages:
            if curr_msg.id in visited_messages:
                logging.warning(f"メッセージチェーンのメッセージ ID {curr_msg.id} でループを検出しました。停止します。")
                user_warnings.add("⚠️ 会話履歴にループを検出しました。ここで停止します。")
                break
            visited_messages.add(curr_msg.id)

            node = self.bot.message_nodes.setdefault(curr_msg.id, MessageNode())
            async with node.lock:
                if node.text is None or (accept_images and not node.images):  # 画像を受け付ける場合、画像もチェック
                    await self._process_message_node(node, curr_msg, accept_images, max_text)

                content = self._compose_message_content(node, max_text, max_images)
                if content:
                    if isinstance(content, str) and not content.strip():
                        pass
                    else:
                        payload: dict = {"content": content, "role": node.role}
                        if accept_usernames and node.user_id:
                            payload["name"] = str(node.user_id)
                        messages.append(payload)
                else:
                    logging.debug(f"メッセージ ID {curr_msg.id} は空のコンテンツとして処理されました。")

                self._update_user_warnings(node, max_text, max_images, user_warnings)

                if node.fetch_next_failed:
                    user_warnings.add(
                        f"⚠️ 会話チェーンの前のメッセージの取得に失敗しました。会話が不完全な可能性があります。"
                    )
                    break

                if len(messages) >= max_messages:  # >= に変更して、正確にmax_messagesで止める
                    # user_warnings.add(f"⚠️ 直近の {len(messages)} 件のメッセージのみを使用しています。") # 最後のメッセージ処理後に警告追加
                    break

                await self._set_next_message(node, curr_msg)
                curr_msg = node.next_message

        if curr_msg and len(messages) >= max_messages:  # ループ終了後、メッセージ制限で止まった場合の警告
            user_warnings.add(f"⚠️ 直近の {max_messages} 件のメッセージのみを使用しています。")

        return messages[::-1], user_warnings

    async def _process_message_node(
            self,
            node: MessageNode,
            msg: discord.Message,
            accept_images: bool,
            max_text: int,
    ) -> None:
        raw_content = msg.content or ""
        replaced_content = await self._replace_mentions(raw_content)

        if msg.author != self.bot.user:
            display_name = msg.author.display_name
            message_content = f"{display_name}: {replaced_content}" if replaced_content.strip() else display_name
        else:
            message_content = replaced_content

        good_atts: dict[str, list[discord.Attachment]] = {
            ft: [att for att in msg.attachments if att.content_type and ft in att.content_type]
            for ft in ALLOWED_FILE_TYPES
        }
        attachment_texts = []
        for att in good_atts.get("text", []):  # .getでキーが存在しない場合に対応
            try:
                text = await self._fetch_attachment_text(att)
                attachment_texts.append(text)
            except Exception as e:
                logging.warning(f"テキスト添付ファイル {att.id} のフェッチに失敗しました: {e}")
                node.has_bad_attachments = True

        embed_desc = [embed.description for embed in msg.embeds if embed.description]
        all_texts = [message_content] + embed_desc + attachment_texts
        node.text = "\n".join(filter(None, all_texts)).strip()

        if node.text.startswith(self.bot.user.mention):
            node.text = node.text.replace(self.bot.user.mention, "", 1).lstrip()
        elif node.text.startswith(f"<@{self.bot.user.id}>"):  # メンションの別形式も考慮
            node.text = node.text.replace(f"<@{self.bot.user.id}>", "", 1).lstrip()

        if accept_images:
            node.images = []
            for att in good_atts.get("image", []):  # .getでキーが存在しない場合に対応
                try:
                    img_data = await self._process_image(att)
                    node.images.append(img_data)
                except Exception as e:
                    logging.warning(f"画像添付ファイル {att.id} の処理に失敗しました: {e}")
                    node.has_bad_attachments = True
        else:
            node.images = []

        node.role = "assistant" if msg.author == self.bot.user else "user"
        node.user_id = msg.author.id if node.role == "user" else None
        if len(msg.attachments) > sum(len(good_atts.get(ft, [])) for ft in ALLOWED_FILE_TYPES):
            node.has_bad_attachments = True

    async def _fetch_attachment_text(self, att: discord.Attachment) -> str:
        response = await self.bot.httpx_client.get(att.url, follow_redirects=True)
        response.raise_for_status()
        return response.text

    async def _process_image(self, att: discord.Attachment) -> dict:
        response = await self.bot.httpx_client.get(att.url, follow_redirects=True)
        response.raise_for_status()
        b64 = b64encode(response.content).decode()
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{att.content_type};base64,{b64}"},
        }

    async def _replace_mentions(self, content: str) -> str:
        user_ids = {int(m.group(1)) for m in MENTION_PATTERN.finditer(content)}
        users: dict[int, str] = {}
        for uid in user_ids:
            try:
                user = self.bot.get_user(uid) or await self.bot.fetch_user(uid)
                users[uid] = user.display_name if user else f"User{uid}"
            except discord.NotFound:
                logging.warning(f"メンション置換中にユーザー ID {uid} が見つかりませんでした。")
                users[uid] = f"不明なユーザー{uid}"
            except Exception as e:
                logging.error(f"メンション置換用のユーザー {uid} のフェッチ中にエラー: {e}")
                users[uid] = f"エラーユーザー{uid}"
        return MENTION_PATTERN.sub(lambda m: users.get(int(m.group(1)), m.group(0)), content)

    async def _set_next_message(self, node: MessageNode, msg: discord.Message) -> None:
        next_msg: Optional[discord.Message] = None
        try:
            if msg.reference and msg.reference.message_id:
                try:
                    next_msg = msg.reference.cached_message or await msg.channel.fetch_message(msg.reference.message_id)
                except (discord.NotFound, discord.HTTPException):
                    logging.debug(f"参照されたメッセージ {msg.reference.message_id} のフェッチに失敗しました (参照)。")
                    node.fetch_next_failed = True

            if next_msg is None and not node.fetch_next_failed and (
                    self.bot.user.mention in msg.content or msg.channel.type == discord.ChannelType.private):
                history_msgs = [m async for m in msg.channel.history(before=msg, limit=1)]
                if history_msgs:
                    prev_msg = history_msgs[0]
                    if prev_msg.type in {discord.MessageType.default, discord.MessageType.reply} and (
                            prev_msg.author == self.bot.user or (
                            msg.channel.type == discord.ChannelType.private and prev_msg.author == msg.author)
                    ):
                        next_msg = prev_msg

            if next_msg is None and not node.fetch_next_failed and isinstance(msg.channel,
                                                                              discord.Thread):  # Thread check
                # スレッドのスターターメッセージは thread.starter_message (プロパティではなくメソッド呼び出しで取得するものではない)
                # starter_message は fetch が必要かもしれないし、キャッシュされているかもしれない
                # スレッドの最初のメッセージかどうかを判断するより信頼性の高い方法は、
                # メッセージIDがスレッドのIDと一致するかどうか (スレッドは最初のメッセージから作成されるため)
                # ただし、starter_message属性のほうが直接的
                try:
                    # スレッドオブジェクトからスターターメッセージを試みる
                    # スレッドが fetch_message 経由で取得された場合、starter_message は None になることがある
                    # この場合、スレッドの最初のメッセージを取得するために履歴を使うか、IDで直接フェッチする必要がある
                    starter_msg_id = msg.channel.id  # スレッドIDは通常最初のメッセージID
                    if msg.id == starter_msg_id and msg.channel.parent_id:  # このメッセージがスレッドを開始したメッセージの場合
                        parent_channel = self.bot.get_channel(msg.channel.parent_id) or await self.bot.fetch_channel(
                            msg.channel.parent_id)
                        if isinstance(parent_channel, (
                        discord.TextChannel, discord.ForumChannel, discord.VoiceChannel)):  # fetch_message を持つチャンネル
                            # スレッドを開始した元のメッセージ (親チャンネルにある) を取得しようとする
                            # これは常に成功するとは限らない (例: スレッドがメッセージなしで作成された場合)
                            # より堅牢なのは、スレッドが何らかのメッセージに「アタッチ」されている場合、そのメッセージを指すことです。
                            # しかし、DiscordのUI上でのスレッド作成方法に依存します。
                            # ここでは、スレッドの「開始点」のメッセージを指すことを試みます。
                            # スレッドの `starter_message` はスレッド内の最初のメッセージを指す。
                            # 親チャンネルの「スレッドを作成したメッセージ」は異なる。
                            # 現状のコードは starter_message の親を指そうとしているように見えるが、それは Discord のモデルとは異なる。
                            # ここでは、スレッド内の最初のメッセージの「返信先」がもしあればそれを辿る、という挙動は既にmsg.referenceでカバーされている。
                            # スレッドの親メッセージ(スレッドを作成したメッセージ)を取得するのは難しい。
                            # `msg.channel.starter_message` を使う方が意図に近いかもしれない。
                            # ただし、`starter_message` は `None` の場合があり、また `fetch_starter_message()` が必要。
                            pass  # このロジックは複雑で、元の意図が「スレッドを開始した親チャンネルのメッセージ」を指すなら大幅な見直しが必要
                            # 現状では、参照(reply)を優先し、次に履歴、という流れは維持する。
                            # スレッドの特殊なチェーン構造は、Discordのデータモデルを深く理解する必要がある。
                            # 一旦、この部分は元のコードの構造から大きく変更せず、コメントで注意喚起。
                        else:
                            logging.debug(
                                f"スレッド {msg.channel.id} の親チャンネル {msg.channel.parent_id} はメッセージ取得可能なタイプではありません。")

                except (discord.NotFound, discord.HTTPException) as e:
                    logging.debug(f"スレッド関連のメッセージ取得に失敗: {e}")
                    node.fetch_next_failed = True
        except Exception as e:
            logging.exception(f"メッセージチェーンの次のメッセージ設定中に予期しないエラー (ID: {msg.id})")
            node.fetch_next_failed = True
            next_msg = None

        node.next_message = next_msg
        if node.next_message:
            logging.debug(f"メッセージ ID {msg.id} は前のメッセージ ID {node.next_message.id} にリンクされました。")
        else:
            logging.debug(f"メッセージ ID {msg.id} はチェーンの終端です。")

    def _compose_message_content(
            self, node: MessageNode, max_text: int, max_images: int
    ) -> str | list:
        limited_text = node.text[:max_text] if node.text is not None else ""
        limited_images = node.images[:max_images] if node.images is not None else []

        content: list = []
        if limited_text.strip():
            content.append({"type": "text", "text": limited_text})
        if limited_images:
            content.extend(limited_images)

        if len(content) == 1 and content[0]["type"] == "text":
            return content[0]["text"]
        if not content:
            return ""
        return content

    def _update_user_warnings(
            self, node: MessageNode, max_text: int, max_images: int, warnings: set[str]
    ) -> None:
        err = self.bot.ERROR_MESSAGES
        if node.text is not None and len(node.text) > max_text:
            warnings.add(
                err.get("msg_max_text_size", "⚠️ メッセージテキストが切り詰められました (>{max_text} 文字)。").format(
                    max_text=max_text))
        if node.images is not None and len(node.images) > max_images:
            if max_images > 0:
                warnings.add(
                    err.get("msg_max_image_size", "⚠️ 最初の {max_images} 件の画像のみを使用しています。").format(
                        max_images=max_images))
            else:
                warnings.add(err.get("msg_error_image", "⚠️ このモデルまたは設定では画像はサポートされていません。"))
        if node.has_bad_attachments:
            warnings.add(err.get("msg_error_attachment",
                                 "⚠️ サポートされていない添付ファイルをスキップ、または処理に失敗しました。"))
        # node.fetch_next_failed の警告は _build_message_chain で直接追加されているため、ここでは不要

    async def _perform_edit(self, msg: discord.Message, content: str) -> None:
        """エラー処理付きでメッセージ編集を安全に実行します。"""
        try:
            if content != msg.content:  # コンテンツが実際に変更された場合のみ編集
                await msg.edit(content=content)
        except discord.NotFound:
            logging.warning(f"おそらく削除されたメッセージ {msg.id} を編集しようとしました。")
        except discord.HTTPException as e:
            logging.warning(f"メッセージ {msg.id} の編集中に HTTPException: {e}")
        except Exception as e:
            logging.error(f"メッセージ {msg.id} の編集中に予期しないエラー: {e}")

    async def _generate_and_send_response(
            self,
            messages: list[dict],
            origin: discord.Message,
            user_warnings: set[str],
            openai_client: AsyncOpenAI,  # このクライアントは呼び出し側で生成される
            model: str,
            max_message_length: int,
    ) -> None:
        response_msgs: list[discord.Message] = []
        last_message_buffer = ""
        edit_task: Optional[asyncio.Task] = None
        self.bot.last_task_time = dt.now().timestamp()  # botインスタンスのlast_task_timeを使用

        initial_warnings_text = " ".join(sorted(user_warnings))
        user_warnings.clear()  # clear after use

        api_kwargs_base = dict(
            model=model,
            stream=True,
            tools=self.bot._enabled_tools(),  # botインスタンスのメソッドを使用
            tool_choice="auto",
            extra_body=self.bot.cfg.get("extra_api_parameters", {}),
        )

        max_tool_loops = 3  # configから取れるようにしても良い
        current_loop = 0  # ループカウンタ

        while current_loop < max_tool_loops:
            current_loop += 1
            api_kwargs = dict(api_kwargs_base, messages=messages)
            tool_call_data_for_assistant: dict[str, dict[str, str | list[str]]] = {}
            assistant_text_content_buffer = ""
            saw_tool_call = False
            llm_response_generated_content = False  # LLMが実際に何かコンテンツを生成したか

            try:
                async with origin.channel.typing():
                    async for chunk in await openai_client.chat.completions.create(**api_kwargs):
                        choice = chunk.choices[0]
                        llm_response_generated_content = True  # チャンクがあれば何かしら生成されている

                        tc_delta_list = getattr(choice.delta, "tool_calls", None)
                        if tc_delta_list:
                            saw_tool_call = True
                            for tc_delta in tc_delta_list:
                                if tc_delta.id not in tool_call_data_for_assistant:
                                    tool_call_data_for_assistant[tc_delta.id] = {
                                        "name": tc_delta.function.name or "",
                                        "arguments_chunks": []
                                    }
                                if tc_delta.function.name and not tool_call_data_for_assistant[tc_delta.id]["name"]:
                                    tool_call_data_for_assistant[tc_delta.id]["name"] = tc_delta.function.name
                                if tc_delta.function.arguments:
                                    tool_call_data_for_assistant[tc_delta.id]["arguments_chunks"].append(
                                        tc_delta.function.arguments)
                            continue  # tool_callチャンクの場合はテキスト処理をスキップ

                        delta_content = choice.delta.content
                        if delta_content is not None:
                            if saw_tool_call:  # ツールコールと並行してテキストが生成される場合
                                assistant_text_content_buffer += delta_content
                            else:
                                last_message_buffer += delta_content

                            if not saw_tool_call:  # 通常のテキストストリーミング
                                if not response_msgs and initial_warnings_text:
                                    # 最初のメッセージに警告を付加
                                    temp_buffer = initial_warnings_text + "\n" + last_message_buffer
                                    initial_warnings_text = ""  # 一度使ったらクリア
                                else:
                                    temp_buffer = last_message_buffer

                                # メッセージ分割ロジック
                                while len(temp_buffer) > max_message_length:
                                    split_point = temp_buffer.rfind("\n", 0, max_message_length)
                                    if split_point == -1:  # 改行が見つからない場合は強制的に分割
                                        split_point = max_message_length

                                    content_to_send_as_new_message = temp_buffer[:split_point]
                                    temp_buffer = temp_buffer[split_point:].lstrip()  # 次のバッファの先頭の空白を削除

                                    if response_msgs and edit_task is not None and not edit_task.done():
                                        await edit_task  # 前の編集が終わるのを待つ

                                    msg_to_reply = origin if not response_msgs else response_msgs[-1]
                                    try:
                                        # 新しいメッセージとして送信
                                        msg = await msg_to_reply.reply(content=content_to_send_as_new_message,
                                                                       silent=True)
                                        self.bot.message_nodes[msg.id] = MessageNode(
                                            text=content_to_send_as_new_message, next_message=msg_to_reply)
                                        # await self.bot.message_nodes[msg.id].lock.acquire() # ロックは最後にまとめて解放するのでここでは不要かも
                                        response_msgs.append(msg)
                                        self.bot.last_task_time = dt.now().timestamp()
                                    except Exception as send_e:
                                        logging.error(f"メッセージパートの送信に失敗しました (新規): {send_e}")
                                        # エラーメッセージを送信しようとするが、これも失敗する可能性あり
                                        error_msg_content = self.bot.ERROR_MESSAGES.get("send_failed_part",
                                                                                        "⚠️ メッセージの途中で送信に失敗しました。")
                                        try:
                                            await (response_msgs[-1] if response_msgs else origin).reply(
                                                content=error_msg_content, silent=True)
                                        except:
                                            pass
                                        return  # 送信失敗したら処理中断
                                last_message_buffer = temp_buffer  # 残りをバッファに戻す

                                # 編集ロジック (分割されなかった残り、または通常のストリーム)
                                ready_to_edit = (
                                        response_msgs and last_message_buffer and
                                        (edit_task is None or edit_task.done()) and
                                        dt.now().timestamp() - self.bot.last_task_time >= EDIT_DELAY_SECONDS
                                )
                                is_final_chunk_trigger = choice.finish_reason is not None

                                if ready_to_edit or (is_final_chunk_trigger and response_msgs and last_message_buffer):
                                    if edit_task is not None and not edit_task.done():
                                        await edit_task
                                    content_to_edit = last_message_buffer
                                    # ストリーミング中はインジケータを付加しない方が良い場合もあるが、元のコードに合わせておく
                                    # if not is_final_chunk_trigger and STREAMING_INDICATOR:
                                    #    content_to_edit += STREAMING_INDICATOR

                                    msg_to_edit = response_msgs[-1]
                                    edit_task = asyncio.create_task(self._perform_edit(msg_to_edit, content_to_edit))
                                    self.bot.last_task_time = dt.now().timestamp()

                        if choice.finish_reason == "tool_calls":  # ツールコールで終了した場合
                            break  # tool_callsを処理するループへ

                # --- ストリーム終了後の処理 ---
                if edit_task is not None and not edit_task.done():  # 最後の編集タスクを待つ
                    await edit_task

                if saw_tool_call:  # ツールコールがあった場合
                    assistant_tool_calls_list = []
                    for call_id, details in tool_call_data_for_assistant.items():
                        function_name = details["name"]
                        arguments_str = "".join(details["arguments_chunks"])
                        try:
                            # argumentsが有効なJSONか確認（LLMが不完全なJSONを返すことがあるため）
                            json.loads(arguments_str)
                        except json.JSONDecodeError:
                            logging.warning(f"ツール {function_name} の引数が不正なJSONです: {arguments_str}")
                            # 不正な場合はツールコールをスキップするか、エラーとして処理
                            messages.append({  # エラーとしてLLMに伝える
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": function_name,
                                "content": f"Error: Invalid JSON arguments provided for tool {function_name}.",
                            })
                            continue  # 次のツールコールへ

                        assistant_tool_calls_list.append({
                            "id": call_id, "type": "function",
                            "function": {"name": function_name, "arguments": arguments_str}
                        })

                    if assistant_tool_calls_list:  # 有効なツールコールがある場合
                        messages.append({
                            "role": "assistant",
                            "content": assistant_text_content_buffer.strip() if assistant_text_content_buffer.strip() else None,
                            # contentが空ならNone
                            "tool_calls": assistant_tool_calls_list
                        })
                        # assistant_text_content_buffer = "" # クリア

                        for call in assistant_tool_calls_list:
                            tool_name = call["function"]["name"]
                            actives = self.bot.cfg.get("active_tools", None)
                            if tool_name not in self.bot.plugins or (actives is not None and tool_name not in actives):
                                messages.append({
                                    "role": "tool", "tool_call_id": call["id"], "name": tool_name,
                                    "content": f"[{tool_name}] は無効化されています。"
                                })
                                continue

                            plugin = self.bot.plugins[tool_name]
                            try:
                                args = json.loads(call["function"]["arguments"])
                                result = await plugin.run(arguments=args, bot=self.bot)  # self.bot を渡す
                                messages.append({
                                    "role": "tool", "tool_call_id": call["id"], "name": tool_name,
                                    "content": result
                                })
                            except Exception as e_plugin:
                                logging.error(f"プラグイン {tool_name} の実行中にエラー: {e_plugin}", exc_info=True)
                                messages.append({
                                    "role": "tool", "tool_call_id": call["id"], "name": tool_name,
                                    "content": f"Error running tool {tool_name}: {str(e_plugin)}"
                                })
                        # last_message_buffer = "" # ツールコール後はリセット
                        continue  # 次のLLM呼び出しループへ
                    else:  # 有効なツールコールがなかった場合 (JSONパース失敗など)
                        logging.warning("ツールコールが検出されましたが、有効なツール詳細が見つかりませんでした。")
                        # フォールバックとして通常のテキスト応答を試みるか、エラーメッセージを追加
                        if not assistant_text_content_buffer and not last_message_buffer:  # LLMがテキストも生成しなかった場合
                            messages.append({
                                "role": "user",  # AIに再度処理を促すためuserロール
                                "content": "Tool call was attempted but failed because the tool details were missing or invalid. Please respond based on the previous context if possible, or indicate you couldn't use the tool."
                            })
                        # last_message_bufferに何かあれば、それが最終応答になるようにループを抜ける
                        # assistant_text_content_buffer はこの時点で last_message_buffer にマージされるべき
                        last_message_buffer = assistant_text_content_buffer + last_message_buffer
                        assistant_text_content_buffer = ""
                        break  # tool loop を抜けて通常の応答処理へ

                # ツールコールがなかった、またはツールループが終了した場合
                # initial_warnings_text がまだ残っていて、メッセージが未送信の場合
                if not response_msgs and initial_warnings_text:
                    final_content = initial_warnings_text + "\n" + last_message_buffer
                    initial_warnings_text = ""
                else:
                    final_content = last_message_buffer

                if final_content.strip() or not llm_response_generated_content:  # 空白のみでないか、LLMが何も生成しなかった場合も送信試行 (エラーメッセージ用)
                    if not response_msgs:  # まだメッセージを送信していない場合
                        try:
                            # final_contentが空でも、警告だけ送る場合がある
                            content_to_send = final_content.strip() if final_content.strip() else initial_warnings_text.strip()
                            if not content_to_send and not llm_response_generated_content:  # LLMが何も生成せず警告もない場合は、デフォルトエラー
                                content_to_send = self.bot.ERROR_MESSAGES.get("empty_response",
                                                                              "🤔 何も応答がありませんでした。")

                            if content_to_send:  # 送るべきコンテンツがある場合のみ送信
                                msg = await origin.reply(content=content_to_send, silent=True)
                                self.bot.message_nodes[msg.id] = MessageNode(text=content_to_send, next_message=origin)
                                response_msgs.append(msg)
                        except Exception as send_e:
                            logging.error(f"最終メッセージの送信に失敗しました (新規): {send_e}")
                            # エラーメッセージ送信試行
                            error_msg_final = self.bot.ERROR_MESSAGES.get("send_failed_final",
                                                                          "⚠️ レスポンスの送信に失敗しました。")
                            try:
                                await origin.reply(content=error_msg_final, silent=True)
                            except:
                                pass
                    elif response_msgs and final_content:  # 既存メッセージを編集
                        # final_contentがresponse_msgs[-1].contentと異なる場合のみ編集
                        if final_content != response_msgs[-1].content:
                            await self._perform_edit(response_msgs[-1], final_content)
                elif response_msgs and initial_warnings_text:  # LLM応答は空だが警告があり、既にメッセージを送信済み
                    # 既存のメッセージの先頭に警告を追記して編集
                    existing_content = response_msgs[-1].content
                    new_content_with_warning = initial_warnings_text + "\n" + existing_content
                    if new_content_with_warning != existing_content:
                        await self._perform_edit(response_msgs[-1], new_content_with_warning)

                break  # while max_tool_loops を抜ける (ツールコールなし、またはツール処理完了)

            except RateLimitError:
                logging.warning("OpenAI Rate Limit Error (429) が発生しました。")
                ratelimit_msg = self.bot.ERROR_MESSAGES.get("ratelimit_error", "⚠️ レート制限です。")
                try:
                    await origin.reply(content=ratelimit_msg, silent=True)
                except:
                    pass
                return  # 処理中断
            except Exception as e_gen:
                logging.exception("レスポンス生成中にエラーが発生しました (一般)。")
                general_error_msg = self.bot.ERROR_MESSAGES.get("general_error", "⚠️ 不明なエラーが発生しました。")
                try:
                    await (response_msgs[-1] if response_msgs else origin).reply(content=general_error_msg, silent=True)
                except:
                    pass
                return  # 処理中断

        if current_loop >= max_tool_loops and saw_tool_call:  # ツールループ上限に達した場合
            logging.warning(f"ツール呼び出しがループ上限 ({max_tool_loops}) に達しました。")
            # 最後のバッファに残っているテキストがあればそれを送信
            if last_message_buffer.strip() or assistant_text_content_buffer.strip():
                final_loop_limit_content = (assistant_text_content_buffer + last_message_buffer).strip()
                if not response_msgs:
                    try:
                        await origin.reply(final_loop_limit_content, silent=True)
                    except Exception as e:
                        logging.error(f"ツールループ上限時のメッセージ送信失敗: {e}")
                elif response_msgs and final_loop_limit_content != response_msgs[-1].content:
                    try:
                        await self._perform_edit(response_msgs[-1], final_loop_limit_content)
                    except Exception as e:
                        logging.error(f"ツールループ上限時のメッセージ編集失敗: {e}")
            else:  # 何もテキストがなければ、ループ上限に達した旨を通知
                loop_limit_msg = self.bot.ERROR_MESSAGES.get("tool_loop_limit",
                                                             "⚠️ ツールの処理が複雑すぎたため、途中で停止しました。")
                try:
                    await (response_msgs[-1] if response_msgs else origin).reply(content=loop_limit_msg, silent=True)
                except:
                    pass

        # --- 全体終了後の後処理 ---
        full_response_text_parts = []
        for msg in response_msgs:
            node = self.bot.message_nodes.get(msg.id)
            if node and node.text:
                full_response_text_parts.append(node.text)
            elif msg.content:  # ノードが見つからない場合、メッセージのcontentから直接取得
                full_response_text_parts.append(msg.content)

        full_response_text = "".join(full_response_text_parts)

        if full_response_text:  # 何か応答があった場合のみログ出力
            logging.info(
                "LLMレスポンス完了 (起点ID: %s): %s",
                origin.id,
                full_response_text[:500] + ("..." if len(full_response_text) > 500 else ""),
            )

        for msg in response_msgs:  # 送信したメッセージのノードに完全なテキストを反映 (主に編集用)
            node = self.bot.message_nodes.get(msg.id)
            if node:
                node.text = full_response_text  # 分割されていた場合、完全な応答で上書き
                if node.lock.locked():
                    node.lock.release()  # このタイミングでまとめて解放

        # MAX_MESSAGE_NODES の型ヒントが main から解決できるようにする
        max_nodes: int = getattr(self.bot, 'MAX_MESSAGE_NODES', 100)  # main.MAX_MESSAGE_NODES を参照したい
        if len(self.bot.message_nodes) > max_nodes:
            over = len(self.bot.message_nodes) - max_nodes
            # 古いものから削除するためにソートする。キーはメッセージID (作成順に近い)
            mids_to_pop = sorted(self.bot.message_nodes.keys())[:over]
            logging.info(f"古いメッセージノードを {over} 件削除します...")
            for mid in mids_to_pop:
                node_to_pop = self.bot.message_nodes.pop(mid, None)
                if node_to_pop:
                    try:
                        # ロックの解放を試みる (既に解放されているかもしれない)
                        if node_to_pop.lock.locked():
                            node_to_pop.lock.release()
                    except Exception as e_lock:
                        logging.debug(f"ノード {mid} のロック解放中にエラー (無視): {e_lock}")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.author == self.bot.user:
            return

        if not should_respond(self.bot.user, message):
            return

        if not self._is_authorised(message):
            return

        provider_model_str = self.bot.cfg.get("model", "")
        if not provider_model_str:
            logging.error("config.yaml に 'model' キーが見つかりません – 中止します。")
            return

        try:
            provider, model_name = provider_model_str.split("/", 1)
        except ValueError:
            logging.error(f"無効なモデル形式 '{provider_model_str}'。形式は 'provider/model' である必要があります。")
            try:
                await message.reply("無効なモデル設定です。ボットの設定を確認してください。", silent=True)
            except:
                pass
            return

        provider_cfg = self.bot.cfg.get("providers", {}).get(provider)
        if not provider_cfg:
            logging.error(f"config.yaml にプロバイダー '{provider}' が見つかりません – 中止します。")
            try:
                await message.reply(f"プロバイダー '{provider}' が設定されていません。", silent=True)
            except:
                pass
            return

        # OpenAIクライアントの初期化 (他のプロバイダも同様のインターフェースなら共通化可能)
        # 現状はOpenAI専用のようだが、将来的にはプロバイダごとにクライアントを切り替える仕組みが必要
        if provider.lower() == "openai" or provider_cfg.get("api_type") == "openai":  # OpenAI互換APIを想定
            openai_client = AsyncOpenAI(
                base_url=provider_cfg.get("base_url"),
                api_key=provider_cfg.get("api_key", "sk-no-key-required"),
            )
        # elif provider.lower() == "google": # 例: Google GenAI
        #    genai.configure(api_key=provider_cfg.get("api_key"))
        #    # Googleのクライアントを使う処理 ... (ただし現在のコードはOpenAI API Streamを期待)
        #    logging.warning("Google GenAIプロバイダーは現在このコードでは完全にはサポートされていません。")
        #    try: await message.reply("Google GenAIは現在ストリーミング未対応です。", silent=True); return
        #    except: pass
        else:
            logging.error(f"サポートされていないプロバイダー: {provider}")
            try:
                await message.reply(f"プロバイダー '{provider}' はサポートされていません。", silent=True)
            except:
                pass
            return

        accept_images = any(tag in model_name for tag in VISION_MODEL_TAGS)
        accept_usernames = provider in PROVIDERS_SUPPORTING_USERNAMES

        max_text = self.bot.cfg.get("max_text", 5_000)
        max_images = self.bot.cfg.get("max_images", 0) if accept_images else 0
        max_messages = self.bot.cfg.get("max_messages", 5)
        max_discord_msg_len = 1980  # Discordのメッセージ長制限 (安全マージン込み)

        history_messages, user_warnings = await self._build_message_chain(
            message, max_messages, max_text, max_images, accept_images, accept_usernames
        )

        server_name = message.guild.name if message.guild else "DM"
        user_name = message.author.display_name
        logging.info(
            "[%s] ユーザー: %s (ID: %s) | 添付: %d | 会話: %d | 内容: %s",
            server_name, user_name, message.author.id,
            len(message.attachments), len(history_messages), message.content[:100]  # 内容は短縮
        )

        api_payload_messages = []
        if self.bot.SYSTEM_PROMPT:
            api_payload_messages.append({"role": "system", "content": self.bot.SYSTEM_PROMPT})
        # スタータープロンプトは、通常アシスタントからの最初の発話として設定される
        # history_messagesが空で、かつSTARTER_PROMPTがある場合に付与するのが一般的
        if not history_messages and self.bot.STARTER_PROMPT:
            api_payload_messages.append({"role": "assistant", "content": self.bot.STARTER_PROMPT})

        api_payload_messages.extend(history_messages)

        if not api_payload_messages or (len(api_payload_messages) == 1 and api_payload_messages[0]["role"] == "system"):
            # システムプロンプトのみ、または空の場合は、ユーザーメッセージがないため応答しない
            # ただし、STARTER_PROMPTがある場合はそれに応答するかもしれないので、そのロジックは維持
            # ここでは、実質的なユーザー入力がないと判断した場合
            if not (len(api_payload_messages) > 0 and api_payload_messages[-1][
                "role"] == "assistant" and self.bot.STARTER_PROMPT):
                logging.info("APIに送信する実質的なメッセージがないため、処理をスキップします。")
                if user_warnings:  # 警告だけは送信
                    try:
                        await message.reply("\n".join(user_warnings), silent=True)
                    except:
                        pass
                return

        await self._generate_and_send_response(
            api_payload_messages, message, user_warnings, openai_client, model_name, max_discord_msg_len
        )

    # --- Slash Commands ---
    @app_commands.command(name="help", description="ヘルプメッセージを表示します")
    async def _help(self, interaction: discord.Interaction) -> None:
        help_text = self.bot.cfg.get("help_message", "ヘルプメッセージが設定されていません。")
        await interaction.response.send_message(help_text, ephemeral=False)

    @app_commands.command(name="arona", description="arona music botのリポジトリを表示します")
    async def _arona(self, interaction: discord.Interaction) -> None:
        if ARONA_REPOSITORY:  # 空でないことを確認
            message = f"アロナのリポジトリはこちらです！\n{ARONA_REPOSITORY}"
            await interaction.response.send_message(message, ephemeral=False)
        else:
            await interaction.response.send_message("リポジトリURLが設定されていません。", ephemeral=True)

    @app_commands.command(name="plana", description="llmcord-JP-planaのリポジトリを表示します")
    async def _plana(self, interaction: discord.Interaction) -> None:
        if PLANA_REPOSITORY:
            message = f"プラナのリポジトリはこちらです！\n{PLANA_REPOSITORY}"
            await interaction.response.send_message(message, ephemeral=False)
        else:
            await interaction.response.send_message("リポジトリURLが設定されていません。", ephemeral=True)

    @app_commands.command(name="support", description="サポートサーバーの招待コードを表示します")
    async def _support(self, interaction: discord.Interaction) -> None:
        if SUPPORT_SERVER_INVITE_LINK and SUPPORT_SERVER_INVITE_LINK != "https://discord.gg/HogeFugaPiyo":
            message = f"サポートサーバーへの招待リンクはこちらです！\n{SUPPORT_SERVER_INVITE_LINK}"
            await interaction.response.send_message(message, ephemeral=False)
        else:
            await interaction.response.send_message("サポートサーバーの招待リンクが設定されていません。", ephemeral=True)

    @app_commands.command(name="invite", description="Botをサーバーに招待します")
    async def _invite(self, interaction: discord.Interaction) -> None:
        # INVITE_URL はこのファイルの先頭で定義されている
        # もしconfigから取りたい場合は self.bot.cfg.get("invite_url") のようにする
        invite_url_to_use = INVITE_URL
        if not invite_url_to_use or invite_url_to_use == "YOUR_INVITE_URL_HERE":  # プレースホルダーチェック
            await interaction.response.send_message("招待URLが設定されていません。", ephemeral=True)
            logging.warning("Invite URL is not set or is a placeholder.")
            return

        embed = discord.Embed(
            title="🔗 ボット招待",
            description=f"PLANAをあなたのサーバーに招待しませんか？\n以下のリンクから招待できます。",
            color=discord.Color.brand_green()
        )
        embed.add_field(name="招待リンク", value=f"[ここをクリックして招待する]({invite_url_to_use})", inline=False)
        if self.bot.user and self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        embed.set_footer(text=f"コマンド実行者: {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)


async def setup(bot: DiscordLLMBot):  # main.pyのDiscordLLMBotクラスを型ヒント
    llm_cog = LLMCog(bot)
    await bot.add_cog(llm_cog)
    logging.info("llmCog がロードされ、ボットに追加されました。")