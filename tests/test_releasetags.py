import sqlite3
import subprocess

import pytest

from releasetags import Publisher, PublishItem
from releasetags import gitutil


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def make_repo(path):
    path.mkdir(parents=True)
    git(path, "init", "-q")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return git(path, "rev-parse", "HEAD")


def make_external_tag(repo, name, commit, message="external"):
    git(repo, "tag", "-a", "-f", name, "-m", message, commit)
    return git(repo, "rev-parse", f"refs/tags/{name}")


def force_state(db_path, batch_id, state):
    """Simulate a crash: rewind a batch to a non-terminal state."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "UPDATE batches SET state = ? WHERE batch_id = ?", (state, batch_id)
        )


@pytest.fixture
def publisher(tmp_path):
    pub = Publisher(str(tmp_path / "state.db"))
    yield pub, tmp_path / "state.db"
    pub.close()


def test_recover_recreates_missing_ref_with_identical_object(publisher):
    pub, db_path = publisher
    tmp_path = db_path.parent
    repo_a = make_repo(tmp_path / "a")
    repo_b = make_repo(tmp_path / "b")
    items = [
        PublishItem(str(tmp_path / "a"), repo_a, "v1.0"),
        PublishItem(str(tmp_path / "b"), repo_b, "v2.0"),
    ]

    assert pub.publish("batch-1", items) == "COMMITTED"
    oid_a = gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/v1.0")
    oid_b = gitutil.resolve_ref(str(tmp_path / "b"), "refs/tags/v2.0")
    assert oid_a and oid_b and oid_a != oid_b

    # Crash after commit, then an external tool deletes one ref.
    force_state(db_path, "batch-1", "IN_PROGRESS")
    git(tmp_path / "a", "tag", "-d", "v1.0")

    # Recovery recreates the missing ref with the exact same tag object.
    assert pub.recover("batch-1") == "COMMITTED"
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/v1.0") == oid_a
    assert gitutil.resolve_ref(str(tmp_path / "b"), "refs/tags/v2.0") == oid_b

    # Repeated recovery is a no-op; object identity stays stable.
    assert pub.recover("batch-1") == "COMMITTED"
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/v1.0") == oid_a

    # Re-publishing the same batch_id continues the original plan.
    assert pub.publish("batch-1", items) == "COMMITTED"
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/v1.0") == oid_a


def test_abort_preserves_borrowed_and_external_objects(publisher):
    pub, db_path = publisher
    tmp_path = db_path.parent
    repo_a = make_repo(tmp_path / "a")
    repo_b = make_repo(tmp_path / "b")
    repo_c = make_repo(tmp_path / "c")

    borrowed_oid = make_external_tag(tmp_path / "a", "borrowed", repo_a)
    items = [
        PublishItem(str(tmp_path / "a"), repo_a, "borrowed"),
        PublishItem(str(tmp_path / "b"), repo_b, "own1"),
        PublishItem(str(tmp_path / "c"), repo_c, "own2"),
    ]
    assert pub.publish("batch-2", items) == "COMMITTED"
    own1_oid = gitutil.resolve_ref(str(tmp_path / "b"), "refs/tags/own1")
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/borrowed") == borrowed_oid

    # Crash, then an external tool replaces own1 with a different tag object
    # that still points at the same commit.
    force_state(db_path, "batch-2", "IN_PROGRESS")
    replacement_oid = make_external_tag(tmp_path / "b", "own1", repo_b, "replacement")
    assert replacement_oid != own1_oid
    assert gitutil.peel_to_commit(str(tmp_path / "b"), "refs/tags/own1") == repo_b

    # Recovery must abort: own2 removed, borrowed and replacement preserved.
    assert pub.recover("batch-2") == "ABORTED"
    assert gitutil.resolve_ref(str(tmp_path / "c"), "refs/tags/own2") is None
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/borrowed") == borrowed_oid
    assert gitutil.resolve_ref(str(tmp_path / "b"), "refs/tags/own1") == replacement_oid

    # Terminal state: further recover/publish perform no git writes.
    assert pub.recover("batch-2") == "ABORTED"
    assert pub.publish("batch-2", items) == "ABORTED"
    assert gitutil.resolve_ref(str(tmp_path / "c"), "refs/tags/own2") is None
    assert gitutil.resolve_ref(str(tmp_path / "b"), "refs/tags/own1") == replacement_oid


def test_conflicting_existing_tag_aborts_without_touching_it(publisher):
    pub, db_path = publisher
    tmp_path = db_path.parent
    repo_a = make_repo(tmp_path / "a")
    git(tmp_path / "a", "commit", "-q", "--allow-empty", "-m", "other")
    other = git(tmp_path / "a", "rev-parse", "HEAD")
    foreign_oid = make_external_tag(tmp_path / "a", "taken", other)

    state = pub.publish("batch-3", [PublishItem(str(tmp_path / "a"), repo_a, "taken")])
    assert state == "ABORTED"
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/taken") == foreign_oid
    assert pub.recover("batch-3") == "ABORTED"
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/taken") == foreign_oid


def test_borrowed_tag_removed_externally_aborts(publisher):
    pub, db_path = publisher
    tmp_path = db_path.parent
    repo_a = make_repo(tmp_path / "a")
    borrowed_oid = make_external_tag(tmp_path / "a", "borrowed", repo_a)

    items = [PublishItem(str(tmp_path / "a"), repo_a, "borrowed")]
    assert pub.publish("batch-4", items) == "COMMITTED"

    # Crash, then the borrowed tag disappears before recovery.
    force_state(db_path, "batch-4", "IN_PROGRESS")
    git(tmp_path / "a", "tag", "-d", "borrowed")
    assert pub.recover("batch-4") == "ABORTED"
    assert gitutil.resolve_ref(str(tmp_path / "a"), "refs/tags/borrowed") is None

    # The borrowed object itself is never touched by the abort.
    assert git(tmp_path / "a", "cat-file", "-t", borrowed_oid) == "tag"


def test_recover_unknown_batch_raises(publisher):
    pub, _ = publisher
    with pytest.raises(KeyError):
        pub.recover("no-such-batch")
