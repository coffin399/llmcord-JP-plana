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

### [日本語](README_ja.md) | [English](README_en.md) | [中文](README_zh.md) | [繁體中文](README_zh-TW.md) | [한국어](README_ko.md)

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
![](https://coffin399.github.io/coffin299page/assets/badge.svg)

</div>

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

    *   `token`: **Required.** Discord Bot Token
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

> For additional features (Twitch notifications, Media downloader), please refer to the language-specific documentation.

---

## ✨ Key Features

- 🗣️ **AI Chat (LLM)** - Conversation with advanced language models
- 🎶 **Music Playback** - High-quality music streaming from YouTube, SoundCloud
- 🖼️ **Image Recognition** - AI-powered image understanding
- 📡 **Real-Time Notifications** - Earthquake alerts (Japan), Twitch streams
- 🎮 **Entertainment** - Akinator, Gacha, and more
- 🛠️ **Utilities** - Server management and information commands
- 📥 **Media Downloader** - Download videos and audio

---

## 🔒 Privacy

PLANA **does not use Message Content Intent**. It only collects:
- Messages that @mention PLANA
- Replies to PLANA's messages

**No other server messages are collected or stored.**

---

## 📞 Support

- **Discord:** `coffin299`
- **X (Twitter):** [@coffin299](https://x.com/coffin399)
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