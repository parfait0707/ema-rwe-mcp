# このディレクトリのCodex設定

`AGENTS.md`はリポジトリの開発・検索ルールです。MCP設定はルート直下の任意の`config.toml`ではなく、Codexのプロジェクト設定として読み込まれる`.codex/config.toml`に置いています。[公式MCP設定](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)に従い、`mcp_servers.ema-rwe`のcommand、args、cwd、envを指定しています。

このチェックアウトの`.venv/Scripts/python.exe -m ema_rwe.mcp.server`を起動し、DB、保存PDF、HTTPキャッシュ、医薬品辞書をこのリポジトリ内の`data/`・`cache/`に配置します。別の場所へ移動した場合は設定の絶対パスを変更してください。

Codexでこのディレクトリを開き直し、MCP一覧に`ema-rwe`があることを確認してください。CLIでは`/mcp`で確認できます。プロジェクト設定は[信頼済みのプロジェクトで読み込まれます](https://learn.chatgpt.com/docs/config-file/config-basic)。この作業ではユーザー全体の設定や信頼設定を変更していません。

内部LLMが未設定の場合は、呼出元Codexが抽出・追加探索を行います。内部LLMを使う場合は環境変数で設定してください。`env_vars`には転送する変数名のみを書き、APIキーそのものは保存していません。Claude CodeにはこのTOMLが自動適用されないため、READMEのClaude Code登録例を利用してください。
