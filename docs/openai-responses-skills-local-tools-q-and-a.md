# OpenAI Responses API + Skills / Shell / Function Tools Q&A

作成日: 2026-05-31

このメモは、このチャットセッションで行った「Anthropic Skills API の代替として OpenAI Responses API + OpenAI Skills + Shell tool + local/function tools を使う案」についての質問と回答を整理したもの。

## Q1. このリポジトリの Anthropic Skills API の代替実装として、OpenAI Responses API + local function tools を使うのはどうか。

### 質問

このリポジトリのanthorpic skills apiの代替実装として、openai responses api + local function tools を使おうと思うのですが、どうでしょうか？

### 回答

結論: ありです。ただし「Anthropic Skills API の代替」としては、Responses API + local function tools だけより、まずは Responses API + local function tools で安全なMVP、必要なら OpenAI Skills + Shell に寄せる、が現実的です。

判断:

- この repo の本質は、serve time に `SKILL.md` / `INDEX.md` を探索し、最後に `get_document` で本文を取ることです。README でも「No vector DB, no retrieval index」と明記されています。
- 現実装は Anthropic の `Skills API + code_execution + get_document` に強く依存しています。
- OpenAI 公式 docs 上、Responses API は function calling と外部データ連携を正式にサポートしています。
- function calling は「モデルが tool call を出す → アプリ側で実行 → `function_call_output` を返す」流れなので、`get_document` や `read_index` は置換しやすいです。
- OpenAI にも Skills があります。`SKILL.md` bundle、hosted/local shell、`name` / `description` / `path` の提示、必要時に `SKILL.md` を読む挙動があり、Anthropic Skills にかなり近いです。
- Shell tool は hosted container と local runtime の両方に対応しています。
- Anthropic 側も Skills は `container.skills` と code execution tool 前提です。
- Corpus2Skill 論文自体も、階層 skill directory を LLM が navigate する設計です。

おすすめ構成:

```text
Responses API
  instructions = OpenAI向けに調整した SYSTEM_PROMPT
  tools:
    - list_top_skills()
    - read_nav_file(path)
    - search_entity_index(entity)
    - get_document(doc_id)
```

この構成なら、任意 shell 実行を許さず、ローカル filesystem へのアクセスも allowlist できます。`cat | grep` をモデルに実行させるより、`read_nav_file` / `search_entity_index` のような安全な関数に分解する方が production 実装として堅いです。

ただし、論文実装に近い「Skills の progressive disclosure」を再現したいなら、次の構成が近いです。

```text
OpenAI Responses API
  + OpenAI Skills
  + Shell tool hosted/local
  + get_document function tool
```

注意点:

- local function tools だけだと、Anthropic Skills API の「skill bundle を mount して metadata から必要時に読む」部分はネイティブには再現しません。自前の `list/read/search` tools で代替する形です。
- OpenAI Skills docs では、Skill instructions は system ではなく user prompt input 扱いです。なので `Every factual claim must trace to get_document` などの hard rules は必ず Responses API の `instructions` 側に置くべきです。
- OpenAI File Search は便利ですが、この repo の「serve time に vector DB / BM25 / retrieval index なし」という特徴から外れます。
- 既存の `SYSTEM_PROMPT` は `Code execution`, `ls`, `cat`, `grep` 前提なので、OpenAI function tools 版では書き換えが必要です。

結論として、案は妥当です。特に最初の移植は OpenAI Responses API + local function tools が一番安全で実装しやすいです。ただし「Anthropic Skills API 互換」を強く意識するなら、次段階で OpenAI Skills + Shell + function tool も検証対象に入れるべきです。

根拠:

- OpenAI Responses API: https://developers.openai.com/api/reference/responses/overview
- OpenAI Function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI Skills: https://developers.openai.com/api/docs/guides/tools-skills
- OpenAI Shell tool: https://developers.openai.com/api/docs/guides/tools-shell
- Anthropic Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Corpus2Skill paper: https://arxiv.org/abs/2604.14572

## Q2. 理想の Anthropic Skills API 代替実装は、Responses API + OpenAI Skills + Shell tool hosted/local + get_document function tool でよいか。それぞれ何をするものか。

### 質問

理想のanthropic skills api の代替実装としては、以下の構成がいいのでしょうか？もしそうなら、以下の４つはそれぞれ何をするためのものですか？anthoropic skills apiの機能と照らし合わせて解説してください。

```text
OpenAI Responses API
  + OpenAI Skills
  + Shell tool hosted/local
  + get_document function tool
```

### 回答

