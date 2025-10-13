<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  来和Plana聊天吧！
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


**语言 / Languages:** [日本語](README_ja.md) | [English](README_en.md) | [中文](README_zh.md) | [繁體中文](README_zh-TW.md) | [한국어](README_ko.md)

[概述](#-概述) • [功能](#-主要功能) • [安装配置](#️-安装与配置-自托管)

</div>

---

### 🤖 邀请Bot到您的服务器

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ 点击此处邀请PLANA ⬅️</strong>
  </a>
</h3>

*   如果您进行自托管，请在 `config.yaml` 中设置您自己的Bot邀请URL。

### 💬 支持
*   如有操作问题或错误报告，请使用 `/support` 命令显示的联系方式。

---

## 📖 概述

**llmcord-JP-PLANA**（通称：**PLANA**）是基于 [llmcord](https://github.com/jakobdylanc/llmcord) 开发的多功能Discord机器人。提供与大型语言模型（LLM）的对话、高级音乐播放、图像识别、实时通知、娱乐功能以及实用的服务器工具。支持OpenAI兼容API，可与几乎所有LLM集成，包括远程托管和本地托管。

### 🔒 隐私保护设计

**PLANA不使用特权意图（Message Content Intent）。**
因此，仅获取以下消息：

- @提及PLANA的消息
- 对PLANA消息的回复

**不会获取或存储服务器中的其他任何消息。**

---

## ✨ 主要功能

### 🗣️ AI对话 (LLM)
通过提及机器人（`@PLANA`）或回复机器人的消息来开始与AI的对话。

*   **持续对话：** 通过持续回复保持上下文的对话。
*   **图像识别：** 在消息中附加图片，AI将尝试理解图像内容（如果模型支持视觉）。
*   **工具使用（网络搜索）：** 当AI判断需要时，会在互联网上搜索信息并用于响应（需要Google Custom Search API密钥）。
*   **对话历史管理：** 使用 `/clear_history` 命令重置当前频道的对话历史。
*   **可自定义的AI个性：** 通过编辑 `config.yaml` 中的系统提示词，自由更改AI的基本性格和响应风格。

<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

#### 🧠 个性与记忆功能
PLANA具有高级个性设置和记忆功能，使对话更加丰富。

*   **按频道设置AI个性（AI Bio）：**
    
    可以为每个频道单独设置AI的性格和角色。例如，一个频道可以有"像猫一样说话的AI"，另一个频道可以有"专业技术支持AI"。
    
    *   `/set-ai-bio [bio]`: 设置频道的AI个性。
    *   `/show-ai-bio`: 显示当前AI个性设置。
    *   `/reset-ai-bio`: 重置为默认设置。

*   **按用户记忆（User Bio）：**
    
    AI可以记住每个用户的信息。告诉它"我的名字是XX"或使用 `/set-user-bio` 命令，AI将记住您的名字和偏好，并在以后的对话中使用。此信息在服务器之间保持。
    
    *   `/set-user-bio [bio] [mode]`: 将您的信息保存到AI的记忆中（有覆盖/追加模式）。
    *   `/show-user-bio`: 显示AI存储的关于您的信息。
    *   `/reset-user-bio`: 从AI的记忆中删除您的信息。

*   **全局共享内存：**
    
    可以存储在机器人所在的所有服务器中共享的信息。便于保存机器人范围的规则或开发者的公告。
    
    *   `/memory-save [key] [value]`: 将信息保存到全局内存。
    *   `/memory-list`: 列出所有存储的信息。
    *   `/memory-delete [key]`: 删除指定的信息。

*   **模型切换：**
    
    可以灵活地为每个频道更改使用的AI模型。根据对话目的，可以使用最佳模型（例如：高性能模型、快速响应模型等）。
    
    *   `/switch-models [model]`: 从可用模型列表中选择切换。
    *   `/switch-models-default-server`: 将模型重置为服务器的默认设置。

### 🎶 高级音乐播放
在语音频道中享受高质量音乐。

*   **支持多种来源：** 可以播放来自YouTube、SoundCloud URL或搜索查询的音乐。
*   **播放控制：** 使用直观的命令进行操作，如 `/play`、`/pause`、`/resume`、`/stop`、`/skip`、`/volume`。
*   **高级队列管理：** 使用 `/queue` 查看队列，`/shuffle` 随机播放，`/remove` 删除单曲，`/clear` 清空全部。
*   **循环播放：** 使用 `/loop` 命令在无循环、单曲循环和队列循环之间切换。
*   **跳转功能：** 使用 `/seek` 命令自由移动播放位置。
*   **自动管理：** 当语音频道无人时自动退出，高效管理资源。

### 🎮 游戏与娱乐
*   **/akinator:** 与著名的阿基纳特玩角色猜谜游戏。支持多语言。
*   **/gacha:** 模拟碧蓝档案风格的学生招募（抽卡）。
*   **/meow:** 从TheCatAPI显示随机可爱猫咪图片。
*   **/yandere, /danbooru:** 动漫图片搜索（仅限NSFW频道）。

### 🛠️ 实用命令
提供用于服务器管理和信息检索的实用斜杠命令。

*   **/help, /llm_help:** 显示全面的帮助和AI使用指南。
*   **/ping:** 显示机器人的当前响应时间（延迟）。
*   **/serverinfo:** 显示详细的服务器信息。
*   **/userinfo [user]:** 显示用户信息。
*   **/avatar [user]:** 以高质量显示用户的头像。
*   **/invite:** 显示机器人邀请链接。
*   **/support:** 显示开发者的联系信息。
*   **/roll, /check, /diceroll:** 掷骰子功能。
*   **/timer:** 计时器功能。

### 📥 媒体下载器
从YouTube等网站下载视频或音频，并生成临时共享链接。

*   **/ytdlp_video [query]:** 下载视频（支持1080p及以上）。
*   **/ytdlp_audio [query] [format]:** 提取并下载音频。

<p align="center">
 <img src="https://i.imgur.com/pigk6eH.png" alt="video downloader">
</p>

### 📡 通知功能
*   **地震与海啸警报（日本）：**
    - 通过P2P地震信息实时接收（WebSocket）
    - 紧急地震速报（EEW）
    - 震度信息和海啸警报
    - 震源地图显示

*   **Twitch直播通知：**
    - 直播开始自动通知
    - 自定义消息设置
    - 支持多个频道

---

## ⚠️ AI使用指南和免责声明

使用本Bot的AI功能之前，请务必阅读以下指南。**使用Bot即表示您同意这些指南。**

### 📋 使用条款

*   **关于数据输入的注意事项：**
    
    **请勿输入个人信息或机密信息。**（例如：姓名、地址、密码、NDA信息、公司内部信息）

*   **关于使用生成内容的注意事项：**
    
    **AI生成的信息可能不准确或包含偏见。** 将生成的内容作为参考信息，**务必自行进行事实核查。**
    
    开发者对使用生成内容导致的任何损害不承担责任。使用风险**自负**。

---

## 🔐 隐私政策

### 📊 收集的数据

PLANA仅收集和处理以下数据：

1. **发送给PLANA的消息**
   - 带有@提及的消息
   - 对PLANA消息的回复
   - **不收集其他消息**

2. **用户设置数据**
   - 通过 `/set-user-bio` 注册的信息
   - 通过 `/memory-save` 保存的信息
   - 通知设置

3. **技术信息**
   - 命令执行日志
   - 错误日志

### 🎯 数据使用目的

- **服务提供：** 执行AI对话、音乐播放和通知功能
- **调试：** 错误修正和功能改进
- **统计：** 了解使用模式（匿名化）

### 🔒 匿名化处理

发送给PLANA的消息在以下匿名化处理后可能用于调试：

- 删除用户ID和服务器ID
- 删除可识别个人的信息
- 转换为统计数据

### ⏱️ 数据保留期限

- **对话历史：** 仅在会话期间（Bot重启时删除）
- **用户设置：** 直到明确删除

---

## ⚙️ 安装与配置（自托管）

### 前置条件
*   Python 3.8或更高版本
*   Git
*   FFmpeg（音乐功能所需）
*   Docker & Docker Compose（可选，推荐）

### 步骤1：基本设置

1.  **克隆存储库：**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **配置 `config.yaml`：**
    
    复制 `config.default.yaml` 创建 `config.yaml`。
    
    打开生成的 `config.yaml` 并**至少配置以下设置：**

    *   `token`: **必需。** Discord Bot Token
    *   `llm:` 部分: `model`、`providers`（API密钥等）

### 步骤2：附加功能设置（可选）

#### Twitch通知设置

1.  **获取Twitch API密钥：**
    - [Twitch Developer Console](https://dev.twitch.tv/console)
    - Category: `Chat Bot`
    - OAuth Redirect URLs: `http://localhost`

2.  **添加到 `config.yaml`：**
    ```yaml
    twitch:
      client_id: "YOUR_TWITCH_CLIENT_ID"
      client_secret: "YOUR_TWITCH_CLIENT_SECRET"
    ```

#### 媒体下载器设置

1.  **Google Drive API设置：**
    - [Google Cloud Console](https://console.cloud.google.com/)
    - 启用Google Drive API
    - 创建OAuth Client ID（桌面应用）
    - 下载 `client_secrets.json`

2.  **设置文件夹ID：**
    - 在Google Drive中创建文件夹
    - 编辑 `PLANA/downloader/ytdlp_downloader_cog.py` 中的 `GDRIVE_FOLDER_ID`

### 步骤3：启动Bot

#### 🚀 Windows（简单）
```bash
# 双击 startPLANA.bat
```

#### 💻 标准方法
```bash
pip install -r requirements.txt
python main.py
```

#### 🐳 Docker（推荐）
```bash
docker compose up --build -d
```

---

## 🛡️ 安全

### 报告漏洞

如果发现安全问题，请私下联系我们：

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)

---

## 📜 许可证

本项目根据MIT许可证发布。

---

## 🤝 贡献

欢迎提交拉取请求！对于重大更改，请先打开一个issue进行讨论。

---

## 📞 支持

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin399)
- GitHub Issues: [Issues](https://github.com/coffin399/llmcord-JP-plana/issues)

---

## 🙏 致谢

- [llmcord](https://github.com/jakobdylanc/llmcord) - 原始项目基础
- [discord.py](https://github.com/Rapptz/discord.py) - Discord API包装器
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 视频下载器
- [P2P地震情報](https://www.p2pquake.net/) - 地震警报API
- [TheCatAPI](https://thecatapi.com/) - 猫咪图片API

---

<div align="center">

**Dev by ごみぃ(coffin299) & えんじょ(Autmn134F)**

</div>