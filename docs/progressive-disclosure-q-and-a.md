# Anthropic Skills API / Progressive Disclosure Q&A

このメモは、Corpus2Skill リポジトリにおける Anthropic Skills API、Agent Skills の progressive disclosure、`serve.py` の `SYSTEM_PROMPT` の関係について、会話中の質問と回答を整理したもの。

調査日: 2026-05-31

## 参照した一次情報

- Anthropic Agent Skills overview: https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- Anthropic Skills in the API guide: https://platform.claude.com/docs/en/build-with-claude/skills-guide
- Anthropic Create Skill API reference: https://platform.claude.com/docs/en/api/beta/skills/create
- Anthropic engineering blog, "Equipping agents for the real world with Agent Skills": https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- Claude Code skills docs: https://code.claude.com/docs/en/skills
- OpenAI Harmony response format, roles and instruction hierarchy: https://developers.openai.com/cookbook/articles/openai-harmony

## 参照したリポジトリ内ファイル

- `README.md`
- `corpus2skill/serve.py`
- `corpus2skill/skill_builder.py`
- `corpus2skill/config.py`

## Q1. このリポジトリでは Anthropic Skills API を使っているか。そもそもこれは何か。

### 質問

このリポジトリでは、anthropic skills api を使っていますね？まずこれは何ですか？

### 回答

はい。このリポジトリは Anthropic Skills API を使っている。

Anthropic Agent Skills は、`SKILL.md` を中心にしたファイルベースの能力パッケージである。Skill は instructions、metadata、optional resources、scripts、templates などを含み、Claude が関連すると判断したときに自動的に使う。

Anthropic Skills API は、その Custom Skill bundle を API 経由で登録し、Messages API の `container.skills` に指定して Claude の code execution container 内で利用可能にする仕組みである。

### Corpus2Skill での使われ方

Corpus2Skill は通常の「作業手順 Skill」としてだけでなく、文書コーパスを `SKILL.md` / `INDEX.md` の階層に変換し、Claude にその階層を探索させるために Skills API を使っている。

`README.md` では、Corpus2Skill は documents を structured tree of Anthropic Skills に変換し、query time に LLM agent が `SKILL.md` / `INDEX.md` を読みながら階層探索し、必要な full document を `get_document` tool で取得すると説明している。

主な実装箇所:

- `corpus2skill/serve.py`
  - `SKILLS_BETA = "skills-2025-10-02"`
  - `_upload_skills()` で top-level Skill directories を Anthropic Skills API に upload
  - `answer_query()` で upload 済み `skill_id` を `container.skills` に渡す
- `corpus2skill/skill_builder.py`
  - `.claude/skills/` 以下に `SKILL.md` / `INDEX.md` / navigation files を生成
- `corpus2skill/config.py`
  - `ServeConfig.skills_betas` に `code-execution-2025-08-25` と `skills-2025-10-02`

## Q2. このリポジトリの元になった論文は何か。

### 質問

このリポジトリの元になった論文があるのですが、それをまず web-search をして探してください。

### 回答

見つかった論文は次のもの。

**"Don't Retrieve, Navigate: Distilling Enterprise Knowledge into Navigable Agent Skills for QA and RAG"**

- arXiv: https://arxiv.org/abs/2604.14572
- OpenReview PDF: https://openreview.net/pdf/d9c7026c20cb0763733cddeac9b74fc5c8ca20f0.pdf

確認できた情報:

- arXiv ID: `2604.14572`
- 初回投稿: 2026-04-16
- 調査時点の最新版: v3, 2026-05-15
- 著者: Yiqun Sun, Pengfei Wei, Lawrence B. Hsieh
- OpenReview PDF 内に `Code is available at https://github.com/dukesun99/Corpus2Skill` と記載がある。

注意点:

- 2026-05-31 時点では arXiv / OpenReview PDF として確認した。
- 査読済み出版物かどうかは、この調査範囲では確定していない。

