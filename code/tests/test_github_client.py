"""Tests for the GitHub client (pr_assigner.github_client).

Uses respx to intercept httpx HTTP calls without real network access.
"""
from __future__ import annotations

import pytest
import respx
from httpx import Response

from pr_assigner.github_client import GitHubClient, GitHubError

BASE = "https://api.github.com"

# Patch to skip the rate-limit sleep during tests
pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch):
    """Remove the anti-rate-limit delay in tests."""
    import pr_assigner.github_client as module

    monkeypatch.setattr(module, "_SEARCH_DELAY", 0)


# ─────────────────────── count_reviewed_this_month ────────────────────────── #


@respx.mock
async def test_calculate_monthly_review_score():
    import math

    # 1. Búsqueda devuelve 1 PR en prefijo wsc-
    respx.get(f"{BASE}/search/issues").mock(
        return_value=Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {
                        "number": 10,
                        "repository_url": f"{BASE}/repos/my-org/service-a",
                    }
                ],
            },
        )
    )
    # 2. Detalle del PR: 100 adiciones, 50 borrados, 5 comentarios, 3 review_comments
    respx.get(f"{BASE}/repos/my-org/service-a/pulls/10").mock(
        return_value=Response(
            200,
            json={"additions": 100, "deletions": 50, "comments": 5, "review_comments": 3},
        )
    )
    async with GitHubClient("fake-token", BASE) as client:
        score, prs = await client.calculate_monthly_review_score("alice", ["service-a"])

    # ScorePR = 1.0 + 0.5*log10(151) + min(8*0.1, 1.0)
    expected = 1.0 + 0.5 * math.log10(151) + min(8 * 0.1, 1.0)
    assert score == pytest.approx(expected, rel=1e-6)
    assert len(prs) == 1


@respx.mock
async def test_calculate_monthly_review_score_no_prs():
    """Score is zero when no PRs were reviewed this month."""
    respx.get(f"{BASE}/search/issues").mock(
        return_value=Response(200, json={"total_count": 0, "items": []})
    )
    async with GitHubClient("fake-token", BASE) as client:
        score, prs = await client.calculate_monthly_review_score("alice", ["service-a"])
    assert score == 0.0
    assert len(prs) == 0


@respx.mock
async def test_calculate_monthly_review_score_multiple_repos():
    """Score is summed across all repos passed in."""
    import math

    respx.get(f"{BASE}/search/issues").mock(
        return_value=Response(
            200,
            json={
                "total_count": 1,
                "items": [
                    {"number": 1, "repository_url": f"{BASE}/repos/my-org/service-a"},
                ],
            },
        )
    )
    respx.get(f"{BASE}/repos/my-org/service-a/pulls/1").mock(
        return_value=Response(200, json={"additions": 10, "deletions": 5, "comments": 0, "review_comments": 0})
    )
    async with GitHubClient("fake-token", BASE) as client:
        score, prs = await client.calculate_monthly_review_score("alice", ["service-a", "your-github-org/wsc-boman"])

    expected = 1.0 + 0.5 * math.log10(16) + 0.0
    assert score == pytest.approx(expected, rel=1e-6)
    assert len(prs) == 1


# ─────────────────────── request_reviewer ─────────────────────────────────── #


@respx.mock
async def test_request_reviewer_success():
    respx.post(f"{BASE}/repos/service-a/pulls/42/requested_reviewers").mock(
        return_value=Response(201, json={"number": 42})
    )
    async with GitHubClient("fake-token", BASE) as client:
        await client.request_reviewer("service-a", 42, "alice")


@respx.mock
async def test_request_reviewer_http_error():
    respx.post(f"{BASE}/repos/service-a/pulls/42/requested_reviewers").mock(
        return_value=Response(422, json={"message": "Validation Failed"})
    )
    async with GitHubClient("fake-token", BASE) as client:
        with pytest.raises(GitHubError, match="422"):
            await client.request_reviewer("service-a", 42, "ghost")


# ─────────────────────── get_pr_info ──────────────────────────────────────── #


@respx.mock
async def test_get_pr_info_devuelve_datos():
    payload = {
        "number": 7,
        "user": {"login": "author"},
        "requested_reviewers": [{"login": "existing-reviewer"}],
    }
    respx.get(f"{BASE}/repos/your-github-org/spa-boman/pulls/7").mock(
        return_value=Response(200, json=payload)
    )
    async with GitHubClient("fake-token", BASE) as client:
        info = await client.get_pr_info("your-github-org/spa-boman", 7)
    assert info["number"] == 7
    assert info["user"]["login"] == "author"


@respx.mock
async def test_get_pr_info_not_found():
    respx.get(f"{BASE}/repos/your-github-org/spa-boman/pulls/999").mock(
        return_value=Response(404, json={"message": "Not Found"})
    )
    async with GitHubClient("fake-token", BASE) as client:
        with pytest.raises(GitHubError, match="404"):
            await client.get_pr_info("your-github-org/spa-boman", 999)

