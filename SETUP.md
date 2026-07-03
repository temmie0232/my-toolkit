# SETUP — Claude 向けセットアップ手順書

> このドキュメントは **Claude が読んで実行する** ための手順書。
> 人間が手で追ってもよいが、本来は `claude` 起動後に
> 「このリポジトリの SETUP.md を読んで、その通りにセットアップして」と言って任せる用。

## Claude への前提指示（必ず守る）

- **すべて冪等に**。2 回実行しても壊れないこと。各ステップは「既に済んでいるか」を確認してからやる。
- **破壊的操作の前にバックアップ**。既存ファイル（シンボリックリンクでない実体）を置き換えるときは `xxx.bak` に退避してから。
- **不明点・上書きは確認**。判断に迷う上書きや、想定外の既存状態を見つけたら止めてユーザーに聞く。
- **各ステップの結果を報告**。何をリンク／インストールしたか、スキップしたかを簡潔に伝える。
- 想定環境は **WSL2 / Linux**。`$REPO` はこのリポジトリのルート（例: `~/my-toolkit`）を指す。

---

## ステップ 0: 前提チェック

```bash
# 必須コマンド
command -v git curl
# Claude のホーム（無ければ一度 claude を起動してログインしてもらう）
ls -d ~/.claude
```

`~/.claude` が無ければ、先に `claude` を一度起動してログインするようユーザーに促して止まる。

---

## ステップ 1: Claude スキルをリンク

`$REPO/claude/skills/` 配下の各スキルを `~/.claude/skills/` にシンボリックリンクする。
編集が双方向で同期されるよう、コピーではなく symlink にする。

```bash
mkdir -p ~/.claude/skills
for skill_dir in "$REPO"/claude/skills/*/; do
  skill="$(basename "$skill_dir")"
  dest="$HOME/.claude/skills/$skill"
  # 実体（symlink でない）が既にあればバックアップ
  if [ -e "$dest" ] && [ ! -L "$dest" ]; then
    mv "$dest" "$dest.bak"
    echo "バックアップ: $dest -> $dest.bak"
  fi
  ln -sfn "${skill_dir%/}" "$dest"
  echo "リンク: $dest -> ${skill_dir%/}"
done
```

リンクされるスキル: `cognitive-walkthrough` / `goal-locked-loop` / `meeting-minutes` /
`study-material-optimizer` / `tutor` / `anki` / `shared`（学習パイプラインの共通リソース。
SKILL.md を持たないが、optimizer/tutor/anki が参照する教材フォーマット規約が入っている）。

---

## ステップ 2: シェル設定

### 2a. `.vimrc` をリンク

```bash
dest="$HOME/.vimrc"
if [ -e "$dest" ] && [ ! -L "$dest" ]; then
  mv "$dest" "$dest.bak"
  echo "バックアップ: $dest -> $dest.bak"
fi
ln -sfn "$REPO/shell/vimrc" "$dest"
echo "リンク: $dest -> $REPO/shell/vimrc"
```

### 2b. `.bashrc` に汎用ブロックを追記（冪等）

`shell/bashrc.snippet` は `# >>> my-toolkit` ... `# <<< my-toolkit` のマーカーで囲ってある。
**既にマーカーがある場合は追記しない**。

```bash
if ! grep -q ">>> my-toolkit" ~/.bashrc 2>/dev/null; then
  printf '\n' >> ~/.bashrc
  cat "$REPO/shell/bashrc.snippet" >> ~/.bashrc
  echo "追記: ~/.bashrc に my-toolkit ブロックを追加"
else
  echo "スキップ: ~/.bashrc は既に設定済み"
fi
```

> 注意: 会社固有の CA 証明書（`NODE_EXTRA_CA_CERTS`）などは **このリポジトリには含めない**。
> 必要な環境では各自で `~/.bashrc` に追記すること。

---

## ステップ 3: rtk（トークン圧縮プロキシ）+ フック

Claude Code のコマンド出力を圧縮してトークン消費を抑える [rtk](https://github.com/rtk-ai/rtk) を入れ、
`settings.json` にフックを登録する。`settings.json` 全体は触らず、**rtk のフックだけ**を足す。

```bash
RTK_BIN="$(command -v rtk || echo "$HOME/.local/bin/rtk")"
if [ ! -x "$RTK_BIN" ]; then
  echo "rtk をインストール..."
  curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh
  RTK_BIN="$(command -v rtk || echo "$HOME/.local/bin/rtk")"
fi
if [ -x "$RTK_BIN" ]; then
  "$RTK_BIN" init -g --hook-only --auto-patch
  echo "rtk フック登録済み（~/.claude/settings.json の PreToolUse/Bash）"
else
  echo "警告: rtk が見つからない。~/.local/bin を PATH に追加して再実行（ステップ 2b 反映後に新しいシェルで）"
fi
```

---

## ステップ 4: playwright-mcp + Chromium（cognitive-walkthrough 用）

`cognitive-walkthrough` スキルがブラウザ操作に使う MCP サーバとブラウザを入れて、Claude に登録する。

```bash
# 1) MCP サーバ本体（グローバル）
npm install -g @playwright/mcp@latest
command -v playwright-mcp   # バイナリ名を確認（環境により mcp-server-playwright の場合あり）

# 2) Chromium 本体
npx playwright install chromium

# 3) インストールされた chrome のパスを検出
CHROME="$(ls ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1)"
echo "chrome: $CHROME"
```

検出した `$CHROME` を使って、`~/.claude/.mcp.json` に playwright サーバを登録する。
**既存の `~/.claude/.mcp.json` があれば、その `mcpServers` にマージする**（丸ごと上書きしない）。

```jsonc
{
  "mcpServers": {
    "playwright": {
      "command": "playwright-mcp",
      "args": [
        "--executable-path", "<検出した $CHROME のパス>",
        "--browser", "chromium",
        "--ignore-https-errors"
      ]
    }
  }
}
```

> `--executable-path` のパスはマシンごとに変わる（`chromium-1223` 等のバージョン番号が違う）。
> 必ず上で検出した実パスを埋めること。

---

## ステップ 5: Anthropic 製スキル（任意）

`frontend-design` / `frontend-design-review` / `skill-creator` は配布物なので、このリポジトリには同梱していない。
必要なら公式から取得して `~/.claude/skills/` に置く。

```bash
git clone --depth 1 https://github.com/anthropics/skills.git /tmp/anthropic-skills
# 使いたいものだけコピー（例）
cp -r /tmp/anthropic-skills/skills/frontend-design ~/.claude/skills/ 2>/dev/null || true
```

> どれを入れるかはユーザーに確認してから。不要なら丸ごとスキップしてよい。

---

## ステップ 6: 検証

```bash
echo "=== スキルのリンク ==="; ls -la ~/.claude/skills | grep -E "cognitive-walkthrough|goal-locked-loop|meeting-minutes|study-material-optimizer|tutor|anki|shared"
echo "=== vimrc ==="; ls -la ~/.vimrc
echo "=== bashrc ==="; grep -c ">>> my-toolkit" ~/.bashrc
echo "=== rtk ==="; command -v rtk && grep -c "rtk hook" ~/.claude/settings.json
echo "=== playwright ==="; command -v playwright-mcp; ls ~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | head -1
```

最後に「何を入れて／リンクして／スキップしたか」をまとめて報告する。
`~/.bashrc` を変更した場合は、新しいシェルを開くか `source ~/.bashrc` するようユーザーに伝える。