## Q3. 論文では progressive disclosure を利用して LLM 自身に文書探索させたと書かれているか。

### 質問

この論文では本文を見てみると、progressive disclosure という agent skills の性質を利用してドキュメント探索をLLM自身にさせたというテキストが書かれていたかと思いますが、確認してください。

### 回答

はい、その理解で合っている。

論文本文では、filesystem-based skill packages は progressive disclosure により、起動時は軽量 metadata だけを見せ、必要時に full content を読み込むと説明している。その性質を使って、文書コーパスを navigable skill hierarchy に蒸留し、agent に探索させるという主張になっている。

論文上の整理:

- 従来の RAG:
  - retrieval system が query に対して top-k chunks / documents を返す
  - LLM は渡された context に基づいて回答する
- Corpus2Skill:
  - compile time に documents を embedding / clustering / summarization で skill hierarchy に変換する
  - serve time に LLM agent が tree を browse して evidence を探す
  - `description -> SKILL.md -> INDEX.md -> document` のように段階的に情報を読む

重要な点は、論文の progressive disclosure は「Agent Skills のファイルを必要時だけ読む」という仕組みを、文書探索のための情報階層に転用していること。

## Q4. progressive disclosure のルールはどこにあるのか。`serve.py` のプロンプトとは違うのではないか。

### 質問

progressive disclosure というルールというかLLMの動き方を指示しているのは、どこですか？serve.pyには本来のagent skills のprogressive disclosure とはちょっと違うような意味のプロンプトが書かれているように思うのですが、確認してください。

### 回答

その違和感は正しい。

この repo の `serve.py` にあるのは、本来の Agent Skills の progressive disclosure そのものを定義するプロンプトではなく、Corpus2Skill 用の「階層ファイル探索プロンプト」である。

### 本来の Agent Skills progressive disclosure

Anthropic 公式の Agent Skills では、progressive disclosure は概ね次の段階で説明されている。

1. `name` / `description` metadata が最初から context に入る
2. task に relevant と判断されたとき `SKILL.md` 本文を読む
3. 追加 resources は必要時だけ読む

Anthropic engineering blog では、`name` / `description` が system prompt に pre-load されること、それが progressive disclosure の第1段階であること、Claude が relevant と判断したら full `SKILL.md` を読むことが説明されている。

Anthropic API docs では、`container.skills` に Skill を指定すると、metadata discovery、file loading、automatic use が行われ、full Skill instructions は必要時だけ load されると説明されている。

### `serve.py` の `SYSTEM_PROMPT`

一方、`serve.py` の `SYSTEM_PROMPT` は、LLM に次のような探索手順を明示する。

- top-level skills を最低2候補 scan
- query に entity があれば `entity_index.json` を `grep`
- `SKILL.md` / `INDEX.md` を読む
- leaf の doc rows から doc_id を選ぶ
- `get_document` で full document を取得する
- factual claim は取得した document に trace できる必要がある

これは native Agent Skills の progressive disclosure とは別の、Corpus2Skill 独自の prompt-driven navigation policy である。

### 正確な整理

- Anthropic Skills progressive disclosure:
  - Skill metadata / files を段階的に見せる実行基盤
  - metadata -> `SKILL.md` -> resources
- Corpus2Skill の `serve.py` prompt:
  - その基盤上に置かれた `SKILL.md` / `INDEX.md` / `entity_index.json` をどう探索するかの手順
  - 最終回答の根拠を document retrieval に限定する QA policy

したがって、論文の progressive disclosure という表現は、API の段階ロード機構と、Corpus2Skill の prompt-driven navigation を組み合わせて呼んでいる面がある。

## Q5. この repo の LLM は Anthropic 側の progressive disclosure に従うのか、`serve.py` のプロンプトに従うのか、両方なのか。

### 質問

