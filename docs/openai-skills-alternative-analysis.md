# OpenAI で Anthropic Skills API 相当を実現できるか

作成日: 2026-05-30

## 結論

OpenAI には Anthropic Skills API 相当にかなり近い仕組みがある。2026-05-30 時点では、OpenAI 公式ドキュメントに `Skills`、`Shell`、`Containers`、`Agents SDK`、`Tool search`、`File search` があり、Corpus2Skill の Anthropic 依存部分は OpenAI に移植可能性がある。

ただし、移植方針は2つに分かれる。

```text
A. Corpus2Skill の思想を維持する移植
  OpenAI Skills + hosted Shell container + custom function tool
  -> SKILL.md / INDEX.md を shell で探索
  -> doc_id から get_document 相当で全文取得

B. OpenAI 標準RAGへ寄せる移植
  OpenAI File Search + Vector Stores
  -> semantic + keyword search
  -> Corpus2Skill の「navigate, not retrieve」という特徴は弱まる
```

Corpus2Skill の設計思想を維持するなら、最も近い代替は **OpenAI Skills + Shell tool hosted container + function calling** である。`File Search` は文書QAとしては実用的だが、serve time に検索インデックスを使うため、Corpus2Skill の「serve time に embeddings / BM25 / vector DB を使わない」という特徴とは異なる。

## 調査対象

このリポジトリの現状:

- `pyproject.toml` は `anthropic>=0.40.0` に依存しており、OpenAI SDK 依存はない。
- `corpus2skill/serve.py` は Anthropic の `client.beta.skills.create(...)`、`container.skills`、`code_execution` を使っている。
- serve time の文書探索は、`SYSTEM_PROMPT` による `SKILL.md` / `INDEX.md` 探索と、独自 `get_document` tool に依存している。

主なローカル該当箇所:

- `corpus2skill/serve.py:23` - `SKILLS_BETA = "skills-2025-10-02"`
- `corpus2skill/serve.py:28` - `SYSTEM_PROMPT`
- `corpus2skill/serve.py:106` - `GET_DOCUMENT_TOOL`
- `corpus2skill/serve.py:125` - Anthropic `code_execution` tool
- `corpus2skill/serve.py:278` - `_upload_skills()`
- `corpus2skill/serve.py:309` - `client.beta.skills.create(...)`
- `corpus2skill/serve.py:382` - `skill_ids`
- `corpus2skill/serve.py:388` - `container = {"skills": skill_ids}`
- `corpus2skill/serve.py:417` - `client.beta.messages.create(...)` に渡す API request

## OpenAI 側の一次情報

### OpenAI Skills

公式ドキュメント:

- https://developers.openai.com/api/docs/guides/tools-skills

OpenAI の `Skills` は、`SKILL.md` manifest と複数ファイルからなる versioned bundle である。OpenAI 公式ドキュメントでは、company style guides から multi-step workflows まで、プロセスや規約を codify するための modular instructions と説明されている。

重要点:

- `SKILL.md` は front matter と instructions を含む。
- directory upload と zip upload ができる。
- `POST /v1/skills` で作成できる。
- version を持つ。
- hosted skill を作らず、inline zip bundle を `environment.skills` に入れる方法もある。
- OpenAI curated skill もあり、例として `openai-spreadsheets` が挙げられている。
- OpenAI Skills は open Agent Skills standard と互換と説明されている。

OpenAI Skills の最小例:

```markdown
---
name: basic-math
description: Add or multiply numbers.
---

Use this skill when you need a quick sum or product of numbers.
```

OpenAI Skills 作成の例:

```bash
curl -X POST 'https://api.openai.com/v1/skills' \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -F 'files[]=@./basic_math/SKILL.md;filename=basic_math/SKILL.md;type=text/markdown' \
  -F 'files[]=@./basic_math/calculate.py;filename=basic_math/calculate.py;type=text/plain'
```

