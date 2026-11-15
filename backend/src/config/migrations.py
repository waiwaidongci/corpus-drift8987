"""幂等启动迁移：metadata.create_all 负责新表，这里为既有库补列。

仅做追加式、可重复执行的轻量变更；生产升级仍应规划正式迁移。
新表（snapshot_uploads/staged_utterances）及其约束由 create_all 在首次启动建立。
"""
from sqlalchemy import inspect
from src.config.database import engine


def _column_exists(inspector,table,column):
 return any(c['name']==column for c in inspector.get_columns(table))


def run_migrations():
 inspector=inspect(engine)
 tables=inspector.get_table_names()
 if 'corpus_snapshots' not in tables:
  return
 with engine.begin() as conn:
  from sqlalchemy import text
  if not _column_exists(inspector,'corpus_snapshots','status'):
   conn.execute(text("ALTER TABLE corpus_snapshots ADD COLUMN status VARCHAR(20) DEFAULT 'ready'"))
  if not _column_exists(inspector,'corpus_snapshots','creator_id'):
   conn.execute(text("ALTER TABLE corpus_snapshots ADD COLUMN creator_id INT NULL"))
