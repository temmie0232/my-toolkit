# my-toolkit

新しい PC でも「**Claude を入れて、このリポジトリを見せて、セットアップしてもらう**」だけで、自分の作業環境（Claude Code のスキル・シェル設定・CLI ツール）が一発で復元される個人ツールキット。

セットアップの実体は [`SETUP.md`](SETUP.md) に書いてある。人間が手順を追ってもいいし、**Claude にこのリポジトリを渡して `SETUP.md` の通りに実行してもらう**のが本来の使い方。

---

## 新しい PC でのセットアップ

### ステップ 1: Claude Code を入れる

[Claude Code](https://claude.com/claude-code) をインストールする。

```bash
# 推奨: 公式インストーラ
curl -fsSL https://claude.ai/install.sh | bash

# もしくは npm 経由
npm install -g @anthropic-ai/claude-code
```

入れたら一度 `claude` を起動してログインし、`~/.claude/` ディレクトリが作られた状態にしておく。

> 前提: `git` と `curl` が入っていること（WSL2 / Ubuntu なら最初から入っている）。

### ステップ 2: このリポジトリをクローン

```bash
git clone https://github.com/temmie0232/my-toolkit.git ~/my-toolkit
cd ~/my-toolkit
```

### ステップ 3: Claude にセットアップを任せる

クローンしたディレクトリで `claude` を起動して、こう言うだけ：

```
このリポジトリの SETUP.md を読んで、その通りにセットアップして
```

Claude が `SETUP.md` を読み、スキルのリンク・シェル設定・CLI ツールの導入を順にやってくれる。
各ステップは冪等で、既存ファイルを壊す前にはバックアップ／確認する作りになっている。

---

## 中身

| パス | 説明 |
|------|------|
| [`SETUP.md`](SETUP.md) | Claude が読んで実行するセットアップ手順書（このリポジトリの心臓部） |
| `claude/skills/cognitive-walkthrough/` | ペルソナになりきって Web アプリの認知的ウォークスルー（UX レビュー）を行うスキル |
| `claude/skills/goal-locked-loop/` | ゴールを確定させてから完了まで敵対的検証ループで回しきるスキル |
| `claude/skills/meeting-minutes/` | 音声文字起こしをフィラー除去・議題別に整形するスキル |
| `claude/skills/study-material-optimizer/` | 素の Markdown 教材をジャンル別（参考書/問題集/ドリル）に学習最適化するスキル |
| `claude/skills/tutor/` | 教材を使った対話型の家庭教師スキル（進捗・弱点を永続記録） |
| `claude/skills/anki/` | 教材・模擬試験から Anki フラッシュカード用 TSV を生成するスキル |
| `claude/skills/shared/` | スキル共通リソース: 教材 Markdown フォーマット規約（optimizer/tutor/anki の共有契約） |
| `shell/vimrc` | Vim 設定（`~/.vimrc` にリンク） |
| `shell/bashrc.snippet` | `~/.bashrc` に追記する汎用部分（PATH / nvm） |

セットアップ時に Claude が導入するもの（リポジトリには同梱しない）：

- **rtk** — Claude Code のコマンド出力を圧縮してトークン消費を減らすプロキシ。フックも登録する（[rtk-ai/rtk](https://github.com/rtk-ai/rtk)）
- **playwright-mcp + Chromium** — `cognitive-walkthrough` がブラウザ操作に使う
- **Anthropic 製スキル**（`frontend-design` / `frontend-design-review` / `skill-creator`）— 配布物のため [anthropics/skills](https://github.com/anthropics/skills) から取得する

---

## このリポジトリで管理しないもの

- **`~/.claude/settings.json` の許可リスト（permissions）** — 環境ごとに育つので持ち運ばない。rtk フックは SETUP が登録する
- **業務固有の設定**（git のユーザー ID、社内 CA 証明書など）— 個人の公開リポジトリには置かない
- **作業日誌などの実データ** — 各プロジェクト側に残す

---

## 更新するとき

スキルは `~/.claude/skills/` にシンボリックリンクされるので、どちら側で編集しても同じファイルが更新される。

```bash
cd ~/my-toolkit
git add -A
git commit -m "update xxx"
git push
```

新しいスキルを足すときは `claude/skills/` にディレクトリを置いて `SETUP.md` をもう一度実行すれば、自動でリンクされる。
