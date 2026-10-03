"""Thin wrappers around the local ``git`` command line tool."""

from __future__ import annotations

import subprocess


class GitError(RuntimeError):
    """Raised when a git command fails unexpectedly."""


def _run(repo: str, *args: str, extra_env: dict[str, str] | None = None) -> str:
    import os

    full_env = dict(os.environ)
    if extra_env:
        full_env.update(extra_env)
    proc = subprocess.run(
        ["git", "-C", repo, *args],
        env=full_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(
            f"git -C {repo} {' '.join(args)} failed: {proc.stderr.strip()}"
        )
    return proc.stdout.strip()


def resolve_ref(repo: str, ref: str) -> str | None:
    """Return the object OID the ref points to, or None if missing."""
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "--verify", "--quiet", ref],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def peel_to_commit(repo: str, ref: str) -> str | None:
    """Recursively dereference ref to a commit OID, or None if missing."""
    return resolve_ref(repo, ref + "^{}")


def create_annotated_tag(
    repo: str, tag: str, commit: str, message: str, committer_date: str
) -> str:
    """Create an annotated tag and return the new tag object OID."""
    _run(
        repo,
        "tag",
        "-a",
        "-m",
        message,
        tag,
        commit,
        extra_env={"GIT_COMMITTER_DATE": committer_date},
    )
    oid = resolve_ref(repo, "refs/tags/" + tag)
    if oid is None:
        raise GitError(f"tag {tag} not found right after creation")
    return oid


def delete_tag(repo: str, tag: str) -> None:
    _run(repo, "tag", "-d", tag)
