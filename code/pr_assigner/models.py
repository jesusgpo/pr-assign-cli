"""Data models for pr-assigner."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReviewerLoad:
    """Workload of a reviewer at a given point in time.

    Attributes:
        username:             GitHub login of the reviewer.
        reviewed_this_month:  Number of PRs reviewed this month (used to compute monthly_review_score).
        monthly_review_score: Accumulated review score for PRs reviewed this month (ΣScorePR).

    Scoring formula per PR:
        ScorePR = ReviewValue + 0.5 × log10(LinesChanged + 1) + min(Comments × 0.1, 1)
        where ReviewValue = 1, LinesChanged = additions + deletions

    Higher score → heavier review load this month → less preferred for assignment.
    """

    username: str
    reviewed_this_month: int = 0
    monthly_review_score: float = 0.0

    @property
    def sort_key(self) -> tuple[float]:
        """Sort key: lower monthly score first."""
        return (self.monthly_review_score,)

    def __str__(self) -> str:
        return (
            f"{self.username} "
            f"[reviewed={self.reviewed_this_month}, "
            f"monthly_score={self.monthly_review_score:.2f}]"
        )


@dataclass
class ReviewerGroup:
    """Configuration for a reviewer group (a named set of repos and their reviewers)."""

    name: str                   # logical group name, e.g. "backend" or "soar"
    reviewers: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)  # "owner/repo" list


@dataclass
class AssignmentResult:
    """Result of a reviewer assignment."""

    repo: str
    pr_number: int
    selected_reviewer: str
    candidates: list[ReviewerLoad]
    dry_run: bool = False
