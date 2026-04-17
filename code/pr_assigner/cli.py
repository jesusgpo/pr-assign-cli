"""Command-line interface for pr-assigner.

Available commands:
  assign  – Select and assign the least-loaded reviewer to a PR.
  load    – Show the current review workload for a repo type.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table

from pr_assigner.config import ConfigError, load_config
from pr_assigner.github_client import GitHubClient, GitHubError
from pr_assigner.models import ReviewerLoad
from pr_assigner.selector import (
    NoReviewersAvailableError,
    assign_reviewer,
    build_reviewer_loads,
    select_reviewer,
    select_reviewers,
)

app = typer.Typer(
    name="pr-assigner",
    help="Assign reviewers to GitHub PRs balancing the review workload.",
    rich_markup_mode="rich",
    no_args_is_help=True,
)

console = Console()
err_console = Console(stderr=True, style="bold red")

# --------------------------------------------------------------------------- #
#  Reusable global options                                                      #
# --------------------------------------------------------------------------- #

_CONFIG_OPTION = Annotated[
    Optional[Path],
    typer.Option(
        "--config",
        "-c",
        help="Path to the YAML configuration file. Default: config.yml",
        show_default=True,
    ),
]

_VERBOSE_OPTION = Annotated[
    bool,
    typer.Option("--verbose", "-v", help="Enable DEBUG logging."),
]


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(format="%(levelname)s %(name)s: %(message)s", level=level)


def _abort(msg: str) -> None:
    err_console.print(f"[ERROR] {msg}")
    raise typer.Exit(1)


# --------------------------------------------------------------------------- #
#  Command: assign                                                              #
# --------------------------------------------------------------------------- #


@app.command()
def assign(
    repo: Annotated[
        Optional[str],
        typer.Argument(help="Full repo name: owner/repo  (e.g. service-a). Interactive if omitted."),
    ] = None,
    pr_number: Annotated[
        Optional[int],
        typer.Argument(help="Pull request number. Interactive if omitted."),
    ] = None,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt and assign directly."),
    ] = False,
    count: Annotated[
        int,
        typer.Option("--count", "-n", help="Number of reviewers to assign (default: 1).", min=1),
    ] = 1,

    config: _CONFIG_OPTION = None,
    verbose: _VERBOSE_OPTION = False,
) -> None:
    """Select and assign the least-loaded reviewer(s) to a pull request.

    \b
    If repo or pr_number are omitted, an interactive menu is shown.
    Always shows the proposed reviewer(s) and asks for confirmation unless --yes is passed.

    Examples:
      pr-assigner assign service-a 42
      pr-assigner assign service-a 42 --yes
      pr-assigner assign service-a 42 --count 2
      pr-assigner assign          # fully interactive
    """
    _setup_logging(verbose)

    # ── Repo selection ───────────────────────────────────────────────── #
    if repo is None:
        try:
            cfg = load_config(config)
        except ConfigError as e:
            _abort(str(e))
            return

        all_repos = [r for g in cfg.groups.values() for r in g.repos]
        if not all_repos:
            _abort("No repos configured in config.yml.")
            return

        repo = _pick_from_list("Select a repository", all_repos)
        if repo is None:
            raise typer.Exit(0)

    asyncio.run(_assign_async(repo, pr_number, yes, config, count))


def _pick_from_list(title: str, items: list[str]) -> str | None:
    """Display a numbered menu and return the selected item."""
    console.print(f"\n[bold magenta]{title}:[/bold magenta]")
    for i, item in enumerate(items, start=1):
        console.print(f"  [cyan]{i:>3}.[/cyan] {item}")
    console.print()

    while True:
        raw = typer.prompt("Enter number (or 'q' to quit)", default="")
        if raw.strip().lower() in {"q", "quit", "exit", ""}:
            return None
        try:
            idx = int(raw.strip())
            if 1 <= idx <= len(items):
                return items[idx - 1]
        except ValueError:
            pass
        console.print(f"[yellow]Please enter a number between 1 and {len(items)}.[/yellow]")


async def _assign_async(
    repo: str,
    pr_number: int | None,
    yes: bool,
    config_path: Path | None,
    count: int = 1,
) -> None:
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        _abort(str(e))
        return

    async with GitHubClient(cfg.github_token, cfg.github_base_url) as client:
        # ── PR selection (interactive if pr_number not given) ──────────── #
        if pr_number is None:
            try:
                open_prs = await client.list_open_prs(repo)
            except GitHubError as e:
                _abort(f"Error de GitHub API: {e}")
                return

            if not open_prs:
                _abort(f"No open pull requests found in '{repo}'.")
                return

            console.print(f"\n[bold magenta]Open pull requests in [cyan]{repo}[/cyan]:[/bold magenta]")
            for i, pr in enumerate(open_prs, start=1):
                reviewers = pr.get("reviewers", [])
                reviewer_badge = (
                    "  [green]reviewers: " + ", ".join(reviewers) + "[/green]"
                    if reviewers
                    else ""
                )
                console.print(
                    f"  [cyan]{i:>3}.[/cyan] [bold]#{pr['number']}[/bold]  {pr['title']}"
                    f"  [dim](@{pr['author']})[/dim]{reviewer_badge}"
                )
            console.print()

            while True:
                raw = typer.prompt("Enter PR number (or 'q' to quit)", default="")
                if raw.strip().lower() in {"q", "quit", "exit", ""}:
                    raise typer.Exit(0)
                try:
                    idx = int(raw.strip())
                    match = next((p for p in open_prs if p["number"] == idx), None)
                    if match:
                        pr_number = idx
                        break
                    # also allow positional index
                    if 1 <= idx <= len(open_prs):
                        pr_number = open_prs[idx - 1]["number"]
                        break
                except ValueError:
                    pass
                console.print(
                    "[yellow]Enter a valid PR number from the list above.[/yellow]"
                )

        # Match repo to group by checking the repos list
        group = cfg.get_group_for_repo(repo)
        if group is None:
            _abort(
                f"No group configured for repo '{repo}'. "
                f"Check the groups in config.yml."
            )
            return

        if not group.reviewers:
            _abort(f"Group '{group.name}' has no reviewers configured.")
            return

        # ── Compute workload once, then loop with skip support ────────── #
        try:
            all_repos = [r for g in cfg.groups.values() for r in g.repos]
            pr_info = await client.get_pr_info(repo, pr_number)
            already_assigned = {r["login"] for r in pr_info.get("requested_reviewers", [])}
            pr_author = pr_info.get("user", {}).get("login", "")
            candidates = await build_reviewer_loads(client, group.reviewers, all_repos)
        except GitHubError as e:
            _abort(f"GitHub API error: {e}")
            return

        # ── Idempotency guard: skip if already has enough reviewers ──── #
        if len(already_assigned) >= count:
            console.print(
                f"[dim]PR #{pr_number} already has {len(already_assigned)} reviewer(s) "
                f"assigned ({', '.join(sorted(already_assigned))}). "
                f"Nothing to do.[/dim]"
            )
            return

        skipped: set[str] = set()
        base_exclude = already_assigned | {pr_author}

        while True:
            try:
                selected_list = select_reviewers(
                    candidates, count=count, exclude=base_exclude | skipped
                )
            except NoReviewersAvailableError:
                _abort("No more eligible reviewers for this PR.")
                return

            # ── Show proposal ──────────────────────────────────────────── #
            _print_load_table(candidates, highlight=selected_list[0].username)
            names = ", ".join(
                f"[bold cyan]{r.username}[/bold cyan]" for r in selected_list
            )
            console.print(
                f"\n[bold]Proposed reviewer(s):[/bold] {names} "
                f"→ [white]{repo}[/white]#[white]{pr_number}[/white]"
            )

            if yes:
                break

            # ── Confirm: y / s / q ─────────────────────────────────────── #
            console.print(
                "\n  [green]y[/green] assign   "
                "[yellow]s[/yellow] skip last proposed   "
                "[red]q[/red] abort"
            )
            while True:
                raw = typer.prompt("Choice", default="y").strip().lower()
                if raw in {"y", "yes"}:
                    action = "assign"
                    break
                if raw in {"s", "skip"}:
                    action = "skip"
                    break
                if raw in {"q", "quit", "abort"}:
                    action = "abort"
                    break
                console.print("[yellow]Please enter y, s or q.[/yellow]")

            if action == "assign":
                break
            if action == "abort":
                console.print("[yellow]Aborted.[/yellow]")
                raise typer.Exit(0)
            # skip the last candidate in the proposed set; re-run selection
            last = selected_list[-1].username
            console.print(f"[dim]Skipping {last}…[/dim]")
            skipped.add(last)

        # ── Assign ────────────────────────────────────────────────────── #
        try:
            repo_full = repo if "/" in repo else f"{cfg.org}/{repo}"
            await client.request_reviewers(
                repo_full, pr_number, [r.username for r in selected_list]
            )
        except GitHubError as e:
            _abort(f"GitHub API error: {e}")
            return

        names_plain = ", ".join(
            f"[bold cyan]{r.username}[/bold cyan]" for r in selected_list
        )
        console.print(
            f"\n[green]ASSIGNED[/green] → {names_plain} "
            f"on [white]{repo}[/white]#[white]{pr_number}[/white]"
        )


# --------------------------------------------------------------------------- #
#  Command: load                                                                #
# --------------------------------------------------------------------------- #


@app.command()
def load(
    config: _CONFIG_OPTION = None,
    verbose: _VERBOSE_OPTION = False,
    show_prs: Annotated[
        bool,
        typer.Option("--show-prs", help="Print the individual PRs for each reviewer."),
    ] = False,
) -> None:
    """Show the global review workload for all configured reviewers.

    \b
    Examples:
      pr-assigner load
      pr-assigner load --show-prs
    """
    _setup_logging(verbose)
    asyncio.run(_load_async(config, show_prs=show_prs))


async def _load_async(config_path: Path | None, show_prs: bool = False) -> None:
    try:
        cfg = load_config(config_path)
    except ConfigError as e:
        _abort(str(e))
        return

    all_repos = [r for g in cfg.groups.values() for r in g.repos]

    # Collect unique reviewers across all groups
    all_reviewers = list(dict.fromkeys(
        username for g in cfg.groups.values() for username in g.reviewers
    ))

    if not all_reviewers:
        console.print("[yellow]No reviewers configured.[/yellow]")
        return

    since = date.today().replace(day=1).isoformat()
    console.print(f"[bold]Fetching global workload — org: [cyan]{cfg.org}[/cyan] — since: [cyan]{since}[/cyan][/bold]")

    async def _load_one(username: str) -> ReviewerLoad:
        score, prs = await client.calculate_monthly_review_score(username, all_repos)
        if show_prs:
            pr_details[username] = prs
        return ReviewerLoad(
            username=username,
            reviewed_this_month=len(prs),
            monthly_review_score=score,
        )

    pr_details: dict[str, list] = {}
    try:
        async with GitHubClient(cfg.github_token, cfg.github_base_url) as client:
            loads = list(await asyncio.gather(*[_load_one(u) for u in all_reviewers]))
    except GitHubError as e:
        _abort(f"GitHub API error: {e}")
        return

    _print_load_table(loads)

    if show_prs:
        for reviewer in sorted(loads, key=lambda r: r.sort_key, reverse=True):
            prs = pr_details.get(reviewer.username, [])
            console.print(f"\n[bold cyan]{reviewer.username}[/bold cyan] — {len(prs)} PRs reviewed:")
            for pr in prs:
                console.print(f"  [dim]{pr['repo']}[/dim]#{pr['number']}  {pr['title']}")
                console.print(f"  [link={pr['url']}]{pr['url']}[/link]")


# --------------------------------------------------------------------------- #
#  Rendering helper                                                             #
# --------------------------------------------------------------------------- #


def _print_load_table(
    candidates: list[ReviewerLoad],
    highlight: str | None = None,
) -> None:
    table = Table(title="Reviewer workload", show_header=True, header_style="bold magenta")
    table.add_column("#", justify="right", style="dim", width=3)
    table.add_column("Reviewer", min_width=20)
    table.add_column("Reviewed (month)", justify="right")
    table.add_column("Monthly score", justify="right")

    sorted_candidates = sorted(candidates, key=lambda r: r.sort_key, reverse=True)

    for i, reviewer in enumerate(sorted_candidates, start=1):
        is_selected = reviewer.username == highlight
        name_cell = (
            f"[bold green]► {reviewer.username}[/bold green]"
            if is_selected
            else reviewer.username
        )
        table.add_row(str(i), name_cell, str(reviewer.reviewed_this_month), f"{reviewer.monthly_review_score:.2f}")

    console.print(table)


# --------------------------------------------------------------------------- #
#  Entry point                                                                  #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    app()
