from sqlalchemy.orm import Mapped,mapped_column
from sqlalchemy import String,Integer,DateTime,ForeignKey,Text,UniqueConstraint
from datetime import datetime,timezone
from src.config.database import Base

class SnapshotUpload(Base):
 __tablename__='snapshot_uploads'
 id:Mapped[int]=mapped_column(primary_key=True)
 snapshot_id:Mapped[int]=mapped_column(ForeignKey('corpus_snapshots.id'))
 creator_id:Mapped[int]=mapped_column(ForeignKey('users.id'))
 status:Mapped[str]=mapped_column(String(20),default='open')
 total_batches:Mapped[int]=mapped_column(Integer,default=0)
 received_batches:Mapped[int]=mapped_column(Integer,default=0)
 row_count:Mapped[int]=mapped_column(Integer,default=0)
 error:Mapped[str|None]=mapped_column(String(1000),nullable=True)
 created_at:Mapped[datetime]=mapped_column(DateTime,default=lambda:datetime.now(timezone.utc))
 updated_at:Mapped[datetime]=mapped_column(DateTime,default=lambda:datetime.now(timezone.utc),onupdate=lambda:datetime.now(timezone.utc))

class StagedUtterance(Base):
 # (upload_id,reference) 唯一约束同时承担去重索引；重复编号在此被数据库硬拒绝
 __tablename__='staged_utterances'
 __table_args__=(UniqueConstraint('upload_id','reference',name='uq_staged_upload_reference'),)
 id:Mapped[int]=mapped_column(primary_key=True)
 upload_id:Mapped[int]=mapped_column(ForeignKey('snapshot_uploads.id'))
 batch_no:Mapped[int]=mapped_column(Integer)
 reference:Mapped[str]=mapped_column(String(80))
 features:Mapped[str]=mapped_column(Text)
