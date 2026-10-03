"""Publish / Recover state machine."""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import gitutil, store

STATE_COMMITTED = store.STATE_COMMITTED
STATE_ABORTED = store.STATE_ABORTED


@dataclass(frozen=True)
class BatchItem:
    repo: str
    commit: str
    tag: str


@dataclass(frozen=True)
class BatchState:
    batch_id: str
    state: str


class ReleaseTagger:
    def __init__(self, db_path: str) -> None:
        self._store = store.Store(db_path)

    def close(self) -> None:
        self._store.close()

    def get_state(self, batch_id: str) -> str | None:
        return self._store.get_batch_state(batch_id)

    def publish(self, batch_id: str, items: list[BatchItem]) -> BatchState:
        if self._store.get_batch_state(batch_id) is None:
            self._create_plan(batch_id, items)
        # An existing batch_id always continues its original plan;
        # newly supplied items are ignored.
        return self.recover(batch_id)

    def recover(self, batch_id: str) -> BatchState:
        state = self._store.get_batch_state(batch_id)
        if state is None:
            raise KeyError(f"unknown batch_id: {batch_id}")
        if state in store.TERMINAL_STATES:
            # Terminal batches never touch git again.
            return BatchState(batch_id, state)
        if state == store.STATE_ABORTING:
            self._undo(batch_id)
            return BatchState(batch_id, store.STATE_ABORTED)
        return self._publish(batch_id)

    # -- planning ---------------------------------------------------------

    def _create_plan(self, batch_id: str, items: list[BatchItem]) -> None:
        plan = []
        conflict = False
        for item in items:
            ref = "refs/tags/" + item.tag
            existing = gitutil.resolve_ref(item.repo, ref)
            if existing is None:
                plan.append(
                    dict(
                        repo=item.repo,
                        commit=item.commit,
                        tag=item.tag,
                        kind=store.KIND_CREATE,
                        planned_oid=None,
                        tagger_date=None,
                    )
                )
            elif gitutil.peel_to_commit(item.repo, ref) == item.commit:
                # Borrow the pre-existing tag object as-is.
                plan.append(
                    dict(
                        repo=item.repo,
                        commit=item.commit,
                        tag=item.tag,
                        kind=store.KIND_BORROW,
                        planned_oid=existing,
                        tagger_date=None,
                    )
                )
            else:
                conflict = True
                plan.append(
                    dict(
                        repo=item.repo,
                        commit=item.commit,
                        tag=item.tag,
                        kind=store.KIND_CREATE,
                        planned_oid=None,
                        tagger_date=None,
                    )
                )
        self._store.create_batch(batch_id, plan)
        if conflict:
            # Nothing was created yet, so the batch can go straight
            # to the terminal ABORTED state.
            self._store.set_state(batch_id, store.STATE_ABORTED)

    # -- publishing -------------------------------------------------------

    def _publish(self, batch_id: str) -> BatchState:
        items = self._store.get_items(batch_id)
        for item in items:
            if not self._fulfill(item):
                self._store.set_state(batch_id, store.STATE_ABORTING)
                self._undo(batch_id)
                return BatchState(batch_id, store.STATE_ABORTED)
        self._store.set_state(batch_id, store.STATE_COMMITTED)
        return BatchState(batch_id, store.STATE_COMMITTED)

    def _fulfill(self, item) -> bool:
        ref = "refs/tags/" + item["tag"]
        repo = item["repo"]
        if item["kind"] == store.KIND_BORROW:
            return gitutil.resolve_ref(repo, ref) == item["planned_oid"]
        # kind == create
        planned = item["planned_oid"]
        if planned is None:
            tagger_date = f"{int(time.time())} +0000"
            oid = gitutil.create_annotated_tag(
                repo,
                item["tag"],
                item["commit_oid"],
                f"release batch {item['batch_id']}",
                tagger_date,
            )
            self._store.set_planned_oid(
                item["batch_id"], repo, oid, tagger_date
            )
            return True
        current = gitutil.resolve_ref(repo, ref)
        if current == planned:
            return True
        if current is None:
            # Recreate deterministically: identical content and tagger
            # date reproduce the exact same tag object.
            oid = gitutil.create_annotated_tag(
                repo,
                item["tag"],
                item["commit_oid"],
                f"release batch {item['batch_id']}",
                item["tagger_date"],
            )
            return oid == planned
        return False

    # -- undo -------------------------------------------------------------

    def _undo(self, batch_id: str) -> None:
        for item in self._store.get_items(batch_id):
            if item["kind"] != store.KIND_CREATE:
                continue  # borrowed tags are never touched
            planned = item["planned_oid"]
            if planned is None:
                continue
            ref = "refs/tags/" + item["tag"]
            if gitutil.resolve_ref(item["repo"], ref) == planned:
                gitutil.delete_tag(item["repo"], item["tag"])
        self._store.set_state(batch_id, store.STATE_ABORTED)
