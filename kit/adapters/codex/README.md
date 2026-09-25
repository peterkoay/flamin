Codex adapter. Rendered files: `AGENTS.md`, `.codex/config.toml` (agents limits and inline hooks) and `.codex/agents/*.toml` (all shared; hooks carry a POSIX command and a Windows command in one file).
After any change to the hooks in `.codex/config.toml`, trust the hooks again in `/hooks` (D-29).
