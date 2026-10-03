"""Thin wrappers around the local ``git`` command-line tool."""

from __future__ import annotations

import hashlib
import subprocess


class GitError(RuntimeError):
    pass


def _run(repo: str, args: list[str], input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", repo, *args],
        input=input_text,
        capture_output=True,
        text=True,
    )


def tag_ref(tag_name: str) -> str:
    return f"refs/tags/{tag_name}"


def resolve_ref(repo: str, ref: str) -> str | None:
    """Return the object OID the ref points to, or None if missing."""
    proc = _run(repo, ["rev-parse", "--verify", "--quiet", ref])
    oid = proc.stdout.strip()
    if proc.returncode != 0 or not oid:
        return None
    return oid


def peel_to_commit(repo: str, rev: str) -> str | None:
    """Recursively dereference rev to a commit OID, or None on failure."""
    proc = _run(repo, ["rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"])
    oid = proc.stdout.strip()
    if proc.returncode != 0 or not oid:
        return None
    return oid


def create_tag_object(repo: str, commit: str, tag_name: str, batch_id: str) -> str:
    """Create a deterministic annotated tag object and return its OID.

    The tagger timestamp is derived from batch_id so that retrying the same
    batch always reproduces the exact same tag object.
    """
    digest = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()
    timestamp = 1_700_000_000 + (int(digest[:8], 16) % 10_000_000)
    content = (
        f"object {commit}\n"
        f"type commit\n"
        f"tag {tag_name}\n"
        f"tagger Release Bot <release-bot@localhost> {timestamp} +0000\n"
        f"\n"
        f"batch: {batch_id}\n"
    )
    proc = _run(repo, ["mktag"], input_text=content)
    if proc.returncode != 0:
        raise GitError(f"git mktag failed in {repo}: {proc.stderr.strip()}")
    return proc.stdout.strip()


def update_ref(repo: str, ref: str, oid: str) -> None:
    """Point ref at oid, creating it if needed."""
    proc = _run(repo, ["update-ref", ref, oid])
    if proc.returncode != 0:
        raise GitError(f"git update-ref failed in {repo}: {proc.stderr.strip()}")


def delete_ref(repo: str, ref: str) -> None:
    """Delete ref if it exists."""
    proc = _run(repo, ["update-ref", "-d", ref])
    if proc.returncode != 0:
        raise GitError(f"git update-ref -d failed in {repo}: {proc.stderr.strip()}")
