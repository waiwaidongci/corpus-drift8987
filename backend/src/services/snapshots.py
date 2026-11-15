import json
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from fastapi import HTTPException
from src.models import CorpusSnapshot,Utterance,SnapshotUpload,StagedUtterance
from src.constants.enums import UploadStatus,SnapshotStatus
from src.utils.serialize import row
from .audit import record

def list_snapshots(db):
 return [dict(row(s),count=db.scalar(select(func.count()).select_from(Utterance).where(Utterance.snapshot_id==s.id))) for s in db.scalars(select(CorpusSnapshot).where(CorpusSnapshot.status==SnapshotStatus.READY).order_by(CorpusSnapshot.id))]

def get_utterances(db,id,limit=None):
 s=db.get(CorpusSnapshot,id)
 if not s or s.status!=SnapshotStatus.READY:raise HTTPException(404,'快照不存在')
 q=select(Utterance).where(Utterance.snapshot_id==id).order_by(Utterance.reference)
 if limit:q=q.limit(limit)
 return [row(x) for x in db.scalars(q)]

def create_snapshot(db,data,user):
 if db.scalar(select(CorpusSnapshot).where(CorpusSnapshot.name==data.name)):raise HTTPException(409,'快照名称已存在')
 refs=[x.reference for x in data.utterances]
 if len(refs)!=len(set(refs)):raise HTTPException(422,'reference重复')
 obj=CorpusSnapshot(**data.model_dump(exclude={'utterances'}),status=SnapshotStatus.READY,creator_id=user.id)
 db.add(obj);db.flush();db.add_all([Utterance(snapshot_id=obj.id,**x.model_dump()) for x in data.utterances]);db.commit()
 record(db,user.email,'snapshot.import',{'snapshot_id':obj.id,'rows':len(refs)});return row(obj)

def list_uploads(db,user):
 # 进度对所有登录用户可见（与校对留痕一致）；写操作仍按创建者隔离
 q=select(SnapshotUpload).order_by(SnapshotUpload.id.desc())
 out=[]
 for u in db.scalars(q):
  snap=db.get(CorpusSnapshot,u.snapshot_id)
  out.append({'id':u.id,'snapshot_id':u.snapshot_id,'name':snap.name if snap else '','kind':snap.kind if snap else '','filename':snap.filename if snap else '','status':u.status,'total_batches':u.total_batches,'received_batches':u.received_batches,'row_count':u.row_count,'error':u.error,'created_at':u.created_at.isoformat(),'updated_at':u.updated_at.isoformat()})
 return out

def _get_locked_upload(db,upload_id):
 obj=db.get(SnapshotUpload,upload_id,with_for_update=True)
 if not obj:raise HTTPException(404,'上传会话不存在')
 return obj

def init_upload(db,data,user):
 if db.scalar(select(CorpusSnapshot).where(CorpusSnapshot.name==data.name)):raise HTTPException(409,'快照名称已存在')
 snap=CorpusSnapshot(name=data.name,kind=data.kind,filename=data.filename,description=data.description,status=SnapshotStatus.IMPORTING,creator_id=user.id)
 db.add(snap);db.flush()
 up=SnapshotUpload(snapshot_id=snap.id,creator_id=user.id,status=UploadStatus.OPEN,total_batches=data.total_batches)
 db.add(up);db.flush()
 record(db,user.email,'snapshot.upload.init',{'upload_id':up.id,'snapshot_id':snap.id,'total_batches':data.total_batches})
 return _upload_view(db,up)

def _upload_view(db,up):
 snap=db.get(CorpusSnapshot,up.snapshot_id)
 return {'id':up.id,'snapshot_id':up.snapshot_id,'name':snap.name,'kind':snap.kind,'filename':snap.filename,'status':up.status,'total_batches':up.total_batches,'received_batches':up.received_batches,'row_count':up.row_count,'error':up.error,'created_at':up.created_at.isoformat(),'updated_at':up.updated_at.isoformat()}

