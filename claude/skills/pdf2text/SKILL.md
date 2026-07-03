---
name: pdf2text
description: >
  PDFファイルをClaudeの視覚読み取り＋Workflow並列変換で高精度なMarkdownに忠実変換するスキル。
  PyMuPDFで全ページをPNG化し、並列エージェントの視覚読み取り→CONT規約による機械結合→
  TOC突合→サンプリング再精査→欠落再変換までやりきる。学習パイプラインの入口
  （既定の出力先は ~/learning/<教材名>/raw.md）。
  このスキルは以下のときに必ず使う:「PDFをMarkdownに変換」「PDF変換」「pdf2text」「pdf2md」
  「PDFをテキスト化」「PDFを読み込んで教材に」と言われたとき、またはPDFファイルを指して
  変換・取り込みを頼まれたとき。
  忠実変換に徹する。学習用の加工（まとめ表・演習問題等）は study-material-optimizer の役割。
---

# PDF to Markdown Converter

PDFファイルをClaude視覚読み取り + Workflowで並列変換し、高精度なMarkdownを生成するスキル。

## 使い方

```
/pdf2text /path/to/file.pdf
/pdf2text /path/to/file.pdf 1-50        # ページ範囲指定
```

## 設計方針（なぜこの構成か）

- **Claude視覚読み取りが最も高精度**（特に日本語）。外部OCR（PaddleOCR等）はカタカナ小文字・長音符を誤認識しやすく不要
- **ReadツールのPDF直接読み取りは使わない**。poppler(pdftoppm)未導入の環境ではReadのPDFページ描画が失敗する。テキストベースPDFも含め、全PDFをPyMuPDFでPNG化して視覚読み取りする
- **ページ境界が最大の品質リスク**。項目（問題・段落・表）がセクション境界で分断されると、後半担当エージェントが見出しや問題文を「発明」する事故が起きる（実例: 問題集で原本に無い設問文が捏造された）。対策として全セクションに前後1ページの文脈を渡し、`<!--CONT-->` 規約で機械的にマージする
- **結合はWorkflow外のPythonで行う**。大量テキストを1エージェントに渡すAssemble方式はプロンプト長超過で失敗する

## 手順

### Phase 1: PDF解析

1. 引数を解析する。第1引数がPDFパス、第2引数（任意）がページ範囲。
2. PyMuPDFでPDFを開き、以下を取得する:
   - 総ページ数、メタデータ（タイトル、著者）
   - TOC（目次構造）: `doc.get_toc()`
   - テキスト抽出可否: 最初の5ページで `page.get_text()` を試す（後のプロンプト補助情報に使うだけで、経路は分岐しない）
3. PyMuPDFが未インストールの場合:
   ```
   python3 -m pip install --user --break-system-packages pymupdf
   ```

### Phase 2: ページ画像化（全PDF共通）

4. PyMuPDFで全ページをPNG画像に変換（200 DPI）:
   ```python
   import fitz, os
   doc = fitz.open(pdf_path)
   os.makedirs(pages_dir, exist_ok=True)
   for i in range(len(doc)):
       pix = doc[i].get_pixmap(dpi=200)
       pix.save(f'{pages_dir}/page_{i+1:03d}.png')
   ```
   - 保存先: 元PDFと同じディレクトリの `pages/` サブフォルダ

5. **DPIと実効解像度の知見**: APIは長辺約1568px・約1.15メガピクセルを超える画像を縮小するため、A4全ページはDPIをいくら上げても**実効110dpi程度**でモデルに届く。つまり200→300に上げても精度は変わらない（ファイルが重くなるだけ）。**小さい文字・密な表・コードが多いページで精度が必要な場合は、DPIではなくページを上下2分割（10%オーバーラップ）して2枚の画像にする**のが正解。通常の書籍は200 DPI全ページ1枚で十分。

### Phase 3: セクション分割

6. TOCがある場合はそれを元に分割。ない場合は4-6ページごとに機械的に分割。
   - 1セクションあたり **最大6ページ**（短いほど精度が上がる）
   - 章の途中で切らないよう目次を尊重する
   - 各セクション: `{ label: "セクション名", startPage: N, endPage: M }`
   - セクション数が50を超える場合は、ユーザーにページ範囲を絞るか確認する

### Phase 4: Workflow実行

