# Anthropic Agent Skills / Skills API と Corpus2Skill の関係

作成日: 2026-05-29

## 結論

Corpus2Skill は Anthropic Agent Skills を「通常の作業手順 Skill」として使っているというより、Anthropic Skills API と Code Execution container を使って、生成済みの `SKILL.md` / `INDEX.md` ファイル群を Claude から読める状態にし、その上で独自の system prompt によって文書ツリーを探索させている。

そのため、最も正確な理解は次の通り。

```text
Anthropic Agent Skills / Skills API
  = Skill bundle を登録し、Messages API の container 内で使えるようにする基盤

Corpus2Skill
  = その Skill bundle を文書探索用インデックスとして生成し、
    system prompt で SKILL.md -> INDEX.md -> doc_id -> get_document の探索を指示する実装
```

「Anthropic Agent Skills の progressive disclosure がそのまま主役として文書検索をしている」と言うのは強すぎる。実際には、公式の Skill 機構は `name` / `description` のメタデータ提示、Skill ファイルの container 配置、Code Execution 経由のファイルアクセスという土台を提供し、Corpus2Skill 側がアプリケーション固有の段階的探索を prompt と生成ファイルで実装している。

## 用語整理

### Agent Skills

Agent Skills は、`SKILL.md` を中心にしたファイルベースの能力パッケージである。公式ドキュメントでは、Skill は instructions、metadata、optional resources、scripts、templates などを含み、Claude が関連時に自動的に使うものとして説明されている。

公式ドキュメント:

- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview

公式ドキュメント上の progressive disclosure は、概ね次の3段階。

```text
Level 1: Metadata
  - SKILL.md frontmatter の name / description
  - startup/system prompt 側に軽量に入る

Level 2: Instructions
  - request が Skill description に合うと、Claude が SKILL.md 本文を bash などで読む
  - ここで初めて SKILL.md 本文が context window に入る

Level 3: Resources / code
  - SKILL.md が参照する補助 Markdown、reference files、scripts などを必要時だけ読む/実行する
```

公式ドキュメントでは、Skill は VM / filesystem 上のディレクトリとして存在し、Claude が bash で `SKILL.md` や補助ファイルを読む、と説明されている。

### Anthropic Skills API

Skills API は、Custom Skill を Anthropic API 上で作成・一覧・取得・削除・バージョン管理するための API である。Agent Skills という概念そのものではなく、API 経由で Custom Skill を管理する管理面と考えるのがよい。

公式ドキュメント:

- https://platform.claude.com/docs/en/build-with-claude/skills-guide
- https://platform.claude.com/docs/en/api/beta/skills/create

公式ガイドでは、Custom Skill は `SKILL.md` を top-level に含む bundle としてアップロードする。API 利用時は `skills-2025-10-02` beta header と Code Execution Tool が必要になる。

### container

`container` は Messages API のパラメータで、Code Execution Tool が動く Anthropic 管理の隔離実行環境を指す。公式ドキュメントでは、Code Execution Tool は secure, containerized environment で実行されると説明されている。

公式ドキュメント:

- https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool

この container には filesystem があり、Skill files はこの環境にコピーされる。container は再利用でき、既存 container ID を指定すると、複数 request にまたがってファイル状態を維持できる。

### container.skills

`container.skills` は、Messages API request で「この container 内で使える Skill」を指定する配列である。

公式ガイドの構造:

```python
container={
    "skills": [
        {"type": "custom", "skill_id": "skill_...", "version": "latest"}
    ]
}
```

公式ガイドでは、`container` parameter に `skill_id`、`type`、optional `version` を指定し、Skills は code execution environment で実行されると説明されている。また、1 request あたり最大 8 Skills という制限がある。

## Claude は OSS なのか

Claude モデル本体や Anthropic API サーバー内部、Skill 選択ロジック全体は、少なくともこの調査範囲では OSS として公開されているものではない。

公開されているものはある。

- Anthropic の Agent Skills 公開リポジトリ: https://github.com/anthropics/skills
- その repo は Skill examples、spec、template、document skills などを含む

ただし、この repo は Claude モデル本体や Anthropic API 内部実装ではない。公式 repo 自体も、Claude で使う skills の実装・サンプル・仕様を示すものであり、Claude の内部推論や Skills 自動選択の実装コードを検証できるものではない。

したがって、「Claude が `name` / `description` を見て、必要時に `SKILL.md` を読む」という説明の根拠は、Anthropic 公式ドキュメント上の仕様説明であって、Claude 本体の OSS 実装を読んだ結果ではない。

