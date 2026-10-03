# task26100579

可恢复的跨仓库发布标签组件（Python3 + SQLite + 本机 Git）。

## 库用法

```python
from releasetags import Publisher, PublishItem

pub = Publisher("state.db")
state = pub.publish("batch-1", [
    PublishItem("/abs/path/repo-a", "<full-commit-oid>", "v1.0"),
    PublishItem("/abs/path/repo-b", "<full-commit-oid>", "v2.0"),
])
# 崩溃或外部干扰后：
state = pub.recover("batch-1")   # 直到 COMMITTED 或 ABORTED
```

- 新建标签为带 batch_id 的 annotated tag，tagger 时间戳由 batch_id 派生，
  同一批次重试产生完全相同的标签对象。
- 已存在的同名标签只有递归解引用到目标 commit 才被借用，且保留原对象。
- 批次状态机：IN_PROGRESS → COMMITTED / ABORTING → ABORTED，
  终态后不再执行任何 Git 写操作；持久化在 SQLite（`state.db`）。

## 运行测试

```sh
python3 -m pytest
```