7. Workflowスクリプトを構築する（テンプレートは後述）。要点:
   - Convert phaseのみ。**Assembleエージェントは作らない**
   - 各エージェントに**担当ページ + 前後1ページ（文脈専用）**を渡す
   - セクションデータはスクリプトに直接埋め込むのが再現性の面で堅牢（argsで渡す場合はJSON値として渡すこと。JSON文字列化して渡すと`args.map`等が壊れる）

8. 各変換エージェントへのプロンプトに含める規則:
   - 担当ページの画像をReadツールで1枚ずつ読み取る
   - **文脈ページ（前後1ページ）は読んで文脈把握にのみ使い、内容は転写しない**
   - **担当範囲の先頭が項目（文・問題・表・リスト）の途中から始まる場合、出力の1行目を `<!--CONT-->` とし、見出し・問題番号・設問文を発明せずに続きだけを転写する**
   - **担当範囲の末尾が項目の途中で終わる場合も、そこまでを忠実に転写して終える（結末を補完しない）**
   - 整形ルール: 見出し→`#`/`##`/`###`、箇条書き・番号リスト→Markdown記法、表→Markdownテーブル、ページ番号・ヘッダー・フッター除去、専門用語保持、図は `[Figure: 説明]`、コラム・注釈は `>` 引用
   - 「変換したMarkdownテキストだけを返すこと」

### Phase 5: 結果の結合と後処理

9. Workflow完了後、**トランスクリプトディレクトリの `journal.jsonl` をPythonでパース**して各エージェントの返り値を取得する（各agentの実際の返り値が記録されている。Readツールで読むと全文がコンテキストに載るのでPythonで処理する）。journal.jsonlが無い場合のみ、agent-*.jsonl から「セクション:」ラベルで抽出する旧方式にフォールバック。
10. **全セクションが揃っているか必ず検証する**。欠落・null・空文字のセクションはラベルとページ範囲をログに出し、Phase 8の再変換対象に積む。黙って読み飛ばさない。
11. セクション順に結合する。このとき `<!--CONT-->` 規約でマージする（スクリプトは後述）:
    - `<!--CONT-->` で始まるパートは前パートに直結する
    - 接合部で文が途切れている場合のみ行を結合する（文断裂修正）
    - 接合部±15行以内の同一見出しのみ重複除去する。**文書全体に対する「300行以内の同一見出し削除」のような広域dedupは絶対に行わない**（実書籍では「ワンポイントアドバイス」「問題」「解答」等の同一見出しが正当に近接反復する。過去に57連続の正規見出しを壊しかけた）
12. 全体後処理（保守的なもののみ）:
    - 見出しレベル修正: 節番号付き見出し（`# 1-4 ...`）が `#` になっていたら `##` に
    - **`---` の一括除去はしない**（正当な水平線・区切りを壊す。CONTマージが正しく動けばエージェント間区切りはそもそも発生しない）

### Phase 6: TOC整合性チェック

13. PDFのTOCとMarkdownの見出しを突合して構造的欠落を検出する。**正規化なしの部分一致は使わない**（実例: 「〜」WAVE DASHと「～」全角チルダの差だけで5セクションを誤欠落判定 / 逆に「第1章 問題」が `## 問題` に部分一致して章見出し欠落を見逃した）。ルール:
    - 両者を正規化: NFKC → 波ダッシュ/チルダ統一（`〜`→`～`）→ 空白・全角空白除去 → 小文字化
    - 「問題」「解答」「解説」「目次」「索引」等の**汎用タイトルは突合対象から除外**（何にでもマッチするため）
    - 判定は正規化後の完全一致、または `difflib.SequenceMatcher` ratio ≥ 0.85
    - 一致しないTOCエントリを「欠落候補」としてページ番号付きでリストアップ
14. 欠落候補が0件ならPhase 7へ。1件以上あればログに表示し、Phase 7の精査で重点確認する。

### Phase 7: 再精査（2ラウンド）

