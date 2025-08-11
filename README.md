<h1 align="center">
  Plana-Cord-JP
</h1>

<h3 align="center"><i>
  プラナちゃんとおしゃべりしよう！
</i></h3>

![](https://i.imgur.com/Q3VuxzG.png)


Plana-Cord-JP (通称: PLANA) は、[llmcord](https://github.com/jakobdylanc/llmcord) を基盤として開発された多機能Discordボットです。大規模言語モデル (LLM) との対話、音楽再生、画像認識、便利なサーバーユーティリティ機能などを提供します。リモートホスト型やローカルホスト型など、ほぼすべてのLLMに対応しています。

Plana-Cord-JP (commonly known as PLANA) is a multi-functional Discord bot developed based on [llmcord](https://github.com/jakobdylanc/llmcord). It offers features such as conversations with Large Language Models (LLMs), music playback, image recognition, and useful server utilities. It supports almost all LLMs, including remotely hosted and locally hosted ones.

## 🚀 クイックスタート / Quick Start

### 🤖 Botをあなたのサーバーに招待 / Invite Plana to Your Server:
*   [ここをクリックしてプラナをサーバーに招待できます / Click here to invite PLANA to your server](https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot)

### 💬 サポートサーバー / Support Server:
*   [サポートサーバーに参加する / Join the Support Server](https://discord.gg/SjuWKtwNAG)

## ✨ 主な機能 / Main Features
### 🗣️ AIとの対話 (LLM) / AI Chat (LLM)
Botにメンション (`@PLANA`) を付けて話しかけることで、AIとの会話が始まります。
Start a conversation with the AI by mentioning the bot (`@PLANA`).

*   **継続的な会話 / Continuous Conversations:** 返信を続けることで会話を展開できます。
    You can develop conversations by continuing to reply.
*   **画像認識 / Image Recognition:** メッセージと一緒に画像を添付すると、AIが画像の内容も理解しようとします (対応モデルの場合)。
    Attach images with your message, and the AI will try to understand their content (for compatible models).
*   **過去のメッセージへの応答 / Replying to Past Messages:** 過去のメッセージに返信して会話を「巻き戻す」ことができます。
    You can "rewind" conversations by replying to past messages.
*   **任意のメッセージへの言及 / Referencing Any Message:** サーバー内の任意のメッセージに返信しながらBotにメンションを付けて、その内容について質問できます。
    Reply to any message in the server while mentioning the bot to ask about its content.
*   **自動メッセージ結合 / Automatic Message Grouping:** 同じユーザーによる連続メッセージは自動的にまとめて処理されます。
    Consecutive messages from the same user are automatically processed together.
*   **カスタマイズ可能なAIパーソナリティ / Customizable AI Personality:** システムプロンプトを編集することで、AIの性格や応答スタイルを変更できます。
    You can change the AI's personality and response style by editing the system prompt.
*   **ウェブ検索機能 (Search Agent) / Web Search (Search Agent):** AIが必要と判断した場合、インターネットで情報を検索して応答に利用します (Google AI Studio APIキーが必要です)。
    If the AI deems it necessary, it will search the internet for information to use in its response (requires a Google AI Studio API key).

### 🎶 音楽再生 / Music Playback
ボイスチャンネルで音楽を楽しめます。
Enjoy music in your voice channels.

*   **再生とキューイング / Play & Queue:** 曲名、URL (YouTube, SoundCloudなど)、または検索クエリで音楽を再生・キューに追加。
    Play or queue music by song name, URL (YouTube, SoundCloud, etc.), or search query.
*   **再生コントロール / Playback Controls:** 一時停止、再開、停止、スキップ、音量調整。
    Pause, resume, stop, skip, and adjust volume.
*   **キュー管理 / Queue Management:** 現在のキューの表示、シャッフル、クリア、特定の曲の削除。
    View, shuffle, clear the current queue, or remove specific songs.
*   **ループ再生 / Loop Modes:** ループなし、現在の曲のループ、キュー全体ループの切り替え。
    Toggle between no loop, looping the current song, or looping the entire queue.
*   **自動退出 / Auto-Leave:** ボイスチャンネルに誰もいなくなると一定時間後に自動で退出します。
    Automatically leaves the voice channel after a certain period if no one is present.

### 🛠️ ユーティリティコマンド (スラッシュコマンド) / Utility Commands (Slash Commands)
その他、スラッシュコマンドを提供します。
Provides useful slash commands.

*   `/help`: Botのヘルプ情報とAI利用ガイドラインを表示します。
    Displays help information for the bot and AI usage guidelines.
*   `/llm_help`: AI対話機能に特化した詳細なヘルプとガイドラインを表示します。
    Displays detailed help and guidelines specific to the AI chat feature.
*   `/ping`: Botのレイテンシを表示します。
    Shows the bot's current latency.
*   `/serverinfo`: 現在のサーバー情報を表示します。
    Displays information about the current server.
*   `/userinfo [user]`: 指定ユーザーの情報を表示します。
    Displays information about the specified user.
*   `/avatar [user]`: 指定ユーザーのアバター画像を表示します。
    Displays the avatar image of the specified user.
*   `/invite`: Botをサーバーに招待するリンクを表示します。
    Displays the link to invite the bot to your server.
*   `/support`: サポートサーバーへの招待リンクを表示します。
    Displays the invitation link to the support server.
*   `/plana` & `/arona`: 関連リポジトリへのリンクを表示します。
    Displays links to related repositories.

### その他 / Other Features
*   **テキストファイル添付対応 / Text File Attachment Support:** `.txt`, `.py`, `.c` などのテキストファイルをLLMが読み込めます。
    LLM can read text files like `.txt`, `.py`, `.c`, etc.
*   **完全非同期処理 / Fully Asynchronous Processing:**

---

### ⚠️ AI利用ガイドラインと免責事項 / AI Usage Guidelines and Disclaimer
本BotのAI機能を利用する前に、以下のガイドラインを必ずお読みください。**Botの利用をもって、本ガイドラインに同意したものとみなします。**
Please read the following guidelines carefully before using the AI features of this bot. **By using the bot, you are deemed to have agreed to these guidelines.**

*   **データ入力に関する注意 / Precautions for Data Input:**
    *   **個人情報や機密情報を絶対に入力しないでください。** (例: 氏名, 住所, パスワード, NDA情報, 企業の内部情報)
        **Never input personal or confidential information.** (e.g., names, addresses, passwords, NDA-protected information, internal company data).
    *   **第三者の著作物や知的財産を無断で入力しないでください。**
        **Do not input third-party copyrighted materials or intellectual property without permission.**

*   **生成物の利用に関する注意 / Precautions for Using Generated Output:**
    *   **AIの生成する情報には、不正確な内容や偏見が含まれる可能性があります。** 生成された内容は参考情報として扱い、**必ずご自身でファクトチェックを行ってください。**
        **Information generated by the AI may be inaccurate or contain biases.** Treat the output as a reference and **always perform your own fact-checking.**
    *   生成物を利用した結果生じたいかなる損害についても、開発者は責任を負いません。利用は**自己責任**でお願いします。
        The developers are not liable for any damages resulting from the use of the generated content. Use is at your **own risk**.
    *   生成物が第三者の既存の権利（著作権など）を侵害する可能性もゼロではありません。商用利用など、重要な用途での利用には特にご注意ください。
        There is a non-zero possibility that the generated content may infringe upon existing third-party rights (e.g., copyright). Be especially cautious when using it for important purposes, such as commercial use.

*   **禁止事項 / Prohibited Uses:**
    *   法令や公序良俗に反する目的での利用。
        Use for purposes that violate laws or public order.
    *   他者の権利を侵害したり、名誉を毀損したりする目的での利用。
        Use for the purpose of infringing on the rights of others or for defamation.
    *   差別的、暴力的、または非倫理的なコンテンツの生成。
        Generation of discriminatory, violent, or unethical content.

---

## ⚙️ インストールと設定 / Installation and Setup

1.  **リポジトリをクローンします / Clone the repository:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **(初回起動時) `config.yaml` の生成 / (First time) Generate `config.yaml`:**
    `config.default.yaml` が存在する場合、初回の `python main.py` 実行時に `config.yaml` が自動的にコピー生成されます。
    If `config.default.yaml` exists, `config.yaml` will be automatically copied and generated on the first run of `python main.py`.
    **生成された `config.yaml` を開き、ボットトークン、APIキー、Search Agentキー、その他の必要な設定を編集してください。**
    **Open the generated `config.yaml` and edit the bot token, API keys,Search Agent Key and other necessary settings.**

3.  **`config.yaml` を設定します / Configure `config.yaml`:**

    #### Discord 設定 / Discord Settings:

    | 設定 (Setting)          | 説明 (Description)                                                                                                                                                              |
    |-------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
    | **`bot_token`**         | **必須。**[Discord Developer Portal](https://discord.com/developers/applications) でBotを作成し、トークンを取得します。"MESSAGE CONTENT INTENT" を有効にしてください。<br>**Required.** Create a bot and get its token from the [Discord Developer Portal](https://discord.com/developers/applications). Enable the "MESSAGE CONTENT INTENT". |
    | **`client_id`**         | BotのクライアントID。"OAuth2" タブで見つけられます。<br>The bot's client ID. Found in the "OAuth2" tab.                                                                                        |
    | **`prefix`**            | プレフィックスコマンドの接頭辞 (例: `!!`)。<br>The prefix for prefix commands (e.g., `!!`).                                                                                                |
    | **`status_message`**    | Botのカスタムステータスメッセージ。`{prefix}` と `{guild_count}` が使えます。最大128文字。<br>Custom status message for the bot. `{prefix}` and `{guild_count}` can be used. Max 128 characters. |
    | **`allowed_channel_ids`** | Botが反応するチャンネルIDのリスト。空白で全チャンネル対応。<br>List of channel IDs where the bot will respond. Leave blank for all channels.                                                              |
    | **`allowed_role_ids`**  | Botを使用できるロールIDのリスト。空白で全員利用可。指定するとDMでの利用不可。<br>List of role IDsurethane that can use the bot. Leave blank for everyone. If specified, DM usage is disabled. |
    | **`admin_user_ids`**    | Bot管理者ユーザーIDのリスト。<br>List of bot administrator user IDs.                                                                                                                     |
    | **`bot_invite_url`**    | `/invite` コマンドで表示されるBotの招待URL。<br>The bot's invitation URL displayed by the `/invite` command.                                                                                 |
    | **`support_server_invite_url`** | `/support` コマンドで表示されるサポートサーバーの招待URL。<br>The support server invitation URL displayed by the `/support` command.                                                              |
    | **`arona_repository_url`** | `/arona` コマンドで表示されるリポジトリURL。<br>The repository URL displayed by the `/arona` command.                                                                                      |
    | **`plana_repository_url`** | `/plana` コマンドで表示されるリポジトリURL。<br>The repository URL displayed by the `/plana` command.                                                                                      |
    | **`sync_slash_commands`** | `true` の場合、起動時にスラッシュコマンドを同期します。<br>If `true`, syncs slash commands on startup.                                                                                               |
    | **`test_guild_id`**     | 開発中、スラッシュコマンドを即時反映させるためのテストサーバーID。本番時は空欄またはコメントアウト。<br>Test server ID for immediate slash command updates during development. Leave blank or comment out for production. |

    #### LLM 設定 / LLM Settings (under `llm:` key):

    | 設定 (Setting)                  | 説明 (Description)                                                                                                                                                                                              |
    |---------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
    | **`max_text`**                  | 1メッセージあたりの最大テキスト長 (添付ファイル含む)。(デフォルト: `100000`)<br>Max text length per message (including attachments). (Default: `100000`)                                                                                    |
    | **`max_images`**                | 1メッセージあたりの最大画像添付数 (ビジョンモデル使用時)。(デフォルト: `1`)<br>Max image attachments per message (for vision models). (Default: `1`)                                                                                          |
    | **`max_messages`**              | 返信チェインでの最大メッセージ保持数。(デフォルト: `10`)<br>Max messages to keep in a reply chain. (Default: `10`)                                                                                                                    |
    | **`providers`**                 | 各LLMプロバイダーを `base_url` と `api_key` で設定。主要プロバイダーはテンプレートに記載済み。(OpenAI互換APIのみ)<br>Configure each LLM provider with `base_url` and `api_key`. Common providers are in the template. (OpenAI-compatible APIs only) |
    | **`model`**                     | 使用するメインLLMモデル。`<provider name>/<model name>` 形式。(例: `openai/gpt-4o`)<br>Main LLM model to use. Format `<provider name>/<model name>`. (e.g., `openai/gpt-4o`)                                                 |
    | **`extra_api_parameters`**      | `temperature` や `max_tokens` などのAPIパラメータ。(デフォルト: `max_tokens=4096, temperature=0.7`)<br>API parameters like `temperature` and `max_tokens`. (Default: `max_tokens=4096, temperature=0.7`)                     |
    | **`system_prompt`**             | AIの性格や役割を定義するシステムプロンプト。なんかいい感じのものを書いてください。<br>System prompt to define the AI's personality and role.                                                                                                          |
    | **`starter_prompt`**            | 会話開始時のAIの最初の発言を促すプロンプト (任意)。<br>Prompt to encourage the AI's first utterance at the start of a conversation (optional).                                                                                       |
    | **`active_tools`**              | 有効にするツールのリスト (例: `["search"]`)。<br>List of tools to enable (e.g., `["search"]`).                                                                                                                              |
    | **`max_tool_iterations`**       | ツール呼び出しの最大反復回数。(デフォルト: `3`)<br>Maximum number of tool call iterations. (Default: `3`)                                                                                                                         |
    | **`search_agent`**              | ウェブ検索エージェントの設定 (`model`, `api_key`, `format_control`)。<br>Settings for the web search agent (`model`, `api_key`, `format_control`).                                                                           |
    | **`error_msg`**                 | ユーザーに表示される各種エラーメッセージ (日英併記可能)。<br>Various error messages displayed to the user (can be bilingual).                                                                                                            |

    #### 音楽再生設定 / Music Playback Settings (under `music:` key):

    | 設定 (Setting)                | 説明 (Description)                                                                                                                                                                    |
    |-------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
    | **`default_volume`**          | デフォルトの再生音量 (0-100)。(デフォルト: `50`)<br>Default playback volume (0-100). (Default: `50`)                                                                                         |
    | **`max_queue_size`**          | キューに追加できる曲の最大数。(デフォルト: `100`)<br>Maximum number of songs that can be added to the queue. (Default: `100`)                                                                 |
    | **`auto_leave_timeout`**      | VCに誰もいなくなってから自動退出するまでの秒数。(デフォルト: `60`)<br>Seconds to wait before auto-leaving an empty voice channel. (Default: `60`)                                                    |
    | **`ffmpeg_before_options`**   | FFmpegの `before_options`。(デフォルト: `-reconnect 1 ...`)<br>FFmpeg `before_options`. (Default: `-reconnect 1 ...`)                                                                     |
    | **`ffmpeg_options`**          | FFmpegの `options`。(デフォルト: `-vn`)<br>FFmpeg `options`. (Default: `-vn`)                                                                                                             |
    | **`niconico`**                | ニコニコ動画のログイン情報 (`email`, `password`) (任意)。<br>Niconico login credentials (`email`, `password`) (optional).                                                                      |
    | **`messages`**                | 音楽機能関連のBotの応答メッセージ (日英併記可能)。<br>Bot response messages related to music features (can be bilingual).                                                                           |


4.  **依存関係をインストールし実行します / Install dependencies and run:**

    **Dockerなし (または `start_plana.bat`) / Without Docker (or run `start_plana.bat`):**
    ```bash
    python -m pip install -U -r requirements.txt
    python main.py
    ```
   
    **Dockerあり / With Docker:**
    ```bash
    docker compose up --build -d 
    ```
    (初回以降は `--build` は不要 / `--build` is not needed after the first time)

<p align="center">
現在開発中です。仕様は変更される可能性があります。非他機能版はmainブランチを参照してください。
Currently in development. Specifications subject to change. See the main branch for the version without this/these feature(s).
</p>