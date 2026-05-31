# OpenAI Hosted Shell / Local Function Tools Safety Q&A

作成日: 2026-05-31

このメモは、このチャットセッションで行った「Corpus2Skill の Anthropic Skills API 代替として OpenAI Hosted Shell / OpenAI Skills / local function tools を使えるか」「安全性と回答品質をどう比較するか」についての質問と回答を整理したもの。

主に以下を参照した。

- OpenAI Responses API: https://developers.openai.com/api/reference/responses/overview
- OpenAI Tools overview: https://developers.openai.com/api/docs/guides/tools
- OpenAI Function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI Skills: https://developers.openai.com/api/docs/guides/tools-skills
- OpenAI Shell tool: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Agent Builder safety: https://developers.openai.com/api/docs/guides/agent-builder-safety
- OpenAI File Search: https://developers.openai.com/api/docs/guides/tools-file-search
- OpenAI Tool Search: https://developers.openai.com/api/docs/guides/tools-tool-search
- Anthropic Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Anthropic Skills guide: https://platform.claude.com/docs/en/build-with-claude/skills-guide
- Anthropic Create Skill API: https://platform.claude.com/docs/en/api/beta/skills/create
- Corpus2Skill paper: https://arxiv.org/abs/2604.14572
- OWASP LLM06:2025 Excessive Agency: https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
- NCSC, Prompt injection is not SQL injection: https://www.ncsc.gov.uk/blog-post/prompt-injection-is-not-sql-injection
- AgentVigil paper page: https://huggingface.co/papers/2505.05849

## Q1. このリポジトリで使われている Anthropic Skills API の代替実装として、OpenAI 関連ライブラリやフレームワークは使えるか。

### 質問

docs ディレクトリ配下のファイルを参照したり、web-search、openai-docs をしたりして、このリポジトリで使われている anthropic skills api の代替実装として openai 関連のライブラリやフレームワークが使えるか、調査してください。

### 回答

結論: 使える。ただし drop-in replacement ではなく、`serve.py` の API 呼び出し、tool loop、prompt 配置を OpenAI 用に作り直す必要がある。

最も近い代替は次の構成。

```text
OpenAI Responses API
  + OpenAI Skills
  + Hosted Shell or local shell
  + get_document function tool
```

Corpus2Skill の現行実装は、Anthropic 側では概ね次の役割分担になっている。

| 現行 Anthropic 側 | 役割 |
|---|---|
| `client.beta.skills.create(...)` | `.claude/skills/...` の Skill bundle を upload する |
| `container.skills` | upload 済み Skill を code execution container に mount する |
| `code_execution` | Claude が `ls`, `cat`, `grep` で `SKILL.md` / `INDEX.md` を探索する |
| `get_document` custom tool | leaf `INDEX.md` で見つけた `doc_id` から本文を取得する |

OpenAI 側では、これに近い対応がある。

| Corpus2Skill / Anthropic 側 | OpenAI 側の近い代替 | 評価 |
|---|---|---|
| `client.beta.skills.create(...)` | `POST /v1/skills` / OpenAI Skills | かなり近い |
| `container.skills` | Shell tool `environment.skills` / Containers `skills` | かなり近い |
| `code_execution` | OpenAI Shell tool hosted container | 近い |
| `ls` / `cat` / `grep` による探索 | Hosted Shell / local shell の shell command | 近い |
| `get_document` custom tool | OpenAI function calling / Agents SDK function tool | 置換可能 |
| serve time に検索 index なし | Skill tree + Shell traversal | 維持可能 |
| 文書 QA の簡易実装 | File Search + Vector Stores | 実用的だが思想は変わる |

OpenAI File Search も文書 QA には有用だが、vector store と semantic / keyword search を使うため、Corpus2Skill の「serve time に embeddings / BM25 / vector DB を使わない」という特徴とは別方式になる。

根拠:

- OpenAI Responses API は stateful interaction、built-in tools、function calling を提供する。  
  https://developers.openai.com/api/reference/responses/overview
- OpenAI Skills は `SKILL.md` manifest を含む versioned bundle で、hosted/local shell environments で利用できる。  
  https://developers.openai.com/api/docs/guides/tools-skills
- OpenAI Shell tool は Hosted Shell container または local runtime で command を実行できる。  
  https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Function calling は、モデルが tool call を出し、アプリ側が実行結果を返す仕組みなので、`get_document` を移植しやすい。  
  https://developers.openai.com/api/docs/guides/function-calling