または zip:

```bash
curl -X POST 'https://api.openai.com/v1/skills' \
  -H "Authorization: Bearer $OPENAI_API_KEY" \
  -F 'files=@./basic_math.zip;type=application/zip'
```

### OpenAI Skills の model-visible behavior

公式ドキュメント:

- https://developers.openai.com/api/docs/guides/tools-skills

OpenAI 公式ドキュメントは、skills が tool に対して available になると、platform が各 skill の `name`、`description`、`path` を user prompt context に追加すると説明している。モデルはその metadata に基づいて skill を使うか判断し、使う場合は `path` を使って `SKILL.md` の full Markdown instructions を読む。

これは Anthropic Agent Skills の progressive disclosure と非常に近い。つまり、最初から全 Skill 本文を context に入れるのではなく、まず軽量 metadata を見せ、必要になったら `SKILL.md` を読むという設計である。

注意点:

- Skill instructions は system prompt ではなく user prompt input として扱われる。
- 明示制御したい場合は「use the `<skill name>` skill」と指示できる。

### OpenAI Shell tool

公式ドキュメント:

- https://developers.openai.com/api/docs/guides/tools-shell

OpenAI の Shell tool は Responses API の tool で、hosted container execution をサポートする。Skills は hosted shell environment に mount できる。

Skills を付けて container を作る例:

```python
from openai import OpenAI

client = OpenAI()

container = client.containers.create(
    name="skill-container",
    skills=[
        {"type": "skill_reference", "skill_id": "skill_..."},
        {"type": "skill_reference", "skill_id": "openai-spreadsheets", "version": "latest"},
    ],
)
```

Responses API で shell tool に skills を付ける例:

```python
from openai import OpenAI

client = OpenAI()

response = client.responses.create(
    model="gpt-5.5",
    tools=[
        {
            "type": "shell",
            "environment": {
                "type": "container_auto",
                "skills": [
                    {"type": "skill_reference", "skill_id": "<skill_id>"},
                    {"type": "skill_reference", "skill_id": "<skill_id>", "version": 2},
                ],
            },
        }
    ],
    input="Use the skills to add 144 and 377.",
)
```

Shell tool の重要点:

- hosted container はデフォルトで outbound network access がない。
- network access を使う場合は org allowlist と request 側の `network_policy` が必要。
- `container_auto` で request 用 container を provision できる。
- 既存 container を再利用する場合は `container_reference` / container id を使う。
- `environment.skills` は skill reference と inline skill bundle を受け取れる。

### OpenAI Agents SDK

公式ドキュメント:

- https://openai.github.io/openai-agents-python/tools/

OpenAI Agents SDK は、OpenAI-hosted tools、local/runtime tools、function tools、agents as tools などを扱う。Anthropic Skills API から Corpus2Skill を移植する場合、特に重要なのは次。

- `ShellTool`
- `ShellToolSkillReference`
- `Function tools`
- `ToolSearchTool`
- `FileSearchTool`
- `CodeInterpreterTool`

Agents SDK の hosted container shell + skills の例:

```python
from agents import Agent, Runner, ShellTool, ShellToolSkillReference

csv_skill: ShellToolSkillReference = {
    "type": "skill_reference",
    "skill_id": "skill_...",
    "version": "1",
}

agent = Agent(
    name="Container shell agent",
    model="gpt-5.5",
    instructions="Use the mounted skill when helpful.",
    tools=[
        ShellTool(
            environment={
                "type": "container_auto",
                "network_policy": {"type": "disabled"},
                "skills": [csv_skill],
            }
        )
    ],
)

result = await Runner.run(
    agent,
    "Use the configured skill to analyze CSV files in /mnt/data.",
)
```

Agents SDK の説明では、`ShellTool` は hosted container execution と local execution の両方に対応する。Corpus2Skill の現在の Anthropic `code_execution` に最も近いのは、この hosted container shell mode である。

