"""Load and validate configuration from a YAML file."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from pr_assigner.models import ReviewerGroup

_DEFAULT_CONFIG_PATH = Path("config.yml")


class ConfigError(Exception):
    """Configuration error."""


def _resolve_env_vars(value: str) -> str:
    """Replace ${ENV_VAR} placeholders with values from the environment."""
    import re

    def replacer(match: re.Match) -> str:
        var = match.group(1)
        result = os.environ.get(var)
        if result is None:
            raise ConfigError(f"Environment variable '{var}' is not defined (required by config)")
        return result

    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    with path.open() as f:
        return yaml.safe_load(f) or {}


class Config:
    """Container for the application configuration."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self._raw = raw
        self._github = raw.get("github", {})
        self._groups = self._parse_groups(raw.get("groups", {}), self._github.get("org", ""))

    # ------------------------------------------------------------------ #
    #  Quick-access properties                                             #
    # ------------------------------------------------------------------ #

    @property
    def github_token(self) -> str:
        token = self._github.get("token", "${GITHUB_TOKEN}")
        return _resolve_env_vars(token)

    @property
    def github_base_url(self) -> str:
        return self._github.get("base_url", "https://api.github.com")

    @property
    def org(self) -> str:
        org = self._github.get("org")
        if not org:
            raise ConfigError("'github.org' is required in the configuration")
        return org

    @property
    def groups(self) -> dict[str, ReviewerGroup]:
        return self._groups

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    def get_group_for_repo(self, repo: str) -> ReviewerGroup | None:
        """Return the ReviewerGroup that contains the given repo.

        Accepts both full names (owner/repo) and short names (repo only).
        """
        for group in self._groups.values():
            for r in group.repos:
                if r == repo or r.split("/")[-1] == repo:
                    return group
        return None

    def get_group_by_name(self, name: str) -> ReviewerGroup:
        """Return the ReviewerGroup by name."""
        group = self._groups.get(name)
        if group is None:
            raise ConfigError(
                f"Group '{name}' is not configured. "
                f"Available: {list(self._groups)}"
            )
        return group

    # ------------------------------------------------------------------ #
    #  Parsing                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_groups(raw: dict[str, Any], org: str) -> dict[str, ReviewerGroup]:
        result: dict[str, ReviewerGroup] = {}
        for group_name, group_cfg in raw.items():
            if not isinstance(group_cfg, dict):
                raise ConfigError(f"groups.{group_name} must be a YAML object")
            reviewers = group_cfg.get("reviewers", [])
            if not isinstance(reviewers, list) or not all(isinstance(r, str) for r in reviewers):
                raise ConfigError(
                    f"groups.{group_name}.reviewers must be a list of strings"
                )
            repos_raw = group_cfg.get("repos", [])
            if not isinstance(repos_raw, list) or not all(isinstance(r, str) for r in repos_raw):
                raise ConfigError(
                    f"groups.{group_name}.repos must be a list of strings"
                )
            # Normalize: prepend org if repo has no owner prefix
            repos = [
                r if "/" in r else f"{org}/{r}"
                for r in repos_raw
            ]
            result[group_name] = ReviewerGroup(
                name=group_name,
                reviewers=reviewers,
                repos=repos,
            )
        return result


def load_config(path: Path | str | None = None) -> Config:
    """Load configuration from a YAML file."""
    resolved = Path(path) if path else _DEFAULT_CONFIG_PATH
    raw = _load_raw(resolved)
    return Config(raw)
