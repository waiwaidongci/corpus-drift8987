"""分批上传服务测试：跨批重复整份拒绝、未发布不可比较、并发隔离、发布搬运。

运行：JWT_SECRET=t PYTHONPATH=... python3 -P test_uploads.py
"""
import os,sys,tempfile
sys.path.insert(0,'/workspace/backend')
from sqlalchemy import create_engine,select,func
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from src.config.database import Base
from src.models import CorpusSnapshot,Utterance
from src.models.upload import SnapshotUpload,StagedUtterance
from src.services import snapshots as S
from src.constants.enums import UploadStatus,SnapshotStatus

class U:
 def __init__(self,uid,email,role='analyst'):self.id=uid;self.email=email;self.role=role

def feat(i):return {'duration':1.0+i,'noise':2.0,'token_rate':3.0,'language':'zh','device':'mobile'}
def rows(prefix,n):return [{'reference':f'{prefix}-{k:06d}','features':feat(k)} for k in range(n)]
class Batch:
 def __init__(self,no,data):self.batch_no=no;self.utterances=[type('X',(),x)() for x in [{'reference':r['reference'],'features':r['features']} for r in data]]
class Init:
 def __init__(self,d):self.__dict__.update(d)
class Pub:
 def __init__(self,t):self.total_batches=t