## Corpus2Skill の全体構造

README では、Corpus2Skill は文書集合を Anthropic Skills の構造化ツリーへ変換し、serve 時には LLM agent が `SKILL.md` / `INDEX.md` を読みながら下位トピックへ降り、doc_id を見つけて `get_document` で全文を取得すると説明している。

該当箇所:

- `README.md:7`
- `README.md:23`
- `README.md:135`

実装上は次の流れ。

```text
compile time
  1. input documents を読み込む
  2. embedding を作る
  3. 階層クラスタリングする
  4. LLM で cluster summary / label / entity を作る
  5. .claude/skills/ 以下に SKILL.md / INDEX.md を生成する
  6. full text は documents.json に分離保存する

serve time
  1. .claude/skills/ の top-level Skill を Anthropic Skills API に upload
  2. upload 済み skill_id を container.skills に渡す
  3. Claude に Code Execution Tool と system prompt を渡す
  4. Claude が ls / cat / grep で SKILL.md / INDEX.md / entity_index.json を読む
  5. leaf INDEX.md の doc_id を見つける
  6. client-side tool get_document(doc_id) を呼ぶ
  7. Python 側が documents.json から全文を返す
```

## Corpus2Skill の Skill 生成

### top-level は SKILL.md、下位は INDEX.md

`skill_builder.py` は、top-level に `SKILL.md`、下位階層に `INDEX.md` を書く。

該当箇所:

- `corpus2skill/skill_builder.py:149`

```python
def _nav_filename(depth: int) -> str:
    """Top-level gets SKILL.md; deeper levels get INDEX.md."""
    return "SKILL.md" if depth == 0 else "INDEX.md"
```

### SKILL.md frontmatter

`_format_skill_md()` は YAML frontmatter に `name` と `description` を生成する。

該当箇所:

- `corpus2skill/skill_builder.py:307`
- `corpus2skill/skill_builder.py:321`

生成される構造:

```markdown
---
name: ...
description: >
  ...
level: ...
num_documents: ...
---

## Overview
...
```

公式 Agent Skills の minimum requirement は `name` と `description` なので、この点では Anthropic Agent Skills の形式に乗っている。

### SKILL.md / INDEX.md の中身

`_format_skill_md()` は、各ノードに次のような navigation aid を入れる。

- `## Overview`
- `## Related skills`
- `## Entities & document types`
- `## Example documents in this skill`
- `## Contents`
- sub-group の一覧
- document row の一覧
- `### See also`

該当箇所:

- `corpus2skill/skill_builder.py:339`
- `corpus2skill/skill_builder.py:359`
- `corpus2skill/skill_builder.py:370`
- `corpus2skill/skill_builder.py:386`
- `corpus2skill/skill_builder.py:392`
- `corpus2skill/skill_builder.py:401`
- `corpus2skill/skill_builder.py:427`

特に sub-group については、生成される Markdown に「各 sub-group の `INDEX.md` を読め」という案内を入れる。

該当箇所:

- `corpus2skill/skill_builder.py:395`

document row については、「`get_document` に doc_id を渡して全文を読め」という案内を入れる。

該当箇所:

- `corpus2skill/skill_builder.py:404`
- `corpus2skill/skill_builder.py:415`

### full text は Skill 内に入れない

`skill_builder.py` 冒頭コメントは、Skill ディレクトリには `SKILL.md` / `INDEX.md` のみを書き、full document content は `documents.json` に保存して `get_document` tool で提供すると説明している。

該当箇所:

- `corpus2skill/skill_builder.py:1`
- `corpus2skill/skill_builder.py:7`

実装としても `documents.json` を output directory 直下に書く。

該当箇所:

- `corpus2skill/skill_builder.py:124`

## Corpus2Skill の serve 時実装

### Skill upload

`serve.py` は `.claude/skills/` 配下の top-level Skill directory を Anthropic Skills API に upload する。

該当箇所:

- `corpus2skill/serve.py:278`
- `corpus2skill/serve.py:309`

```python
skill = client.beta.skills.create(
    display_title=upload_name,
    files=files,
    betas=[SKILLS_BETA],
)
```

`SKILLS_BETA` は次。

該当箇所:

- `corpus2skill/serve.py:23`

```python
SKILLS_BETA = "skills-2025-10-02"
```

### Code Execution Tool

Skill は Code Execution container で使われるため、`serve.py` は Code Execution Tool を渡す。

該当箇所:

- `corpus2skill/serve.py:125`

```python
CODE_EXECUTION_TOOL = {"type": "code_execution_20250825", "name": "code_execution"}
```

