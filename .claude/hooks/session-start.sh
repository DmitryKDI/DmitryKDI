#!/bin/bash
set -euo pipefail

# Only run in Claude Code on the web / remote sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

# --- RTK (rtk-ai/rtk): token-optimized command wrapping ---
# Installs the rtk binary if missing, then re-registers the global
# PreToolUse hook so bash commands get auto-rewritten to their rtk
# equivalents in this session too (the global hook lives in
# ~/.claude/settings.json, which does not persist across containers).
if ! command -v rtk >/dev/null 2>&1; then
  # Fast path: download a pinned release binary directly. Some sandboxed
  # environments block api.github.com and github.com/releases/latest (used
  # by the official install.sh to resolve "latest"), but direct
  # /releases/download/<tag>/... asset URLs still work. Bump RTK_VERSION
  # occasionally to pick up newer releases.
  RTK_VERSION="v0.42.4"
  RTK_ASSET=""
  case "$(uname -m)" in
    x86_64) RTK_ASSET="rtk-x86_64-unknown-linux-musl.tar.gz" ;;
    aarch64|arm64) RTK_ASSET="rtk-aarch64-unknown-linux-gnu.tar.gz" ;;
  esac
  if [ -n "$RTK_ASSET" ]; then
    TMP_TARBALL="$(mktemp -u).tar.gz"
    if curl -fsSL "https://github.com/rtk-ai/rtk/releases/download/${RTK_VERSION}/${RTK_ASSET}" -o "$TMP_TARBALL" 2>/dev/null; then
      mkdir -p "$HOME/.local/bin"
      tar -xzf "$TMP_TARBALL" -C "$HOME/.local/bin" rtk 2>/dev/null || true
      chmod +x "$HOME/.local/bin/rtk" 2>/dev/null || true
    fi
    rm -f "$TMP_TARBALL"
  fi
fi

if ! command -v rtk >/dev/null 2>&1; then
  # Fallback: official install script (works when api.github.com is reachable).
  curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh >/dev/null 2>&1 || true
fi

if ! command -v rtk >/dev/null 2>&1 && command -v cargo >/dev/null 2>&1; then
  # Last resort: build from source (slow, ~3 min, but always works if cargo is present).
  cargo install --git https://github.com/rtk-ai/rtk --locked >/dev/null 2>&1 || true
fi

if command -v rtk >/dev/null 2>&1; then
  rtk init --global --auto-patch >/dev/null 2>&1 || true
fi

# --- Remotion project dependencies ---
if [ -f "$CLAUDE_PROJECT_DIR/remotion-video/package.json" ]; then
  (cd "$CLAUDE_PROJECT_DIR/remotion-video" && npm install)
fi