15. **精査対象数は書籍サイズに比例させる**: 1ラウンドあたり `max(10, 総セクション数の25%)` をサンプリング（表・図・コラムが多い章とPhase 6の欠落候補を優先）。**未精査セクション数を必ずログに出す**（「全部見た」と誤認しないため）。
16. Workflowで並列精査エージェントを起動する。各エージェントは:
    - 担当セクションの **元画像**（Readツールで視覚読み取り）と **対応するMarkdownテキスト**（該当行範囲を切り出して渡す）の両方を受け取る
    - 比較観点: **脱落**（元にあるのに無い）/ **捏造**（元に無い見出し・設問文・結び）/ **誤字**（OCR由来: カタカナ小文字、長音符、似た漢字）/ **書式崩れ**（表のプレーンテキスト化、見出しレベル、箇条書き）/ **順序逆転**
    - JSON形式で報告: `{ "section": "...", "issues": [{"type": "...", "location": "...", "original": "...", "current": "...", "fix": "..."}] }`（schemaオプションで構造化出力を強制）
17. 報告された問題をPythonで自動修正する。ただし:
    - **置換前に `content.count(current) == 1` を確認し、一意な場合のみ `str.replace(current, fix, 1)` を実行**（グローバル置換は無関係箇所を壊す）
    - 一意でない・見つからない・複雑な問題は「修正不可リスト」に残す
18. ラウンド2: ラウンド1で問題が出たセクション + 未サンプリングのセクションから同数を追加精査。同じ方法で修正。

### Phase 8: 欠落セクション再変換

19. 対象: Phase 5で欠落検出されたセクション、Phase 6の欠落候補のうちPhase 7でも確認されたもの、精査で「丸ごと脱落」報告されたもの。0件ならスキップ。
20. 該当ページの画像（前後1ページの文脈付き）でPhase 4と同じプロンプトの変換エージェントを個別に起動する（少数ならAgentツール、10件以上ならWorkflow）。
21. 再変換結果を正しい位置に差し込み、接合部の重複見出し除去を再実行する。

### Phase 9: 修正不可項目のエージェント修正

22. Phase 7の「修正不可リスト」の問題を修正エージェントに委託する。0件ならスキップ。
23. 各問題（近接するものはグループ化）につき:
    - 入力: 該当箇所の **元画像** + **現在のMarkdown**（該当行範囲、前後50行の余裕）+ **問題の説明**
    - 指示: 「元画像を正として該当箇所を修正し、修正後のテキスト（該当行範囲全体）だけを返すこと」
    - 出力でReadツール確認 → Editツール置換
24. 修正成功数・残存数を記録する。

### Phase 10: 保存とクリーンアップ

25. 出力先は学習パイプラインの規約に従い、**デフォルトで `~/learning/<教材名>/raw.md`**（教材名はPDF名や内容から命名）。判断に迷う場合のみユーザーに確認。ユーザーが別の出力先を指定していればそれに従う。
26. Writeツールで保存し、画像フォルダ（`pages/`）を削除してクリーンアップ。
27. 完了報告に含める: 総ページ数、セクション数、出力サイズとパス、TOC突合結果（欠落候補数）、精査カバレッジ（精査済み/全セクション）、検出・修正数、再変換数、エージェント修正の成功/残存数、最終未修正問題（あれば内容も）。

## Workflowスクリプトテンプレート

補足: Workflowのargsは**JSON値として渡せば安定して使える**（壊れるのはJSON文字列化して渡した場合）。テンプレートリテラルも通常のJavaScriptとして動作する。ただしセクションデータの直接埋め込みは再現性・レジューム性の面で引き続き推奨。**Assembleエージェントは作らない**（結合はPhase 5のPythonで行う）。