`ServeConfig` 側でも beta headers に `code-execution-2025-08-25` と `skills-2025-10-02` が入っている。

該当箇所:

- `corpus2skill/config.py:57`

### container.skills

`answer_query()` は upload 済み Skill ID を `container.skills` に変換する。

該当箇所:

- `corpus2skill/serve.py:382`

```python
skill_ids = [
    {"type": "custom", "skill_id": sid, "version": "latest"}
    for sid in manifest.values()
]
```

それを `container` に入れる。

該当箇所:

- `corpus2skill/serve.py:388`

```python
container = {"skills": skill_ids} if skill_ids else None
```

Messages API call 時に `container` を渡す。

該当箇所:

- `corpus2skill/serve.py:417`
- `corpus2skill/serve.py:426`

```python
if container:
    create_kwargs["container"] = container
```

### system prompt による探索指示

Corpus2Skill の文書探索は、`serve.py` の `SYSTEM_PROMPT` が強く駆動している。

該当箇所:

- `corpus2skill/serve.py:28`

この prompt は、Claude に次を明示する。

- `SKILL.md` / `INDEX.md` は navigation aids であり、回答本文の根拠ではない
- factual claim は `get_document` で取得した document に基づける
- top-level Skill を少なくとも2つ scan する
- entity があれば `entity_index.json` を `cat | grep` する
- candidate ごとに `INDEX.md` へ降りる
- leaf の `INDEX.md` rows に対して `cat ... | grep ...` する
- doc_id を見つけて `get_document` を呼ぶ

該当箇所:

- `corpus2skill/serve.py:38`
- `corpus2skill/serve.py:39`
- `corpus2skill/serve.py:65`
- `corpus2skill/serve.py:69`
- `corpus2skill/serve.py:71`
- `corpus2skill/serve.py:76`
- `corpus2skill/serve.py:78`
- `corpus2skill/serve.py:87`
- `corpus2skill/serve.py:91`

ここが、Corpus2Skill 独自の progressive disclosure の中心である。

### get_document

`GET_DOCUMENT_TOOL` は、leaf-level `INDEX.md` にある doc_id で全文を取る custom client-side tool として定義されている。

該当箇所:

- `corpus2skill/serve.py:106`

```python
GET_DOCUMENT_TOOL = {
    "name": "get_document",
    "description": (
        "Retrieve the full text of a document by its ID. "
        "Document IDs are listed in the leaf-level INDEX.md files. "
        "Call this after navigating the skill hierarchy to find relevant document IDs."
    ),
    ...
}
```

Python 側の実装は `documents.json` を読み、doc_id で全文を返す。

該当箇所:

- `corpus2skill/serve.py:331`
- `corpus2skill/serve.py:347`
- `corpus2skill/serve.py:493`

## 「二重の progressive disclosure」は正確か

初期の説明で「二重」と表現したが、厳密には注意が必要。

### 公式 Agent Skills 側

公式仕様上は、`container.skills` に Skill を渡すと、Claude は metadata を見て、必要時に `SKILL.md` を読み、さらに必要時に補助ファイルを読む、という progressive disclosure がある。

公式根拠:

- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- https://platform.claude.com/docs/en/build-with-claude/skills-guide

ただし、この挙動は Anthropic 側の closed な実行系に依存する。コードとしてこの repo 内で確認できるものではない。

### Corpus2Skill 側

Corpus2Skill では、Skill 内部にさらに文書探索ツリーを作る。

```text
Skill metadata
  -> top-level SKILL.md
  -> lower-level INDEX.md
  -> leaf INDEX.md
  -> doc_id
  -> get_document(doc_id)
  -> full document text
```

これは公式 Agent Skills の標準的な「作業手順 -> reference file -> script」ではなく、Corpus2Skill が生成した文書探索用の階層インデックスである。

### より正確な表現

「二重の progressive disclosure」という表現は、抽象的には以下の意味で使える。

```text
Layer 1: Anthropic Skill mechanism
  - Skill metadata と Skill files を container で利用可能にする
  - 公式仕様では SKILL.md / resources を必要時に読む

Layer 2: Corpus2Skill navigation
  - Skill の中身を文書ツリーとして構成する
  - system prompt と generated markdown で段階的探索を指示する
```

しかし、実装上の主役は Layer 2 である。Corpus2Skill は、公式 Agent Skills の自律的な Skill 選択だけに任せて文書検索しているわけではない。むしろ `SYSTEM_PROMPT` で明示的に `ls` / `cat` / `grep` を使わせている。

したがって、最も誤解が少ない表現は次。

