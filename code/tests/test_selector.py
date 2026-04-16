"""Tests for the reviewer selection logic (pr_assigner.selector)."""
from __future__ import annotations

import pytest

from pr_assigner.models import ReviewerLoad
from pr_assigner.selector import NoReviewersAvailableError, select_reviewer


class TestSelectReviewer:
    def test_selects_lowest_score(self):
        """The reviewer with the lowest monthly score wins."""
        candidates = [
            ReviewerLoad("alice", monthly_review_score=5.0),
            ReviewerLoad("bob", monthly_review_score=1.0),
            ReviewerLoad("carol", monthly_review_score=10.0),
        ]
        result = select_reviewer(candidates)
        assert result.username == "bob"

    def test_tiebreak_by_monthly_score(self):
        """The reviewer with the lower monthly score wins."""
        candidates = [
            ReviewerLoad("alice", monthly_review_score=8.5),
            ReviewerLoad("bob", monthly_review_score=3.2),
            ReviewerLoad("carol", monthly_review_score=5.0),
        ]
        result = select_reviewer(candidates)
        assert result.username == "bob"

    def test_full_tiebreak_by_alphabetical_order(self):
        """With identical workload, the first alphabetically wins."""
        candidates = [
            ReviewerLoad("zoe", monthly_review_score=0.0),
            ReviewerLoad("alice", monthly_review_score=0.0),
            ReviewerLoad("martin", monthly_review_score=0.0),
        ]
        result = select_reviewer(candidates)
        assert result.username == "alice"

    def test_excludes_already_assigned_reviewers(self):
        """A reviewer already assigned to the PR must not be selected."""
        candidates = [
            ReviewerLoad("alice", monthly_review_score=0.0),
            ReviewerLoad("bob", monthly_review_score=1.0),
        ]
        result = select_reviewer(candidates, exclude={"alice"})
        assert result.username == "bob"

    def test_excludes_pr_author(self):
        """The PR author must not be selected (passed as excluded)."""
        candidates = [
            ReviewerLoad("author", monthly_review_score=0.0),
            ReviewerLoad("reviewer", monthly_review_score=2.0),
        ]
        result = select_reviewer(candidates, exclude={"author"})
        assert result.username == "reviewer"

    def test_raises_if_no_candidates(self):
        """NoReviewersAvailableError is raised when all candidates are excluded."""
        candidates = [ReviewerLoad("alice", monthly_review_score=0.0)]
        with pytest.raises(NoReviewersAvailableError):
            select_reviewer(candidates, exclude={"alice"})

    def test_raises_with_empty_list(self):
        """NoReviewersAvailableError is raised with an empty candidate list."""
        with pytest.raises(NoReviewersAvailableError):
            select_reviewer([])

    def test_single_candidate_is_selected(self):
        """A single candidate is always selected."""
        candidates = [ReviewerLoad("solo", monthly_review_score=20.0)]
        result = select_reviewer(candidates)
        assert result.username == "solo"

    def test_lowest_score_wins(self):
        """The reviewer with the lowest monthly score always wins."""
        candidates = [
            ReviewerLoad("light", monthly_review_score=0.0),
            ReviewerLoad("heavy", monthly_review_score=100.0),
        ]
        result = select_reviewer(candidates)
        assert result.username == "light"

    def test_exclude_multiple_reviewers(self):
        """Excluding several reviewers at once works correctly."""
        candidates = [
            ReviewerLoad("alice", monthly_review_score=0.0),
            ReviewerLoad("bob", monthly_review_score=0.0),
            ReviewerLoad("carol", monthly_review_score=1.0),
        ]
        result = select_reviewer(candidates, exclude={"alice", "bob"})
        assert result.username == "carol"