```javascript
export const meta = {
  name: 'pdf2text',
  description: 'PDF to Markdown conversion with parallel visual reading agents',
  phases: [
    { title: 'Convert', detail: 'Each agent visually reads page images and converts to Markdown' }
  ]
}

const pagesDir = '/absolute/path/to/pages'
const LAST_PAGE = 320  // 総ページ数
const sections = [
  { label: "第1章 概要", startPage: 1, endPage: 5 },
  { label: "第1章 続き", startPage: 6, endPage: 10 },
  // ... 全セクション
]

function img(p) { return pagesDir + '/page_' + String(p).padStart(3, '0') + '.png' }

phase('Convert')
const converted = await parallel(sections.map(function(sec, i) {
  var own = []
  for (var p = sec.startPage; p <= sec.endPage; p++) own.push('- ' + img(p))
  var ctx = []
  if (sec.startPage > 1) ctx.push('- 直前ページ（文脈専用・転写禁止）: ' + img(sec.startPage - 1))
  if (sec.endPage < LAST_PAGE) ctx.push('- 直後ページ（文脈専用・転写禁止）: ' + img(sec.endPage + 1))

  var prompt = 'PDFのページ画像をMarkdownに変換してください。\n\n' +
    'セクション: ' + sec.label + '\n' +
    '担当ページ: ' + sec.startPage + '-' + sec.endPage + '\n\n' +
    '## まず文脈ページを読む（内容は出力しない）\n' + (ctx.join('\n') || '（なし）') + '\n\n' +
    '## 次に担当ページを順に読み、変換する\n' + own.join('\n') + '\n\n' +
    '## 境界規則（最重要）\n' +
    '- 担当範囲の先頭が項目（文・問題・表・リスト）の途中なら、出力の1行目を <!--CONT--> とし、見出し・問題番号・設問文を発明せず続きだけを転写する\n' +
    '- 担当範囲の末尾が項目の途中で終わる場合も、そこまでを忠実に転写して終える（結末を補完しない）\n' +
    '- 原本に無いテキストを一切追加しない\n\n' +
    '## 整形ルール\n' +
    '- 見出しは #, ##, ### に変換（章=1、節=2、項=3）\n' +
    '- 箇条書き・番号リストはMarkdown記法\n' +
    '- 表はMarkdownテーブル記法\n' +
    '- ページ番号・ヘッダー・フッターは除去\n' +
    '- 専門用語・略語はそのまま保持\n' +
    '- 図は [Figure: 説明] 形式\n' +
    '- コラムや注釈は > (引用ブロック)\n' +
    '- 太字は **太字**\n\n' +
    '変換したMarkdownだけを返すこと（説明不要）'

  return function() {
    return agent(prompt, { label: 'convert:' + sec.label, phase: 'Convert' })
  }
}))

return converted
```

## 結果抽出・結合スクリプト（Python）

```python
import json, glob, os, re

transcript_dir = "WORKFLOW_TRANSCRIPT_DIR"
section_labels = ["第1章 概要", "第1章 続き"]  # セクション順

results = {}

# 1) journal.jsonl から抽出（第一候補: 各agentの返り値がそのまま記録されている）
journal = os.path.join(transcript_dir, "journal.jsonl")
if os.path.exists(journal):
    for line in open(journal):
        try:
            e = json.loads(line)
        except Exception:
            continue
        label = str(e.get("label") or e.get("agentLabel") or "")
        if label.startswith("convert:"):
            r = e.get("result") or e.get("returnValue") or e.get("output")
            if isinstance(r, str) and r.strip():
                results[label[len("convert:"):]] = r

# 2) フォールバック: agent-*.jsonl のプロンプトから「セクション:」を拾い最終テキストを取る
if len(results) < len(section_labels):
    for f in sorted(glob.glob(transcript_dir + "/agent-*.jsonl")):
        lines = open(f).readlines()
        try:
            first = json.loads(lines[0])
        except Exception:
            continue
        content = first.get("message", {}).get("content", "")
        prompt = content if isinstance(content, str) else next(
            (c["text"] for c in content if isinstance(c, dict) and c.get("type") == "text"), "")
        if "セクション:" not in prompt:
            continue
        label = prompt.split("セクション:")[1].split("\n")[0].strip()
        if label in results:
            continue
        last_text = ""
        for line in lines:
            try:
                e = json.loads(line)
                if e.get("message", {}).get("role") == "assistant":
                    for c in e["message"].get("content", []):
                        if isinstance(c, dict) and c.get("type") == "text" and c["text"].strip():
                            last_text = c["text"]
            except Exception:
                pass
        if last_text.strip():
            results[label] = last_text

# 3) 欠落検証（黙って読み飛ばさない）
missing = [sl for sl in section_labels if sl not in results or not results[sl].strip()]
if missing:
    print("!! 欠落セクション（Phase 8で再変換すること）:", missing)

MD_LINE = re.compile(r'^(\s*)(#|\||[-*+]\s|>|```|\d+\.\s)')
END_PUNCT = re.compile(r'[。．！？：」』〉》\)）\]］]$')