## Q2. 「近い実装」と「production 安全性優先」で違うものを使うのはなぜか。

### 質問

近い実装と production 安全性優先で違うものをつかっているのはどうしてですか？なぜ安全性を優先すると、local function tools になるのでしょうか？また、local function tools とはなんですか？近い実装が安全ではない理由は何ですか？

### 回答

結論: 「近い実装」は Anthropic の構造を再現するために Shell / Skills を使う。一方、production 安全性では、必要最小限の操作だけを関数として渡す方が安全である。

違いは、モデルに渡す権限の粒度である。

Hosted Shell / Shell tool 版:

```text
モデル -> 任意の shell command を提案/実行
例: ls, cat, grep, find, sed, awk, python, rm, curl など
```

Local function tools 版:

```text
モデル -> 許可済み関数だけ呼ぶ
例:
  - list_top_skills()
  - read_nav_file(path)
  - search_entity_index(entity)
  - get_document(doc_id)
```

local function tools とは、OpenAI Function calling で定義する「アプリ側の関数」である。モデルは JSON schema に従って「この関数をこの引数で呼びたい」と返し、実際の処理は Python / サーバー側コードが実行する。

例:

```text
モデル: read_nav_file({"path": "skill-01/SKILL.md"}) を呼びたい
アプリ: path が c2s_output/.claude/skills 配下か検証
アプリ: ファイルを読む
アプリ: 結果を function_call_output としてモデルへ返す
```

local function tools の利点は、アプリ側で次を決定的に制限できる点。

- `path` は `.claude/skills` 配下だけ
- 読める拡張子は `.md`, `.json` だけ
- 書き込み不可
- network 不可
- `doc_id` は `documents.json` に存在するものだけ
- 返す文字数を上限付きにする
- 全 tool call をログに残す

近い実装が「危険」というより、Shell は open-ended tool なので、production では権限が広すぎる可能性がある。

OpenAI 公式も、Shell tool について「arbitrary shell commands can be dangerous」であり、sandbox、allowlist / denylist、監査ログが必要と説明している。Skills についても、Skill content は planning / tool usage / command execution に影響し、prompt injection-driven data exfiltration のリスクがあると説明している。

根拠:

- OpenAI Shell tool safety: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Skills risks and safety: https://developers.openai.com/api/docs/guides/tools-skills#risks-and-safety
- OpenAI Function calling: https://developers.openai.com/api/docs/guides/function-calling
- OpenAI Agent Builder safety: https://developers.openai.com/api/docs/guides/agent-builder-safety
- OWASP LLM06:2025 Excessive Agency: https://genai.owasp.org/llmrisk/llm062025-excessive-agency/

## Q3. OpenAI Hosted Shell とは何か。

### 質問

openai の hosted shell とはなんですか？

### 回答

Hosted Shell は、OpenAI 管理の一時コンテナ内で、LLM に shell command を実行させる Responses API tool である。

OpenAI Shell tool には2種類の実行先がある。

- `hosted shell`: OpenAI 管理 container で shell command を実行する
- `local shell`: 自分の runtime で `shell_call` を受け取り、自分で実行して結果を返す

Hosted Shell の主な仕様:

- Responses API 専用。Chat Completions API では使えない。
- 現在の hosted runtime は Debian 12 ベース。
- default working directory は `/mnt/data`。
- `/mnt/data` は user-downloadable artifacts 用のサポート対象パス。
- interactive TTY は非対応。
- `sudo` は使えない。
- default では outbound network access がない。
- `container_auto` で request 用 container を自動 provision できる。
- 明示 container を作成して `container_reference` で再利用できる。
- OpenAI Skills を `tools[].environment.skills` や container `skills` に mount できる。
- shell 実行は Responses output 上で `shell_call` / `shell_call_output` として表現される。

