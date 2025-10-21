<h1 align="center">
  PLANA
</h1>

<h3 align="center"><i>
  Plana와 대화해요!
</i></h3>

<p align="center">
  <img src="https://i.imgur.com/Q3VuxzG.png" alt="Plana Banner">
</p>

<div align="center">

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![Discord.py](https://img.shields.io/badge/discord.py-2.0+-blue.svg)](https://github.com/Rapptz/discord.py)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](../LICENSE)
[![](https://coffin399.github.io/coffin299page/assets/badge.svg)](https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot)
[![Discord](https://img.shields.io/discord/1305004687921250436?logo=discord&logoColor=white&label=Discord&color=5865F2)](https://discord.gg/H79HKKqx3s)</div>


**언어 / Languages:** [日本語](docs/README_ja.md) | [English](docs/README_en.md) | [中文](docs/README_zh.md) | [繁體中文](docs/README_zh-TW.md) | [한국어](docs/README_ko.md)

[개요](#-개요) • [기능](#-주요-기능) • [설치 및 설정](#️-설치-및-설정-셀프-호스팅)

</div>

---

### 🤖 서버에 Bot 초대하기

<h3 align="center">
  <a href="https://discord.com/oauth2/authorize?client_id=1031673203774464160&permissions=551906765824&scope=bot" title="Click to invite PLANA to your server!">
    <strong>➡️ 여기를 클릭하여 PLANA 초대하기 ⬅️</strong>
  </a>
</h3>

*   셀프 호스팅을 하는 경우 `config.yaml`에 자신의 Bot 초대 URL을 설정하세요.

### 💬 지원
*   Bot 사용에 관한 질문이나 버그 보고는 `/support` 명령어로 표시되는 연락처로 문의하세요.

---

## 📖 개요

**llmcord-JP-PLANA**(통칭: **PLANA**)는 [llmcord](https://github.com/jakobdylanc/llmcord)를 기반으로 개발된 다기능 Discord 봇입니다. 대규모 언어 모델(LLM)과의 대화, 고급 음악 재생, 이미지 인식, 실시간 알림, 엔터테인먼트 기능 및 유용한 서버 유틸리티를 제공합니다. OpenAI 호환 API를 지원하여 원격 호스팅 및 로컬 호스팅을 포함한 거의 모든 LLM과 통합할 수 있습니다.

### 🔒 개인정보 보호 설계

**PLANA는 특권 인텐트(Message Content Intent)를 사용하지 않습니다.**
따라서 다음 메시지**만** 수집합니다:

- PLANA를 @멘션한 메시지
- PLANA의 메시지에 대한 답장

**그 외의 서버 내 메시지는 일체 수집하거나 저장하지 않습니다.**

---

## ✨ 주요 기능

### 🗣️ AI 대화 (LLM)
봇을 멘션(`@PLANA`)하거나 봇의 메시지에 답장하여 AI와 대화를 시작합니다.

*   **지속적인 대화:** 답장을 계속하면 문맥을 유지한 대화가 가능합니다.
*   **이미지 인식:** 메시지와 함께 이미지를 첨부하면 AI가 이미지 내용도 이해하려고 시도합니다(비전 모델 지원 시).
*   **도구 사용(웹 검색):** AI가 필요하다고 판단하면 인터넷에서 정보를 검색하여 응답에 활용합니다(Google Custom Search API 키 필요).
*   **대화 기록 관리:** `/clear_history` 명령어로 현재 채널의 대화 기록을 초기화할 수 있습니다.
*   **커스터마이징 가능한 AI 성격:** `config.yaml`의 시스템 프롬프트를 편집하여 AI의 기본 성격과 응답 스타일을 자유롭게 변경할 수 있습니다.

<p align="center">
  <img src="https://i.imgur.com/wjdPNFQ.png" alt="PLANA MODEL CHANGE">
</p>

#### 🧠 성격 및 기억 기능
PLANA는 대화를 더욱 풍부하게 하는 고급 성격 설정 및 기억 기능을 갖추고 있습니다.

*   **채널별 AI 성격(AI Bio):**
    
    채널마다 AI의 성격과 역할을 개별적으로 설정할 수 있습니다. 예를 들어, 한 채널에서는 "고양이처럼 말하는 AI", 다른 채널에서는 "전문 기술 지원 AI"로 동작하게 할 수 있습니다.
    
    *   `/set-ai-bio [bio]`: 채널의 AI 성격을 설정합니다.
    *   `/show-ai-bio`: 현재 AI 성격 설정을 표시합니다.
    *   `/reset-ai-bio`: 설정을 기본값으로 되돌립니다.

*   **사용자별 기억(User Bio):**
    
    AI는 각 사용자의 정보를 기억할 수 있습니다. "내 이름은 XX입니다"라고 알려주거나 `/set-user-bio` 명령어를 사용하면 AI가 이름과 선호도를 기억하고 이후 대화에 활용합니다. 이 정보는 서버 간에 유지됩니다.
    
    *   `/set-user-bio [bio] [mode]`: AI 메모리에 정보를 저장합니다(덮어쓰기/추가 모드 있음).
    *   `/show-user-bio`: AI가 저장한 사용자 정보를 표시합니다.
    *   `/reset-user-bio`: AI 메모리에서 사용자 정보를 삭제합니다.

*   **글로벌 공유 메모리:**
    
    봇이 참여한 모든 서버에서 공유되는 정보를 저장할 수 있습니다. 봇 전체에서 공유할 규칙이나 개발자의 공지사항 등을 저장하는 데 유용합니다.
    
    *   `/memory-save [key] [value]`: 글로벌 메모리에 정보를 저장합니다.
    *   `/memory-list`: 저장된 모든 정보를 나열합니다.
    *   `/memory-delete [key]`: 지정된 정보를 삭제합니다.

*   **모델 전환:**
    
    채널마다 사용할 AI 모델을 유연하게 변경할 수 있습니다. 대화 목적에 맞춰 최적의 모델(예: 고성능 모델, 빠른 응답 모델 등)을 선택할 수 있습니다.
    
    *   `/switch-models [model]`: 사용 가능한 모델 목록에서 선택하여 전환합니다.
    *   `/switch-models-default-server`: 모델을 서버의 기본 설정으로 되돌립니다.

### 🎶 고급 음악 재생
음성 채널에서 고품질 음악을 즐기세요.

*   **다양한 소스 지원:** YouTube, SoundCloud URL 또는 검색 쿼리로 음악을 재생할 수 있습니다.
*   **재생 제어:** `/play`, `/pause`, `/resume`, `/stop`, `/skip`, `/volume` 등 직관적인 명령어로 조작할 수 있습니다.
*   **고급 대기열 관리:** `/queue`로 대기열 확인, `/shuffle`로 셔플, `/remove`로 개별 삭제, `/clear`로 전체 삭제가 가능합니다.
*   **반복 재생:** `/loop` 명령어로 반복 없음, 단일 트랙 반복, 전체 대기열 반복 간 전환할 수 있습니다.
*   **탐색 기능:** `/seek` 명령어로 재생 위치를 자유롭게 이동할 수 있습니다.
*   **자동 관리:** 음성 채널에 아무도 없으면 자동으로 퇴장하여 리소스를 효율적으로 관리합니다.

### 🎮 게임 및 엔터테인먼트
*   **/akinator:** 유명한 아키네이터와 캐릭터 맞추기 게임을 즐기세요. 다국어 지원.
*   **/gacha:** 블루 아카이브 스타일의 학생 모집(가챠) 시뮬레이션.
*   **/meow:** TheCatAPI에서 무작위 귀여운 고양이 이미지를 표시합니다.
*   **/yandere, /danbooru:** 애니메이션 이미지 검색(NSFW 전용 채널).

### 🛠️ 유틸리티 명령어
서버 관리 및 정보 검색에 유용한 슬래시 명령어를 제공합니다.

*   **/help, /llm_help:** 포괄적인 도움말 및 AI 사용 가이드라인을 표시합니다.
*   **/ping:** 봇의 현재 응답 시간(지연 시간)을 표시합니다.
*   **/serverinfo:** 상세한 서버 정보를 표시합니다.
*   **/userinfo [user]:** 사용자 정보를 표시합니다.
*   **/avatar [user]:** 사용자의 아바타를 고화질로 표시합니다.
*   **/invite:** 봇 초대 링크를 표시합니다.
*   **/support:** 개발자 연락처 정보를 표시합니다.
*   **/roll, /check, /diceroll:** 주사위 굴리기 기능.
*   **/timer:** 타이머 기능.

### 📥 미디어 다운로더
YouTube 등의 사이트에서 동영상이나 오디오를 다운로드하고 임시 공유 링크를 생성합니다.

*   **/ytdlp_video [query]:** 동영상 다운로드(1080p 이상 지원).
*   **/ytdlp_audio [query] [format]:** 오디오 추출 및 다운로드.

<p align="center">
 <img src="https://i.imgur.com/pigk6eH.png" alt="video downloader">
</p>

### 📡 알림 기능
*   **지진 및 쓰나미 경보(일본):**
    - P2P 지진 정보로부터 실시간 수신(WebSocket)
    - 긴급 지진 속보(EEW)
    - 진도 정보 및 쓰나미 경보
    - 진원지 지도 표시

*   **Twitch 방송 알림:**
    - 방송 시작 자동 알림
    - 맞춤 메시지 설정
    - 여러 채널 지원

---

## ⚠️ AI 사용 가이드라인 및 면책 조항

본 Bot의 AI 기능을 사용하기 전에 다음 가이드라인을 반드시 읽어주세요. **Bot을 사용함으로써 본 가이드라인에 동의한 것으로 간주됩니다.**

### 📋 이용 약관

*   **데이터 입력에 관한 주의사항:**
    
    **개인 정보나 기밀 정보를 절대 입력하지 마세요.** (예: 이름, 주소, 비밀번호, NDA 정보, 회사 내부 정보)

*   **생성물 사용에 관한 주의사항:**
    
    **AI가 생성하는 정보에는 부정확한 내용이나 편견이 포함될 수 있습니다.** 생성된 내용은 참고 정보로 취급하고 **반드시 직접 사실 확인을 수행하세요.**
    
    생성물을 사용한 결과 발생한 어떠한 손해에 대해서도 개발자는 책임지지 않습니다. 사용은 **자기 책임** 하에 부탁드립니다.

---

## 🔐 개인정보 보호정책

### 📊 수집하는 데이터

PLANA는 다음 데이터만 수집하고 처리합니다:

1. **PLANA에게 전송된 메시지**
   - @멘션된 메시지
   - PLANA 메시지에 대한 답장
   - **그 외의 메시지는 수집하지 않음**

2. **사용자 설정 데이터**
   - `/set-user-bio`로 등록한 정보
   - `/memory-save`로 저장한 정보
   - 알림 설정

3. **기술 정보**
   - 명령어 실행 로그
   - 오류 로그

### 🎯 데이터 사용 목적

- **서비스 제공:** AI 대화, 음악 재생, 알림 기능 실행
- **디버깅:** 오류 수정 및 기능 개선
- **통계:** 사용 패턴 파악(익명화됨)

### 🔒 익명화 처리

PLANA에 전송된 메시지는 다음의 익명화 처리 후 디버깅에 사용될 수 있습니다:

- 사용자 ID, 서버 ID 삭제
- 개인을 특정할 수 있는 정보 삭제
- 통계 데이터로 변환

### ⏱️ 데이터 보존 기간

- **대화 기록:** 세션 중에만(Bot 재시작 시 삭제)
- **사용자 설정:** 명시적으로 삭제할 때까지

---

## ⚙️ 설치 및 설정 (셀프 호스팅)

### 전제 조건
*   Python 3.8 이상
*   Git
*   FFmpeg(음악 기능에 필요)
*   Docker & Docker Compose(선택 사항, 권장)

### 단계 1: 기본 설정

1.  **리포지토리 복제:**
    ```bash
    git clone https://github.com/coffin399/llmcord-JP-plana
    cd llmcord-JP-plana
    ```

2.  **`config.yaml` 설정:**
    
    `config.default.yaml`을 복사하여 `config.yaml`을 생성합니다.
    
    생성된 `config.yaml`을 열고 **최소한 다음 항목을 설정하세요:**

    *   `token`: **필수.** Discord Bot Token
    *   `llm:` 섹션: `model`, `providers`(API 키 등)

### 단계 2: 추가 기능 설정(선택 사항)

#### Twitch 알림 설정

1.  **Twitch API 키 취득:**
    - [Twitch Developer Console](https://dev.twitch.tv/console)
    - Category: `Chat Bot`
    - OAuth Redirect URLs: `http://localhost`

2.  **`config.yaml`에 추가:**
    ```yaml
    twitch:
      client_id: "YOUR_TWITCH_CLIENT_ID"
      client_secret: "YOUR_TWITCH_CLIENT_SECRET"
    ```

#### 미디어 다운로더 설정

1.  **Google Drive API 설정:**
    - [Google Cloud Console](https://console.cloud.google.com/)
    - Google Drive API 활성화
    - OAuth Client ID(데스크톱 앱) 생성
    - `client_secrets.json` 다운로드

2.  **폴더 ID 설정:**
    - Google Drive에 폴더 생성
    - `PLANA/downloader/ytdlp_downloader_cog.py`의 `GDRIVE_FOLDER_ID` 편집

### 단계 3: Bot 시작

#### 🚀 Windows(간단)
```bash
# startPLANA.bat 더블클릭
```

#### 💻 표준 방법
```bash
pip install -r requirements.txt
python main.py
```

#### 🐳 Docker(권장)
```bash
docker compose up --build -d
```

---

## 🛡️ 보안

### 취약점 보고

보안 문제를 발견한 경우 비공개로 연락 주세요:

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin299)

---

## 📜 라이선스

이 프로젝트는 MIT 라이선스 하에 공개됩니다.

---

## 🤝 기여

풀 리퀘스트 환영합니다! 주요 변경 사항의 경우 먼저 이슈를 열어 토론해 주세요.

---

## 📞 지원

- Discord: `coffin299`
- X (Twitter): [@coffin299](https://x.com/coffin399)
- GitHub Issues: [Issues](https://github.com/coffin399/llmcord-JP-plana/issues)

---

## 🙏 감사의 말

- [llmcord](https://github.com/jakobdylanc/llmcord) - 원본 프로젝트 기반
- [discord.py](https://github.com/Rapptz/discord.py) - Discord API 래퍼
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - 동영상 다운로더
- [P2P地震情報](https://www.p2pquake.net/) - 지진 경보 API
- [TheCatAPI](https://thecatapi.com/) - 고양이 이미지 API

---

<div align="center">

**Dev by ごみぃ(coffin299) & えんじょ(Autmn134F)**

</div>