### OpenAI Code Interpreter

公式ドキュメント:

- https://developers.openai.com/api/docs/guides/tools-code-interpreter

Code Interpreter は sandboxed virtual machine で Python code を実行する tool である。container object が必要で、auto mode と explicit mode がある。

Code Interpreter の特徴:

- Python 実行に向く。
- container に files を含められる。
- container は auto create / explicit create できる。
- memory tier は `1g`、`4g`、`16g`、`64g` などがある。
- container は ephemeral に扱うべきで、期限切れになると状態は失われる。

Corpus2Skill への適用:

- `SKILL.md` / `INDEX.md` を単に読むだけなら Shell tool の方が自然。
- Python で `documents.json` を検索する補助処理を container 内に持たせるなら Code Interpreter も使える。
- ただし、Anthropic の `code_execution` で `ls` / `cat` / `grep` する設計に近いのは Shell tool である。

### OpenAI File Search / Vector Stores

公式ドキュメント:

- https://developers.openai.com/api/docs/guides/tools-file-search
- https://developers.openai.com/api/docs/guides/retrieval

File Search は Responses API の hosted tool で、vector store にアップロードしたファイルに対して semantic search と keyword search を行う。OpenAI が tool execution を管理するため、アプリ側で検索処理を実装しなくてよい。

File Search の特徴:

- 事前に vector store を作る。
- ファイルを upload して vector store に追加する。
- Responses API の tools に `file_search` を指定する。
- モデルが必要と判断すると tool を自動実行する。
- semantic + keyword search を使う。

Corpus2Skill への適用:

- 実用的な文書QAを作るだけなら有力。
- ただし Corpus2Skill の「serve time に vector DB / BM25 / embeddings を使わない」という主張とは異なる。
- 論文実装の代替というより、RAG baseline / product implementation としての代替になる。

### OpenAI Tool Search

公式ドキュメント:

- https://developers.openai.com/api/docs/guides/tools-tool-search
- https://openai.github.io/openai-agents-python/tools/

Tool Search は、モデルが必要に応じて tool を検索・load するための仕組みである。OpenAI 公式ドキュメントでは、すべての tool definitions を最初から model context に入れず、必要になった tool subset だけを load することで token 使用量と cost を抑える用途として説明されている。

重要点:

- `gpt-5.4` 以降が `tool_search` をサポートする。
- `tools` array に `{"type": "tool_search"}` を追加する。
- function tools では `defer_loading: true` を付ける。
- namespace や MCP server で grouping するのが推奨される。
- namespace / MCP server の場合、最初は name と description だけが見える。
- individual deferred function の場合、主に parameter schema の遅延読み込みになる。
- hosted tool search と client-executed tool search がある。

Corpus2Skill への適用:

- Anthropic / OpenAI Skills の progressive disclosure と同じ問題意識を tool surface に適用する機能である。
- `get_document` 以外にも多数の補助 tool を持つ設計に拡張するなら有用。
- ただし、`SKILL.md` / `INDEX.md` というファイルツリー探索そのものを置き換えるものではない。

## Anthropic と OpenAI の対応関係

| Corpus2Skill / Anthropic 側 | OpenAI 側の近い代替 | 評価 |
|---|---|---|
| `client.beta.skills.create(...)` | `POST /v1/skills` / OpenAI SDK `client.skills` 相当 | かなり近い |
| Anthropic `container.skills` | Shell tool `environment.skills` / Containers `skills` | かなり近い |
| Anthropic `code_execution` | OpenAI Shell tool hosted container | 最も近い |
| Anthropic Code Execution で `ls` / `cat` / `grep` | OpenAI Shell tool で shell command 実行 | 近い |
| `SKILL.md` frontmatter の `name` / `description` | OpenAI Skills `SKILL.md` frontmatter | 近い |
| Skill metadata から必要時に `SKILL.md` を読む | OpenAI Skills の `name` / `description` / `path` 提示と `SKILL.md` 読み込み | 近い |
| `get_document` custom tool | OpenAI function calling / Agents SDK function tool | 置換可能 |
| serve time に検索インデックスなし | Shell tool + Skill tree traversal | 維持可能 |
| 文書QAだけを簡単に実装 | File Search + Vector Stores | 実用的だが思想は変わる |