progressive disclosure 自体は主に Anthropic Skills API / Agent Skills の実行機構側にあるとのことですが、このリポジトリではLLMはこの実行機構側のprogressive disclosure ルールに従うのか、それとも serve.pyのプロンプト文に従うのか、それとも両方なのかどちらでしょうか？

### 回答

両方である。ただし役割が違う。

この repo で実際の探索方針を強く決めているのは `serve.py` の `SYSTEM_PROMPT` である。一方、Anthropic Skills API の progressive disclosure は、Skill の metadata / files を Claude に段階的に見せる実行基盤である。

### Anthropic Skills API 側

`serve.py` では以下を行う。

1. `.claude/skills/...` を `client.beta.skills.create()` で upload
2. upload 済み Skill IDs を `container.skills` に指定
3. code execution tool とともに Messages API request に渡す

これにより Claude は Skill の `name` / `description` を見られ、必要時に Skill files を container 内で読める。

### Corpus2Skill / `serve.py` 側

`serve.py` の `SYSTEM_PROMPT` は、Claude に具体的な行動を指示する。

- 最低2つの top-level skill を scan
- `entity_index.json` を使う
- `SKILL.md` / `INDEX.md` を読む
- leaf-level doc rows から doc_id を選ぶ
- `get_document` で本文取得

### 結論

この repo の LLM は、

- ファイルの存在・段階的ロード・metadata discovery については Anthropic Skills API の仕組みに乗る
- どの順序で探索し、何を根拠として答えるかについては `serve.py` の `SYSTEM_PROMPT` に従う

という構造である。

`SYSTEM_PROMPT` がなければ native Agent Skills の automatic use だけでも一部は動く可能性がある。ただし、Corpus2Skill 論文のような「複数候補を比較し、INDEX.md を降り、doc_id を取って本文取得する」挙動はかなり弱くなるはずである。

## Q6. Anthropic 側の progressive disclosure と `serve.py` の `SYSTEM_PROMPT` はなぜ競合しないのか。

### 質問

progressive disclosure を基盤として使い、その上に serve.py の探索プロンプトで Corpus2Skill 的な動きを実装しているとのことですが、両者のルールは競合しないのはなぜでしょうか？どちらかのルールやプロンプトにLLMが強く引っ張られるとしたら、なぜ後者のserve.py の探索プロンプトをLLMが強く意識して動作するのでしょうか？

### 回答

競合しにくい理由は、両者が同じ種類の「行動ルール」ではないからである。

Anthropic 側の progressive disclosure は、主にファイルを段階的に見せる仕組みである。`serve.py` は、そのファイルをどう探索するかの system prompt である。

### 1. Progressive disclosure は探索方針ではない

Anthropic Skills の progressive disclosure は主に、

- 最初は Skill の `name` / `description` だけ見える
- 必要になったら `SKILL.md` を読む
- さらに必要なら補助ファイルを読む

という情報開示・ロードの仕組みである。

これは「最低2候補を比較せよ」「`INDEX.md` を降りろ」「`get_document` を呼べ」という検索戦略までは指定しない。

### 2. `serve.py` は明示的な system prompt

この repo では `serve.py` の `SYSTEM_PROMPT` が、Claude に具体的な行動を指示している。

代表的な指示:

- Every factual claim must trace to a document retrieved via `get_document`
- `SKILL.md` / `INDEX.md` files are navigation aids
- Scan at least 2 candidate top-level skills
- Use `entity_index.json` when query mentions a specific entity
- Descend one level before pruning
- Use leaf `INDEX.md` rows to pick doc_ids
- Read at least one full document via `get_document` before answering

これは Messages API の `system` に渡されるため、LLM にとっては通常のファイル内容や Skill 内の説明より強いタスク定義として扱われる。

### 3. Skill files は主に索引であり、強い命令ではない

`skill_builder.py` が生成する `SKILL.md` / `INDEX.md` は、主に次の構成である。

