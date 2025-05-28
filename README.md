<h1 align="center">
  plana-cord-JP
</h1>

## 面倒な人向け(BOT招待):
### [ここをクリックでBOTをサーバーに入れられます！ / Click here to invite the BOT ](https://discord.com/api/oauth2/authorize?client_id=1031673203774464160&permissions=412317273088&scope=bot)

<h3 align="center"><i>
  プラナちゃんとおしゃべりしよう！
</i></h3>

![plana-cord demo gif](https://i.imgur.com/4muvYWH.gif)

planacordは、llmcordを基盤として開発された、Discord内でLLM（大規模言語モデル）と直接会話が可能になるツールです。リモートホスト型やローカルホスト型など、ほぼすべてのLLMに対応しています。

## サポートサーバーはこちら:
### [サポートサーバーに入るにはこちら](https://discord.gg/SjuWKtwNAG)
## 概要

### 返信型LLM BOT
BOTにメンションを付けて話しかけることで会話が始まり、その後返信を続けることで会話を展開できます。

次のようなことが可能です:
- 自分の会話を続ける、または他の人の会話を引き継ぐ
- 過去のメッセージに返信して会話を「巻き戻す」
- サーバー内の任意のメッセージに返信しながらBOTにメンションを付けて、その内容について質問する

さらに以下の特徴があります:
- 同じユーザーによる連続メッセージは自動的にまとめて処理されます。最新のメッセージに返信するだけで、BOTはすべての内容を参照します。
- 任意の会話を簡単に[スレッド](https://support.discord.com/hc/ja/articles/4403205878423-Threads-FAQ)に移行可能。任意のメッセージからスレッドを作成し、その中でBOTにメンションを付けて会話を続けられます。
- BOTへのDMでは返信なしで会話が自動的に続きます。新しい会話を始めるには、BOTにメンションを付けます。任意の箇所から返信して続けることも可能です。

### 任意のLLMを選択可能
llmcordが対応しているAPIプラットフォームは以下の通りです:
- [OpenAI API](https://platform.openai.com/docs/models)
- [xAI API](https://docs.x.ai/docs#models) (**New!**)
- [Mistral API](https://docs.mistral.ai/platform/endpoints)
- [Groq API](https://console.groq.com/docs/models)
- [OpenRouter API](https://openrouter.ai/docs/models)

ローカルモデルの例:
- [Ollama](https://ollama.com)
- [oobabooga](https://github.com/oobabooga/text-generation-webui)
- [Jan](https://jan.ai)
- [LM Studio](https://lmstudio.ai)

また、OpenAI互換APIを任意のURLで利用することも可能です。


### その他の特徴:
- ビジョンモデル（gpt-4o, claude-3, llavaなど）を使用する場合、画像添付に対応
- テキストファイル添付（.txt, .py, .c など）に対応
- カスタマイズ可能なパーソナリティ（プラナのシステムプロンプトを書き換える事で可能）
- ユーザーアイデンティティ対応
- 設定のホットリロード（再起動せずに設定を変更可能）
- 適切な警告を表示（例: "⚠️ 画像が見えません ><"）
- メッセージデータを効率的にキャッシュ管理（サイズ制限あり、メモリリークなし、レースコンディションなし）
- 完全非同期処理
- Pythonファイル1つ、約400行

## インストール手順

1. リポジトリをクローンします:
   ```bash
   git clone https://github.com/coffin399/llmcord-JP-plana
   ```

2. config.ymlを設定します:

### Discord 設定:

| Setting                 | Description                                                                                                                           |
|-------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| **bot_token**           | [discord.com/developers/applications](https://discord.com/developers/applications) でbot及びbotトークンを作成し、 "MESSAGE CONTENT INTENT"を有効にします |
| **client_id**           | "OAuth2" タブで見つけることが出来ます。                                                                                                              |
| **status_message**      | カスタムメッセージを設定することが出来ます。 **最大128文字**                                                                                                    |
| **allowed_channel_ids** | チャンネルIDを指定すればそのチャンネルのみでの会話が可能です。 **空白にすることで全チャンネルで有効にできます**                                                                           |
| **allowed_role_ids**    | BOTを使用できるロールIDのリストです。 **空白にすると全員が利用可能です。1つ以上指定するとDMでの会話が出来なくなります。**                                                                   |
| **max_text**            | 1つのメッセージで許可される最大テキスト量。（添付ファイルのテキストも含む） <br />(Default: `100,000`)                                                                     |
| **max_images**          | 1つのメッセージで許可される最大画像添付数。 **(ビジョンモデルを使用する場合のみ適用）**<br />(Default: `5`)                                                                   |
| **max_messages**        | 返信チェーンで許可される最大メッセージ数。<br />(Default: `25`)                                                                                            |

### LLM settings:

| Setting                  | Description                                                                                                                                       |
|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| **providers**            | それぞれプロバイダを `base_url` と `api_key` で追加します。 一般的なプロバイダ (`openai`, `ollama`, etc.) は既に含まれています. **(OpenAI互換APIのみサポートします。)**                            |
| **model**                | プロバイダとモデルの設定`<provider name>/<model name>`, 例:<br /><br />-`openai/gpt-4o`<br />-`ollama/llama3.2`<br />-`openrouter/anthropic/claude-3.5-sonnet` |
| **extra_api_parameters** | TemperatureとMax Output Tokensの設定 <br />(Default: `max_tokens=4096, temperature=1.0`)                                                              |
| **system_prompt**        | ここにいい感じのプロンプトを書くことでキャラクターになりきる事が出来ます。                                                                                                             |
| **starter_prompt**       | ここにいい感じのスタータープロンプトを入力するとキャラクターの一貫性が上がります。                                                                                                         |
| **bio_record**           | bio(記憶機能)関連の設定。bio_record.messageは記録時の特別メッセージ。                                                                                                    |
| **error_msg**            | ユーザーに示されるエラーメッセージの設定。                                                                                                                             |


4. 実行:
   **Dockerなし(またはstartPLANA.batをダブルクリック):**
   ```bash
   python -m pip install -U -r requirements.txt
   python llmcord.py
   ```
   
   **Dockerあり:**
   ```bash
   docker compose up
   ```
   
<p align="center">
© NEXON Games Co.,Ltd & Yostar,Inc. for headers and icons.
</p>
