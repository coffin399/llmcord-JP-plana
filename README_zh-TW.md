<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  來和Plana聊天吧！
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![](https://coffin399.github.io/coffin299page/assets/badge.svg)](https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot)
[![Discord](https://img.shields.io/discord/1305004687921250436?logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/YyjyfXunaD)</div>


**語言 / Languages:** [日本語](README_ja.md) | [English](README_en.md) | [中文](README_zh.md) | [繁體中文](README_zh-TW.md) | [한국어](README_ko.md)

[概述](#-概述) • [功能](#-主要功能) • [安裝配置](#️-安裝與配置-自架設)

</div>

---

### 🤖 邀請Bot到您的伺服器

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ 點擊此處邀請PLANA ⬅️</strong>
  </a>
</h3>

*   如果您進行自架設，請在 `config.yaml` 中設定您自己的Bot邀請URL。

### 💬 支援
*   如有操作問題或錯誤回報，請使用 `/support` 指令顯示的聯絡方式。

---

## 📖 概述

**llmcord-JP-PLANA**（通稱：**PLANA**）是基於 [llmcord](https://github.com/jakobdylanc/llmcord) 開發的多功能Discord機器人。提供與大型語言模型（LLM）的對話、高級音樂播放、圖像識別、即時通知、娛樂功能以及實用的伺服器工具。支援OpenAI相容API，可與幾乎所有LLM整合，包括遠端託管和本地託管。

### 🔒 隱私保護設計

**PLANA不使用特權意圖（Message Content Intent）。**
因此，僅獲取以下訊息：

- @提及PLANA的訊息
- 對PLANA訊息的回覆

**不會獲取或儲存伺服器中的其他任何訊息。**

---

## ✨ 主要功能

### 🗣️ AI對話 (LLM)
透過提及機器人（`@PLANA`）或回覆機器人的訊息來開始與AI的對話。

*   **持續對話：** 透過持續回覆保持上下文的對話。
*   **圖像識別：** 在訊息中附加圖片，AI將嘗試理解圖像內容（如果模型支援視覺）。
*   **工具使用（網路搜尋）：** 當AI判斷需要時，會在網際網路上搜尋資訊並用於回應（需要Google Custom Search API金鑰）。
*   **對話歷史管理：** 使用 `/clear_history` 指令重置當前頻道的對話歷史。
*   **可自訂的AI個性：** 透過編輯 `config.yaml` 中的系統提示詞，自由更改AI的基本性格和回應風格。

<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

#### 🧠 個性與記憶功能
PLANA具有進階個性設定和記憶功能，使對話更加豐富。

*   **按頻道設定AI個性（AI Bio）：**
    
    可以為每個頻道單獨設定AI的性格和角色。例如，一個頻道可以有「像貓一樣說話的AI」，另一個頻道可以有「專業技術支援AI」。
    
    *   `/set-ai-bio [bio]`: 設定頻道的AI個性。
    *   `/show-ai-bio`: 顯示當前AI個性設定。
    *   `/reset-ai-bio`: 重置為預設設定。

*   **按使用者記憶（User Bio）：**
    
    AI可以記住每個使用者的資訊。告訴它「我的名字是XX」或使用 `/set-user-bio` 指令，AI將記住您的名字和偏好，並在以後的對話中使用。此資訊在伺服器之間保持。
    
    *   `/set-user-bio [bio] [mode]`: 將您的資訊儲存到AI的記憶中（有覆蓋/附加模式）。
    *   `/show-user-bio`: 顯示AI儲存的關於您的資訊。
    *   `/reset-user-bio`: 從AI的記憶中刪除您的資訊。

*   **全域共享記憶體：**
    
    可以儲存在機器人所在的所有伺服器中共享的資訊。便於儲存機器人範圍的規則或開發者的公告。
    
    *   `/memory-save [key] [value]`: 將資訊儲存到全域記憶體。
    *   `/memory-list`: 列出所有儲存的資訊。
    *   `/memory-delete [key]`: 刪除指定的資訊。

*   **模型切換：**
    
    可以靈活地為每個頻道更改使用的AI模型。根據對話目的，可以使用最佳模型（例如：高效能模型、快速回應模型等）。
    
    *   `/switch-models [model]`: 從可用模型清單中選擇切換。
    *   `/switch-models-default-server`: 將模型重置為伺服器的預設設定。

### 🎶 高級音樂播放
在語音頻道中享受高品質音樂。

*   **支援多種來源：** 可以播放來自YouTube、SoundCloud URL或搜尋查詢的音樂。
*   **播放控制：** 使用直觀的指令進行操作，如 `/play`、`/pause`、`/resume`、`/stop`、`/skip`、`/volume`。
*   **高級佇列管理：** 使用 `/queue` 查看佇列，`/shuffle` 隨機播放，`/remove` 刪除單曲，`/clear` 清空全部。
*   **循環播放：** 使用 `/loop` 指令在無循環、單曲循環和佇列循環之間切換。
*   **跳轉功能：** 使用 `/seek` 指令自由移動播放位置。
*   **自動管理：** 當語音頻道無人時自動退出，高效管理資源。

### 🎮 遊戲與娛樂
*   **/akinator:** 與著名的阿基納特玩角色猜謎遊戲。支援多語言。
*   **/gacha:** 模擬碧藍檔案風格的學生招募（抽卡）。
*   **/meow:** 從TheCatAPI顯示隨機可愛貓咪圖片。
*   **/yandere, /danbooru:** 動漫圖片搜尋（僅限NSFW頻道）。

### 🛠️ 實用指令
提供用於伺服器管理和資訊檢索的實用斜線指令。

*   **/help, /llm_help:** 顯示全面的說明和AI使用指南。
*   **/ping:** 顯示機器人的當前回應時間（延遲）。
*   **/serverinfo:** 顯示詳細的伺服器資訊。
*   **/userinfo [user]:** 顯示使用者資訊。
*   **/avatar [user]:** 以高品質顯示使用者的頭像。
*   **/invite:** 顯示機器人邀請連結。
*   **/support:** 顯示開發者的聯絡資訊。
*   **/roll, /check, /diceroll:** 擲骰子功能。
*   **/timer:** 計時器功能。

### 📥 媒體下載器
從YouTube等網站下載影片或音訊，並產生臨時共享連結。

*   **/ytdlp_video [query]:** 下載影片（支援1080p及以上）。
*   **/ytdlp_audio [query] [format]:** 提取並下載音訊。

<p align="center">
 <img src="https://i.imgur.com/pigk6eH.png" alt="video downloader">
</p>

### 📡 通知功能
*   **地震與海嘯警報（日本）：**
    - 透過P2P地震資訊即時接收（WebSocket）
    - 緊急地震速報（EEW）
    - 震度資訊和海嘯警報
    - 震源地圖顯示

*   **Twitch直播通知：**
    - 直播開始自動通知
    - 自訂訊息設定
    - 支援多個頻道

---

## ⚠️ AI使用指南和免責聲明

使用本Bot的AI功能之前，請務必閱讀以下指南。**使用Bot即表示您同意這些指南。**

### 📋 使用條款

*   **關於資料輸入的注意事項：**
    
    **請勿輸入個人資訊或機密資訊。**（例如：姓名、地址、密碼、NDA資訊、公司內部資訊）

*   **關於使用生成內容的注意事項：**
    
    **AI生成的資訊可能不準確或包含偏見。** 將生成的內容作為參考資訊，**務必自行進行事實查核。**
    
    開發者對使用生成內容導致的任何損害不承擔責任。使用風險**自負**。

---

## 🔐 隱私政策

### 📊 收集的資料

PLANA僅收集和處理以下資料：

1. **傳送給PLANA的訊息**
   - 帶有@提及的訊息
   - 對PLANA訊息的回覆
   - **不收集其他訊息**

2. **使用者設定資料**
   - 透過 `/set-user-bio` 註冊的資訊
   - 透過 `/memory-save` 儲存的資訊
   - 通知設定

3. **技術資訊**
   - 指令執行日誌
   - 錯誤日誌

### 🎯 資料使用目的

- **服務提供：** 執行AI對話、音樂播放和通知功能
- **偵錯：** 錯誤修正和功能改進
- **統計：** 瞭解使用模式（匿名化）

### 🔒 匿名化處理

傳送給PLANA的訊息在以下匿名化處理後可能用於偵錯：

- 刪除使用者ID和伺服器ID
- 刪除可識別個人的資訊
- 轉換為統計資料

### ⏱️ 資料保留期限

- **對話歷史：** 僅在會話期間（Bot重新啟動時刪除）
- **使用者設定：** 直到明確刪除

---

## ⚙️ 安裝與配置（自架設）

### 前置條件
*   Python 3.8或更高版本
*   Git
*   FFmpeg（音樂功能所需）
*   Docker & Docker Compose（可選，推薦）

### 步驟1：基本設定

1.  **複製儲存庫：**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **配置 `config.yaml`：**
    
    複製 `config.default.yaml` 建立 `config.yaml`。
    
    開啟產生的 `config.yaml` 並**至少配置以下設定：**

    *   `token`: **必需。** Discord Bot Token
    *   `llm:` 部分: `model`、`providers`（API金鑰等）

### 步驟2：附加功能設定（可選）

#### Twitch通知設定

1.  **取得Twitch API金鑰：**
    - [Twitch Developer Console](https://dev.twitch.tv/console)
    - Category: `Chat Bot`
    - OAuth Redirect URLs: `http://localhost`

2.  **新增到 `config.yaml`：**
    ```yaml
    twitch:
      client_id: "YOUR_TWITCH_CLIENT_ID"
      client_secret: "YOUR_TWITCH_CLIENT_SECRET"
    ```

#### 媒體下載器設定

1.  **Google Drive API設定：**
    - [Google Cloud Console](https://console.cloud.google.com/)
    - 啟用Google Drive API
    - 建立OAuth Client ID（桌面應用程式）
    - 下載 `client_secrets.json`

2.  **設定資料夾ID：**
    - 在Google Drive中建立資料夾
    - 編輯 `PLANA/downloader/ytdlp_downloader_cog.py` 中的 `GDRIVE_FOLDER_ID`

### 步驟3：啟動Bot

#### 🚀 Windows（簡單）
```bash
# 雙擊 startPLANA.bat
```

#### 💻 標準方法
```bash
pip install -r requirements.txt
python main.py
```

#### 🐳 Docker（推薦）
```bash
docker compose up --build -d
```

---

## 🛡️ 安全性

### 回報漏洞

如果發現安全性問題，請私下聯絡我們：

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)

---

## 📜 授權

本專案根據MIT授權發布。

---

## 🤝 貢獻

歡迎提交拉取請求！對於重大變更，請先開啟一個issue進行討論。

---

## 📞 支援

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin399)
- GitHub Issues: [Issues](https://github.com/coffin399/llmcord-JP-plana/issues)

---

## 🙏 致謝

- [llmcord](https://github.com/jakobdylanc/llmcord) - 原始專案基礎
- [discord.py](https://github.com/Rapptz/discord.py) - Discord API包裝器
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 影片下載器
- [P2P地震情報](https://www.p2pquake.net/) - 地震警報API
- [TheCatAPI](https://thecatapi.com/) - 貓咪圖片API

---

<div align="center">

**Dev by ごみぃ(coffin299) & えんじょ(Autmn134F)**

</div>