- YAML frontmatter: `name`, `description`, `level`, `num_documents`
- `## Overview`
- `## Related skills`
- `## Entities & document types`
- `## Example documents in this skill`
- `## Contents`
- doc rows
- `### See also`

通常の procedural skill のような「この作業では必ずこう振る舞え」という強い命令ではなく、文書探索用の索引に近い。そのため `serve.py` の探索方針と衝突しにくい。

### 4. 競合が起きうるケース

完全に競合しないとは言えない。

例えば、生成された `SKILL.md` に強い命令文が入り、`serve.py` と矛盾すれば、モデルが多少引っ張られる可能性はある。

ただし、この repo では生成される `SKILL.md` / `INDEX.md` が索引・概要・doc_id 一覧中心なので、`SYSTEM_PROMPT` と衝突しにくい。

### 結論

Anthropic の progressive disclosure は「読み方の基盤」、`serve.py` は「探索アルゴリズムの指示」である。Corpus2Skill 的な挙動を実際に作っている主役は後者である。

## Q7. Anthropic 側にも progressive disclosure の内部 system prompt があるはずだが、`serve.py` の `SYSTEM_PROMPT` と競合しないのはなぜか。

### 質問

SYSTEM_PROMPT が会話の system 指示として毎回渡されるとのことですが、SYSTEM_PROMPT は最終的には、client.beta.messages.createの引数として変数に格納されて渡されています。anthoropic skills api側の内部機構側におそらく progressive disclosure のシステムプロンプトが書かれていると思いますが、少なくとも codex にはこのprogressive disclosure ルールのプロンプトが書かれていましたが、このanthoropic 側のprogressive disclosure のプロンプトと 今回の serve.py のSYSTEM_PROMPT は競合しないのが僕には不思議です。詳細に web-search や openai-docs をして詳細に考察してください。

### 回答

原理的には、両方が同じ Claude 入力に入ると考えるのが自然である。ただし、競合しにくい。理由は、Anthropic 側の progressive disclosure が「Skill をいつ・どうロードできるか」のメタ制御であり、`serve.py` が「ロード可能なファイル群をどう探索して回答するか」のタスク指示だからである。

ただし重要な注意点として、Anthropic Skills API が内部でどのような exact system prompt を挿入しているかは公開されていない。公開資料から確実に言えるのは次の範囲までである。

- metadata は system prompt に入る
- Skill files は container にコピーされる
- full Skill instructions は必要時に読む
- Skills は code execution environment で使われる

### 公開情報から見える Anthropic 側の挙動

Anthropic API docs の "How Skills are loaded" では、`container.skills` に Skills を指定したとき、以下が起きると説明されている。

1. Metadata Discovery: Claude sees metadata for each Skill, `name` and `description`, in the system prompt
2. File Loading: Skill files are copied into the container at `/skills/{directory}/`
3. Automatic Use: Claude automatically loads and uses Skills when relevant to the request
4. Composition: Multiple Skills compose together for complex workflows

また、progressive disclosure architecture により、Claude は full Skill instructions を必要時だけ load する。

Anthropic engineering blog でも同様に、agent は startup 時に installed skills の `name` / `description` を system prompt に pre-load し、それを見て relevant な Skill の full `SKILL.md` を読むと説明している。

### `serve.py` で同時に渡されるもの

`answer_query()` では、最終的に `client.beta.messages.create(**create_kwargs)` に以下が同時に渡される。

- `model=cfg.llm_model`
- `system=system_prompt`
- `tools=tools`
- `messages=messages`
- `betas=cfg.skills_betas`
- `container=container`

つまり 1回の API request 内に次が同居する。

- Anthropic Skills 側が注入・管理する Skill metadata / file loading mechanism
- Corpus2Skill の `SYSTEM_PROMPT`
- code execution tool
- `get_document` custom tool
- user query

### なぜ競合しにくいか

#### 1. Anthropic 側の progressive disclosure は availability / loading のルール

Anthropic 側のルールは主に、