def join_cont(prev: str, cont: str) -> str:
    """<!--CONT--> パートを前パートへ直結。接合部の文断裂のみ修正する。"""
    prev_lines = prev.rstrip("\n").split("\n")
    cont_lines = cont.lstrip("\n").split("\n")
    a = prev_lines[-1] if prev_lines else ""
    b = cont_lines[0] if cont_lines else ""
    if a.strip() and b.strip() and not MD_LINE.match(a) and not MD_LINE.match(b) \
            and not END_PUNCT.search(a.rstrip()):
        prev_lines[-1] = a.rstrip() + b.lstrip()
        cont_lines = cont_lines[1:]
    return "\n".join(prev_lines) + ("\n" + "\n".join(cont_lines) if cont_lines else "")

def dedup_joint_heading(prev: str, nxt: str) -> str:
    """次パートの「先頭行」が前パート「最後の見出し」と同一のときのみ除去する。
    （境界でエージェントが同じ見出しを再出力した場合の対策。範囲をこれ以上広げると
    正当な反復見出し——例:「ワンポイントアドバイス」——を誤削除するので広げないこと）"""
    prev_heads = [l.lstrip('#').strip() for l in prev.split("\n") if l.startswith('#')]
    out = nxt.split("\n")
    first = 0
    while first < len(out) and not out[first].strip():
        first += 1
    if prev_heads and first < len(out) and out[first].startswith('#') \
            and out[first].lstrip('#').strip() == prev_heads[-1]:
        del out[first]
        while first < len(out) and not out[first].strip():
            del out[first]
    return "\n".join(out)

merged = []
for sl in section_labels:
    part = results.get(sl, "").strip()
    if not part:
        continue
    if part.startswith("<!--CONT-->") and merged:
        merged[-1] = join_cont(merged[-1], part[len("<!--CONT-->"):])
    elif merged:
        merged.append(dedup_joint_heading(merged[-1], part))
    else:
        merged.append(part)

content = "\n\n".join(merged)

# 全体後処理（保守的なもののみ。広域の重複見出し削除・--- 一括除去は行わない）
content = re.sub(r'^# (\d+[-\.]\d+\s)', r'## \1', content, flags=re.MULTILINE)
```

## TOC突合スクリプト（Python）

```python
import fitz, re, unicodedata
from difflib import SequenceMatcher

# 汎用タイトル（何にでもマッチし章文脈なしでは突合不能）と前付・後付（意図的省略）は除外
GENERIC = {"問題", "解答", "解説", "目次", "索引", "はじめに", "まとめ", "演習問題"}
FRONTMATTER = ("表紙", "奥付", "商標", "著者紹介", "著者略歴", "スタッフリスト", "カバー", "大扉")

def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    s = s.replace("〜", "～").replace("−", "-").replace("―", "-")
    return re.sub(r"[\s　]+", "", s).lower()

doc = fitz.open(pdf_path)
heads = [norm(m.group(1)) for m in re.finditer(r'^#{1,3}\s+(.+)$', content, re.M)]
gen = {norm(g) for g in GENERIC}

missing = []
for lvl, title, page in doc.get_toc():
    tn = norm(title)
    core = re.sub(r'^第\d+章', '', tn)  # TOC複合タイトル「第4章 問題」→「問題」
    if not tn or not core or tn in gen or core in gen:
        continue
    if any(w in tn for w in FRONTMATTER):
        continue
    ok = any(tn == h or SequenceMatcher(None, tn, h).ratio() >= 0.85 for h in heads)
    if not ok:
        missing.append((lvl, title, page))

for m in missing:
    print("欠落候補:", m)
```

（検証済み: 旧ロジックはこの2冊で誤検知8件・見逃し1件だったが、本ロジックは誤検知0件、
意図的に見出しを消した回帰テストで欠落1件を正確に検出した）

## 注意事項

- PyMuPDFが未インストールの場合は自動インストールする
- **ReadツールでPDFを直接読まない**（poppler未導入環境では失敗する。全PDFをPNG化経路で処理）
- 原本に無いテキストの追加は変換・修正のどの段階でも禁止（忠実変換が本スキルの契約。学習用の加工は study-material-optimizer の仕事）
- 精度が足りないページはDPI引き上げではなく**ページ上下分割**で対処する（APIの縮小により実効解像度はDPIに比例しない）
- セクション数が50を超える場合はユーザーにページ範囲を絞るか確認する
