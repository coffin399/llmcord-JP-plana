<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  プラナちゃんとおしゃべりしよう！ / Let's chat with Plana!
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[日本語](#-概要--overview) • [English](#-overview--概要) • [機能 / Features](#-主な機能--main-features) • [セットアップ / Setup](#️-インストールと設定-セルフホスト--installation-and-setup-self-hosting)

</div>

---

### 🤖 Botをあなたのサーバーに招待 / Invite Plana to Your Server

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ ここをクリックでPLANAをサーバーに招待する / Click here to invite PLANA ⬅️</strong>
  </a>
</h3>

*   セルフホストを行う場合は、`config.yaml` にご自身のBotの招待URLを設定してください。
*   If you are self-hosting, please set your bot's invitation URL in `config.yaml`.

### 💬 サポート / Support
*   Botの操作に関する質問や不具合報告は、`/support` コマンドで表示される連絡先までお願いします。
*   For questions or bug reports, please use the contact information displayed with the `/support` command.

---

## 📖 概要 / Overview

**llmcord-JP-PLANA** (通称: **PLANA**) は、[llmcord](https://github.com/jakobdylanc/llmcord) を基盤として開発された多機能Discordボットです。大規模言語モデル (LLM) との対話、高機能な音楽再生、画像認識、リアルタイム通知、エンターテイメント機能、そして便利なサーバーユーティリティを提供します。OpenAI互換APIに対応しており、リモートホスト型やローカルホスト型など、ほぼすべてのLLMと連携可能です。

**llmcord-JP-PLANA** (commonly known as **PLANA**) is a multi-functional Discord bot developed based on [llmcord](https://github.com/jakobdylanc/llmcord). It offers conversations with Large Language Models (LLMs), high-fidelity music playback, image recognition, real-time notifications, entertainment features, and useful server utilities. It supports OpenAI-compatible APIs, allowing integration with almost all LLMs, including remotely hosted and locally hosted ones.

### 🔒 プライバシー保護設計 / Privacy-Focused Design

**PLANAは特権インテント（Message Content Intent）を使用していません。**
そのため、以下のメッセージ**のみ**を取得します：

- PLANAへの@メンション
- PLANAのメッセージへの返信

**それ以外のサーバー内メッセージは一切取得・保存されません。**

**PLANA does not use the Message Content Intent (Privileged Intent).**
Therefore, it **only** collects the following messages:

- Messages that @mention PLANA
- Replies to PLANA's messages

**No other messages in the server are collected or stored.**

---

## ✨ 主な機能 / Main Features

### 🗣️ AIとの対話 (LLM) / AI Chat (LLM)
Botにメンション (`@PLANA`) を付けて話しかけるか、Botのメッセージに返信することで、AIとの会話が始まります。

Start a conversation with the AI by mentioning the bot (`@PLANA`) or replying to one of its messages.

*   **継続的な会話 / Continuous Conversations:** 返信を続けることで文脈を維持した会話が可能です。Context-aware conversations by continuing to reply.
*   **画像認識 / Image Recognition:** メッセージと一緒に画像を添付すると、AIが画像の内容も理解しようとします (ビジョンモデル対応の場合)。Attach images with your message, and the AI will attempt to understand the image content (if the model supports vision).
*   **ツール利用 (ウェブ検索) / Tool Use (Web Search):** AIが必要と判断した場合、インターネットで情報を検索して応答に利用します (Google Custom Search APIキーが必要です)。The AI can search the internet for information when needed (requires Google Custom Search API key).
*   **会話履歴の管理 / Conversation History Management:** `/clear_history` コマンドで現在のチャンネルの会話履歴をリセットできます。Reset conversation history for the current channel with `/clear_history`.
*   **カスタマイズ可能なAIパーソナリティ / Customizable AI Personality:** `config.yaml` のシステムプロンプトを編集することで、AIの基本的な性格や応答スタイルを自由に変更できます。Customize the AI's personality and response style by editing the system prompt in `config.yaml`.

<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

#### 🧠 パーソナリティ & 記憶機能 / Personality & Memory Features
PLANAは、会話をより豊かにするための高度なパーソナリティ設定と記憶機能を備えています。

PLANA features advanced personality settings and memory functions to enrich conversations.

*   **チャンネル別AIパーソナリティ (AI Bio) / Per-Channel AI Personality (AI Bio):**
    
    チャンネルごとにAIの性格や役割を個別に設定できます。例えば、あるチャンネルでは「猫になりきって話すAI」、別のチャンネルでは「専門的な技術サポートAI」として振る舞わせることが可能です。
    
    Set unique AI personalities and roles for each channel. For example, one channel can have an "AI that talks like a cat," while another has a "professional technical support AI."
    
    *   `/set-ai-bio [bio]`: チャンネルのAIパーソナリティを設定します。Set the AI personality for the channel.
    *   `/show-ai-bio`: 現在のAIパーソナリティ設定を表示します。Display the current AI personality settings.
    *   `/reset-ai-bio`: 設定をデフォルトに戻します。Reset settings to default.

*   **ユーザー別記憶 (User Bio) / Per-User Memory (User Bio):**
    
    AIはユーザー一人ひとりの情報を記憶できます。「私の名前は〇〇です」と教えたり、`/set-user-bio` コマンドを使ったりすることで、AIはあなたの名前や好みを覚え、以降の会話でそれを活用します。この情報はサーバーをまたいで保持されます。
    
    The AI can remember information about each user. Tell it "My name is XX" or use the `/set-user-bio` command, and the AI will remember your name and preferences for future conversations. This information persists across servers.
    
    *   `/set-user-bio [bio] [mode]`: あなたの情報をAIに記憶させます（上書き/追記モードあり）。Save your information to the AI's memory (with overwrite/append modes).
    *   `/show-user-bio`: AIが記憶しているあなたの情報を表示します。Display the information the AI has stored about you.
    *   `/reset-user-bio`: あなたの情報をAIの記憶から削除します。Delete your information from the AI's memory.

*   **グローバル共有メモリ / Global Shared Memory:**
    
    Botが参加している全てのサーバーで共有される情報を記憶させることができます。Bot全体で共有したいルールや、開発者からのお知らせなどを保存するのに便利です。
    
    Store information that is shared across all servers where the bot is present. Useful for bot-wide rules or announcements from developers.
    
    *   `/memory-save [key] [value]`: グローバルメモリに情報を保存します。Save information to global memory.
    *   `/memory-list`: 保存されている全ての情報を一覧表示します。List all stored information.
    *   `/memory-delete [key]`: 指定した情報を削除します。Delete specified information.

*   **モデル切り替え / Model Switching:**
    
    チャンネルごとに使用するAIモデルを柔軟に変更できます。会話の目的に合わせて、最適なモデル（例: 高性能モデル、高速応答モデルなど）を使い分けることが可能です。
    
    Flexibly switch AI models per channel. Choose the optimal model (e.g., high-performance model, fast-response model) based on the conversation purpose.
    
    *   `/switch-models [model]`: 利用可能なモデルリストから選択して切り替えます。Switch models by selecting from the available model list.
    *   `/switch-models-default-server`: モデルをサーバーのデフォルト設定に戻します。Reset the model to the server's default settings.

### 🎶 高機能な音楽再生 / Advanced Music Playback
ボイスチャンネルで高品質な音楽を楽しめます。

Enjoy high-quality music in your voice channels.

*   **多彩なソースに対応 / Various Sources:** YouTube, SoundCloudなどのURLや検索クエリから音楽を再生・キューに追加できます。Play music from YouTube, SoundCloud URLs, or search queries.
*   **再生コントロール / Playback Controls:** `/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/volume` など、直感的なコマンドで操作できます。Intuitive commands like `/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/volume`.
*   **高度なキュー管理 / Advanced Queue Management:** `/queue` でのキュー表示、`/shuffle` でのシャッフル、`/remove` での個別削除、`/clear` での全削除が可能です。View queue with `/queue`, shuffle with `/shuffle`, remove individual songs with `/remove`, clear all with `/clear`.
*   **ループ再生 / Loop Modes:** `/loop` コマンドで、ループなし・1曲ループ・キュー全体のループを切り替えられます。Toggle between no loop, single track loop, or full queue loop with `/loop`.
*   **シーク機能 / Seek Function:** `/seek` コマンドで再生位置を自由に移動できます。Jump to any position in the track with `/seek`.
*   **自動管理 / Automatic Management:** ボイスチャンネルに誰もいなくなると自動で退出するなど、リソースを効率的に管理します。Automatically leaves voice channels when empty, efficiently managing resources.

### 🎮 ゲーム & エンターテイメント / Games & Entertainment
*   **/akinator:** あの有名な魔人アキネーターと、キャラクター当てゲームで遊べます。多言語対応。Play the famous character guessing game with Akinator. Multi-language support.
*   **/gacha:** ブルーアーカイブ風の生徒募集（ガチャ）をシミュレートできます。Simulate Blue Archive-style student recruitment (gacha).
*   **/meow:** TheCatAPIからランダムで可愛い猫の画像を表示します。Display random cute cat pictures from TheCatAPI.
*   **/yandere, /danbooru:** アニメ画像検索（NSFW専用チャンネル）。Anime image search (NSFW channels only).

### 🛠️ ユーティリティコマンド / Utility Commands
サーバー管理や情報確認に役立つ便利なスラッシュコマンドを提供します。

Provides useful slash commands for server management and information retrieval.

*   **/help, /llm_help:** Botの全機能のヘルプとAI利用ガイドラインを表示します。Display comprehensive help and AI usage guidelines.
*   **/ping:** Botの現在の応答速度（レイテンシ）を表示します。Display the bot's current response time (latency).
*   **/serverinfo:** サーバーの詳細情報を表示します。Display detailed server information.
*   **/userinfo [user]:** ユーザー情報を表示します。Display user information.
*   **/avatar [user]:** ユーザーのアバター画像を高画質で表示します。Display a user's avatar in high quality.
*   **/invite:** Botの招待リンクを表示します。Display bot invitation link.
*   **/support:** 開発者への連絡方法を表示します。Display contact information for the developer.
*   **/roll, /check, /diceroll:** ダイスロール機能。Dice rolling features.
*   **/timer:** タイマー機能。Timer feature.

### 📥 メディアダウンロード / Media Downloader
YouTubeなどのサイトから動画や音声をダウンロードし、一時的な共有リンクを生成します。

Downloads video or audio from sites like YouTube and generates a temporary shareable link.

*   **/ytdlp_video [query]:** 動画をダウンロードします（1080p以上対応）。Download videos (supports 1080p and above).
*   **/ytdlp_audio [query] [format]:** 音声を抽出してダウンロードします。Extract and download audio.

<p align="center">
 <img src="https://i.imgur.com/pigk6eH.png" alt="video downloader">
</p>

### 📡 通知機能 / Notification Features
*   **地震・津波速報（日本）/ Earthquake & Tsunami Alerts (Japan):**
    - P2P地震情報からリアルタイム受信（WebSocket）/ Real-time reception via WebSocket
    - 緊急地震速報（EEW）/ Earthquake Early Warning
    - 震度情報・津波警報 / Seismic intensity and tsunami warnings
    - 震源地マップ表示 / Epicenter map display

*   **Twitch配信通知 / Twitch Stream Notifications:**
    - 配信開始の自動通知 / Automatic stream start notifications
    - カスタムメッセージ設定 / Custom message settings
    - 複数チャンネル対応 / Multiple channel support

---

## ⚠️ AI利用ガイドラインと免責事項 / AI Usage Guidelines and Disclaimer

本BotのAI機能を利用する前に、以下のガイドラインを必ずお読みください。**Botの利用をもって、本ガイドラインに同意したものとみなします。**

Please read the following guidelines carefully before using the AI features of this bot. **By using the bot, you are deemed to have agreed to these guidelines.**

### 📋 利用規約 / Terms of Use

*   **データ入力に関する注意 / Precautions for Data Input:**
    
    **個人情報や機密情報を絶対に入力しないでください。** (例: 氏名, 住所, パスワード, NDA情報, 企業の内部情報)
    
    **Never input personal or confidential information.** (e.g., names, addresses, passwords, NDA-protected information, internal company data)

*   **生成物の利用に関する注意 / Precautions for Using Generated Output:**
    
    **AIの生成する情報には、不正確な内容や偏見が含まれる可能性があります。** 生成された内容は参考情報として扱い、**必ずご自身でファクトチェックを行ってください。**
    
    **Information generated by the AI may be inaccurate or contain biases.** Treat the output as a reference and **always perform your own fact-checking.**
    
    生成物を利用した結果生じたいかなる損害についても、開発者は責任を負いません。利用は**自己責任**でお願いします。
    
    The developers are not liable for any damages resulting from the use of the generated content. Use is at your **own risk**.

---

## 🔐 プライバシーポリシー / Privacy Policy

### 📊 収集するデータ / Data Collection

PLANAは以下のデータのみを収集・処理します：

PLANA only collects and processes the following data:

1. **PLANAに送信されたメッセージ / Messages Sent to PLANA**
   - @メンションされたメッセージ / Messages with @mentions
   - PLANAのメッセージへの返信 / Replies to PLANA's messages
   - **それ以外のメッセージは収集していません** / **No other messages are collected**

2. **ユーザー設定データ / User Settings Data**
   - `/set-user-bio` で登録した情報 / Information registered via `/set-user-bio`
   - `/memory-save` で保存した情報 / Information saved via `/memory-save`
   - 通知設定 / Notification settings

3. **技術情報 / Technical Information**
   - コマンド実行ログ / Command execution logs
   - エラーログ / Error logs

### 🎯 データの利用目的 / Purpose of Data Use

- **サービス提供 / Service Provision:** AI対話、音楽再生、通知機能の実行 / Execution of AI chat, music playback, and notification features
- **デバッグ / Debugging:** エラー修正と機能改善 / Error correction and feature improvements
- **統計 / Statistics:** 使用状況の把握（匿名化済み）/ Understanding usage patterns (anonymized)

### 🔒 匿名化処理 / Anonymization Process

PLANAに送信されたメッセージは、以下の匿名化処理を施した上でデバッグに利用される場合があります：

Messages sent to PLANA may be used for debugging after the following anonymization processes:

- ユーザーID、サーバーIDの削除 / Removal of user IDs and server IDs
- 個人を特定できる情報の削除 / Removal of personally identifiable information
- 統計データへの変換 / Conversion to statistical data

### ⏱️ データの保存期間 / Data Retention Period

- **会話履歴 / Conversation History:** セッション中のみ（Bot再起動で削除）/ Session only (deleted on bot restart)
- **ユーザー設定 / User Settings:** 明示的に削除されるまで / Until explicitly deleted
- **ログファイル / Log Files:** 最大30日間 / Maximum 30 days

### 🗑️ データの削除 / Data Deletion

以下のコマンドでいつでもデータを削除できます：

You can delete your data at any time using the following commands:

- `/reset-user-bio` - ユーザー情報の削除 / Delete user information
- `/memory-delete <キー>` - メモリの削除 / Delete memory entries

### 🤝 第三者への提供 / Third-Party Disclosure

収集したデータは以下の場合を除き第三者に提供しません：

Collected data will not be provided to third parties except in the following cases:

- ユーザーの同意がある場合 / When user consent is obtained
- 法令に基づく開示が必要な場合 / When disclosure is required by law

---

## ⚙️ インストールと設定 (セルフホスト) / Installation and Setup (Self-Hosting)

### 前提条件 / Prerequisites
*   Python 3.8以上 / Python 3.8 or higher
*   Git
*   FFmpeg (音楽機能に必要 / Required for music features)
*   Docker & Docker Compose (任意、推奨 / Optional, Recommended)

### 手順1：基本設定 / Step 1: Basic Setup

1.  **リポジトリをクローンします / Clone the repository:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **`config.yaml` の設定 / Configure `config.yaml`:**
    
    `config.default.yaml` をコピーして `config.yaml` を作成します。
    
    Copy `config.default.yaml` to create `config.yaml`.
    
    生成された `config.yaml` を開き、**最低限以下の項目を設定してください。**
    
    Open the generated `config.yaml` and **configure at least the following settings:**

    *   `token`: **必須。** Discord Bot Token
    *   `llm:` セクション: `model`, `providers` (APIキーなど) / LLM settings

### 手順2：追加機能のセットアップ (任意) / Step 2: Setup for Additional Features (Optional)

#### Twitch通知機能の設定 / Twitch Notification Setup

1.  **Twitch APIキーを取得 / Get Twitch API keys:**
    - [Twitch Developer Console](https://dev.twitch.tv/console)
    - Category: `Chat Bot`
    - OAuth Redirect URLs: `http://localhost`

2.  **`config.yaml` に追記 / Add to `config.yaml`:**
    ```yaml
    twitch:
      client_id: "YOUR_TWITCH_CLIENT_ID"
      client_secret: "YOUR_TWITCH_CLIENT_SECRET"
    ```

#### メディアダウンロード機能の設定 / Media Downloader Setup

1.  **Google Drive API設定 / Google Drive API Setup:**
    - [Google Cloud Console](https://console.cloud.google.com/)
    - Google Drive APIを有効化 / Enable Google Drive API
    - OAuth Client ID (デスクトップアプリ) を作成 / Create OAuth Client ID (Desktop app)
    - `client_secrets.json` をダウンロード / Download `client_secrets.json`

2.  **フォルダIDの設定 / Set Folder ID:**
    - Google Driveにフォルダを作成 / Create a folder in Google Drive
    - `PLANA/downloader/ytdlp_downloader_cog.py` の `GDRIVE_FOLDER_ID` を編集 / Edit `GDRIVE_FOLDER_ID`

### 手順3：Botの起動 / Step 3: Start the Bot

#### 🚀 Windows (簡単) / Windows (Easy)
```bash
# start_plana.bat をダブルクリック
# Double-click start_plana.bat
```

#### 💻 標準的な方法 / Standard Method
```bash
pip install -r requirements.txt
python main.py
```

#### 🐳 Docker (推奨) / Docker (Recommended)
```bash
docker compose up --build -d
```

---

## 🛡️ セキュリティ / Security

### 脆弱性の報告 / Reporting Vulnerabilities

セキュリティ上の問題を発見した場合は、公開せずに以下に連絡してください：

If you discover a security issue, please contact us privately:

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)

---

## 📜 ライセンス / License

このプロジェクトはMITライセンスの下で公開されています。

This project is released under the MIT License.

---

## 🤝 コントリビューション / Contributing

プルリクエストを歓迎します！大きな変更の場合は、まずIssueで議論してください。

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## 📞 サポート / Support

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin399)
- GitHub Issues: [Issues](https://github.com/coffin399/llmcord-JP-plana/issues)

---

## 🙏 謝辞 / Acknowledgments

- [llmcord](https://github.com/jakobdylanc/llmcord) - 元となったプロジェクト / Original project base
- [discord.py](https://github.com/Rapptz/discord.py) - Discord APIラッパー / Discord API wrapper
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 動画ダウンローダー / Video downloader
- [P2P地震情報](https://www.p2pquake.net/) - 地震速報API / Earthquake alert API
- [TheCatAPI](https://thecatapi.com/) - 猫画像API / Cat image API

---

<div align="center">

**Dev by ごみぃ(coffin299) & えんじょ(Autmn134F)**

[⬆ トップへ戻る / Back to Top](#plana)

</div>