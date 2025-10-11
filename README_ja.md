<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  プラナちゃんとおしゃべりしよう！
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![](https://coffin399.github.io/coffin299page/assets/badge.svg)

**言語 / Languages:** [日本語](README_ja.md) | [English](README_en.md) | [中文](README_zh.md) | [繁體中文](README_zh-TW.md) | [한국어](README_ko.md)

[概要](#-概要) • [機能](#-主な機能) • [セットアップ](#️-インストールと設定-セルフホスト)

</div>

---

### 🤖 Botをあなたのサーバーに招待

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ ここをクリックでPLANAをサーバーに招待する ⬅️</strong>
  </a>
</h3>

*   セルフホストを行う場合は、`config.yaml` にご自身のBotの招待URLを設定してください。

### 💬 サポート
*   Botの操作に関する質問や不具合報告は、`/support` コマンドで表示される連絡先までお願いします。

---

## 📖 概要

**llmcord-JP-PLANA** (通称: **PLANA**) は、[llmcord](https://github.com/jakobdylanc/llmcord) を基盤として開発された多機能Discordボットです。大規模言語モデル (LLM) との対話、高機能な音楽再生、画像認識、リアルタイム通知、エンターテイメント機能、そして便利なサーバーユーティリティを提供します。OpenAI互換APIに対応しており、リモートホスト型やローカルホスト型など、ほぼすべてのLLMと連携可能です。

### 🔒 プライバシー保護設計

**PLANAは特権インテント（Message Content Intent）を使用していません。**
そのため、以下のメッセージ**のみ**を取得します：

- PLANAへの@メンション
- PLANAのメッセージへの返信

**それ以外のサーバー内メッセージは一切取得・保存されません。**

---

## ✨ 主な機能

### 🗣️ AIとの対話 (LLM)
Botにメンション (`@PLANA`) を付けて話しかけるか、Botのメッセージに返信することで、AIとの会話が始まります。

*   **継続的な会話:** 返信を続けることで文脈を維持した会話が可能です。
*   **画像認識:** メッセージと一緒に画像を添付すると、AIが画像の内容も理解しようとします (ビジョンモデル対応の場合)。
*   **ツール利用 (ウェブ検索):** AIが必要と判断した場合、インターネットで情報を検索して応答に利用します (Google Custom Search APIキーが必要です)。
*   **会話履歴の管理:** `/clear_history` コマンドで現在のチャンネルの会話履歴をリセットできます。
*   **カスタマイズ可能なAIパーソナリティ:** `config.yaml` のシステムプロンプトを編集することで、AIの基本的な性格や応答スタイルを自由に変更できます。

<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

#### 🧠 パーソナリティ & 記憶機能
PLANAは、会話をより豊かにするための高度なパーソナリティ設定と記憶機能を備えています。

*   **チャンネル別AIパーソナリティ (AI Bio):**
    
    チャンネルごとにAIの性格や役割を個別に設定できます。例えば、あるチャンネルでは「猫になりきって話すAI」、別のチャンネルでは「専門的な技術サポートAI」として振る舞わせることが可能です。
    
    *   `/set-ai-bio [bio]`: チャンネルのAIパーソナリティを設定します。
    *   `/show-ai-bio`: 現在のAIパーソナリティ設定を表示します。
    *   `/reset-ai-bio`: 設定をデフォルトに戻します。

*   **ユーザー別記憶 (User Bio):**
    
    AIはユーザー一人ひとりの情報を記憶できます。「私の名前は〇〇です」と教えたり、`/set-user-bio` コマンドを使ったりすることで、AIはあなたの名前や好みを覚え、以降の会話でそれを活用します。この情報はサーバーをまたいで保持されます。
    
    *   `/set-user-bio [bio] [mode]`: あなたの情報をAIに記憶させます（上書き/追記モードあり）。
    *   `/show-user-bio`: AIが記憶しているあなたの情報を表示します。
    *   `/reset-user-bio`: あなたの情報をAIの記憶から削除します。

*   **グローバル共有メモリ:**
    
    Botが参加している全てのサーバーで共有される情報を記憶させることができます。Bot全体で共有したいルールや、開発者からのお知らせなどを保存するのに便利です。
    
    *   `/memory-save [key] [value]`: グローバルメモリに情報を保存します。
    *   `/memory-list`: 保存されている全ての情報を一覧表示します。
    *   `/memory-delete [key]`: 指定した情報を削除します。

*   **モデル切り替え:**
    
    チャンネルごとに使用するAIモデルを柔軟に変更できます。会話の目的に合わせて、最適なモデル（例: 高性能モデル、高速応答モデルなど）を使い分けることが可能です。
    
    *   `/switch-models [model]`: 利用可能なモデルリストから選択して切り替えます。
    *   `/switch-models-default-server`: モデルをサーバーのデフォルト設定に戻します。

### 🎶 高機能な音楽再生
ボイスチャンネルで高品質な音楽を楽しめます。

*   **多彩なソースに対応:** YouTube, SoundCloudなどのURLや検索クエリから音楽を再生・キューに追加できます。
*   **再生コントロール:** `/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/volume` など、直感的なコマンドで操作できます。
*   **高度なキュー管理:** `/queue` でのキュー表示、`/shuffle` でのシャッフル、`/remove` での個別削除、`/clear` での全削除が可能です。
*   **ループ再生:** `/loop` コマンドで、ループなし・1曲ループ・キュー全体のループを切り替えられます。
*   **シーク機能:** `/seek` コマンドで再生位置を自由に移動できます。
*   **自動管理:** ボイスチャンネルに誰もいなくなると自動で退出するなど、リソースを効率的に管理します。

### 🎮 ゲーム & エンターテイメント
*   **/akinator:** あの有名な魔人アキネーターと、キャラクター当てゲームで遊べます。多言語対応。
*   **/gacha:** ブルーアーカイブ風の生徒募集（ガチャ）をシミュレートできます。
*   **/meow:** TheCatAPIからランダムで可愛い猫の画像を表示します。
*   **/yandere, /danbooru:** アニメ画像検索（NSFW専用チャンネル）。

### 🛠️ ユーティリティコマンド
サーバー管理や情報確認に役立つ便利なスラッシュコマンドを提供します。

*   **/help, /llm_help:** Botの全機能のヘルプとAI利用ガイドラインを表示します。
*   **/ping:** Botの現在の応答速度（レイテンシ）を表示します。
*   **/serverinfo:** サーバーの詳細情報を表示します。
*   **/userinfo [user]:** ユーザー情報を表示します。
*   **/avatar [user]:** ユーザーのアバター画像を高画質で表示します。
*   **/invite:** Botの招待リンクを表示します。
*   **/support:** 開発者への連絡方法を表示します。
*   **/roll, /check, /diceroll:** ダイスロール機能。
*   **/timer:** タイマー機能。

### 📥 メディアダウンロード
YouTubeなどのサイトから動画や音声をダウンロードし、一時的な共有リンクを生成します。

*   **/ytdlp_video [query]:** 動画をダウンロードします（1080p以上対応）。
*   **/ytdlp_audio [query] [format]:** 音声を抽出してダウンロードします。

<p align="center">
 <img src="https://i.imgur.com/pigk6eH.png" alt="video downloader">
</p>

### 📡 通知機能
*   **地震・津波速報（日本）:**
    - P2P地震情報からリアルタイム受信（WebSocket）
    - 緊急地震速報（EEW）
    - 震度情報・津波警報
    - 震源地マップ表示

*   **Twitch配信通知:**
    - 配信開始の自動通知
    - カスタムメッセージ設定
    - 複数チャンネル対応

---

## ⚠️ AI利用ガイドラインと免責事項

本BotのAI機能を利用する前に、以下のガイドラインを必ずお読みください。**Botの利用をもって、本ガイドラインに同意したものとみなします。**

### 📋 利用規約

*   **データ入力に関する注意:**
    
    **個人情報や機密情報を絶対に入力しないでください。** (例: 氏名, 住所, パスワード, NDA情報, 企業の内部情報)

*   **生成物の利用に関する注意:**
    
    **AIの生成する情報には、不正確な内容や偏見が含まれる可能性があります。** 生成された内容は参考情報として扱い、**必ずご自身でファクトチェックを行ってください。**
    
    生成物を利用した結果生じたいかなる損害についても、開発者は責任を負いません。利用は**自己責任**でお願いします。

---

## 🔐 プライバシーポリシー

### 📊 収集するデータ

PLANAは以下のデータのみを収集・処理します：

1. **PLANAに送信されたメッセージ**
   - @メンションされたメッセージ
   - PLANAのメッセージへの返信
   - **それ以外のメッセージは収集していません**

2. **ユーザー設定データ**
   - `/set-user-bio` で登録した情報
   - `/memory-save` で保存した情報
   - 通知設定

3. **技術情報**
   - コマンド実行ログ
   - エラーログ

### 🎯 データの利用目的

- **サービス提供:** AI対話、音楽再生、通知機能の実行
- **デバッグ:** エラー修正と機能改善
- **統計:** 使用状況の把握（匿名化済み）

### 🔒 匿名化処理

PLANAに送信されたメッセージは、以下の匿名化処理を施した上でデバッグに利用される場合があります：

- ユーザーID、サーバーIDの削除
- 個人を特定できる情報の削除
- 統計データへの変換

### ⏱️ データの保存期間

- **会話履歴:** セッション中のみ（Bot再起動で削除）
- **ユーザー設定:** 明示的に削除されるまで

---

## ⚙️ インストールと設定 (セルフホスト)

### 前提条件
*   Python 3.8以上
*   Git
*   FFmpeg (音楽機能に必要)
*   Docker & Docker Compose (任意、推奨)

### 手順1：基本設定

1.  **リポジトリをクローンします:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **`config.yaml` の設定:**
    
    `config.default.yaml` をコピーして `config.yaml` を作成します。
    
    生成された `config.yaml` を開き、**最低限以下の項目を設定してください。**

    *   `token`: **必須。** Discord Bot Token
    *   `llm:` セクション: `model`, `providers` (APIキーなど)

### 手順2：追加機能のセットアップ (任意)

#### Twitch通知機能の設定

1.  **Twitch APIキーを取得:**
    - [Twitch Developer Console](https://dev.twitch.tv/console)
    - Category: `Chat Bot`
    - OAuth Redirect URLs: `http://localhost`

2.  **`config.yaml` に追記:**
    ```yaml
    twitch:
      client_id: "YOUR_TWITCH_CLIENT_ID"
      client_secret: "YOUR_TWITCH_CLIENT_SECRET"
    ```

#### メディアダウンロード機能の設定

1.  **Google Drive API設定:**
    - [Google Cloud Console](https://console.cloud.google.com/)
    - Google Drive APIを有効化
    - OAuth Client ID (デスクトップアプリ) を作成
    - `client_secrets.json` をダウンロード

2.  **フォルダIDの設定:**
    - Google Driveにフォルダを作成
    - `PLANA/downloader/ytdlp_downloader_cog.py` の `GDRIVE_FOLDER_ID` を編集

### 手順3：Botの起動

#### 🚀 Windows (簡単)
```bash
# startPLANA.bat をダブルクリック
```

#### 💻 標準的な方法
```bash
pip install -r requirements.txt
python main.py
```

#### 🐳 Docker (推奨)
```bash
docker compose up --build -d
```

---

## 🛡️ セキュリティ

### 脆弱性の報告

セキュリティ上の問題を発見した場合は、公開せずに以下に連絡してください：

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)

---

## 📜 ライセンス

このプロジェクトはMITライセンスの下で公開されています。

---

## 🤝 コントリビューション

プルリクエストを歓迎します！大きな変更の場合は、まずIssueで議論してください。

---

## 📞 サポート

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin399)
- GitHub Issues: [Issues](https://github.com/coffin399/llmcord-JP-plana/issues)

---

## 🙏 謝辞

- [llmcord](https://github.com/jakobdylanc/llmcord) - 元となったプロジェクト
- [discord.py](https://github.com/Rapptz/discord.py) - Discord APIラッパー
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 動画ダウンローダー
- [P2P地震情報](https://www.p2pquake.net/) - 地震速報API
- [TheCatAPI](https://thecatapi.com/) - 猫画像API

---

<div align="center">

**Dev by ごみぃ(coffin299) & えんじょ(Autmn134F)**

</div>