## Corpus2Skill を OpenAI に移植する場合の設計案

### 案A: Corpus2Skill の思想を維持する

目的:

- `SKILL.md` / `INDEX.md` 階層を維持する。
- serve time に vector store / BM25 を使わない。
- Claude ではなく OpenAI model に `cat` / `grep` 相当の探索をさせる。
- `get_document` 相当を function tool として実装する。

構成:

```text
compile time
  Corpus2Skill 既存処理を維持
  -> .claude/skills/ 相当の Skill tree を生成
  -> documents.json を生成

upload time
  OpenAI Skills API に top-level Skill bundle を upload
  または inline skill bundle として Shell environment に渡す

serve time
  Responses API or Agents SDK
  -> Shell tool with hosted container
  -> environment.skills に skill_reference を指定
  -> instructions に Corpus2Skill の navigation policy を移植
  -> function tool get_document(doc_id) を定義
```

OpenAI Agents SDK の形に寄せた疑似コード:

```python
from agents import Agent, Runner, ShellTool, function_tool

@function_tool
def get_document(doc_id: str) -> str:
    return doc_store.get(doc_id, f"Document not found: {doc_id}")

agent = Agent(
    name="Corpus2Skill OpenAI Agent",
    model="gpt-5.5",
    instructions=SYSTEM_PROMPT_FOR_OPENAI,
    tools=[
        ShellTool(
            environment={
                "type": "container_auto",
                "network_policy": {"type": "disabled"},
                "skills": [
                    {"type": "skill_reference", "skill_id": "...", "version": "latest"},
                ],
            }
        ),
        get_document,
    ],
)

result = await Runner.run(agent, user_query)
```

この案の利点:

- Corpus2Skill の独自性を維持できる。
- `SKILL.md` / `INDEX.md` の階層探索をそのまま使える可能性が高い。
- Anthropic Skills API 依存を OpenAI Skills / Shell に置き換えられる。

この案のリスク:

- OpenAI hosted Shell / Skills の API surface は新しめで、SDK version と model support の確認が必要。
- Anthropic の `container.skills` と OpenAI の `environment.skills` は似ているが完全互換ではない。
- OpenAI Skills の file count / zip size / uncompressed file size 制限に合わせる必要がある。
- Corpus2Skill の `SYSTEM_PROMPT` は Anthropic tool behavior 前提の文言を含むため、OpenAI Responses / Agents SDK 用に調整が必要。

### 案B: OpenAI File Search に寄せる

目的:

- 実装を簡単にする。
- OpenAI managed retrieval に任せる。
- 文書QAの品質と運用性を重視する。

構成:

```text
compile time
  documents を OpenAI Vector Store に upload

serve time
  Responses API
  -> File Search tool
  -> vector_store_ids を指定
  -> model が semantic + keyword search で関連文書を取得
```

疑似コード:

```python
from openai import OpenAI

client = OpenAI()

response = client.responses.create(
    model="gpt-5.5",
    input="How do I add a custom domain to my site?",
    tools=[{
        "type": "file_search",
        "vector_store_ids": ["vs_..."],
        "max_num_results": 5,
    }],
)

print(response.output_text)
```

この案の利点:

- 実装量が少ない。
- OpenAI managed tool として運用しやすい。
- semantic + keyword search を併用できる。

この案のリスク:

- Corpus2Skill の主張である「No vector DB, no retrieval index at serve time」から外れる。
- SKILL tree、entity_index、Related skills、Example documents などの設計資産を使わない。
- 論文実装の比較対象としては、別方式の RAG になる。

