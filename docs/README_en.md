<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  Let's chat with Plana!
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](../LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/coffin399/llmcord-JP-plana)
[![](https://coffin399.github.io/coffin299page/assets/badge.svg)](https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot)
[![Discord](https://img.shields.io/discord/1305004687921250436?logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/H79HKKqx3s)</div>


**Languages:** [日本語](README_ja.md) | [English](README_en.md) | [中文](README_zh.md) | [繁體中文](README_zh-TW.md) | [한국어](README_ko.md)

[Overview](#-overview) • [Features](#-main-features) • [Setup](#️-installation-and-setup-self-hosting)

</div>

---

### 🤖 Invite PLANA to Your Server

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ Click here to invite PLANA ⬅️</strong>
  </a>
</h3>

*   If you are self-hosting, please set your bot's invitation URL in `config.yaml`.

### 💬 Support
*   For questions or bug reports, please use the contact information displayed with the `/support` command.

---

## 📖 Overview

**llmcord-JP-PLANA** (commonly known as **PLANA**) is a multi-functional Discord bot developed based on [llmcord](https://github.com/jakobdylanc/llmcord). It offers conversations with Large Language Models (LLMs), high-fidelity music playback, image recognition, real-time notifications, entertainment features, and useful server utilities. It supports OpenAI-compatible APIs, allowing integration with almost all LLMs, including remotely hosted and locally hosted ones.

### 🔒 Privacy-Focused Design

**PLANA does not use the Message Content Intent (Privileged Intent).**
Therefore, it **only** collects the following messages:

- Messages that @mention PLANA
- Replies to PLANA's messages

**No other messages in the server are collected or stored.**

---

## ✨ Main Features

### 🤖 AI Chat (LLM)
Start a conversation with the AI by mentioning the bot (`@PLANA`) or replying to one of its messages.

*   **Multiple AI Models:** OpenAI GPT-4o, Google Gemini 2.5 Pro, NVIDIA NIM, Mistral, and more
*   **Continuous Conversations:** Context-aware conversations by continuing to reply.
*   **Image Recognition:** Attach images with your message, and the AI will attempt to understand the image content (if the model supports vision).
*   **Image Generation:** High-quality image generation using Stable Diffusion WebUI Forge
*   **Tool Use (Web Search):** The AI can search the internet for information when needed (requires Google Custom Search API key).
*   **Conversation History Management:** Reset conversation history for the current channel with `/clear_history`.
*   **Customizable AI Personality:** Customize the AI's personality and response style by editing the system prompt in `config.yaml`.
*   **Multi-language Support:** Automatically adapts to user's language

<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

#### 🧠 Personality & Memory Features
PLANA features advanced personality settings and memory functions to enrich conversations.

*   **Per-Channel AI Personality (AI Bio):**
    
    Set unique AI personalities and roles for each channel. For example, one channel can have an "AI that talks like a cat," while another has a "professional technical support AI."
    
    *   `/set-ai-bio [bio]`: Set the AI personality for the channel.
    *   `/show-ai-bio`: Display the current AI personality settings.
    *   `/reset-ai-bio`: Reset settings to default.

*   **Per-User Memory (User Bio):**
    
    The AI can remember information about each user. Tell it "My name is XX" or use the `/set-user-bio` command, and the AI will remember your name and preferences for future conversations. This information persists across servers.
    
    *   `/set-user-bio [bio] [mode]`: Save your information to the AI's memory (with overwrite/append modes).
    *   `/show-user-bio`: Display the information the AI has stored about you.
    *   `/reset-user-bio`: Delete your information from the AI's memory.

*   **Global Shared Memory:**
    
    Store information that is shared across all servers where the bot is present. Useful for bot-wide rules or announcements from developers.
    
    *   `/memory-save [key] [value]`: Save information to global memory.
    *   `/memory-list`: List all stored information.
    *   `/memory-delete [key]`: Delete specified information.

*   **Model Switching:**
    
    Flexibly switch AI models per channel. Choose the optimal model (e.g., high-performance model, fast-response model) based on the conversation purpose.
    
    *   `/switch-models [model]`: Switch models by selecting from the available model list.
    *   `/switch-models-default-server`: Reset the model to the server's default settings.

### 🎶 Advanced Music Playback
Enjoy high-quality music in your voice channels.

*   **Various Sources:** Play music from YouTube, SoundCloud, Niconico, and other platforms via URLs or search queries.
*   **Playback Controls:** Intuitive commands like `/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/volume`.
*   **Advanced Queue Management:** View queue with `/queue`, shuffle with `/shuffle`, remove individual songs with `/remove`, clear all with `/clear`.
*   **Loop Modes:** Toggle between no loop, single track loop, or full queue loop with `/loop`.
*   **Seek Function:** Jump to any position in the track with `/seek`.
*   **Automatic Management:** Automatically leaves voice channels when empty, efficiently managing resources.

### 🎮 Games & Entertainment
*   **/akinator:** Play the famous character guessing game with Akinator. Multi-language support.
*   **/gacha:** Simulate Blue Archive-style student recruitment (gacha).
*   **/meow:** Display random cute cat pictures from TheCatAPI.
*   **/yandere, /danbooru:** Anime image search (NSFW channels only).

### 🛠️ Utility Commands
Provides useful slash commands for server management and information retrieval.

*   **/help, /llm_help:** Display comprehensive help and AI usage guidelines.
*   **/ping:** Display the bot's current response time (latency).
*   **/serverinfo:** Display detailed server information.
*   **/userinfo [user]:** Display user information.
*   **/avatar [user]:** Display a user's avatar in high quality.
*   **/invite:** Display bot invitation link.
*   **/support:** Display contact information for the developer.
*   **/roll, /check, /diceroll:** Dice rolling features.
*   **/timer:** Timer feature.

### 🎯 Game Trackers
*   **Valorant:** Player statistics tracking
*   **Rainbow Six Siege:** Game statistics display

### 🔊 Text-to-Speech (TTS)
*   **Style-Bert-VITS2:** High-quality Japanese voice synthesis
*   **Join/Leave Notifications:** Voice notifications in voice channels
*   **Customizable:** Speech rate, style, and emotion adjustments

### 📥 Media Downloader
Downloads video or audio from sites like YouTube and generates a temporary shareable link.

*   **/ytdlp_video [query]:** Download videos (supports 1080p and above).
*   **/ytdlp_audio [query] [format]:** Extract and download audio.

<p align="center">
 <img src="https://i.imgur.com/pigk6eH.png" alt="video downloader">
</p>

### 📡 Notification Features
*   **Earthquake & Tsunami Alerts (Japan):**
    - Real-time reception via P2P Earthquake Information (WebSocket)
    - Earthquake Early Warning (EEW)
    - Seismic intensity and tsunami warnings
    - Epicenter map display

*   **Twitch Stream Notifications:**
    - Automatic stream start notifications
    - Custom message settings
    - Multiple channel support

---

## ⚠️ AI Usage Guidelines and Disclaimer

Please read the following guidelines carefully before using the AI features of this bot. **By using the bot, you are deemed to have agreed to these guidelines.**

### 📋 Terms of Use

*   **Precautions for Data Input:**
    
    **Never input personal or confidential information.** (e.g., names, addresses, passwords, NDA-protected information, internal company data)

*   **Precautions for Using Generated Output:**
    
    **Information generated by the AI may be inaccurate or contain biases.** Treat the output as a reference and **always perform your own fact-checking.**
    
    The developers are not liable for any damages resulting from the use of the generated content. Use is at your **own risk**.

---

## 🔐 Privacy Policy

### 📊 Data Collection

PLANA only collects and processes the following data:

1. **Messages Sent to PLANA**
   - Messages with @mentions
   - Replies to PLANA's messages
   - **No other messages are collected**

2. **User Settings Data**
   - Information registered via `/set-user-bio`
   - Information saved via `/memory-save`
   - Notification settings

3. **Technical Information**
   - Command execution logs
   - Error logs

### 🎯 Purpose of Data Use

- **Service Provision:** Execution of AI chat, music playback, and notification features
- **Debugging:** Error correction and feature improvements
- **Statistics:** Understanding usage patterns (anonymized)

### 🔒 Anonymization Process

Messages sent to PLANA may be used for debugging after the following anonymization processes:

- Removal of user IDs and server IDs
- Removal of personally identifiable information
- Conversion to statistical data

### ⏱️ Data Retention Period

- **Conversation History:** Session only (deleted on bot restart)
- **User Settings:** Until explicitly deleted

---

## ⚙️ Installation and Setup (Self-Hosting)

### Prerequisites
*   Python 3.8 or higher
*   Git
*   FFmpeg (Required for music features)
*   Docker & Docker Compose (Optional, Recommended)

### Step 1: Basic Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **Configure `config.yaml`:**
    
    Copy `config.default.yaml` to create `config.yaml`.
    
    Open the generated `config.yaml` and **configure at least the following settings:**

    *   `token`: **Required.** Discord Bot Token
    *   `llm:` section: `model`, `providers` (API keys, etc.)

### Step 2: Setup for Additional Features (Optional)

#### Twitch Notification Setup

1.  **Get Twitch API keys:**
    - [Twitch Developer Console](https://dev.twitch.tv/console)
    - Category: `Chat Bot`
    - OAuth Redirect URLs: `http://localhost`

2.  **Add to `config.yaml`:**
    ```yaml
    twitch:
      client_id: "YOUR_TWITCH_CLIENT_ID"
      client_secret: "YOUR_TWITCH_CLIENT_SECRET"
    ```

#### Media Downloader Setup

1.  **Google Drive API Setup:**
    - [Google Cloud Console](https://console.cloud.google.com/)
    - Enable Google Drive API
    - Create OAuth Client ID (Desktop app)
    - Download `client_secrets.json`

2.  **Set Folder ID:**
    - Create a folder in Google Drive
    - Edit `GDRIVE_FOLDER_ID` in `PLANA/downloader/ytdlp_downloader_cog.py`

### Step 3: Start the Bot

#### 🚀 Windows (Easy)
```bash
# Double-click startPLANA.bat
```

#### 💻 Standard Method
```bash
pip install -r requirements.txt
python main.py
```

#### 🐳 Docker (Recommended)
```bash
docker compose up --build -d
```

---

## 🛡️ Security

### Reporting Vulnerabilities

If you discover a security issue, please contact us privately:

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)

---

## 📜 License

This project is released under the MIT License.

---

## 🤝 Contributing

Pull requests are welcome! For major changes, please open an issue first to discuss what you would like to change.

---

## 📞 Support

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)
- GitHub Issues: [Issues](https://github.com/coffin399/llmcord-JP-plana/issues)

---

## 🙏 Acknowledgments

- [llmcord](https://github.com/jakobdylanc/llmcord) - Original project base
- [discord.py](https://github.com/Rapptz/discord.py) - Discord API wrapper
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - Video downloader
- [P2P地震情報](https://www.p2pquake.net/) - Earthquake alert API
- [TheCatAPI](https://thecatapi.com/) - Cat image API

---

<div align="center">

**Dev by ごみぃ(coffin299) & えんじょ(Autmn134F)**


</div>