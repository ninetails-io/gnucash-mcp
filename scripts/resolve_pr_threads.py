#!/usr/bin/env python3
"""Resolve PR review threads by author.

Primary use case: bot reviewers (Copilot) don't resolve their own
threads after the author addresses their findings. Manually
clicking "Resolve conversation" on each thread is friction the
procedure should erase. This script resolves all unresolved
threads from a given author on a given PR via the GitHub GraphQL
API, in one shot.

Usage::

    # Resolve all unresolved Copilot threads on PR #87
    uv run python scripts/resolve_pr_threads.py 87

    # Resolve all threads by a specific user
    uv run python scripts/resolve_pr_threads.py 87 --by alice

    # Resolve everything regardless of author
    uv run python scripts/resolve_pr_threads.py 87 --all

    # Preview what would be resolved without changing state
    uv run python scripts/resolve_pr_threads.py 87 --dry-run

Defaults to ``copilot-pull-request-reviewer[bot]`` because that's
the project's standing automated reviewer.

Requires the ``gh`` CLI authenticated with permissions to modify
the target repo's PRs.
"""

import argparse
import json
import subprocess
import sys

# Copilot's GitHub App login. GitHub's GraphQL ``User.login``
# field strips the ``[bot]`` suffix that the REST API exposes
# — both forms refer to the same identity, but the API surface
# you query determines which one to compare against. We query
# GraphQL, so the bare form is correct.
DEFAULT_AUTHOR = "copilot-pull-request-reviewer"

# 100 is GitHub's per-page max for reviewThreads; PRs with more
# than 100 threads would need pagination, which we'll add when
# we hit one (none in this project's history yet).
_FETCH_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          comments(first: 1) {
            nodes {
              author { login }
              path
              line
              originalLine
            }
          }
        }
      }
    }
  }
}
"""

_RESOLVE_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { isResolved }
  }
}
"""


def _run_gh(args: list[str]) -> dict:
    """Run a gh subcommand and return parsed JSON output.

    Fails loud on non-zero exit — the caller should not paper
    over auth errors or repo-not-found.
    """
    result = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def _resolve_owner_repo() -> tuple[str, str]:
    """Resolve the current directory's GitHub repo to (owner, name).

    Uses ``gh repo view --json`` so it works from any branch /
    sub-directory of a gh-tracked checkout.
    """
    info = _run_gh(["repo", "view", "--json", "owner,name"])
    return info["owner"]["login"], info["name"]


def _fetch_threads(owner: str, repo: str, pr: int) -> list[dict]:
    """Fetch all review threads on the given PR.

    Returns the list of thread nodes (each with id, isResolved,
    and the first comment for author/file/line context).
    """
    payload = _run_gh([
        "api", "graphql",
        "-f", f"query={_FETCH_THREADS_QUERY}",
        "-F", f"owner={owner}",
        "-F", f"repo={repo}",
        "-F", f"pr={pr}",
    ])
    return (
        payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    )


def _resolve_thread(thread_id: str) -> bool:
    """Resolve a single thread by its node id. Returns True on
    success (the API echoes back ``isResolved: true``)."""
    payload = _run_gh([
        "api", "graphql",
        "-f", f"query={_RESOLVE_MUTATION}",
        "-F", f"threadId={thread_id}",
    ])
    return (
        payload["data"]["resolveReviewThread"]["thread"]["isResolved"]
    )


def _format_thread(thread: dict) -> str:
    """Format a thread for display — file:line plus author."""
    comments = thread["comments"]["nodes"]
    if not comments:
        return f"  (thread {thread['id']} — no comments visible)"
    c = comments[0]
    author = c["author"]["login"] if c["author"] else "(unknown)"
    path = c.get("path") or "?"
    line = c.get("line") or c.get("originalLine") or "?"
    return f"  {path}:{line}  by {author}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Resolve PR review threads by author.",
    )
    parser.add_argument(
        "pr_number", type=int,
        help="PR number to operate on.",
    )
    parser.add_argument(
        "--by", default=DEFAULT_AUTHOR,
        help=(
            "Only resolve threads whose first comment is by this "
            f"author. Default: {DEFAULT_AUTHOR!r}."
        ),
    )
    parser.add_argument(
        "--all", action="store_true",
        help=(
            "Resolve every unresolved thread regardless of "
            "author. Overrides --by."
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be resolved without modifying state.",
    )
    args = parser.parse_args()

    owner, repo = _resolve_owner_repo()
    threads = _fetch_threads(owner, repo, args.pr_number)

    # Filter: unresolved first, then by author (unless --all).
    candidates = [t for t in threads if not t["isResolved"]]
    if not args.all:
        candidates = [
            t for t in candidates
            if t["comments"]["nodes"]
            and t["comments"]["nodes"][0]["author"]
            and t["comments"]["nodes"][0]["author"]["login"] == args.by
        ]

    if not candidates:
        print(
            f"No unresolved threads to resolve on "
            f"{owner}/{repo}#{args.pr_number}"
            + (f" (filter: by={args.by!r})" if not args.all else "")
        )
        return 0

    print(
        f"{'Would resolve' if args.dry_run else 'Resolving'} "
        f"{len(candidates)} thread(s) on {owner}/{repo}#{args.pr_number}:"
    )
    for thread in candidates:
        print(_format_thread(thread))

    if args.dry_run:
        return 0

    resolved_count = 0
    for thread in candidates:
        try:
            if _resolve_thread(thread["id"]):
                resolved_count += 1
        except subprocess.CalledProcessError as e:
            # Surface the failure but continue — one bad thread
            # shouldn't abort the rest.
            print(
                f"  ERROR resolving {thread['id']}: "
                f"{e.stderr.strip() or e}",
                file=sys.stderr,
            )

    print(f"Resolved {resolved_count}/{len(candidates)} threads.")
    return 0 if resolved_count == len(candidates) else 1


if __name__ == "__main__":
    sys.exit(main())
