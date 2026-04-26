#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="crosmos"
PLUGIN_DIR_NAME="hermes-crosmos"
DEFAULT_BASE_URL="https://api.crosmos.dev/api/v1"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DIR="$HERMES_HOME/plugins/$PLUGIN_NAME"
HERMES_ENV_FILE="$HERMES_HOME/.env"
CROSMOS_CONFIG_FILE="$HERMES_HOME/crosmos.json"

info() { printf -- '-> %s\n' "$*"; }
success() { printf 'OK %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
fail() {
  printf 'ERROR %s\n' "$*" >&2
  exit 1
}

have_cmd() { command -v "$1" >/dev/null 2>&1; }

read_env_value() {
  local key="$1" file="$2"
  [ -f "$file" ] || return 0
  awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1) }' "$file"
}

upsert_env_value() {
  local key="$1" value="$2" file="$3" tmp_file
  mkdir -p "$(dirname "$file")"
  tmp_file="${file}.tmp.$$"
  if [ -f "$file" ]; then
    awk -v key="$key" -v value="$value" '
            BEGIN { updated = 0 }
            index($0, key "=") == 1 { if (!updated) { print key "=" value; updated = 1 }; next }
            { print }
            END { if (!updated) print key "=" value }
        ' "$file" >"$tmp_file"
  else
    printf '%s=%s\n' "$key" "$value" >"$tmp_file"
  fi
  mv "$tmp_file" "$file"
}

verify_api_key() {
  local key="$1" url="$2" health_url
  have_cmd curl || return 1
  # /health is mounted at the host root, not under the API prefix.
  health_url="$(printf '%s' "$url" | sed -E 's|^(https?://[^/]+).*|\1/health|')"
  curl -fsSL --connect-timeout 5 --max-time 10 \
    -H "Authorization: Bearer $key" \
    -H "Content-Type: application/json" \
    "$health_url" >/dev/null 2>&1
}

get_first_org_id() {
  local key="$1" url="$2" response
  response="$(
    curl -fsSL --connect-timeout 5 --max-time 10 \
      -H "Authorization: Bearer $key" \
      -H "Content-Type: application/json" \
      "${url%/}/orgs" 2>/dev/null
  )"
  printf '%s' "$response" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    orgs = d.get("orgs", [])
    print(orgs[0]["id"] if orgs else "")
except Exception:
    print("")
'
}

create_space() {
  local key="$1" url="$2" name="$3" org_id="$4" response
  response="$(
    curl -fsSL --connect-timeout 5 --max-time 15 \
      -X POST \
      -H "Authorization: Bearer $key" \
      -H "Content-Type: application/json" \
      -d "{\"org_id\": \"$org_id\", \"name\": \"$name\", \"description\": \"Hermes agent memory\"}" \
      "${url%/}/spaces" 2>/dev/null
  )"
  printf '%s' "$response" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print(d.get("id", ""))
except Exception:
    print("")
'
}

have_cmd hermes || fail "hermes command is required; install Hermes first"
have_cmd curl || fail "curl is required for API calls"
have_cmd python3 || fail "python3 is required"

# install
info "installing $PLUGIN_DIR_NAME via hermes plugins install"
HERMES_HOME="$HERMES_HOME" hermes plugins install "crosmos-app/$PLUGIN_DIR_NAME" 2>/dev/null || {
  # Fallback: clone manually
  if [ ! -d "$PLUGIN_DIR" ]; then
    info "cloning plugin manually"
    git clone "https://github.com/crosmos-app/$PLUGIN_DIR_NAME.git" "$PLUGIN_DIR" 2>/dev/null ||
      fail "unable to install plugin; check network or install manually"
  fi
}

[ -f "$PLUGIN_DIR/__init__.py" ] || fail "installed plugin is missing __init__.py"
[ -f "$PLUGIN_DIR/plugin.yaml" ] || fail "installed plugin is missing plugin.yaml"

info "plugin installed at $PLUGIN_DIR"

# config — always overwrite saved base URL with the current default
# (only honor an explicit CROSMOS_BASE_URL passed in the environment)
CROSMOS_BASE_URL="${CROSMOS_BASE_URL:-$DEFAULT_BASE_URL}"

existing_key="$(read_env_value "CROSMOS_API_KEY" "$HERMES_ENV_FILE")"
CROSMOS_API_KEY="${CROSMOS_API_KEY:-}"

if [ -z "$CROSMOS_API_KEY" ]; then
  CROSMOS_API_KEY="$existing_key"
