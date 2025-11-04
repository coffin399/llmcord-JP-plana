<h1 align="center">
  llmcord
</h1>

<h3 align="center"><i>
  Talk to LLMs with your friends!<br>
</i></h3>

<p align="center">
  <img src="https://github.com/user-attachments/assets/7791cc6b-6755-484f-a9e3-0707765b081f" alt="">
</p>

llmcord transforms Discord into a collaborative LLM frontend. It works with practically any LLM, remote or locally hosted, and is extensible with plugins.

<hr>

llmcordはDiscordを共同LLMフロントエンドに変換します。リモートまたはローカルでホストされている、事実上すべてのLLMで動作し、プラグインで拡張可能です。

## Features / 機能

### Reply-based chat system / 返信ベースのチャットシステム:
Just @ the bot to start a conversation and reply to continue. Build conversations with reply chains!

<hr>

ボットに@メンションして会話を開始し、返信して続けます。返信チェーンで会話を構築しましょう！


**You can: / できること:**
- Branch conversations endlessly / 会話を無限に分岐させる
- Continue other people's conversations / 他の人の会話を続ける
- @ the bot while replying to ANY message to include it in the conversation / どんなメッセージに返信する際にボットに@メンションして、そのメッセージを会話に含める

**Additionally: / さらに:**
- When DMing the bot, conversations continue automatically (no reply required). To start a fresh conversation, just @ the bot. You can still reply to continue from anywhere. / ボットにDMを送ると、会話は自動的に続きます（返信は不要です）。新しい会話を始めるには、ボットに@メンションしてください。どこからでも返信して会話を続けることができます。
- You can branch conversations into [threads](https://support.discord.com/hc/en-us/articles/4403205878423-Threads-FAQ). Just create a thread from any message and @ the bot inside to continue. / 会話をスレッドに分岐させることができます。どのメッセージからでもスレッドを作成し、その中でボットに@メンションして会話を続けます。
- Back-to-back messages from the same user are automatically chained together. Just reply to the latest one and the bot will see all of them. / 同じユーザーからの連続したメッセージは自動的に連結されます。最新のメッセージに返信するだけで、ボットはそれらすべてを認識します。

---

### Model switching with `/model` / `/model`でのモデル切り替え:
![image](https://github.com/user-attachments/assets/568e2f5c-bf32-4b77-ab57-198d9120f3d2)

llmcord supports remote models from: / llmcordは以下のリモートモデルをサポートしています:
- [OpenAI API](https://platform.openai.com/docs/models)
- [xAI API](https://docs.x.ai/docs/models)
- [Google Gemini API](https://ai.google.dev/gemini-api/docs/models)
- [Mistral API](https://docs.mistral.ai/getting-started/models/models_overview)
- [Groq API](https://console.groq.com/docs/models)
- [OpenRouter API](https://openrouter.ai/models)

Or run local models with: / または、以下のローカルモデルを実行できます:
- [Ollama](https://ollama.com)
- [LM Studio](https://lmstudio.ai)
- [vLLM](https://github.com/vllm-project/vllm)

...Or use any other OpenAI compatible API server. / ...または、その他のOpenAI互換APIサーバーを使用できます。

---

### Plugins & Tools / プラグインとツール
llmcord can be extended with plugins that act as tools for the LLM. If a model decides that a tool is needed to answer a user's query, it will call the tool and use its output to formulate a response.

**Default Plugins:**
- **`search_agent`**: Performs a web search using the Google Search API to answer questions about recent events or access information on the internet.
- **`bio_manager`**: Allows users to set and retrieve short biographies for themselves, which the bot can access.
- **`memory_manager`**: Provides the bot with a simple mechanism to remember and recall specific pieces of information across conversations.

<hr>

llmcordは、LLMのツールとして機能するプラグインで拡張できます。モデルがユーザーの質問に答えるためにツールが必要だと判断した場合、そのツールを呼び出し、その出力を使って応答を作成します。

**デフォルトのプラグイン:**
- **`search_agent`**: Google Search APIを使用してウェブ検索を実行し、最近の出来事に関する質問に答えたり、インターネット上の情報にアクセスしたりします。
- **`bio_manager`**: ユーザーが自分用の短い自己紹介を設定・取得できるようにし、ボットがそれにアクセスできるようにします。
- **`memory_manager`**: ボットが会話をまたいで特定の情報を記憶・想起するための簡単なメカニズムを提供します。

---

### And more: / その他:
- Supports image attachments when using a vision model (like gpt-5, grok-4, claude-4, etc.) / ビジョンモデル（gpt-5、grok-4、claude-4など）を使用する場合、画像添付ファイルをサポートします。
- Supports text file attachments (.txt, .py, .c, etc.) / テキストファイルの添付（.txt、.py、.cなど）をサポートします。
- Customizable personality (aka system prompt) / カスタマイズ可能なパーソナリティ（システムプロンプト）
- User identity aware (OpenAI API and xAI API only) / ユーザーIDを認識（OpenAI APIおよびxAI APIのみ）
- Streamed responses (turns green when complete, automatically splits into separate messages when too long) / ストリーミング応答（完了すると緑色に変わり、長すぎると自動的に別々のメッセージに分割されます）
- Hot reloading config (you can change settings without restarting the bot) / 設定のホットリロード（ボットを再起動せずに設定を変更できます）
- Displays helpful warnings when appropriate (like "⚠️ Only using last 25 messages" when the customizable message limit is exceeded) / 適切な場合に役立つ警告を表示します（カスタマイズ可能なメッセージ制限を超えた場合の「⚠️ 過去25件のメッセージのみを使用しています」など）
- Caches message data in a size-managed (no memory leaks) and mutex-protected (no race conditions) global dictionary to maximize efficiency and minimize Discord API calls / 効率を最大化し、Discord API呼び出しを最小限に抑えるため、サイズ管理され（メモリリークなし）、ミューテックスで保護された（競合状態なし）グローバル辞書にメッセージデータをキャッシュします
- Fully asynchronous / 完全非同期

## Instructions / 手順

1. Clone the repo: / リポジトリをクローンします:
   ```bash
   git clone https://github.com/jakobdylanc/llmcord
   ```

2. Create a copy of "config-example.yaml" named "config.yaml" and set it up: / 「config-example.yaml」のコピーを「config.yaml」という名前で作成し、設定します:

### Discord settings / Discord設定:

| Setting / 設定 | Description / 説明 |
| --- | --- |
| **bot_token** | Create a new Discord bot at [discord.com/developers/applications](https://discord.com/developers/applications) and generate a token under the "Bot" tab. Also enable "MESSAGE CONTENT INTENT".<hr> [discord.com/developers/applications](https://discord.com/developers/applications) で新しいDiscordボットを作成し、「Bot」タブでトークンを生成します。「MESSAGE CONTENT INTENT」も有効にしてください。 |
| **client_id** | Found under the "OAuth2" tab of the Discord bot you just made.<hr>作成したDiscordボットの「OAuth2」タブにあります。 |
| **status_message** | Set a custom message that displays on the bot's Discord profile.<br /><br />**Max 128 characters.**<hr>ボットのDiscordプロフィールに表示されるカスタムメッセージを設定します。<br /><br />**最大128文字。** |
| **max_text** | The maximum amount of text allowed in a single message, including text from file attachments. (Default: `100,000`)<hr>ファイル添付のテキストを含む、1つのメッセージで許可される最大テキスト量。（デフォルト: `100,000`） |
| **max_images** | The maximum number of image attachments allowed in a single message. (Default: `5`)<br /><br />**Only applicable when using a vision model.**<hr>1つのメッセージで許可される画像添付の最大数。（デフォルト: `5`）<br /><br />**ビジョンモデルを使用する場合にのみ適用されます。** |
| **max_messages** | The maximum number of messages allowed in a reply chain. When exceeded, the oldest messages are dropped. (Default: `25`)<hr>返信チェーンで許可されるメッセージの最大数。超えた場合、最も古いメッセージは破棄されます。（デフォルト: `25`） |
| **use_plain_responses** | When set to `true` the bot will use plaintext responses instead of embeds. Plaintext responses have a shorter character limit so the bot's messages may split more often. (Default: `false`)<br /><br />**Also disables streamed responses and warning messages.**<hr>`true`に設定すると、ボットは埋め込みの代わりにプレーンテキスト応答を使用します。プレーンテキスト応答は文字数制限が短いため、ボットのメッセージがより頻繁に分割されることがあります。（デフォルト: `false`）<br /><br />**ストリーミング応答と警告メッセージも無効になります。** |
| **allow_dms** | Set to `false` to disable direct message access. (Default: `true`)<hr>`false`に設定すると、ダイレクトメッセージアクセスが無効になります。（デフォルト: `true`） |
| **permissions** | Configure access permissions for `users`, `roles` and `channels`, each with a list of `allowed_ids` and `blocked_ids`.<br /><br />Control which `users` are admins with `admin_ids`. Admins can change the model with `/model` and DM the bot even if `allow_dms` is `false`.<br /><br />**Leave `allowed_ids` empty to allow ALL in that category.**<br /><br />**Role and channel permissions do not affect DMs.**<br /><br />**You can use [category](https://support.discord.com/hc/en-us/articles/115001580171-Channel-Categories-101) IDs to control channel permissions in groups.**<hr>ユーザー、ロール、チャンネルのアクセス権限を、それぞれ`allowed_ids`と`blocked_ids`のリストで設定します。<br /><br />`admin_ids`でどのユーザーが管理者かを制御します。管理者は`/model`でモデルを変更でき、`allow_dms`が`false`でもボットにDMできます。<br /><br />**`allowed_ids`を空にすると、そのカテゴリのすべてを許可します。**<br /><br />**ロールとチャンネルの権限はDMに影響しません。**<br /><br />**[カテゴリ](https://support.discord.com/hc/en-us/articles/115001580171-Channel-Categories-101)IDを使用して、グループでチャンネルの権限を制御できます。** |

### Plugin settings / プラグイン設定:

| Setting / 設定 | Description / 説明 |
| --- | --- |
| **plugins** | This section allows you to enable and configure individual plugins.<br><br>**`search_agent`**: To enable, set `enabled: true`. You must also provide at least one Google Search API key. You can get a key from the [Google Cloud Console](https://console.cloud.google.com/apis/credentials). You can add multiple keys (`api_key`, `api_key1`, etc.) for rotation.<br><br>**`bio_manager`**: Set `enabled: true` to use. No API key required.<br><br>**`memory_manager`**: Set `enabled: true` to use. No API key required.<hr>このセクションでは、個々のプラグインを有効化し、設定することができます。<br><br>**`search_agent`**: 有効にするには `enabled: true` に設定します。また、少なくとも1つのGoogle Search APIキーを提供する必要があります。キーは[Google Cloud Console](https://console.cloud.google.com/apis/credentials)から取得できます。ローテーションのために複数のキー（`api_key`, `api_key1`など）を追加できます。<br><br>**`bio_manager`**: 使用するには `enabled: true` に設定します。APIキーは不要です。<br><br>**`memory_manager`**: 使用するには `enabled: true` に設定します。APIキーは不要です。|

### LLM settings / LLM設定:

| Setting / 設定 | Description / 説明 |
| --- | --- |
| **providers** | Add the LLM providers you want to use, each with a `base_url` and optional `api_key` entry. Popular providers (`openai`, `ollama`, etc.) are already included.<br /><br />**Key Rotation:** You can add multiple keys for a provider (e.g., `api_key`, `api_key1`, `api_key2`). The bot will automatically try the next key if the current one fails.<br /><br />**Only supports OpenAI compatible APIs.**<br /><br />**Some providers may need `extra_headers` / `extra_query` / `extra_body` entries for extra HTTP data. See the included `azure-openai` provider for an example.**<hr>使用したいLLMプロバイダーを、それぞれ`base_url`とオプションの`api_key`エントリと共に追加します。人気のプロバイダー（`openai`、`ollama`など）はすでに含まれています。<br /><br />**キーローテーション:** プロバイダーごとに複数のキー（例: `api_key`, `api_key1`, `api_key2`）を追加できます。現在のキーが失敗した場合、ボットは自動的に次のキーを試します。<br /><br />**OpenAI互換APIのみをサポートします。**<br /><br />**一部のプロバイダーでは、追加のHTTPデータのために`extra_headers` / `extra_query` / `extra_body`エントリが必要になる場合があります。付属の`azure-openai`プロバイダーの例を参照してください。** |
| **models** | Add the models you want to use in `<provider>/<model>: <parameters>` format (examples are included). When you run `/model` these models will show up as autocomplete suggestions.<br /><br />**Refer to each provider's documentation for supported parameters.**<br /><br />**The first model in your `models` list will be the default model at startup.**<br /><br />**Some vision models may need `:vision` added to the end of their name to enable image support.**<hr>`<provider>/<model>: <parameters>`形式で使用したいモデルを追加します（例が含まれています）。`/model`を実行すると、これらのモデルがオートコンプリートの候補として表示されます。<br /><br />**サポートされているパラメータについては、各プロバイダーのドキュメントを参照してください。**<br /><br />**`models`リストの最初のモデルが起動時のデフォルトモデルになります。**<br /><br />**一部のビジョンモデルでは、画像サポートを有効にするために名前の末尾に`:vision`を追加する必要がある場合があります。** |
| **system_prompt** | Write anything you want to customize the bot's behavior!<br /><br />**Leave blank for no system prompt.**<br /><br />**You can use the `{date}` and `{time}` tags in your system prompt to insert the current date and time, based on your host computer's time zone.**<hr>ボットの振る舞いをカスタマイズするために何でも書いてください！<br /><br />**システムプロンプトが不要な場合は空のままにします。**<br /><br />**システムプロンプトで`{date}`および`{time}`タグを使用して、ホストコンピュータのタイムゾーンに基づいた現在の日付と時刻を挿入できます。** |

3. Run the bot: / ボットの実行:

   **No Docker: / Dockerなし:**
   For Windows, you can simply run `start_llmcord.bat` to automatically install dependencies and start the bot. Otherwise, run the commands below.
   Windowsの場合は、`start_llmcord.bat` を実行するだけで、依存関係が自動的にインストールされ、ボットが起動します。それ以外の場合は、以下のコマンドを実行してください。
   ```bash
   python llmcord.py
   ```

   **With Docker: / Dockerあり:**
   ```bash
   docker compose up
   ```

## Notes / 注意事項

- If you're having issues, try my suggestions [here](https://github.com/jakobdylanc/llmcord/issues/19) / 問題が発生した場合は、[こちら](https://github.com/jakobdylanc/llmcord/issues/19)の提案をお試しください

- Only models from OpenAI API and xAI API are "user identity aware" because only they support the "name" parameter in the message object. Hopefully more providers support this in the future. / OpenAI APIとxAI APIのモデルのみが「ユーザーIDを認識」します。これは、メッセージオブジェクトの「name」パラメータをサポートしているのがこれらのプロバイダーだけだからです。将来的にはより多くのプロバイダーがこれをサポートすることを期待しています。

- PRs are welcome :) / プルリクエストは歓迎です :)

## Star History

<a href="https://star-history.com/#jakobdylanc/llmcord&Date">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=jakobdylanc/llmcord&type=Date&theme=dark" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=jakobdylanc/llmcord&type=Date" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=jakobdylanc/llmcord&type=Date" />
  </picture>
</a>
