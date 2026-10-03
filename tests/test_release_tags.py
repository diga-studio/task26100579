"""End-to-end tests against real git repositories in temp directories."""

import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from release_tags import BatchItem, ReleaseTagger, gitutil


def git(repo, *args, env=None):
    full_env = dict(os.environ)
    if env:
        full_env.update(env)
    subprocess.run(
        ["git", "-C", repo, *args], check=True, env=full_env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )


def make_repo(root, name, commits=1):
    repo = os.path.join(root, name)
    os.makedirs(repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    oids = []
    for i in range(commits):
        with open(os.path.join(repo, f"file{i}"), "w") as fh:
            fh.write(f"{name}-{i}")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", f"commit {i}")
        oids.append(gitutil.resolve_ref(repo, "HEAD"))
    return repo, oids


def tag_oid(repo, tag):
    return gitutil.resolve_ref(repo, "refs/tags/" + tag)


class ReleaseTaggerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "state.db")
        self.tagger = ReleaseTagger(self.db)
        self.addCleanup(self.tagger.close)

    def test_repeated_recovery_succeeds_with_stable_tag_identity(self):
        repo_a, (commit_a,) = make_repo(self.tmp.name, "a")
        repo_b, (commit_b,) = make_repo(self.tmp.name, "b")
        items = [
            BatchItem(repo_a, commit_a, "v1.0"),
            BatchItem(repo_b, commit_b, "v2.0"),
        ]

        # Crash while creating the second tag: batch stays PUBLISHING.
        real_create = gitutil.create_annotated_tag
        calls = []

        def flaky_create(repo, tag, commit, message, committer_date):
            calls.append(tag)
            if len(calls) == 2:
                raise gitutil.GitError("simulated crash")
            return real_create(repo, tag, commit, message, committer_date)

        with mock.patch.object(
            gitutil, "create_annotated_tag", side_effect=flaky_create
        ):
            with self.assertRaises(gitutil.GitError):
                self.tagger.publish("batch-1", items)
        self.assertEqual(self.tagger.get_state("batch-1"), "PUBLISHING")

        first_oid = tag_oid(repo_a, "v1.0")
        self.assertIsNotNone(first_oid)
        self.assertIsNone(tag_oid(repo_b, "v2.0"))

        # An external tool deletes the already-created tag before recovery.
        git(repo_a, "tag", "-d", "v1.0")
        self.assertIsNone(tag_oid(repo_a, "v1.0"))

        state = self.tagger.recover("batch-1")
        self.assertEqual(state.state, "COMMITTED")
        # Recreated tag is the *same* object (deterministic content/date).
        self.assertEqual(tag_oid(repo_a, "v1.0"), first_oid)
        second_oid = tag_oid(repo_b, "v2.0")
        self.assertIsNotNone(second_oid)
        self.assertEqual(gitutil.peel_to_commit(repo_a, "refs/tags/v1.0"), commit_a)
        self.assertEqual(gitutil.peel_to_commit(repo_b, "refs/tags/v2.0"), commit_b)
        # Annotated tags carry the batch id.
        body = subprocess.run(
            ["git", "-C", repo_a, "for-each-ref", "refs/tags/v1.0",
             "--format=%(contents)"],
            stdout=subprocess.PIPE, text=True, check=True,
        ).stdout
        self.assertIn("batch-1", body)

        # Repeated recovery of a terminal batch is a no-op: no git writes.
        git(repo_a, "tag", "-d", "v1.0")
        for _ in range(3):
            state = self.tagger.recover("batch-1")
            self.assertEqual(state.state, "COMMITTED")
            self.assertIsNone(tag_oid(repo_a, "v1.0"))
        self.assertEqual(tag_oid(repo_b, "v2.0"), second_oid)

    def test_mixed_borrow_and_create_abort_keeps_external_objects(self):
        repo_a, (commit_a,) = make_repo(self.tmp.name, "a")
        repo_b, (commit_b,) = make_repo(self.tmp.name, "b")
        repo_c, (commit_c,) = make_repo(self.tmp.name, "c")
        repo_d, (commit_d,) = make_repo(self.tmp.name, "d")

        # Pre-existing annotated tag on repo A, to be borrowed.
        git(repo_a, "tag", "-a", "-m", "external", "release",
            commit_a, env={"GIT_COMMITTER_DATE": "1600000000 +0000"})
        borrowed_oid = tag_oid(repo_a, "release")

        items = [
            BatchItem(repo_a, commit_a, "release"),   # borrow
            BatchItem(repo_b, commit_b, "release"),   # create
            BatchItem(repo_c, commit_c, "release"),   # create
            BatchItem(repo_d, commit_d, "release"),   # create (will crash)
        ]

        real_create = gitutil.create_annotated_tag

        def crashing_create(repo, tag, commit, message, committer_date):
            if repo == repo_d:
                raise gitutil.GitError("simulated crash")
            return real_create(repo, tag, commit, message, committer_date)

        with mock.patch.object(
            gitutil, "create_annotated_tag", side_effect=crashing_create
        ):
            with self.assertRaises(gitutil.GitError):
                self.tagger.publish("batch-2", items)
        self.assertEqual(self.tagger.get_state("batch-2"), "PUBLISHING")

        created_b = tag_oid(repo_b, "release")
        created_c = tag_oid(repo_c, "release")
        self.assertIsNotNone(created_b)
        self.assertIsNotNone(created_c)

        # External tool replaces the repo B tag with a *different* tag
        # object that still peels to the same commit.
        git(repo_b, "tag", "-a", "-f", "-m", "external replacement",
            "release", commit_b,
            env={"GIT_COMMITTER_DATE": "1700000000 +0000"})
        replacement_oid = tag_oid(repo_b, "release")
        self.assertNotEqual(replacement_oid, created_b)
        self.assertEqual(
            gitutil.peel_to_commit(repo_b, "refs/tags/release"), commit_b
        )

        state = self.tagger.recover("batch-2")
        self.assertEqual(state.state, "ABORTED")

        # Borrowed tag untouched, external replacement kept, own tag removed.
        self.assertEqual(tag_oid(repo_a, "release"), borrowed_oid)
        self.assertEqual(tag_oid(repo_b, "release"), replacement_oid)
        self.assertIsNone(tag_oid(repo_c, "release"))
        self.assertIsNone(tag_oid(repo_d, "release"))

        # Once aborting, the batch can never be republished.
        git(repo_b, "tag", "-d", "release")  # conflict disappears
        state = self.tagger.recover("batch-2")
        self.assertEqual(state.state, "ABORTED")
        self.assertIsNone(tag_oid(repo_b, "release"))
        self.assertEqual(tag_oid(repo_a, "release"), borrowed_oid)

    def test_conflicting_existing_tag_aborts_immediately(self):
        repo_a, (commit_a,) = make_repo(self.tmp.name, "a")
        repo_b, (commit_b,) = make_repo(self.tmp.name, "b")
        foreign = subprocess.run(
            ["git", "-C", repo_a, "rev-parse", "HEAD"],
            stdout=subprocess.PIPE, text=True, check=True,
        ).stdout.strip()
        # Lightweight tag on a commit the plan does not target.
        git(repo_a, "tag", "taken", foreign)
        other_commit = commit_b

        state = self.tagger.publish(
            "batch-3",
            [BatchItem(repo_a, "0" * 40, "taken"),
             BatchItem(repo_b, other_commit, "fresh")],
        )
        self.assertEqual(state.state, "ABORTED")
        # Foreign tag untouched, nothing created anywhere.
        self.assertEqual(tag_oid(repo_a, "taken"), foreign)
        self.assertIsNone(tag_oid(repo_b, "fresh"))

    def test_existing_batch_id_continues_original_plan(self):
        repo_a, (commit_a,) = make_repo(self.tmp.name, "a")
        repo_b, (commit_b,) = make_repo(self.tmp.name, "b")
        state = self.tagger.publish(
            "batch-4", [BatchItem(repo_a, commit_a, "v1")]
        )
        self.assertEqual(state.state, "COMMITTED")
        # Re-publishing with different items must not touch the new repo.
        state = self.tagger.publish(
            "batch-4", [BatchItem(repo_b, commit_b, "v9")]
        )
        self.assertEqual(state.state, "COMMITTED")
        self.assertIsNone(tag_oid(repo_b, "v9"))
        self.assertIsNotNone(tag_oid(repo_a, "v1"))


if __name__ == "__main__":
    unittest.main()
