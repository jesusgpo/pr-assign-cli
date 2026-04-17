# pr-assign-cli

A GitHub Action and CLI tool that automatically assigns the **least-loaded reviewer(s)** to a pull request, using a workload-balancing algorithm based on the number and complexity of PRs reviewed this month.

---

## How it works

When a PR is opened, the action:

1. Reads your team's `config.yml` to find which group covers the target repo.
2. Queries GitHub to count how many PRs each team member has reviewed this month (and how complex they were).
3. Picks the N least-loaded reviewers.
4. Assigns them to the PR via the GitHub API.

---

## Quick start (GitHub Action)

### 1. Add a config file to your repo

Copy [`.github/examples/pr-assigner.example.yml`](.github/examples/pr-assigner.example.yml) to `.github/pr-assigner.yml` in your repo and edit it:

```yaml
github:
  token: "${GITHUB_TOKEN}"
  org: "my-github-org"

groups:
  my-team:
    repos:
      - my-repo-name
    reviewers:
      - alice
      - bob
      - carol
```

### 2. Add the workflow

Copy [`.github/examples/target-repo-workflow.yml`](.github/examples/target-repo-workflow.yml) to `.github/workflows/auto-assign-reviewers.yml` in your repo:

```yaml
name: Auto-assign reviewers

on:
  pull_request:
    types: [opened, ready_for_review, reopened]

permissions:
  pull-requests: write
  contents: read

jobs:
  assign:
    runs-on: ubuntu-latest
    if: github.event.pull_request.draft == false
    steps:
      - uses: actions/checkout@v4
      - uses: jesusgpo/pr-assign-cli@v1
        with:
          github-token:   ${{ secrets.GITHUB_TOKEN }}
          pr-number:      ${{ github.event.pull_request.number }}
          reviewer-count: 2
          config-path:    .github/pr-assigner.yml
```

That's all — no extra secrets needed, `GITHUB_TOKEN` is provided automatically.

---

## Action inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `github-token` | ✅ | — | GitHub token with `pull-requests: write` scope |
| `pr-number` | ✅ | — | Pull request number |
| `reviewer-count` | ❌ | `2` | Number of reviewers to assign |
| `config-path` | ❌ | `.github/pr-assigner.yml` | Path to your config file |
| `uv-version` | ❌ | `0.9.7` | uv version to install |
| `python-version` | ❌ | `3.11.5` | Python version to use |

---

## Versioning

This action follows [semantic versioning](https://semver.org/).

| Reference | Behaviour |
|---|---|
| `@v1` | Latest `v1.x.x` — **recommended** for most users |
| `@v1.2.3` | Pinned to an exact release — for reproducibility |
| `@main` | Unreleased HEAD — do not use in production |

### Publishing a new release

The only thing you need to do is bump the version in [code/pyproject.toml](code/pyproject.toml) and push to `main`:

```toml
# code/pyproject.toml
[project]
version = "1.2.3"   # ← change this
```

```bash
git add code/pyproject.toml
git commit -m "chore: bump version to 1.2.3"
git push origin main
```

The release workflow detects the change and automatically:
- Creates the git tag `v1.2.3`
- Publishes a GitHub Release with auto-generated notes
- Moves the `v1` floating tag to the same commit

If the tag already exists (e.g. you pushed without bumping the version), the workflow skips silently.

After publishing the release, the `v1` floating tag is updated automatically by [`.github/workflows/release.yml`](.github/workflows/release.yml).

---

## CLI usage

See [`code/README.md`](code/README.md) for CLI installation and usage instructions.