結論: はい。この4点セットが Anthropic Skills API 実装に一番近い代替構成です。ただし `get_document function tool` は Skills API 相当ではなく、Corpus2Skill 独自の「本文取得」機能です。

対応関係:

| OpenAI側 | 役割 | Anthropic側の対応 |
|---|---|---|
| `Responses API` | 全体の会話・tool orchestration | `client.beta.messages.create(...)` |
| `OpenAI Skills` | `SKILL.md` を含む skill bundle | Anthropic `/v1/skills` / Custom Skills |
| `Shell tool hosted/local` | skill files を読ませる実行環境 | Anthropic `code_execution` container |
| `get_document function tool` | `doc_id` から本文を返す | repo独自の `get_document` custom tool |

### 1. OpenAI Responses API

これは「エージェント実行の親」です。ユーザー入力、system/developer相当の `instructions`、Shell tool、function tool をまとめてモデルに渡し、モデルの tool call を受け取ります。

Anthropic でいうと、この部分です。

```python
client.beta.messages.create(
    system=SYSTEM_PROMPT,
    tools=[code_execution, get_document],
    container={"skills": skill_ids},
    messages=messages,
)
```

OpenAIではこれを `client.responses.create(...)` に置き換えます。つまり Responses API 自体は Skills ではなく、Skills / Shell / function tool を束ねる実行APIです。

### 2. OpenAI Skills

これは `SKILL.md` と関連ファイルをまとめた versioned bundle です。Corpus2Skill では `.claude/skills/.../SKILL.md` や `INDEX.md` 群がここに相当します。

Anthropic Skills API の役割にかなり近いです。

- skill を upload / version管理する
- `name` / `description` でモデルに存在を知らせる
- 必要になったら `SKILL.md` や関連ファイルを読む
- Corpus2Skill の階層ナビゲーション対象になる

Anthropic 側の `container.skills` に近いものは、OpenAI では Shell tool の `environment.skills` です。

注意点として、OpenAI docs では Skill instructions は system prompt ではなく user prompt input 扱いです。したがって「`SKILL.md` は根拠ではなく navigation aid」「事実は `get_document` 経由の本文に限定」のような hard rules は Responses API の `instructions` 側に置くべきです。

### 3. Shell tool hosted/local

これはモデルが skill file tree を実際に読むための実行環境です。Corpus2Skill では、モデルが `ls`, `cat`, `grep` で `SKILL.md` / `INDEX.md` / `entity_index.json` を探索します。そのため単に Skills を登録するだけでは足りず、ファイルを読める実行環境が必要です。

Anthropic では Skills API 利用に `code_execution` container が必要です。OpenAI 側ではそれに一番近いのが `Shell tool` です。

- hosted: OpenAI管理の container で実行
- local: 自分のruntimeで shell call を実行して結果を返す

Corpus2Skill用途では、外部ネットワークは基本不要なので hosted なら network disabled が自然です。より安全にしたいなら local shell よりも `read_nav_file(path)` のような function tool 化もありです。

### 4. get_document function tool

これは Skills API の代替ではなく、Corpus2Skill の QA 品質を支える独自toolです。

`SKILL.md` / `INDEX.md` は「どこを見るか」を決める navigation aid で、最終回答の根拠ではありません。leaf の `INDEX.md` で `doc_id` を見つけたあと、`get_document(doc_id)` で `documents.json` から全文を取得し、その本文だけを根拠に答えます。

Anthropic版でも同じ構造です。`serve.py` の `GET_DOCUMENT_TOOL` と `_get_document()` がそれです。

実行フロー:

```text
1. Responses API が instructions + tools を受け取る
2. OpenAI Skills が skill tree を提供する
3. Shell tool で SKILL.md / INDEX.md / entity_index.json を読む
4. doc_id を見つける
5. get_document function tool で全文を取得する
6. 取得本文に基づいて最終回答する
```

根拠:

- OpenAI Responses API: https://developers.openai.com/api/reference/responses/overview
- OpenAI Skills: https://developers.openai.com/api/docs/guides/tools-skills
- OpenAI Shell tool: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Function calling: https://developers.openai.com/api/docs/guides/function-calling
- Anthropic Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Anthropic Create Skill API: https://platform.claude.com/docs/en/api/beta/skills/create

注意点:

- `OpenAI Skills + Shell` は Anthropic Skills API に近いが、drop-in replacement ではありません。
- `get_document` は必須に近いです。これがないと `INDEX.md` の要約だけで答えがちになり、Corpus2Skill の「本文根拠」設計が崩れます。
- 安全性重視なら、Shell で自由に `cat/grep` させるより、`read_index`, `list_children`, `search_entity_index`, `get_document` を function tools に分ける設計も有力です。