### 案C: Tool Search を併用する

目的:

- 大量の補助 function tools / namespaces を遅延読み込みしたい。
- Skill tree traversal とは別に、tool surface の progressive disclosure を実現したい。

構成:

```text
Responses API / Agents SDK
  -> Tool Search
  -> namespace or MCP server
  -> deferred function tools
```

Corpus2Skill への使いどころ:

- `get_document` 以外に、`list_skills`、`read_index`、`search_entity_index`、`compare_candidates` などを function tools 化する場合。
- tenant / corpus ごとに使える tools が異なる場合。
- tool schema が大きくなり、毎回すべて prompt に入れるのが無駄になる場合。

ただし、現状の Corpus2Skill は `get_document` だけなので、Tool Search は必須ではない。

## OpenAI 移植時の主要な実装差分

### 1. Anthropic client の置換

現在:

```python
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
```

OpenAI 案:

```python
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
```

### 2. Skill upload の置換

現在:

```python
skill = client.beta.skills.create(
    display_title=upload_name,
    files=files,
    betas=[SKILLS_BETA],
)
```

OpenAI 案:

```python
skill = client.skills.create(
    files=[
        # SDK の実際の引数形に合わせて multipart upload
    ],
)
```

または shell guide に合わせて `client.containers.create(..., skills=[...])` に直接 inline bundle を渡す。

注意:

- OpenAI Python SDK の `client.skills.create` の実際の型・引数名は利用バージョンで確認が必要。
- 公式 curl 例では `/v1/skills` に multipart upload している。

### 3. Messages API から Responses API / Agents SDK への置換

現在:

```python
response = client.beta.messages.create(
    model=cfg.llm_model,
    max_tokens=8192,
    system=system_prompt,
    tools=tools,
    messages=messages,
    betas=cfg.skills_betas,
    container=container,
)
```

OpenAI Responses API 案:

```python
response = client.responses.create(
    model=cfg.llm_model,
    instructions=SYSTEM_PROMPT_FOR_OPENAI,
    input=query,
    tools=[
        {
            "type": "shell",
            "environment": {
                "type": "container_auto",
                "network_policy": {"type": "disabled"},
                "skills": skill_refs,
            },
        },
        {
            "type": "function",
            "name": "get_document",
            "description": "Retrieve full document text by doc_id.",
            "parameters": {...},
        },
    ],
)
```

Agents SDK 案:

```python
agent = Agent(
    name="Corpus2Skill Agent",
    model=cfg.llm_model,
    instructions=SYSTEM_PROMPT_FOR_OPENAI,
    tools=[ShellTool(...), get_document],
)

result = await Runner.run(agent, query)
```

### 4. Tool result loop の置換

Anthropic 実装では、`tool_use` block を見て Python 側で `get_document` を実行し、`tool_result` を次 turn の user message として返している。

該当箇所:

- `corpus2skill/serve.py:493`

OpenAI Responses API で function calling を直接扱う場合は、function call output を Responses API の次 input に返す loop が必要になる。Agents SDK を使う場合は、Python function tool として登録すれば SDK 側が tool execution を扱えるため、実装量を減らせる可能性がある。

### 5. System prompt の調整

現在の `SYSTEM_PROMPT` は Anthropic Code Execution Tool 前提の表現を含む。

OpenAI 版では、少なくとも次を調整する。

- `Code execution` を `Shell tool` / `hosted shell` に置換する。
- Skill files の mount path は OpenAI の実際の hosted shell behavior に合わせて確認する。
- `get_document` の tool name / function schema を OpenAI 形式に合わせる。
- Skill instructions が user prompt input 扱いになる点を踏まえ、developer / system instructions 側に必須ルールを置く。

## 制限・注意点

### OpenAI Skills の制限

公式ドキュメントで確認できた制限:

