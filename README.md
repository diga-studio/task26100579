# Resumable cross-repo release tagger

A Python 3 library that publishes annotated release tags across multiple
local git repositories as a resumable batch, persisted in SQLite.

## Usage

```python
from release_tags import BatchItem, ReleaseTagger

tagger = ReleaseTagger("state.db")
tagger.publish("batch-1", [
    BatchItem(repo="/abs/path/repo-a", commit="<full oid>", tag="v1.0"),
    BatchItem(repo="/abs/path/repo-b", commit="<full oid>", tag="v2.0"),
])
tagger.recover("batch-1")  # resume until COMMITTED or ABORTED
```

- New tags are annotated tags whose message carries the batch id; retries
  reproduce the exact same tag object (fixed tagger date per batch item).
- A pre-existing tag is borrowed only if it recursively peels to the target
  commit; its original object is never modified or deleted.
- Any mismatch during recovery moves the batch to ABORTED: tags created by
  the batch (and still pointing at the planned object) are removed, while
  borrowed tags and externally written objects are left alone.
- Batches in a terminal state (COMMITTED / ABORTED) never touch git again.

## Tests

```sh
./run_tests.sh
```

Tests use real git repositories created in temporary directories.