典型的な構成:

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
                    {"type": "skill_reference", "skill_id": "<skill_id>"}
                ],
            },
        }
    ],
    input="Use the mounted skill to inspect the document hierarchy.",
)
```

根拠:

- OpenAI Shell guide: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Skills guide: https://developers.openai.com/api/docs/guides/tools-skills

## Q4. Hosted Shell に guardrails を敷けば、grep / cat の柔軟性を使いつつ local function tools 並みの安全性を確保できるか。

### 質問

hosted shell を利用して shell 環境を LLM に用意して、何らかのガードレールを敷くことで、grep や cat などの LLM の柔軟な行動をドキュメント探索に利用しつつ、local function tools 並みの安全性を確保することは出来ないのですか？

### 回答

結論: かなり近づけることはできる。ただし、任意 shell を許す限り、local function tools と同等の「決定的な最小権限」にはなりにくい。

Hosted Shell でできる主な対策:

1. network disabled のまま使う。
2. container に corpus skill files 以外を置かない。
3. API key、secret、private config、raw `documents.json` 全体を入れない。
4. ephemeral container を使い、必要なら完了後に明示 delete する。
5. skill は開発者が事前レビューしたものだけ mount する。
6. end user に任意 Skill を選ばせない。
7. `version: "latest"` ではなく、可能なら Skill version を pin する。
8. prompt で `ls`, `cat`, `grep`, `find` 程度の read-only navigation に限定する。
9. shell command と output を必ずログに残す。
10. `get_document` は function tool 側に残す。
11. 最終回答前に、少なくとも1件以上 `get_document` が呼ばれていることをアプリ側で検査する。
12. 回答の factual claim は `get_document` の本文だけに基づく、という hard rule を Responses API の `instructions` 側に置く。

それでも残る弱点:

- prompt guardrail は決定的ではない。
- 「`grep` / `cat` だけ使え」と書いても、モデルが `python`, `sed`, `awk`, `find`, `rm`, `curl` などを使おうとする可能性は残る。
- Hosted Shell に per-command allowlist をアプリ側で実行前強制する公式機能は、確認した docs には見当たらない。
- strict な command allowlist をやりたいなら、Hosted Shell ではなく local shell mode で `shell_call` を受け取り、アプリ側で許可/拒否する方が向いている。
- LLM は prompt 内で instructions と untrusted data を完全に分離できるわけではないため、prompt injection を完全には防げない。

NCSC は、現行 LLM は instructions と data の security boundary を強制しないと説明している。OWASP LLM06 も、不要に広い tool 権限を Excessive Agency として扱い、最小権限・承認・範囲制限を推奨している。

根拠:

- OpenAI Shell risks and safety: https://developers.openai.com/api/docs/guides/tools-shell#risks-and-safety
- OpenAI Shell local mode: https://developers.openai.com/api/docs/guides/tools-shell#local-shell-mode
- OpenAI Skills risks and safety: https://developers.openai.com/api/docs/guides/tools-skills#risks-and-safety
- OpenAI Agent Builder safety: https://developers.openai.com/api/docs/guides/agent-builder-safety
- NCSC, Prompt injection is not SQL injection: https://www.ncsc.gov.uk/blog-post/prompt-injection-is-not-sql-injection
- OWASP LLM06:2025 Excessive Agency: https://genai.owasp.org/llmrisk/llm062025-excessive-agency/

## Q5. Hosted Shell を使う場合、具体的にどんな対策を入れるべきか。

### 質問

hosted shell を使うか、local function tools を使うかは、実際にどちらも試してみて、精度があまり変わらないようなら、安全性を考慮して local function tools を使うほうが良さそうですね。でももし決定的に hosted shell のほうが精度、つまり最終的な LLM 回答品質が高ければ、hosted shell を利用しつつガードレールを敷く工夫が必要そうですね。この hosted shell でできる対策について更に詳しく調査してください。

### 回答

結論: Hosted Shell が最終回答品質で明確に勝つ場合は、`network disabled + corpus-only container + secretsなし + skill version pin + shell trace監査 + get_document強制` を最低ラインにするのが妥当である。

### 1. Network は原則 disabled のままにする

Corpus2Skill の serve-time 文書探索では外部通信は不要である。したがって Hosted Shell の default である outbound network disabled を維持する。

OpenAI docs では、network access を有効化するには以下が必要とされる。

1. admin が org allow list を dashboard で設定する。
2. request 側で `network_policy` を明示する。

ただし、OpenAI docs は domain allowlist でも prompt injection-driven data exfiltration のリスクがあると説明している。Corpus2Skill 用途では network を開けない判断が自然である。

根拠: https://developers.openai.com/api/docs/guides/tools-shell#network-access

### 2. Container に置くデータを最小化する

Hosted Shell container には、文書探索に必要な navigation files だけを置く。

置いてよいもの:

- top-level `SKILL.md`
- child `INDEX.md`
- `entity_index.json`
- 必要なら軽量 metadata

置かないもの:

- API key
- `.env`
- private config
- raw `documents.json` 全体
- unrelated repository files
- user secrets
- package manager credentials

`documents.json` の本文は Hosted Shell から直接読ませず、`get_document(doc_id)` function tool で取得させる。これにより、Shell は「探索」専用、function tool は「本文取得」専用になる。

根拠: https://developers.openai.com/api/docs/guides/tools-shell#data-retention-and-container-lifecycle

### 3. Container lifecycle を短くする

基本は `container_auto` を使い、request ごとに短命 container として扱う。

複数 turn で同じ container を使う必要がある場合だけ explicit container を作り、短い `expires_after` を設定する。処理完了後は可能なら `client.containers.delete(container_id)` で明示削除する。

OpenAI docs では、Hosted Shell / Code Interpreter の container data は container active 中は残り、期限切れまたは明示削除で削除されると説明されている。

根拠: https://developers.openai.com/api/docs/guides/tools-shell#data-retention-and-container-lifecycle

### 4. Skill は事前レビューし、ユーザーに任意選択させない

OpenAI Skills は `SKILL.md` を含む versioned bundle であり、model planning / tool usage / command execution に影響しうる。したがって Skill は code / prompt と同じくレビュー対象にする。

本番では以下を守る。

- 開発者が検証済みの Skill だけを mount する。
- end user に open catalog から任意 Skill を選ばせない。
- Skill の内容に command 実行、外部通信、secret 参照、write action が含まれないか確認する。
- 本番では `latest` ではなく固定 version を優先する。

根拠: https://developers.openai.com/api/docs/guides/tools-skills#risks-and-safety

### 5. Hard rules は Skill ではなく Responses API instructions に置く

OpenAI Skills docs では、Skill instructions は system prompt input ではなく user prompt input と説明されている。

そのため、次のような hard rules は `SKILL.md` ではなく Responses API の `instructions` 側に置くべきである。

- `SKILL.md` / `INDEX.md` は navigation aid であり、回答根拠ではない。
- Every factual claim must trace to `get_document`.
- 最低1件の full document を `get_document` で読むまで回答しない。
- Shell は探索専用であり、本文根拠は function tool output のみ。
- 不明な場合は推測しない。

根拠: https://developers.openai.com/api/docs/guides/tools-skills#skills-in-the-user-prompt

### 6. Shell trace を監査する

OpenAI Shell は Responses output に `shell_call` / `shell_call_output` を出す。これを保存し、後段で検査する。

検査対象例:

- `rm`, `mv`, `cp`, `chmod`, `chown` など write / destructive 系 command
- `curl`, `wget`, `nc`, `ssh`, `scp` など network 系 command
- `python -c`, `node -e`, `perl -e`, `ruby -e` など任意コード実行
- `base64`, `xxd`, `tar`, `zip` など exfiltration 補助になりうる command
- `/proc`, `/etc`, environment variable 読み取り
- `.env`, key, token, credential を含む path / output
- 出力が異常に長い command

Hosted Shell では command 実行前の強制 allowlist はしにくいが、実行後監査で異常を検知し、回答を reject / retry することはできる。

根拠: https://developers.openai.com/api/docs/guides/tools-shell#risks-and-safety

### 7. `get_document` 呼び出しをサーバー側で必須化する

Corpus2Skill では `SKILL.md` / `INDEX.md` は navigation aid であり、最終回答の根拠ではない。したがって、Hosted Shell 版でも最終回答前に次を検査する。

- `get_document` が少なくとも1回呼ばれている。
- `doc_id` は `documents.json` に存在する。
- `get_document` の output size に上限を設ける。
- 最終回答が `get_document` output に含まれない情報を断定していない。

この検査は prompt だけでなく、アプリ側ロジックで強制する。

### 8. 高リスク操作は Hosted Shell に渡さない

Corpus2Skill の探索に必要なのは read 系操作だけである。したがって、Hosted Shell に以下を任せない。

- 文書本文全体の保存
- credential を必要とする処理
- 外部 fetch
- vector store upload
- repository write
- package install
- 任意 script 実行

これらが必要になった場合は、Hosted Shell ではなく専用 function tool または別 worker に切り出す。

### 9. Eval で Hosted Shell 版と local function tools 版を比較する

判断順序は次が妥当である。

1. 同一 corpus、同一 query set、同一 model で Hosted Shell 版と local function tools 版を評価する。
2. answer accuracy、groundedness、doc recall、tool turns、latency、cost、failure mode を比較する。
3. 品質差が小さいなら local function tools を採用する。
4. Hosted Shell が明確に勝つなら、上記の多層防御を入れて採用を検討する。

評価指標例:

| 指標 | 見るもの |
|---|---|
| answer accuracy | 最終回答が正しいか |
| groundedness | 回答が `get_document` 本文に基づくか |
| doc recall | 必要文書を取得できたか |
| navigation quality | 関連 Skill / INDEX を探索できたか |
| tool turns | 無駄な shell / function call が多すぎないか |
| unsafe command rate | 危険 command が出ないか |
| no-evidence answer rate | `get_document` なしで答えていないか |
| latency / cost | 実運用で許容できるか |

## Q6. 最終的な推奨判断は何か。

### 質問

hosted shell を使うか、local function tools を使うかは、実際にどちらも試してみて、精度があまり変わらないようなら、安全性を考慮して local function tools を使うほうが良さそうですね。でももし決定的に hosted shell のほうが精度、つまり最終的な LLM 回答品質が高ければ、hosted shell を利用しつつガードレールを敷く工夫が必要そうですね。

### 回答

この判断で妥当である。

推奨方針:

```text
第一候補:
  Responses API + local function tools
  - read_nav_file
  - search_entity_index
  - list_children
  - get_document

