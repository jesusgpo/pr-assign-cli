"""Tests for configuration loading and validation (pr_assigner.config)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pr_assigner.config import Config, ConfigError, load_config


# ─────────────────────────── Fixtures ────────────────────────────────────── #


def _make_raw(
    org: str = "your-github-org",
    token: str = "ghp_test123",
    groups: dict | None = None,
) -> dict:
    if groups is None:
        groups = {
            "backend": {
                "repos": ["service-a", "your-github-org/wsc-boman"],
                "reviewers": ["alice", "bob"],
            },
            "frontend": {
                "repos": ["your-github-org/spa-insecquest"],
                "reviewers": ["carol"],
            },
        }
    return {
        "github": {"org": org, "token": token},
        "groups": groups,
    }


# ───────────────────────── Tests de Config ────────────────────────────────── #


class TestConfigBasics:
    def test_org_se_lee_correctamente(self):
        cfg = Config(_make_raw(org="my-org"))
        assert cfg.org == "my-org"

    def test_token_literal(self):
        cfg = Config(_make_raw(token="ghp_literal"))
        assert cfg.github_token == "ghp_literal"

    def test_token_desde_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("MY_TOKEN", "ghp_from_env")
        cfg = Config(_make_raw(token="${MY_TOKEN}"))
        assert cfg.github_token == "ghp_from_env"

    def test_token_env_no_definida_lanza_error(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("UNDEFINED_TOKEN", raising=False)
        cfg = Config(_make_raw(token="${UNDEFINED_TOKEN}"))
        with pytest.raises(ConfigError, match="UNDEFINED_TOKEN"):
            _ = cfg.github_token

    def test_org_obligatoria(self):
        raw = _make_raw()
        raw["github"].pop("org", None)
        cfg = Config(raw)
        with pytest.raises(ConfigError, match="org"):
            _ = cfg.org

    def test_base_url_por_defecto(self):
        cfg = Config(_make_raw())
        assert cfg.github_base_url == "https://api.github.com"


class TestRepoTypes:
    def test_grupos_cargados_correctamente(self):
        cfg = Config(_make_raw())
        assert "backend" in cfg.groups
        assert "frontend" in cfg.groups
        assert cfg.groups["backend"].reviewers == ["alice", "bob"]

    def test_get_group_for_repo(self):
        cfg = Config(_make_raw())
        g = cfg.get_group_for_repo("service-a")
        assert g is not None
        assert g.name == "backend"

    def test_get_group_for_repo_desconocido_devuelve_none(self):
        cfg = Config(_make_raw())
        assert cfg.get_group_for_repo("your-github-org/unknown-repo") is None

    def test_get_group_by_name(self):
        cfg = Config(_make_raw())
        g = cfg.get_group_by_name("frontend")
        assert g.reviewers == ["carol"]

    def test_get_group_by_name_inexistente_lanza_error(self):
        cfg = Config(_make_raw())
        with pytest.raises(ConfigError, match="job"):
            cfg.get_group_by_name("job")

    def test_repos_vacios_permitidos(self):
        raw = _make_raw(groups={"backend": {"repos": [], "reviewers": ["alice"]}})
        cfg = Config(raw)
        assert cfg.groups["backend"].repos == []

    def test_reviewers_vacios_permitidos(self):
        raw = _make_raw(groups={"backend": {"repos": ["service-a"], "reviewers": []}})
        cfg = Config(raw)
        assert cfg.groups["backend"].reviewers == []


class TestLoadConfig:
    def test_carga_desde_fichero(self, tmp_path: Path):
        path = tmp_path / "config.yml"
        path.write_text(yaml.dump(_make_raw()))
        cfg = load_config(path)
        assert cfg.org == "your-github-org"

    def test_missing_file_raises_error(self, tmp_path: Path):
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yml")
