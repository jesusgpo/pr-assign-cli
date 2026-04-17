"""GitHub REST API client.

Uses httpx for all requests. The token is passed in the Authorization header.
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import date
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_SEARCH_DELAY = 1.2  # seconds between /search calls to respect the rate-limit (10 rpm)


class GitHubError(Exception):
    """Generic GitHub API error."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GitHubClient:
    """Lightweight wrapper around the GitHub REST API."""

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-Github-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> GitHubClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ------------------------------------------------------------------ #
    #  Monthly review score                                                #
    # ------------------------------------------------------------------ #

    async def calculate_monthly_review_score(
        self,
        username: str,
        repos: list[str],
    ) -> tuple[float, list[dict[str, Any]]]:
        """Compute ScoreReviewer = ΣScorePR for all PRs reviewed this month.

        Returns:
            (score, prs) — the total score and the list of reviewed PR dicts
            (each has keys: repo, number, title, url).
        """
        start_of_month = date.today().replace(day=1).isoformat()
        repo_scope = " ".join(f"repo:{r}" for r in repos)
        query = (
            f"is:pr reviewed-by:{username} {repo_scope} "
            f"updated:>={start_of_month}"
        )
        prs = await self._search_prs_list(query)
        if not prs:
            return 0.0, []

        scores = await asyncio.gather(
            *[self._score_pr(pr["repo"], pr["number"]) for pr in prs]
        )
        total = sum(scores)
        logger.debug(
            "Monthly score '%s' → %.2f (%d PRs)",
            username, total, len(prs),
        )
        return total, prs

    # ------------------------------------------------------------------ #
    #  Assign reviewer                                                     #
    # ------------------------------------------------------------------ #

    async def list_open_prs(self, repo: str) -> list[dict[str, Any]]:
        """Return open pull requests for a repository.

        Each item has: number, title, url, author.
        """
        results: list[dict[str, Any]] = []
        page = 1
        per_page = 100

        while True:
            params = {"state": "open", "per_page": per_page, "page": page}
            resp = await self._client.get(f"/repos/{repo}/pulls", params=params)
            self._raise_for_status(resp, f"list_open_prs {repo}")
            items: list[dict[str, Any]] = resp.json()
            for item in items:
                reviewers = [
                    r["login"]
                    for r in item.get("requested_reviewers", [])
                ]
                results.append({
                    "number": item["number"],
                    "title": item.get("title", ""),
                    "url": item.get("html_url", ""),
                    "author": item.get("user", {}).get("login", ""),
                    "reviewers": reviewers,
                })
            if len(items) < per_page:
                break
            page += 1

        return results

    async def request_reviewer(
        self,
        repo: str,
        pr_number: int,
        reviewer: str,
    ) -> None:
        """Add a single reviewer to a pull request."""
        await self.request_reviewers(repo, pr_number, [reviewer])

    async def request_reviewers(
        self,
        repo: str,
        pr_number: int,
        reviewers: list[str],
    ) -> None:
        """Add one or more reviewers to a pull request."""
        url = f"/repos/{repo}/pulls/{pr_number}/requested_reviewers"
        resp = await self._client.post(url, json={"reviewers": reviewers})
        self._raise_for_status(resp, f"request_reviewers {repo}#{pr_number}")
        logger.info("Reviewers %s assigned to %s#%d", reviewers, repo, pr_number)

    # ------------------------------------------------------------------ #
    #  PR metadata                                                         #
    # ------------------------------------------------------------------ #

    async def get_pr_info(self, repo: str, pr_number: int) -> dict[str, Any]:
        """Return basic metadata for a pull request."""
        resp = await self._client.get(f"/repos/{repo}/pulls/{pr_number}")
        self._raise_for_status(resp, f"get_pr_info {repo}#{pr_number}")
        return resp.json()

    # ------------------------------------------------------------------ #
    #  Internal helpers                                                    #
    # ------------------------------------------------------------------ #

    async def _search_prs_list(self, query: str) -> list[dict[str, Any]]:
        """Return a list of {'repo': 'owner/repo', 'number': N} for a search query.

        Repositories are already scoped via repo: qualifiers in the query,
        so no client-side filtering is needed.
        """
        results: list[dict[str, Any]] = []
        page = 1
        per_page = 100

        while True:
            await asyncio.sleep(_SEARCH_DELAY)  # avoid search rate-limit (10 req/min)
            params = {"q": query, "per_page": per_page, "page": page}
            resp = await self._client.get("/search/issues", params=params)
            self._raise_for_status(resp, f"search [{query[:60]}…]")
            data = resp.json()
            items: list[dict[str, Any]] = data.get("items", [])

            for item in items:
                repo_url: str = item.get("repository_url", "")
                owner_repo = "/".join(repo_url.split("/")[-2:])
                results.append({
                    "repo": owner_repo,
                    "number": item["number"],
                    "title": item.get("title", ""),
                    "url": item.get("html_url", ""),
                })

            total: int = data.get("total_count", 0)
            if len(items) < per_page or page * per_page >= total:
                break
            page += 1

        return results

    async def _score_pr(self, repo: str, pr_number: int) -> float:
        """Compute ScorePR = ReviewValue + 0.5×log10(LinesChanged+1) + min(Comments×0.1, 1)."""
        resp = await self._client.get(f"/repos/{repo}/pulls/{pr_number}")
        self._raise_for_status(resp, f"score_pr {repo}#{pr_number}")
        data = resp.json()

        lines_changed = data.get("additions", 0) + data.get("deletions", 0)
        comments = data.get("comments", 0) + data.get("review_comments", 0)

        review_value = 1.0
        score = (
            review_value
            + 0.5 * math.log10(lines_changed + 1)
            + min(comments * 0.1, 1.0)
        )
        logger.debug(
            "ScorePR %s#%d → %.2f (lines=%d, comments=%d)",
            repo, pr_number, score, lines_changed, comments,
        )
        return score

    @staticmethod
    def _raise_for_status(resp: httpx.Response, context: str) -> None:
        if resp.is_success:
            return
        try:
            detail = resp.json().get("message", resp.text[:200])
        except Exception:
            detail = resp.text[:200]
        raise GitHubError(
            f"[{context}] HTTP {resp.status_code}: {detail}",
            status_code=resp.status_code,
        )
