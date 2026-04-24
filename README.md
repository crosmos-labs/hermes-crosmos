# Crosmos — Memory for Hermes

Persistent memory layer for Hermes Agent, powered by Crosmos.


## Prerequisites

- Hermes Agent installed and working (`hermes version` should print version info)
- An internet connection (the plugin talks to the Crosmos REST API)
- A Crosmos API key from [console.crosmos.dev](https://console.crosmos.dev)

## Install

Review the script first if you prefer, then run:

```bash
curl -fsSL https://raw.githubusercontent.com/crosmos-app/hermes-crosmos/main/install.sh | bash
```

This single command:

- Installs the plugin via `hermes plugins install`
- Prompts for an API key if `CROSMOS_API_KEY` is not set
- Creates a default memory space
- Verifies connectivity to the Crosmos API
- Saves credentials to `HERMES_HOME/.env` and config to `crosmos.json`

Start a new Hermes session — Crosmos is active immediately.

## Manual Install

```bash
# 1. Install the plugin
hermes plugins install crosmos-app/hermes-crosmos

# 2. Set credentials
export CROSMOS_API_KEY=csk_your_key_here

# 3. Restart Hermes
hermes
```

## Upgrade

```bash
hermes plugins update crosmos
```

Start a new session to pick up changes. Your `.env` and `crosmos.json` are not touched.

## Verify

```bash
hermes memory status
```

You should see `crosmos` listed as the active memory provider.

## How It Works

- **Auto-recall** — Before each LLM turn, relevant context is searched and injected into the conversation
- **Auto-ingest** — After each turn, the user/assistant exchange is ingested into the knowledge graph
- **Manual tools** — `crosmos_remember`, `crosmos_recall`, `crosmos_forget`, `crosmos_graph_stats` available for explicit control

No "remember this" or "search for X" needed. It just works.

## Tools

| Tool | Description |
|------|-------------|
| `crosmos_remember` | Store a fact or conversation into the knowledge graph |
| `crosmos_recall` | Search memories by natural language query |
| `crosmos_forget` | Soft-delete a memory by ID |
| `crosmos_graph_stats` | Show entity/edge counts and top relation types |

## Configuration

All files live under `${HERMES_HOME:-$HOME/.hermes}/`.

**`.env`** — credentials and overrides (env vars take precedence over `crosmos.json`):

| Variable | Default | Description |
|----------|---------|-------------|
| `CROSMOS_API_KEY` | — | API key (required) |
| `CROSMOS_BASE_URL` | `https://api.crosmos.dev/v1` | API endpoint |
| `CROSMOS_SPACE_ID` | — | Memory space UUID (auto-created by install) |

**`crosmos.json`** — provider config:

| Key | Default | Description |
|-----|---------|-------------|
| `base_url` | `https://api.crosmos.dev/v1` | API endpoint |
| `space_id` | — | Memory space UUID |

## Uninstall

```bash
hermes plugins remove crosmos

# Switch to another provider or disable memory
hermes config set memory.provider ""
```

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Plugin not showing in `/plugins` | Run `hermes plugins install crosmos-ai/crosmos-hermes-plugin` |
| "Crosmos is not configured" | Check `CROSMOS_API_KEY` is set in `HERMES_HOME/.env` |
| Memories not recalled | Check `CROSMOS_SPACE_ID` is set; verify with `crosmos_recall` tool |
| Connection errors | Verify `CROSMOS_BASE_URL` and check `HERMES_HOME/logs/agent.log` |

## License

MIT