- どの Skill が存在するかを metadata で知らせる
- Skill files を code execution container に配置する
- relevant なときだけ `SKILL.md` 本文を読む

というもの。

これは「文書QAではどの順序で探索し、どの document を evidence とみなし、どう回答するか」までは決めない。

#### 2. `serve.py` は task-specific な探索アルゴリズム

`serve.py` は、現在の task を「文書コーパスに基づく QA」と定義し、具体的な探索手順を与える。

これは Anthropic 側の汎用的な Skill-use policy よりも、現在の request に対して具体的である。

#### 3. `serve.py` は progressive disclosure を否定していない

`serve.py` は「全 Skill の全ファイルを最初から読め」とは言っていない。むしろ、

- top-level `SKILL.md` を読む
- relevant な `INDEX.md` に降りる
- leaf rows から doc_id を選ぶ
- full document は `get_document` で必要分だけ読む

という段階的な探索を指示している。

これは Anthropic 側の「必要時だけ load する」という progressive disclosure と整合する。

#### 4. OpenAI/Codex 側の比較材料

OpenAI Harmony docs では、message roles の instruction hierarchy として、`system > developer > user > assistant > tool` が示されている。

これは Anthropic の内部仕様そのものではないが、一般に高位の system/developer 指示が通常の content や tool result より強く扱われるという比較材料になる。

今回の `serve.py` の `SYSTEM_PROMPT` は Messages API の `system` パラメータとして渡される。そのため、読まれた Skill file の内容よりも、会話全体のタスク定義として強く働くと考えるのが自然である。

### なぜ `serve.py` の探索プロンプトに強く引っ張られるのか

主な理由は3つ。

1. `SYSTEM_PROMPT` が request ごとの明示的な system instruction として渡される
2. Anthropic 側の Skill 指示は汎用的で、`serve.py` は現在タスクに特化した具体的手順である
3. `serve.py` が `SKILL.md` / `INDEX.md` を "NAVIGATION AIDS" と明示し、最終回答の根拠を `get_document` で取得した full document に限定している

### 不確実な点

- Anthropic Skills API の内部 system prompt 全文は公開されていない
- 厳密な prompt priority の衝突解決順は外部からは断定できない
- したがって「絶対に競合しない」とは言えない

### 実務的な結論

この repo では、Anthropic Skills の progressive disclosure をファイル配布・段階ロードの基盤として使い、その上で Corpus2Skill の実際の QA 探索行動を `serve.py` の `SYSTEM_PROMPT` で上書き気味に制御している。

競合しないというより、`serve.py` が progressive disclosure の上に「このタスクでは何を必要とみなすか」を具体化している、と理解するのが一番正確である。

## 最終整理

Corpus2Skill における関係は次のように整理できる。

```text
Anthropic Skills API / Agent Skills
  - Skill metadata を system prompt に載せる
  - Skill files を container に配置する
  - relevant なときだけ SKILL.md / resources を読ませる
  - progressive disclosure の基盤

Corpus2Skill generated files
  - SKILL.md / INDEX.md / entity_index.json
  - 文書コーパスを階層索引として表現
  - 通常の procedural skill というより navigational index

serve.py SYSTEM_PROMPT
  - どの順序で探索するかを指示
  - 複数候補比較、entity cross-index、leaf doc rows、get_document を強制
  - QA の grounding policy を定義

get_document tool
  - 最終回答の根拠となる full document を取得
```

したがって、論文の「progressive disclosure を利用して LLM 自身にドキュメント探索させる」という主張は、以下の2層を合わせたものとして読むのがよい。

1. Anthropic Skills API の progressive disclosure により、Skill metadata / files を段階的に扱える
2. `serve.py` の `SYSTEM_PROMPT` により、その段階的に読めるファイル群を LLM が Corpus2Skill 的に探索する

この2つは同じ階層の命令ではなく、基盤とタスク方針の関係にあるため、設計上は競合しにくい。