fi

if [ -z "$CROSMOS_API_KEY" ]; then
  printf '\n'
  info "Crosmos API key not found"
  info "Get one at: https://console.crosmos.dev"
  printf 'CROSMOS_API_KEY: '
  read -r CROSMOS_API_KEY
  [ -z "$CROSMOS_API_KEY" ] && fail "API key is required"
fi

upsert_env_value "CROSMOS_API_KEY" "$CROSMOS_API_KEY" "$HERMES_ENV_FILE"
success "saved CROSMOS_API_KEY to $HERMES_ENV_FILE"

upsert_env_value "CROSMOS_BASE_URL" "$CROSMOS_BASE_URL" "$HERMES_ENV_FILE"
success "saved CROSMOS_BASE_URL to $HERMES_ENV_FILE"

# verification
info "verifying Crosmos API connectivity at $CROSMOS_BASE_URL"
if verify_api_key "$CROSMOS_API_KEY" "$CROSMOS_BASE_URL"; then
  success "Crosmos API is reachable"
else
  warn "could not verify Crosmos API key (network issue or wrong URL?)"
  warn "the plugin will be installed but may not function until connectivity is fixed"
fi

# default space (referenced by name; UUID is resolved at runtime)
existing_space_name="$(read_env_value "CROSMOS_SPACE_NAME" "$HERMES_ENV_FILE")"
CROSMOS_SPACE_NAME="${CROSMOS_SPACE_NAME:-${existing_space_name:-hermes-agent}}"

info "ensuring memory space '$CROSMOS_SPACE_NAME' exists"
org_uuid="$(get_first_org_id "$CROSMOS_API_KEY" "$CROSMOS_BASE_URL")"
if [ -z "$org_uuid" ]; then
  warn "could not resolve org for the API key; space creation skipped"
else
  # create_space is idempotent for our purposes: if it already exists the API
  # returns an error and the call is a no-op.
  created_id="$(create_space "$CROSMOS_API_KEY" "$CROSMOS_BASE_URL" "$CROSMOS_SPACE_NAME" "$org_uuid")"
  if [ -n "$created_id" ]; then
    success "created memory space '$CROSMOS_SPACE_NAME'"
  else
    info "space '$CROSMOS_SPACE_NAME' already exists or could not be created (continuing)"
  fi
fi
upsert_env_value "CROSMOS_SPACE_NAME" "$CROSMOS_SPACE_NAME" "$HERMES_ENV_FILE"
success "saved CROSMOS_SPACE_NAME to $HERMES_ENV_FILE"

# config json
python3 - "$CROSMOS_CONFIG_FILE" "$CROSMOS_BASE_URL" "$CROSMOS_SPACE_NAME" <<'PY'
import json, sys
from pathlib import Path

config_path, base_url, space_name = sys.argv[1], sys.argv[2], sys.argv[3]
existing = {}
if Path(config_path).exists():
    try:
        existing = json.loads(Path(config_path).read_text())
    except Exception:
        existing = {}

existing["base_url"] = base_url
existing["space_name"] = space_name
existing.pop("space_id", None)
existing.pop("api_key", None)

Path(config_path).parent.mkdir(parents=True, exist_ok=True)
Path(config_path).write_text(json.dumps(existing, indent=2) + "\n")
PY
success "config saved to $CROSMOS_CONFIG_FILE"

# activate provider
if have_cmd hermes; then
  if HERMES_HOME="$HERMES_HOME" hermes config set memory.provider crosmos >/dev/null 2>&1; then
    success "activated memory.provider=crosmos via hermes CLI"
  else
    warn "could not auto-activate provider; run manually: hermes config set memory.provider crosmos"
  fi
fi

# done stuff
printf '\n'
success "Crosmos Memory plugin is ready!"
printf '\n  Configuration:\n'
printf '    API URL:  %s\n' "$CROSMOS_BASE_URL"
printf '    Space:     %s\n' "${CROSMOS_SPACE_NAME:-<not set>}"
printf '    API Key:   %s...%s\n' "${CROSMOS_API_KEY:0:8}" "${CROSMOS_API_KEY: -4}"
printf '\n  Next steps:\n'
printf '    1. Start a new Hermes session\n'
printf '    2. The plugin will auto-recall context and auto-ingest conversations\n'
printf '    3. Use crosmos_remember, crosmos_recall, crosmos_forget tools explicitly if needed\n'
printf '    4. Run: hermes memory status  (to verify activation)\n'