採用条件:
  Hosted Shell 版と回答品質が同等、または差が小さい。

理由:
  権限を最小化しやすい。
  path allowlist / read-only / output cap / doc_id validation をアプリ側で強制できる。
```

```text
第二候補:
  Responses API + OpenAI Skills + Hosted Shell + get_document function tool

採用条件:
  Hosted Shell 版が明確に高品質。
  特に skill tree navigation、cross-jump、grep 的探索で local function tools 版より良い。

必要条件:
  network disabled
  corpus-only container
  secrets なし
  raw documents.json なし
  reviewed skills only
  version pin
  shell trace audit
  get_document required
  post-check for groundedness
```

品質差が小さいなら local function tools がよい。Hosted Shell の柔軟性は魅力だが、任意 shell command という広い権限を持つため、production では防御と監査の実装コストが上がる。

Hosted Shell が大きく勝つ場合は、Corpus2Skill の研究的特徴、つまり「LLM が filesystem-like skill hierarchy を柔軟に navigate する」能力が効いている可能性がある。その場合は、Hosted Shell を探索専用に閉じ込め、回答根拠は `get_document` function tool に限定する設計がよい。

## 付録: Corpus2Skill 向け Hosted Shell 構成案

```text
Responses API
  model = gpt-5.5 or equivalent tool-capable model
  instructions:
    - You answer by navigating a skill directory.
    - Shell is for navigation only.
    - SKILL.md / INDEX.md are navigation aids, not evidence.
    - Every factual claim must trace to get_document output.
    - Read at least one full document via get_document before answering.
    - Never use network commands.
    - Never read secrets, env vars, or unrelated files.

  tools:
    - shell
      environment:
        type: container_auto
        network: disabled by default
        skills:
          - reviewed skill_reference or inline skill bundle

    - get_document
      type: function
      strict: true
      parameters:
        doc_id: string

  server-side post checks:
    - shell_call audit
    - require >= 1 get_document call
    - validate doc_id
    - cap get_document output
    - reject unsupported commands
    - reject answer if no evidence
