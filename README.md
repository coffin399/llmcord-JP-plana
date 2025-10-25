<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  A Multi-Functional Discord Bot with AI Chat, Music, and More
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>

<div align="center">

![access counter](https://count.getloli.com/@PLANAv3?name=PLANAv3&theme=moebooru&padding=7&offset=0&align=center&scale=1&pixelated=1&darkmode=0)

### [日本語](docs/README_ja.md) | [English](docs/README_en.md) | [中文](docs/README_zh.md) | [繁體中文](docs/README_zh-TW.md) | [한국어](docs/README_ko.md)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/coffin399/llmcord-JP-plana)
[![](https://coffin399.github.io/coffin299page/assets/badge.svg)](https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot)
[![Discord](https://img.shields.io/discord/1305004687921250436?logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/H79HKKqx3s)</div>

---

## 🚀 Quick Start

### Invite PLANA to Your Server

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot">
    <strong>➡️ Click Here to Invite PLANA ⬅️</strong>
  </a>
</h3>

### Self-Hosting

#### Prerequisites
*   Python 3.8 or higher
*   Git
*   FFmpeg (Required for music features)
*   Docker & Docker Compose (Optional, Recommended)

#### Step 1: Basic Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **Configure `config.yaml`:**
    
    Copy `config.default.yaml` to create `config.yaml`.
    
    ```bash
    cp config.default.yaml config.yaml
    ```
    
    Open the generated `config.yaml` and **configure at least the following settings:**

    *   `bot_token`: **Required.** Discord Bot Token
    *   `llm:` section: `model`, `providers` (API keys, etc.)

#### Step 2: Start the Bot

**🚀 Windows (Easy)**
```bash
# Double-click startPLANA.bat
```

**💻 Standard Method**
```bash
pip install -r requirements.txt
python main.py
```

**🐳 Docker (Recommended)**
```bash
docker compose up --build -d
```

> For additional features (Twitch notifications, Media downloader, TTS), please refer to the language-specific documentation.

---

## ✨ Key Features

### 🤖 AI Chat (LLM)
- **Multiple AI Models**: OpenAI GPT-4o, Google Gemini 2.5 Pro, NVIDIA NIM, Mistral, and more
- **Image Recognition**: AI-powered image understanding and analysis
- **Image Generation**: High-quality image generation using Stable Diffusion WebUI Forge
- **Web Search**: Real-time internet search capabilities
- **Memory System**: User bio, channel bio, and global memory management
- **Multi-language Support**: Automatically adapts to user's language

### 🎶 Advanced Music Playback
- **Multiple Sources**: YouTube, SoundCloud, Niconico, and other platforms
- **Advanced Controls**: Play, pause, skip, volume, seek, loop modes
- **Queue Management**: Shuffle, remove, clear, and playlist support
- **Auto Management**: Automatic voice channel management

### 🎯 Game Trackers
- **Valorant**: Player statistics tracking and display
- **Rainbow Six Siege**: Game statistics and performance metrics

### 🔊 Text-to-Speech (TTS)
- **Style-Bert-VITS2**: High-quality Japanese voice synthesis
- **Voice Notifications**: Join/leave announcements in voice channels
- **Customizable**: Speech rate, style, and emotion adjustments

### 📡 Real-Time Notifications
- **Earthquake Alerts**: Japan earthquake and tsunami notifications with maps
- **Twitch Streams**: Automatic stream start notifications

### 🎮 Entertainment Features
- **Akinator**: Character guessing game with multi-language support
- **Gacha System**: Blue Archive-style student recruitment simulation
- **Image Commands**: Cat images, anime image search, and more

### 🛠️ Utility Commands
- **Server Management**: Server info, user info, avatar display
- **Dice Rolling**: Advanced dice roll system with custom notation
- **Timer**: Countdown timer functionality
- **Media Downloader**: Download videos and audio from various platforms

---

## 🔧 Technical Specifications

### Architecture
- **Framework**: discord.py 2.0+
- **Language**: Python 3.8+
- **Configuration**: YAML-based settings
- **Logging**: Discord channel logging with rate limiting
- **Error Handling**: Comprehensive exception handling system

### Supported APIs
- **OpenAI**: GPT-4o, GPT-4 Turbo
- **Google**: Gemini 2.5 Pro/Flash
- **NVIDIA NIM**: Various open-source models
- **Mistral**: Mistral Medium
- **HenrikDev**: Valorant API
- **TheCatAPI**: Cat image API
- **P2P Earthquake**: Japan earthquake data

### Project Structure
```
llmcord-JP-plana/
├── main.py                    # Main entry point
├── config.default.yaml        # Default configuration
├── config.yaml               # Actual configuration (create this)
├── requirements.txt          # Python dependencies
├── startPLANA.bat           # Windows startup script
├── PLANA/                   # Main feature modules
│   ├── llm/                 # AI chat functionality
│   ├── music/               # Music playback
│   ├── images/              # Image commands
│   ├── notifications/       # Notification features
│   ├── tracker/             # Game trackers
│   ├── tts/                 # Text-to-speech
│   ├── utilities/           # Utility commands
│   └── services/            # Common services
├── modules/                 # Legacy modules
├── plugins/                 # Plugins
├── data/                    # Data storage
└── cache/                   # Cache directory
```

---

## 🔒 Privacy

PLANA **does not use Message Content Intent**. It only collects:
- Messages that @mention PLANA
- Replies to PLANA's messages

**No other server messages are collected or stored.**

---

## 📞 Support

- **Discord:** `coffin299`
- **X (Twitter):** [@coffin299](https://x.com/coffin299)
- **GitHub Issues:** [Report Issues](https://github.com/coffin399/llmcord-JP-plana/issues)
- **In-Bot Support:** Use `/support` command

---

## 📜 License

This project is released under the [MIT License](LICENSE).

---

## 🙏 Credits

**Developed by ごみぃ (coffin299) & えんじょ (Autmn134F)**

Based on [llmcord](https://github.com/jakobdylanc/llmcord)

---

<div align="center">

**For detailed documentation, please select your language above.**

</div>