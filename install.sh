#!/usr/bin/env bash
# bitwig-ears installer (macOS).
#
#   ./install.sh              install everything
#   ./install.sh --check      read-only health check of an existing install
#   ./install.sh --uninstall  remove what this script installed
#
# Options: --link (symlink the Bitwig script instead of copying, so repo edits go live on reload)
#          --no-plugin  --no-mcp  --dry-run (print what would happen)
#
# Overrides (for testing): BITWIG_SCRIPTS_DIR, CLAP_DIR
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SCRIPTS_DIR="${BITWIG_SCRIPTS_DIR:-$HOME/Documents/Bitwig Studio/Controller Scripts}/bitwig-ears"
CLAP_DIR="${CLAP_DIR:-$HOME/Library/Audio/Plug-Ins/CLAP}"
SCRIPT_SRC="$ROOT/bitwig/bitwig-ears.control.js"
MCP_NAME="bitwig-ears"

MODE=install; LINK=0; DO_PLUGIN=1; DO_MCP=1; DRY=0
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;;
    --uninstall) MODE=uninstall ;;
    --link) LINK=1 ;;
    --no-plugin) DO_PLUGIN=0 ;;
    --no-mcp) DO_MCP=0 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $a (try --help)"; exit 2 ;;
  esac
done

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
done_ok() { [ "$DRY" = 1 ] || ok "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then printf '  [dry-run] %s\n' "$*"; else "$@"; fi; }
have() { command -v "$1" >/dev/null 2>&1; }

script_version() { sed -n 's/.*defineController("bitwig-ears", "[^"]*", "\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1; }
mcp_registered() { have claude && claude mcp list 2>/dev/null | grep -q "^$MCP_NAME:"; }

check() {
  step "bitwig-ears health check"
  local bad=0
  [ "$(uname)" = "Darwin" ] && ok "macOS" || { bad "not macOS (the installer and plugin bundling are macOS only)"; bad=1; }
  [ -d "/Applications/Bitwig Studio.app" ] && ok "Bitwig Studio installed" || warn "Bitwig Studio not found in /Applications"
  pgrep -f "Bitwig Studio.app/Contents/MacOS/BitwigStudio" >/dev/null && ok "Bitwig is running" || warn "Bitwig is not running"

  if [ -d "$CLAP_DIR/BitwigEarsTap.clap" ]; then
    codesign -v "$CLAP_DIR/BitwigEarsTap.clap" 2>/dev/null && ok "tap plugin installed and signed ($CLAP_DIR)" || { bad "tap plugin present but signature invalid; rerun ./install.sh"; bad=1; }
  else bad "tap plugin not installed"; bad=1; fi

  local dst="$SCRIPTS_DIR/bitwig-ears.control.js"
  if [ -e "$dst" ]; then
    local iv rv; iv="$(script_version "$dst")"; rv="$(script_version "$SCRIPT_SRC")"
    if cmp -s "$dst" "$SCRIPT_SRC"; then ok "controller script installed, up to date (v$iv)"
    else warn "controller script installed (v$iv) differs from the repo (v$rv); rerun ./install.sh and reload it in Bitwig"; fi
  else bad "controller script not installed"; bad=1; fi

  if lsof -nP -iUDP:7879 2>/dev/null | grep -qi bitwig; then ok "controller script is LOADED in Bitwig (listening on UDP 7879)"
  else warn "controller script is not loaded in Bitwig: Settings > Controllers > Add controller > bitwig-ears > MCP Bridge"; fi

  if have claude; then
    mcp_registered && ok "MCP server registered with Claude Code" || warn "MCP server not registered (claude mcp add ...)"
  else warn "'claude' CLI not found; register the MCP server with your client by hand"; fi

  if lsof -nP -iTCP:7878 -sTCP:LISTEN >/dev/null 2>&1; then ok "audio ingest port 7878 is listening (a server is running)"
  else warn "nothing listening on TCP 7878 (the MCP server starts with your client)"; fi
  return $bad
}

uninstall() {
  step "Uninstalling bitwig-ears"
  [ -d "$CLAP_DIR/BitwigEarsTap.clap" ] && { run rm -rf "$CLAP_DIR/BitwigEarsTap.clap"; ok "removed plugin"; } || warn "plugin not installed"
  [ -e "$SCRIPTS_DIR" ] && { run rm -rf "$SCRIPTS_DIR"; ok "removed controller script folder"; } || warn "controller script not installed"
  if [ "$DO_MCP" = 0 ]; then warn "leaving the MCP registration alone (--no-mcp)"
  elif mcp_registered; then run claude mcp remove "$MCP_NAME" >/dev/null && ok "unregistered MCP server"; else warn "MCP server not registered"; fi
  echo; echo "Remove the 'bitwig-ears / MCP Bridge' controller and any 'Bitwig Ears Tap' devices inside Bitwig yourself."
}

install() {
  step "1. Prerequisites"
  [ "$(uname)" = "Darwin" ] || { bad "macOS only"; exit 1; }
  local missing=0
  if [ "$DO_PLUGIN" = 1 ]; then have cargo && ok "cargo (Rust)" || { bad "cargo not found: install Rust from https://rustup.rs"; missing=1; }; fi
  have uv && ok "uv (Python)" || { bad "uv not found: install from https://docs.astral.sh/uv/"; missing=1; }
  [ -d "/Applications/Bitwig Studio.app" ] && ok "Bitwig Studio" || warn "Bitwig Studio not found in /Applications (continuing)"
  [ "$missing" = 0 ] || exit 1

  if [ "$DO_PLUGIN" = 1 ]; then
    step "2. Tap plugin (build, bundle, sign, install)"
    if [ "$DRY" = 1 ]; then echo "  [dry-run] CLAP_DIR='$CLAP_DIR' plugin/bundle-macos.sh --install"
    else CLAP_DIR="$CLAP_DIR" "$ROOT/plugin/bundle-macos.sh" --install; fi
  fi

  step "3. Bitwig controller script"
  run mkdir -p "$SCRIPTS_DIR"
  if [ "$LINK" = 1 ]; then run ln -sf "$SCRIPT_SRC" "$SCRIPTS_DIR/bitwig-ears.control.js"; done_ok "linked $SCRIPTS_DIR/bitwig-ears.control.js"
  else run cp "$SCRIPT_SRC" "$SCRIPTS_DIR/bitwig-ears.control.js"; done_ok "copied to $SCRIPTS_DIR"; fi

  step "4. MCP server dependencies"
  if [ "$DRY" = 1 ]; then echo "  [dry-run] (cd server && uv sync)"; else (cd "$ROOT/server" && uv sync -q) && ok "Python environment ready"; fi

  if [ "$DO_MCP" = 1 ]; then
    step "5. Register the MCP server"
    if have claude; then
      if mcp_registered; then ok "already registered with Claude Code"
      else run claude mcp add "$MCP_NAME" -- uv run --directory "$ROOT/server" server.py && done_ok "registered with Claude Code"; fi
    else
      warn "'claude' CLI not found. Register it with your MCP client using:"
      echo "     uv run --directory \"$ROOT/server\" server.py"
    fi
  fi

  step "Done. Two things only Bitwig can do for you:"
  cat <<EOF
  1. Settings > Controllers > Add controller > vendor "bitwig-ears", model "MCP Bridge".
     A popup "bitwig-ears vX loaded" confirms it. (After script updates: remove and re-add it.)
  2. Put "Bitwig Ears Tap" on tracks you want to hear (rescan plugins if it is not listed),
     or let the assistant add it with ensure_tap.

  Then restart your MCP client, and run  ./install.sh --check  any time to see what's missing.
EOF
}

case "$MODE" in
  check) check ;;
  uninstall) uninstall ;;
  install) install ;;
esac
