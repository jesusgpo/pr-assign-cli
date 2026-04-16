# pr-assigner

Tool to assign reviewers to GitHub pull requests, balancing the review workload across the team.

## Features

- **Smart load balancing**: Picks the reviewer with the lowest workload based on:
  - Number of PRs currently assigned for review (primary criterion)
  - Weighted monthly review score — `ScoreReviewer = Σ [1 + 0.5·log₁₀(lines+1) + min(comments·0.1, 1)]` (tiebreaker)
- **Per-repo-type reviewer lists**: Configure independent pools for `wsc-`, `spa-`, `job-`, `app-`, etc.
- **Automatic exclusions**: Skips reviewers already assigned to the PR and the PR author.
- **Dry-run mode**: Compute the assignment without actually applying it.
- **Friendly CLI**: Typer-based command line with Rich for formatted tables.

## Installation

Requires Python ≥ 3.11. Uses `uv` as the package and dependency manager.

```bash
cd code
uv sync
```

## Configuration

Create a `config.yml` from the provided `config.example.yml`:

```yaml
github:
  org: your-github-org
  token: ${GITHUB_TOKEN}    # resolved from environment variable

repo_types:
  backend:
    repos:
      - service-a
      - service-b
    reviewers:
      - alice
      - bob
      - carol
      
  frontend:
    repos:
      - spa-frontend-a
      - spa-frontend-b
    reviewers:
      - dave
      - eve
      - frank
```

## Usage

### Assign a reviewer to a PR

```bash
# Automatically assign the reviewer with the lowest workload
uv run pr-assigner assign inditex/wsc-sugus 42

# Simulate the assignment without applying it
uv run pr-assigner assign inditex/wsc-sugus 42 --dry-run

# Use a custom config file
uv run pr-assigner assign inditex/wsc-sugus 42 --config /path/to/config.yml

# Verbose mode for debugging
uv run pr-assigner assign inditex/wsc-sugus 42 -v
```

### Show the current reviewer workload

```bash
# Display the workload for the 'wsc' repo type
uv run pr-assigner load wsc

# Other types
uv run pr-assigner load spa
uv run pr-assigner load job
```

## Selection algorithm

1. **Primary criterion**: Fewer currently assigned PRs.
2. **Tiebreaker**: Lower monthly review score.
3. **Final tiebreaker**: Alphabetical order (determinism).
4. **Exclusions**: Reviewers already assigned to the PR and the PR author are skipped.

### Monthly review score

For each PR reviewed this month:
$$\text{ScorePR} = 1 + 0.5 \cdot \log_{10}(\text{lines\_changed} + 1) + \min(\text{comments} \times 0.1,\ 1)$$

$$\text{ScoreReviewer} = \sum \text{ScorePR}$$

## Tests

```bash
uv run pytest tests/ -v

# Or a specific file
uv run pytest tests/test_selector.py -v
```

## Project structure

```
code/
├── pr_assigner/
│   ├── __init__.py           # Package metadata
│   ├── cli.py                # Command-line interface
│   ├── config.py             # Configuration loading and validation
│   ├── github_client.py      # Async GitHub API client
│   ├── models.py             # Dataclasses (ReviewerLoad, etc.)
│   └── selector.py           # Reviewer selection logic
├── tests/
│   ├── test_config.py        # Configuration tests
│   ├── test_github_client.py # GitHub client tests
│   └── test_selector.py      # Selection logic tests
├── config.example.yml        # Example configuration
├── pyproject.toml            # Project metadata (uv + hatchling)
└── README.md                 # This file
```

## Main dependencies

- **httpx**: Async HTTP client
- **typer**: CLI framework
- **pyyaml**: YAML parsing
- **rich**: Formatted output and tables
- **anyio**: Asyncio compatibility

### Dev dependencies

- **pytest**: Testing framework
- **pytest-asyncio**: Plugin for async tests
- **respx**: HTTP request mocking
- **pytest-mock**: Mocking fixtures
