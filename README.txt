Codex Token Saver v0.1.0-beta.3

Windows x64: run .\install.ps1 in PowerShell.
Linux x64: run ./install.sh (Ubuntu 22.04+).

Codex CLI must already be installed and authenticated. Network access is needed
to download pinned Python/engine dependencies and the first local embedding model.
Python 3.12-3.13 is reused if present; otherwise Python 3.13.7 is provisioned.
No administrator permission or Node installation is required.

Open a new terminal after install. Run Codex normally: codex
Review the installed hooks when Codex first asks. Hooks require Codex 0.154+.
If your Codex binary is not on PATH, use: codex-saver run
Open the dashboard: codex-saver ui
Inspect setup: codex-saver doctor
Disable: codex-saver disable
Remove: codex-saver uninstall (telemetry and Codex sessions are retained).

RTK transparently wraps simple git status/diff/log/show and pytest commands.
Compound commands pass through. CCE provides local MCP repository discovery;
first-use indexing/model download can take time; native file tools remain usable.
Observed savings use ceil(UTF-8 bytes / 4), including negative measured overhead.
They are not billing tokens, credits, or an ON/OFF counterfactual.

Support and source: https://github.com/fumingyang2004/codex-token-saver
