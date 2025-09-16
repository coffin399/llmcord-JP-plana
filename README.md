<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  プラナちゃんとおしゃべりしよう！
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>
<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

**llmcord-JP-PLANA** (通称: **PLANA**) は、[llmcord](https://github.com/jakobdylanc/llmcord) を基盤として開発された多機能Discordボットです。大規模言語モデル (LLM) との対話、高機能な音楽再生、画像認識、Akinatorやガチャシミュレーターなどのエンターテイメント機能、そして便利なサーバーユーティリティを提供します。OpenAI互換APIに対応しており、リモートホスト型やローカルホスト型など、ほぼすべてのLLMと連携可能です。

**llmcord-JP-PLANA** (commonly known as **PLANA**) is a multi-functional Discord bot developed based on [llmcord](https://github.com/jakobdylanc/llmcord). It offers conversations with Large Language Models (LLMs), high-fidelity music playback, image recognition, entertainment features like Akinator and a gacha simulator, and useful server utilities. It supports OpenAI-compatible APIs, allowing integration with almost all LLMs, including remotely hosted and locally hosted ones.

## 🚀 クイックスタート / Quick Start

### 🤖 Botをあなたのサーバーに招待 / Invite Plana to Your Server

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ PLANAをあなたのサーバーに招待する ⬅️</strong>
  </a>
</h3>

*   セルフホストを行う場合は、`config.yaml` にご自身のBotの招待URLを設定してください。
*   If you are self-hosting, please set your bot's invitation URL in `config.yaml`.

### 💬 サポート / Support:
*   Botの操作に関する質問や不具合報告は、`/support` コマンドで表示される連絡先までお願いします。
*   For questions or bug reports, please use the contact information displayed with the `/support` command.

## ✨ 主な機能 / Main Features

### 🗣️ AIとの対話 (LLM) / AI Chat (LLM)
Botにメンション (`@PLANA`) を付けて話しかけるか、Botのメッセージに返信することで、AIとの会話が始まります。
Start a conversation with the AI by mentioning the bot (`@PLANA`) or replying to one of its messages.

*   **継続的な会話 / Continuous Conversations:** 返信を続けることで文脈を維持した会話が可能です。
*   **画像認識 / Image Recognition:** メッセージと一緒に画像を添付すると、AIが画像の内容も理解しようとします (ビジョンモデル対応の場合)。
*   **チャンネル毎のモデル切り替え / Per-Channel Model Switching:** チャンネルごとに使用するAIモデルを柔軟に変更できます。会話の目的に合わせて、最適なモデル（例: 高性能モデル、高速応答モデルなど）を使い分けることが可能です。
    *   `/switch-models [model]`: 利用可能なモデルリストから選択して切り替えます。
    *   `/switch-models-default`: モデルをデフォルト設定に戻します。
*   **ツール利用 (ウェブ検索) / Tool Use (Web Search):** AIが必要と判断した場合、インターネットで情報を検索して応答に利用します (Google AI Studio APIキーが必要です)。
*   **会話履歴の管理 / Conversation History Management:** `/clear_history` コマンドで現在のチャンネルの会話履歴をリセットできます。
*   **カスタマイズ可能なAIパーソナリティ / Customizable AI Personality:** `config.yaml` のシステムプロンプトを編集することで、AIの性格や応答スタイルを自由に変更できます。

### 🎶 高機能な音楽再生 / Advanced Music Playback
ボイスチャンネルで高品質な音楽を楽しめます。
Enjoy high-quality music in your voice channels.

*   **多彩なソースに対応 / Various Sources:** YouTube, SoundCloudなどのURLや検索クエリから音楽を再生・キューに追加できます。
*   **再生コントロール / Playback Controls:** `/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/volume` など、直感的なコマンドで操作できます。
*   **高度なキュー管理 / Advanced Queue Management:** `/queue` でのキュー表示、`/shuffle` でのシャッフル、`/remove` での個別削除、`/clear` での全削除が可能です。
*   **ループ再生 / Loop Modes:** `/loop` コマンドで、ループなし・1曲ループ・キュー全体のループを切り替えられます。
*   **自動管理 / Automatic Management:** ボイスチャンネルに誰もいなくなると自動で退出するなど、リソースを効率的に管理します。

### 🎮 ゲーム & エンターテイメント / Games & Entertainment
*   **/akinator:** あの有名な魔人アキネーターと、キャラクター当てゲームで遊べます。多言語対応。
*   **/gacha:** ブルーアーカイブ風の生徒募集（ガチャ）をシミュレートできます。
*   **/meow:** TheCatAPIからランダムで可愛い猫の画像を表示します。

### 🛠️ ユーティリティコマンド / Utility Commands
サーバー管理や情報確認に役立つ便利なスラッシュコマンドを提供します。
Provides useful slash commands for server management and information retrieval.

*   **/help, /llm_help:** Botの全機能のヘルプとAI利用ガイドラインをまとめた総合的なヘルプパネルを表示します。
*   **/ping:** Botの現在の応答速度（レイテンシ）を表示します。
*   **/serverinfo:** サーバーの作成日、メンバー数、チャンネル数などの詳細情報を表示します。
*   **/userinfo [user]:** 指定したユーザー（または自分）のアカウント作成日やサーバー参加日、ロールなどの情報を表示します。
*   **/avatar [user]:** ユーザーのアバター画像を高画質で表示します。
*   **/invite:** Botをあなたのサーバーに招待するためのリンクを表示します。
*   **/support:** 開発者への連絡方法を表示します。

### 📥 メディアダウンロード / Media Downloader
YouTubeなどのサイトから動画や音声をダウンロードし、一時的な共有リンクを生成します。
Downloads video or audio from sites like YouTube and generates a temporary shareable link.

*   **/ytdlp_video [query]:** 動画のURLまたは検索キーワードを指定して、フォーマットを選択しダウンロードします。高画質（1080p以上）のダウンロードにも対応しています。
*   **/ytdlp_audio [query] [audio_format]:** 音声のみを抽出し、指定したフォーマット（MP3, M4Aなど）でダウンロードします。

<p align="center">
 <img src="https://i.imgur.com/EdkYshV.png" alt="video downloader">
</p>

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
    *   他者の権利を侵害したり、名誉を毀損したりする目的での利用。
    *   差別的、暴力的、または非倫理的なコンテンツの生成。

---

## ⚙️ インストールと設定 (セルフホスト) / Installation and Setup (Self-Hosting)

### 前提条件 / Prerequisites
*   Python 3.8以上
*   Git
*   FFmpeg (音楽機能・メディアダウンロード機能を有効にする場合)
*   Docker & Docker Compose (任意、推奨)

### 手順 / Instructions

1.  **リポジトリをクローンします / Clone the repository:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **`config.yaml` の設定 / Configure `config.yaml`:**
    `config.default.yaml` をコピーして `config.yaml` を作成します。初回起動時に自動で生成もされます。
    Copy `config.default.yaml` to create `config.yaml`. It will also be generated automatically on the first run.
    
    **生成された `config.yaml` を開き、少なくとも `bot_token` とLLM関連の設定を編集してください。**
    **Open the generated `config.yaml` and edit at least the `bot_token` and LLM-related settings.**

    <details>
    <summary><b>主要な設定項目一覧 (クリックで展開) / Key Configuration Options (Click to expand)</b></summary>

    #### 全般設定 / General Settings:
    | 設定 (Setting) | 説明 (Description) |
    |---|---|
    | **`bot_token`** | **必須。**[Discord Developer Portal](https://discord.com/developers/applications) で取得したBotのトークン。<br>**Required.** Your bot's token from the [Discord Developer Portal](https://discord.com/developers/applications). |
    | `enabled_cogs` | ロードする機能 (Cog) のリスト。不要な機能はここから削除できます。<br>List of features (Cogs) to load. You can remove unwanted features here. |
    | `status_rotation` | Botのステータスにローテーションで表示するメッセージのリスト。<br>A list of messages to be rotated in the bot's status. |
    | `allowed_channel_ids` | Botが反応するチャンネルIDのリスト。空白で全チャンネル対応。<br>List of channel IDs where the bot will respond. Leave blank for all channels. |
    | `allowed_role_ids` | Botを使用できるロールIDのリスト。空白で全員利用可。<br>List of role IDs that can use the bot. Leave blank for everyone. |
    | `sync_slash_commands` | `true` の場合、起動時にスラッシュコマンドをDiscordに同期します。<br>If `true`, syncs slash commands with Discord on startup. |
    | `test_guild_id` | 開発中、スラッシュコマンドを即時反映させるためのテストサーバーID。<br>Test server ID for immediate slash command updates during development. |
    | `bot_invite_url` | `/invite` コマンドで表示されるBotの招待URL。<br>The bot's invitation URL displayed by the `/invite` command. |

    #### LLM 設定 ( `llm:` ) / LLM Settings (under `llm:`):
    | 設定 (Setting) | 説明 (Description) |
    |---|---|
    | **`model`** | **必須。** 使用するメインLLMモデル。`<provider name>/<model name>` 形式。(例: `openai/gpt-4o`)<br>**Required.** Main LLM model to use. Format `<provider name>/<model name>`. (e.g., `openai/gpt-4o`) |
    | **`providers`** | **必須。** 各LLMプロバイダーの `base_url` と `api_key` を設定します。(OpenAI互換APIのみ)<br>**Required.** Configure each LLM provider with `base_url` and `api_key`. (OpenAI-compatible APIs only) |
    | `available_models` | `/switch-models` コマンドでユーザーが選択できるモデルのリスト。<br>A list of models users can select with the `/switch-models` command.<br>(例/e.g., `["openai/gpt-4o", "mistral/mistral-large-latest"]`) |
    | `system_prompt` | AIの性格や役割を定義するシステムプロンプト。<br>System prompt to define the AI's personality and role. |
    | `max_messages` | 会話履歴として記憶する最大メッセージ数。(デフォルト: `10`)<br>Max messages to keep in a reply chain. (Default: `10`) |
    | `max_images` | 一度に認識できる最大画像数。(デフォルト: `1`)<br>Max image attachments per message. (Default: `1`) |
    | `active_tools` | 有効にするツールのリスト (例: `["search"]`)。<br>List of tools to enable (e.g., `["search"]`). |
    | `search_agent` | ウェブ検索エージェントの設定。`api_key` に **Google AI Studio APIキー** を設定します。<br>Settings for the web search agent. Set your **Google AI Studio API key** in `api_key`. |
    | `extra_api_parameters` | `temperature` や `max_tokens` などのAPIパラメータ。<br>API parameters like `temperature` and `max_tokens`. |

    #### 音楽再生設定 ( `music:` ) / Music Playback Settings (under `music:`):
    | 設定 (Setting) | 説明 (Description) |
    |---|---|
    | `default_volume` | デフォルトの再生音量 (0-200)。(デフォルト: `50`)<br>Default playback volume (0-200). (Default: `50`) |
    | `max_queue_size` | キューに追加できる曲の最大数。(デフォルト: `9000`)<br>Maximum number of songs in the queue. (Default: `9000`) |
    | `auto_leave_timeout` | VCに誰もいなくなってから自動退出するまでの秒数。(デフォルト: `10`)<br>Seconds to wait before auto-leaving an empty voice channel. (Default: `10`) |
    | `max_guilds` | Botが同時に接続できるサーバーの最大数。<br>Maximum number of guilds the bot can be connected to simultaneously. |

    </details>

3.  **Botを起動します / Start the Bot:**

    いくつかの起動方法があります。自分に合った方法を選んでください。
    There are several ways to start the bot. Choose the one that suits you best.

    ---

    #### 🚀 一番簡単な方法 (Windows) / The Easiest Way (Windows)
    
    `start_plana.bat` ファイルをダブルクリックするだけです。
    初回起動時に、必要なライブラリ (`requirements.txt` の内容) が自動的にインストールされます。
    
    Simply double-click the `start_plana.bat` file.
    On the first run, it will also automatically install the necessary libraries from `requirements.txt`.

    ---
    
    #### 💻 標準的な方法 (Windows, Linux, macOS) / Standard Method (Windows, Linux, macOS)

    1.  **依存関係をインストールします / Install dependencies:**
        ターミナル（コマンドプロンプト）で以下のコマンドを実行します。
        Run the following command in your terminal (or command prompt).
        ```bash
        pip install -r requirements.txt
        ```

    2.  **Botを起動します / Start the bot:**
        ```bash
        python main.py
        ```

    ---

    #### 🐳 Dockerを使う方法 (推奨) / Using Docker (Recommended)

    Dockerがインストールされている場合、この方法が最も簡単で環境を汚しません。
    If you have Docker installed, this is the easiest method and keeps your environment clean.
    ```bash
    docker compose up --build -d
    ```
    (2回目以降は `--build` は不要です / `--build` is not needed after the first run)

<p align="center">
現在開発中です。仕様は変更される可能性があります。
<br>
Currently in development. Specifications are subject to change.
</p>