```text
Corpus2Skill は Anthropic Skills API / container.skills をファイル配布・実行環境として利用し、
その上で独自の prompt-driven progressive disclosure を実装している。
```

## system prompt は Anthropic Agent Skills の progressive disclosure を上書きしているのか

上書きしているわけではない。

理由:

- `container.skills` に Skill を渡す処理は残っている
- Code Execution Tool も渡している
- 公式 Skill 形式で `SKILL.md` frontmatter を生成している
- Skill files は container 側に配置される前提で実装されている

ただし、Corpus2Skill の探索行動は Anthropic 側の automatic Skill use だけには依存していない。`SYSTEM_PROMPT` が明示的にファイル探索手順を指示しているため、実務上の文書探索は Corpus2Skill の system prompt によって制御されている。

つまり、関係は次のように見るべき。

```text
上書き:
  No

補助・特殊化:
  Yes

Anthropic の Skill 機構:
  Skill を登録し、container 内で使えるようにする土台

Corpus2Skill の system prompt:
  その土台上で、文書検索に特化した navigation policy を実行させる指示
```

## container.skills に渡した時点で SKILL.md 本文は読まれるのか

公式ドキュメントの説明では、`container.skills` に Skill を指定すると metadata discovery と file loading が行われる。つまり Claude は各 Skill の `name` / `description` を見るが、`SKILL.md` 本文が最初から context に入るわけではない。

公式ガイドは「How Skills are loaded」で次の段階を説明している。

```text
1. Metadata Discovery
2. File Loading into /skills/{directory}/
3. Automatic Use
4. Composition
```

また、progressive disclosure により full Skill instructions は必要時だけ load されると説明している。

したがって、`container.skills` に渡すだけで「Claude が必ず `SKILL.md` 本文まで読む」とは言えない。Corpus2Skill では、この部分を system prompt で補強している。

実装上は、`serve.py` の prompt が明示的に `SKILL.md` / `INDEX.md` を読むよう指示するため、`SKILL.md` 本文の読み込みは公式の automatic trigger だけでなく、Corpus2Skill 独自 prompt によって強く誘導されている。

## 制限・注意点

### Skill 数制限

公式ガイドでは、1 request に指定できる Skill は最大 8 とされている。

参照:

- https://platform.claude.com/docs/en/build-with-claude/skills-guide

Corpus2Skill は `manifest.values()` を全件 `container.skills` に入れている。

該当箇所:

- `corpus2skill/serve.py:382`

そのため、upload 済み Skill 数が 8 を超える場合、API 制限に当たる可能性がある。

### ネットワーク制限

Code Execution container は外部ネットワークアクセスなしと説明されている。

参照:

- https://platform.claude.com/docs/en/agents-and-tools/tool-use/code-execution-tool

Corpus2Skill の serve 時探索は、container 内に配置された Skill files と、client-side `get_document` tool に依存している。外部検索や外部 API には依存していない。

### これは RAG 検索器ではない

README と `serve.py` 冒頭コメントの通り、serve time では embeddings、BM25、FAISS などの retrieval system は使わない。

該当箇所:

- `README.md:23`
- `corpus2skill/serve.py:7`

代わりに、compile time で作った階層インデックスを Claude が navigation する。

### get_document は公式 Skill 標準機能ではない

`get_document` は Corpus2Skill が定義した custom tool であり、Anthropic Agent Skills の標準機能そのものではない。

該当箇所:

- `corpus2skill/serve.py:106`
- `corpus2skill/serve.py:493`

Skill files には doc_id だけを置き、全文は `documents.json` から Python 側が返す。

## まとめ

Corpus2Skill は Agent Skills の形式と Anthropic Skills API を利用しているが、文書探索の中核は公式 Skill 自動実行ではなく、Corpus2Skill が生成する階層化された `SKILL.md` / `INDEX.md` と、`serve.py` の system prompt にある。

短く言うと:

```text
Agent Skills:
  Claude が必要時に読む能力パッケージの仕様・実行モデル

Skills API:
  Custom Skill を登録・管理し、Messages API の container.skills で使うための API

Corpus2Skill:
  文書コーパスを Agent Skills 形式の階層インデックスに変換し、
  Claude にそのツリーを prompt-driven に探索させるシステム
```

このため、Corpus2Skill の設計を評価するときは、「Anthropic Agent Skills の progressive disclosure が完全に自動で文書検索している」と見るより、「Skills API をファイル配布・container 実行基盤として使い、Corpus2Skill が独自の progressive disclosure を上に実装している」と見る方が正確である。