def main():
 engine=create_engine('sqlite:///'+tempfile.mkdtemp()+'/u.db');Base.metadata.create_all(engine);Session=sessionmaker(engine)
 alice=U(1,'alice@y.demo');bob=U(2,'bob@y.demo')
 with Session() as db:
  # 1) 正常三批上传并发布
  init=Init({'name':'snap-a','kind':'candidate','filename':'a.json','description':'','total_batches':3})
  up=S.init_upload(db,init,alice);uid=up['id']
  for i in range(3):
   view=S.add_batch(db,uid,type('B',(),{'batch_no':i+1,'utterances':[type('X',(),x)() for x in [{'reference':r['reference'],'features':r['features']} for r in rows('A',20)[i*7:(i*7+7) if i<2 else 20]]]})(),alice)
  # 上面切片较繁琐，改为干净的整批
  db.rollback()
  # 重建
  up=S.init_upload(db,Init({'name':'snap-a2','kind':'candidate','filename':'a2.json','description':'','total_batches':3}),alice);uid=up['id']
  allrows=rows('A',60)
  for i in range(3):
   chunk=allrows[i*20:(i+1)*20]
   S.add_batch(db,uid,type('B',(),{'batch_no':i+1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in chunk]}),alice)
  view=S.get_upload(db,uid,alice)
  assert view['received_batches']==3 and view['row_count']==60 and view['status']=='open'
  # 未传完不可发布（模拟声明4批）
  try:S.publish_upload(db,uid,Pub(4),alice);raise AssertionError('应拒绝未传完发布')
  except HTTPException as e:assert e.status_code==422 or e.status_code==409,e.status_code
  pub=S.publish_upload(db,uid,Pub(3),alice)
  assert pub['status']=='published'
  snap=db.get(CorpusSnapshot,pub['snapshot_id']);assert snap.status==SnapshotStatus.READY
  cnt=db.scalar(select(func.count()).select_from(Utterance).where(Utterance.snapshot_id==snap.id));assert cnt==60
  # 列表只含 ready
  names=[x['name'] for x in S.list_snapshots(db)];assert 'snap-a2' in names
  print('1) 分批上传+发布+搬运 OK（60片段）')

  # 2) 跨批重复编号 → 整份拒绝，快照不可见、不可比较
  up2=S.init_upload(db,Init({'name':'snap-dup','kind':'reference','filename':'d.json','description':'','total_batches':2}),alice);u2=up2['id']
  S.add_batch(db,u2,type('B',(),{'batch_no':1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in rows('D',20)]}),alice)
  dup=rows('D',20);dup[5]={'reference':'D-000003','features':feat(9)}  # 与第一批 D-000003 重复
  try:
   S.add_batch(db,u2,type('B',(),{'batch_no':2,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in dup]}),alice)
   raise AssertionError('跨批重复必须拒绝')
  except HTTPException as e:
   assert e.status_code==422,e.status_code
  view=S.get_upload(db,u2,alice);assert view['status']=='rejected'
  snap2=db.get(CorpusSnapshot,view['snapshot_id']);assert snap2.status==SnapshotStatus.REJECTED
  assert 'snap-dup' not in [x['name'] for x in S.list_snapshots(db)]
  # 拒绝后不可再追加、不可发布
  for fn,args in [(S.add_batch,(db,u2,type('B',(),{'batch_no':3,'utterances':[type('X',(),{'reference':'Z-1','features':feat(1)})()]}),alice)),(S.publish_upload,(db,u2,Pub(2),alice))]:
   try:fn(*args);raise AssertionError('拒绝后应锁定')
   except HTTPException as e:assert e.status_code in (409,422)
  print('2) 跨批重复整份拒绝+锁定 OK')

  # 3) 批次内重复拒绝；重复批次号拒绝
  up3=S.init_upload(db,Init({'name':'snap-in','kind':'candidate','filename':'i.json','description':'','total_batches':2}),alice);u3=up3['id']
  inner=rows('E',10);inner[2]={'reference':'E-000001','features':feat(1)}
  try:
   S.add_batch(db,u3,type('B',(),{'batch_no':1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in inner]}),alice)
   raise AssertionError('批内重复应拒绝')
  except HTTPException as e:assert e.status_code==422
  assert S.get_upload(db,u3,alice)['status']=='rejected'
  up4=S.init_upload(db,Init({'name':'snap-bn','kind':'candidate','filename':'bn.json','description':'','total_batches':2}),alice);u4=up4['id']
  S.add_batch(db,u4,type('B',(),{'batch_no':1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in rows('F',20)]}),alice)
  try:
   S.add_batch(db,u4,type('B',(),{'batch_no':1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in rows('G',5)]}),alice)
   raise AssertionError('重复批次号应拒绝')
  except HTTPException as e:assert e.status_code==409
  print('3) 批内重复/重复批次号拒绝 OK')

  # 4) 并发互不串数据：两个会话独立，同 reference 字符串在不同 upload 下可共存
  ua=S.init_upload(db,Init({'name':'sess-a','kind':'candidate','filename':'sa.json','description':'','total_batches':1}),alice)
  ub=S.init_upload(db,Init({'name':'sess-b','kind':'candidate','filename':'sb.json','description':'','total_batches':1}),bob)
  shared=rows('SHARED',25)
  S.add_batch(db,ua['id'],type('B',(),{'batch_no':1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in shared]}),alice)
  S.add_batch(db,ub['id'],type('B',(),{'batch_no':1,'utterances':[type('X',(),{'reference':r['reference'],'features':r['features']})() for r in shared]}),bob)
  assert S.get_upload(db,ua['id'],alice)['row_count']==25
  assert S.get_upload(db,ub['id'],bob)['row_count']==25
  # 越权：alice 不能向 bob 的会话追加/发布
  for fn,args in [(S.add_batch,(db,ub['id'],type('B',(),{'batch_no':2,'utterances':[]}),alice)),(S.publish_upload,(db,ub['id'],Pub(1),alice))]:
   try:fn(*args);raise AssertionError('应403')
   except HTTPException as e:assert e.status_code==403
  # bob 发布自己的，互不影响
  S.publish_upload(db,ub['id'],Pub(1),bob)
  assert S.get_upload(db,ua['id'],alice)['status']=='open'
  assert S.get_upload(db,ub['id'],bob)['status']=='published'
  print('4) 会话隔离+越权防护 OK')

  # 5) 未发布(open)快照不参与比较：runs 服务校验
  from src.services.runs import create_run
  open_snap=db.get(CorpusSnapshot,S.get_upload(db,ua['id'],alice)['snapshot_id'])
  ready_ref=db.scalar(select(CorpusSnapshot).where(CorpusSnapshot.name=='snap-dup') )  # rejected
  class Cfg:pass
  class RunIn:
   def __init__(s):s.name='r';s.reference_id=1;s.candidate_id=open_snap.id
   class model_config:pass
  # 直接用简易对象模拟 ComparisonInput
  class DI:
   name='r';reference_id=snap2.id;candidate_id=open_snap.id
   def model_dump(s,exclude=()):return {'features':['duration'],'bins':6,'smoothing':0.5,'fdr_alpha':0.05,'slice_language':None,'slice_device':None}
  try:create_run(db,DI(),alice);raise AssertionError('未就绪快照应被挡下')
  except HTTPException as e:assert e.status_code in (409,422),e.status_code
  print('5) 未传完/被拒快照不参与比较 OK')
 print('ALL UPLOAD TESTS PASSED')

if __name__=='__main__':main()
