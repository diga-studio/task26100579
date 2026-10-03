"""Recoverable cross-repository release tag publisher."""

from __future__ import annotations

from dataclasses import dataclass

from . import gitutil
from . import store as _store


@dataclass(frozen=True)
class PublishItem:
    """One tag to publish: absolute repo path, full commit OID, tag name."""

    repo_path: str
    commit_oid: str
    tag_name: str


class Publisher:
    """Publishes and recovers tagged release batches."""

    def __init__(self, db_path: str) -> None:
        self._store = _store.Store(db_path)

    def close(self) -> None:
        self._store.close()

    def publish(self, batch_id: str, items: list[PublishItem]) -> str:
        """Publish a batch; returns the resulting batch state.

        An existing batch_id always continues its original plan; the items
        argument is only used when the batch is first created.
        """
        state = self._store.get_state(batch_id)
        if state is not None:
            return self._resume(batch_id, state)

        plan: list[_store.Item] = []
        conflict = False
        for item in items:
            ref = gitutil.tag_ref(item.tag_name)
            existing = gitutil.resolve_ref(item.repo_path, ref)
            if existing is None:
                planned = gitutil.create_tag_object(
                    item.repo_path, item.commit_oid, item.tag_name, batch_id
                )
                plan.append(_store.Item(
                    item.repo_path, item.commit_oid, item.tag_name,
                    _store.KIND_CREATE, planned,
                ))
            elif gitutil.peel_to_commit(item.repo_path, ref) == item.commit_oid:
                plan.append(_store.Item(
                    item.repo_path, item.commit_oid, item.tag_name,
                    _store.KIND_BORROW, existing,
                ))
            else:
                conflict = True
                plan.append(_store.Item(
                    item.repo_path, item.commit_oid, item.tag_name,
                    _store.KIND_BORROW, existing,
                ))

        self._store.create_batch(batch_id, plan)
        if conflict:
            self._store.set_state(batch_id, _store.ABORTING)
            return self._undo(batch_id)
        return self._resume(batch_id, _store.IN_PROGRESS)

    def recover(self, batch_id: str) -> str:
        """Continue an unfinished batch until COMMITTED or ABORTED."""
        state = self._store.get_state(batch_id)
        if state is None:
            raise KeyError(f"unknown batch_id: {batch_id}")
        return self._resume(batch_id, state)

    def _resume(self, batch_id: str, state: str) -> str:
        if state in (_store.COMMITTED, _store.ABORTED):
            return state
        if state == _store.ABORTING:
            return self._undo(batch_id)
        return self._apply(batch_id)

    def _apply(self, batch_id: str) -> str:
        for item in self._store.get_items(batch_id):
            ref = gitutil.tag_ref(item.tag_name)
            current = gitutil.resolve_ref(item.repo_path, ref)
            if item.kind == _store.KIND_CREATE:
                if current is None:
                    gitutil.create_tag_object(
                        item.repo_path, item.commit_oid, item.tag_name, batch_id
                    )
                    gitutil.update_ref(item.repo_path, ref, item.planned_object)
                elif current != item.planned_object:
                    self._store.set_state(batch_id, _store.ABORTING)
                    return self._undo(batch_id)
            else:
                if current != item.planned_object:
                    self._store.set_state(batch_id, _store.ABORTING)
                    return self._undo(batch_id)
        self._store.set_state(batch_id, _store.COMMITTED)
        return _store.COMMITTED

    def _undo(self, batch_id: str) -> str:
        for item in self._store.get_items(batch_id):
            if item.kind != _store.KIND_CREATE:
                continue
            ref = gitutil.tag_ref(item.tag_name)
            current = gitutil.resolve_ref(item.repo_path, ref)
            if current == item.planned_object:
                gitutil.delete_ref(item.repo_path, ref)
        self._store.set_state(batch_id, _store.ABORTED)
        return _store.ABORTED
