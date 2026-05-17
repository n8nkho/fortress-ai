#!/usr/bin/env bash
# One-time GitHub push setup for this VM (agent + manual git push).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${HOME}/.ssh/github_deploy"
PUB="${KEY}.pub"

if [[ ! -f "$PUB" ]]; then
  ssh-keygen -t ed25519 -f "$KEY" -N "" -C "khf-vnic-fortress-deploy"
  chmod 600 "$KEY"
fi

mkdir -p "${HOME}/.ssh"
if ! grep -q 'Host github.com' "${HOME}/.ssh/config" 2>/dev/null; then
  cat >> "${HOME}/.ssh/config" <<'EOF'
Host github.com
  HostName github.com
  User git
  IdentityFile ~/.ssh/github_deploy
  IdentitiesOnly yes
EOF
  chmod 600 "${HOME}/.ssh/config"
fi

echo "=== Add this deploy key to GitHub (both repos: fortress-ai + trading-bot) ==="
echo "  Settings → SSH and GPG keys → New SSH key"
echo "  Or per-repo: Settings → Deploy keys → Add (allow write for push)"
echo
cat "$PUB"
echo
echo "=== Optional: gh CLI token (alternative to SSH) ==="
echo "  echo 'ghp_...' > ~/.config/fortress/github_token   # chmod 600"
echo "  gh auth login --with-token < ~/.config/fortress/github_token"
echo "  gh auth setup-git"
echo

for repo in "$ROOT" "${ROOT}/../trading-bot"; do
  [[ -d "$repo/.git" ]] || continue
  name="$(basename "$repo")"
  case "$name" in
    fortress-ai) url="git@github.com:n8nkho/fortress-ai.git" ;;
    trading-bot) url="git@github.com:n8nkho/trading-bot.git" ;;
    *) continue ;;
  esac
  git -C "$repo" remote set-url origin "$url"
  echo "[$name] origin -> $url"
done

echo
echo "Test: ssh -T git@github.com"
ssh -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 || true