- `SKILL.md` / `skill.md` は bundle 内に exactly one。
- front matter validation は Agent Skills specification に従う。
- maximum zip upload size は `50 MB`。
- maximum file count per skill version は `500`。
- maximum uncompressed file size は `25 MB`。

Corpus2Skill は Anthropic 側の `MAX_FILES_PER_SKILL = 200` を意識している。

該当箇所:

- `corpus2skill/serve.py:132`

OpenAI 側では file count limit が 500 とされているため、分割戦略は再調整できる可能性がある。ただし、API 実運用では model context、Shell execution、latency、upload time も制約になる。

### Network access

OpenAI hosted containers はデフォルトで outbound network access がない。network access を有効化するには org allowlist と request 側 `network_policy` が必要。

Corpus2Skill の文書探索は外部ネットワークを必要としないため、`network_policy: {"type": "disabled"}` が自然である。

### Security

OpenAI Skills 公式ドキュメントは、Skills を Responses API と併用する際、prompt injection による data exfiltration などのリスクがあるため、Skill を privileged code and instructions として扱い、開発者が検査して統合すべきだと警告している。

Corpus2Skill の場合:

- 入力文書から生成された `SKILL.md` / `INDEX.md` に悪意ある instruction が混入する可能性がある。
- 現在の実装では `SKILL.md` / `INDEX.md` を navigation aids として扱い、事実根拠は `get_document` 取得文書に限定する system prompt がある。
- OpenAI 移植時も、Skill content をそのまま高優先度 instruction として信頼しない設計が必要。

### Skill instructions の優先度

OpenAI 公式ドキュメントでは、Skill instructions は system prompt ではなく user prompt input として扱われると説明されている。

つまり、Corpus2Skill の hard rules は Skill file ではなく、OpenAI Responses API の `instructions`、または Agents SDK の `instructions` に置くべきである。

例:

```text
Hard rules:
- SKILL.md / INDEX.md are navigation aids only.
- Every factual claim must trace to a document retrieved via get_document.
- Never use SKILL.md / INDEX.md content as final answer evidence.
```

## 判断

OpenAI ライブラリで Anthropic Skills API 相当を実現できるか、という問いへの答えは **Yes**。特に OpenAI Skills と Shell tool hosted container は、Anthropic Skills API + container.skills + code_execution に近い。

ただし、完全な drop-in replacement ではない。

```text
できること:
  - SKILL.md を含む versioned skill bundle を upload / attach する
  - hosted container 上で shell を使う
  - skill metadata を model に見せ、必要時に SKILL.md を読ませる
  - custom function tool で get_document 相当を実装する

注意が必要なこと:
  - API 名、SDK 型、request shape は Anthropic と異なる
  - Anthropic Messages API の tool loop は OpenAI Responses / Agents SDK に合わせて作り直す
  - Skill instructions の優先度が user prompt input 扱いになる
  - hosted shell / skills は対応 model と SDK version の確認が必要
```

Corpus2Skill の研究的特徴を保つなら、OpenAI 移植は `File Search` ではなく、`OpenAI Skills + ShellTool + get_document function tool` を中心に設計するのが妥当である。

## 参考URL

一次情報:

- OpenAI Skills: https://developers.openai.com/api/docs/guides/tools-skills
- OpenAI Shell tool: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Agents SDK Tools: https://openai.github.io/openai-agents-python/tools/
- OpenAI Code Interpreter: https://developers.openai.com/api/docs/guides/tools-code-interpreter
- OpenAI File Search: https://developers.openai.com/api/docs/guides/tools-file-search
- OpenAI Retrieval / Vector Stores: https://developers.openai.com/api/docs/guides/retrieval
- OpenAI Tool Search: https://developers.openai.com/api/docs/guides/tools-tool-search

ローカル実装:

- `corpus2skill/serve.py`
- `corpus2skill/skill_builder.py`
- `corpus2skill/config.py`
- `README.md`
