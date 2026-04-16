"""Reviewer selection logic based on workload.

Business rules:
  1. Candidates: list of ReviewerLoad for the same repo type.
  2. Primary sort:   ascending by `monthly_review_score` (lower accumulated score = preferred).
  3. Final tiebreaker: alphabetical order (determinism).
  4. Reviewers already assigned to the PR are excluded.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from pr_assigner.github_client import GitHubClient
from pr_assigner.models import AssignmentResult, ReviewerGroup, ReviewerLoad

logger = logging.getLogger(__name__)


class NoReviewersAvailableError(Exception):
    """No reviewers available for the requested repo type."""


# --------------------------------------------------------------------------- #
#  Pure selection (no IO) — easily testable                                    #
# --------------------------------------------------------------------------- #


def select_reviewer(
    candidates: Iterable[ReviewerLoad],
    exclude: Iterable[str] = (),
) -> ReviewerLoad:
    """Return the reviewer with the lowest workload (single pick)."""
    result = select_reviewers(candidates, count=1, exclude=exclude)
    return result[0]


def select_reviewers(
    candidates: Iterable[ReviewerLoad],
    count: int = 1,
    exclude: Iterable[str] = (),
) -> list[ReviewerLoad]:
    """Return the *count* reviewers with the lowest workload.

    Args:
        candidates: Workload list for each candidate reviewer.
        count:      Number of reviewers to pick.
        exclude:    Usernames to skip (e.g. already assigned or PR author).

    Returns:
        Ordered list of selected ReviewerLoad objects (lowest score first).

    Raises:
        NoReviewersAvailableError: If fewer eligible candidates exist than requested.
    """
    excluded = set(exclude)
    eligible = [r for r in candidates if r.username not in excluded]

    if not eligible:
        raise NoReviewersAvailableError("No eligible reviewers for this PR.")

    if len(eligible) < count:
        raise NoReviewersAvailableError(
            f"Only {len(eligible)} eligible reviewer(s) available, {count} requested."
        )

    # sort_key = (monthly_review_score ASC, username ASC)
    return sorted(eligible, key=lambda r: (r.monthly_review_score, r.username))[:count]


# --------------------------------------------------------------------------- #
#  IO orchestrator                                                              #
# --------------------------------------------------------------------------- #


async def build_reviewer_loads(
    client: GitHubClient,
    reviewers: list[str],
    all_repos: list[str],
) -> list[ReviewerLoad]:
    """Query GitHub and build the ReviewerLoad list concurrently.

    Computes the workload for the given reviewers across *all* repos.
    Which users are eligible candidates for a specific repo type is a
    separate concern handled by the caller (e.g. assign_reviewer).
    """

    async def _load_one(username: str) -> ReviewerLoad:
        score, prs = await client.calculate_monthly_review_score(username, all_repos)
        return ReviewerLoad(
            username=username,
            reviewed_this_month=len(prs),
            monthly_review_score=score,
        )

    loads = await asyncio.gather(*[_load_one(u) for u in reviewers])
    return list(loads)


async def assign_reviewer(
    client: GitHubClient,
    org: str,
    repo: str,
    pr_number: int,
    group: ReviewerGroup,
    all_repos: list[str],
    dry_run: bool = False,
    count: int = 1,
) -> AssignmentResult:
    """Select and assign the least-loaded reviewer(s) to a pull request.

    Args:
        client:    Initialised GitHub client.
        org:       GitHub organisation.
        repo:      Full repository name (owner/repo).
        pr_number: Pull request number.
        group:     Reviewer group configuration.
        dry_run:   If True, compute the assignment without actually applying it.
        count:     Number of reviewers to assign.

    Returns:
        AssignmentResult with the selected reviewers and all candidates.
    """
    # Fetch already-assigned reviewers and the PR author to exclude them
    pr_info = await client.get_pr_info(repo, pr_number)
    already_assigned = {
        r["login"] for r in pr_info.get("requested_reviewers", [])
    }
    pr_author = pr_info.get("user", {}).get("login", "")
    exclude = already_assigned | {pr_author}

    logger.debug("PR %s#%d — excluded: %s", repo, pr_number, exclude)

    # Compute workload — eligibility is encoded in group.reviewers;
    # load calculation itself is global.
    candidates = await build_reviewer_loads(client, group.reviewers, all_repos)
    selected = select_reviewers(candidates, count=count, exclude=exclude)

    if not dry_run:
        repo_full = repo if "/" in repo else f"{org}/{repo}"
        await client.request_reviewers(repo_full, pr_number, [r.username for r in selected])

    return AssignmentResult(
        repo=repo,
        pr_number=pr_number,
        selected_reviewers=[r.username for r in selected],
        candidates=candidates,
        dry_run=dry_run,
    )