```

## 付録: Corpus2Skill 向け Local Function Tools 構成案

```text
Responses API
  instructions:
    - You answer by navigating a hierarchical skill index.
    - Use tools to inspect only allowed navigation files.
    - Use get_document for final evidence.

  tools:
    - list_top_skills()
    - read_nav_file(path)
    - list_children(path)
    - search_entity_index(entity)
    - get_document(doc_id)

  server-side controls:
    - path must stay under c2s_output/.claude/skills
    - allow only .md and .json navigation files
    - no writes
    - no shell
    - no network
    - output length cap
    - doc_id validation
    - full audit log
```

## 参照 URL 一覧

一次情報 / official:

- https://developers.openai.com/api/reference/responses/overview
- https://developers.openai.com/api/docs/guides/tools
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/guides/tools-skills
- https://developers.openai.com/api/docs/guides/tools-shell
- https://developers.openai.com/api/docs/guides/tools-file-search
- https://developers.openai.com/api/docs/guides/tools-tool-search
- https://developers.openai.com/api/docs/guides/agent-builder-safety
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- https://platform.claude.com/docs/en/build-with-claude/skills-guide
- https://platform.claude.com/docs/en/api/beta/skills/create
- https://www.ncsc.gov.uk/blog-post/prompt-injection-is-not-sql-injection
- https://genai.owasp.org/llmrisk/llm062025-excessive-agency/

論文 / research:

- https://arxiv.org/abs/2604.14572
- https://huggingface.co/papers/2505.05849

