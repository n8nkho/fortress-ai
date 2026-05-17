# GitHub push from this VM

Agents and operators can push after **one-time** auth setup.

## Option A — SSH deploy key (recommended)

```bash
./scripts/setup_github_push.sh
```

Add the printed public key to GitHub:

- **Account:** [SSH keys](https://github.com/settings/keys) (access to `n8nkho/fortress-ai` and `n8nkho/trading-bot`), or
- **Per repo:** Settings → Deploy keys → **Allow write access**

Test:

```bash
ssh -T git@github.com
cd /home/ubuntu/fortress-ai && git push origin main
cd /home/ubuntu/trading-bot && git push origin master
```

## Option B — GitHub CLI token

```bash
mkdir -p ~/.config/fortress && chmod 700 ~/.config/fortress
# Paste a fine-grained PAT with Contents: Read and write on both repos
nano ~/.config/fortress/github_token   # chmod 600
gh auth login --with-token < ~/.config/fortress/github_token
gh auth setup-git
```

Remotes can stay HTTPS when using `gh auth setup-git`.

## Branches

| Repo          | Branch   |
|---------------|----------|
| `fortress-ai` | `main`   |
| `trading-bot` | `master` |

Never commit `.env` or IDE folders.