def add_batch(db,upload_id,data,user):
 up=_get_locked_upload(db,upload_id)
 if up.creator_id!=user.id:raise HTTPException(403,'只能向自己创建的上传会话追加批次')
 if up.status!=UploadStatus.OPEN:raise HTTPException(409,f'上传会话已{up.status}，不可再追加批次')
 if data.batch_no>up.total_batches:raise HTTPException(422,'批次号超出初始化声明的总批次数')
 if db.scalar(select(StagedUtterance.id).where(StagedUtterance.upload_id==upload_id,StagedUtterance.batch_no==data.batch_no).limit(1)):
  raise HTTPException(409,f'批次{data.batch_no}已上传，不可重复提交')
 refs=[x.reference for x in data.utterances]
 if len(refs)!=len(set(refs)):
  return _reject(db,up,'批次内reference重复，整份快照已拒绝',user)
 staged=[StagedUtterance(upload_id=upload_id,batch_no=data.batch_no,reference=x.reference,features=json.dumps(x.features,ensure_ascii=False,allow_nan=False,separators=(',',':'))) for x in data.utterances]
 db.add_all(staged)
 try:
  db.flush()
 except IntegrityError:
  db.rollback()
  up=_get_locked_upload(db,upload_id)
  return _reject(db,up,'跨批次reference重复，整份快照已拒绝',user)
 up.received_batches+=1;up.row_count+=len(refs)
 db.commit();record(db,user.email,'snapshot.upload.batch',{'upload_id':upload_id,'batch_no':data.batch_no,'rows':len(refs),'received':up.received_batches})
 return _upload_view(db,up)

def _reject(db,up,reason,user):
 up.status=UploadStatus.REJECTED;up.error=reason
 snap=db.get(CorpusSnapshot,up.snapshot_id);snap.status=SnapshotStatus.REJECTED
 db.commit();record(db,user.email,'snapshot.upload.rejected',{'upload_id':up.id,'reason':reason})
 raise HTTPException(422,reason)

def publish_upload(db,upload_id,data,user):
 up=_get_locked_upload(db,upload_id)
 if up.creator_id!=user.id:raise HTTPException(403,'只能发布自己创建的上传会话')
 if up.status==UploadStatus.PUBLISHED:return _upload_view(db,up)
 if up.status==UploadStatus.REJECTED:raise HTTPException(409,'上传已被拒绝，无法发布')
 if data.total_batches!=up.total_batches:raise HTTPException(422,'声明总批次数与初始化不一致')
 if up.received_batches!=up.total_batches:raise HTTPException(409,f'批次未传完：已传{up.received_batches}/{up.total_batches}，快照不参与比较')
 total=db.scalar(select(func.count()).select_from(StagedUtterance).where(StagedUtterance.upload_id==upload_id))
 if total<20:raise HTTPException(422,'快照至少需要20条片段')
 if total!=up.row_count:raise HTTPException(422,'暂存计数不一致，拒绝发布')
 # 分批搬运，单次只持有一批行，控制进程内存与事务规模
 copy_from_staging(db,up.snapshot_id,upload_id)
 up.status=UploadStatus.PUBLISHED;up.error=None
 snap=db.get(CorpusSnapshot,up.snapshot_id);snap.status=SnapshotStatus.READY
 db.commit()
 record(db,user.email,'snapshot.upload.published',{'upload_id':upload_id,'snapshot_id':snap.id,'rows':total})
 return _upload_view(db,up)

def copy_from_staging(db,snapshot_id,upload_id,batch_size=2000):
 max_id=db.scalar(select(func.max(StagedUtterance.id)).where(StagedUtterance.upload_id==upload_id)) or 0
 last=0
 while last<max_id:
  rows=db.scalars(select(StagedUtterance).where(StagedUtterance.upload_id==upload_id,StagedUtterance.id>last).order_by(StagedUtterance.id).limit(batch_size)).all()
  if not rows:break
  db.add_all([Utterance(snapshot_id=snapshot_id,reference=r.reference,features=json.loads(r.features)) for r in rows])
  last=rows[-1].id;db.flush()

def get_upload(db,upload_id,user):
 up=db.get(SnapshotUpload,upload_id)
 if not up:raise HTTPException(404,'上传会话不存在')
 return _upload_view(db,up)
