"""HTTP 层端到端：FastAPI TestClient + SQLite，覆盖鉴权、分批上传、并发隔离与比较拦截。"""
import os,sys
sys.path.insert(0,'/workspace/backend');sys.path.insert(0,'/home/node/.local/lib/python3.11/site-packages')
# 必须在导入任何 model 之前替换引擎，使 Base 与后续 create_all 都用内存 SQLite
import src.config.database as dbmod
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
test_engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
dbmod.engine=test_engine;dbmod.SessionLocal=sessionmaker(test_engine,expire_on_commit=False)
import src.config.migrations as mig;mig.engine=test_engine
from src.config.database import Base
import src.models  # noqa: 注册所有表到同一 Base
Base.metadata.create_all(test_engine)
from src.utils.security import hash_password
from src.models import User
with dbmod.SessionLocal() as db:
 db.add(User(email='analyst@y.demo',name='a',role='analyst',password_hash=hash_password('Yanyu2026!')))
 db.add(User(email='viewer@y.demo',name='v',role='viewer',password_hash=hash_password('Yanyu2026!')));db.commit()
os.environ['SEED_DEMO']='false'
from fastapi.testclient import TestClient
from src.main import app
c=TestClient(app)
tok=c.post('/auth/login',json={'email':'analyst@y.demo','password':'Yanyu2026!'}).json()['token']
vtok=c.post('/auth/login',json={'email':'viewer@y.demo','password':'Yanyu2026!'}).json()['token']
H={'Authorization':'Bearer '+tok};VH={'Authorization':'Bearer '+vtok}
def feat(k):return {'duration':1.0+k,'noise':2.0,'token_rate':3.0,'language':'zh','device':'mobile'}
def rows(p,n):return [{'reference':f'{p}-{k:06d}','features':feat(k)} for k in range(n)]

# viewer 写入被拒
r=c.post('/uploads',headers=VH,json={'name':'x','kind':'candidate','filename':'x.json','description':'','total_batches':1});assert r.status_code==403,r.text
# 初始化 + 两批
r=c.post('/uploads',headers=H,json={'name':'big','kind':'candidate','filename':'big.json','description':'d','total_batches':2});assert r.status_code==201,r.text
uid=r.json()['id']
r=c.post(f'/uploads/{uid}/batches',headers=H,json={'batch_no':1,'utterances':rows('B',3000)});assert r.status_code==201,r.text
assert r.json()['received_batches']==1 and r.json()['row_count']==3000
r=c.post(f'/uploads/{uid}/batches',headers=H,json={'batch_no':2,'utterances':rows('C',3000)});assert r.status_code==201,r.text
# 未发布前不在快照列表
assert all(s['name']!='big' for s in c.get('/snapshots',headers=H).json())
# 发布
r=c.post(f'/uploads/{uid}/publish',headers=H,json={'total_batches':2});assert r.status_code==200,r.text
snaps=c.get('/snapshots',headers=H).json();assert any(s['name']=='big' and s['count']==6000 for s in snaps)
big_id=[s['id'] for s in snaps if s['name']=='big'][0]

# 跨批重复整份拒绝
r=c.post('/uploads',headers=H,json={'name':'dup','kind':'reference','filename':'dup.json','description':'','total_batches':2});du=r.json()['id']
assert c.post(f'/uploads/{du}/batches',headers=H,json={'batch_no':1,'utterances':rows('D',2000)}).status_code==201
bad=rows('D',2000);bad[7]={'reference':'D-000003','features':feat(1)}
r=c.post(f'/uploads/{du}/batches',headers=H,json={'batch_no':2,'utterances':bad});assert r.status_code==422,r.text
assert c.get(f'/uploads/{du}',headers=H).json()['status']=='rejected'
assert all(s['name']!='dup' for s in c.get('/snapshots',headers=H).json())

# 并发隔离：两个会话使用相同 reference，互不污染
u1=c.post('/uploads',headers=H,json={'name':'s1','kind':'candidate','filename':'s1.json','description':'','total_batches':1}).json()['id']
u2=c.post('/uploads',headers=H,json={'name':'s2','kind':'candidate','filename':'s2.json','description':'','total_batches':1}).json()['id']
shared=rows('SHARED',2000)
assert c.post(f'/uploads/{u1}/batches',headers=H,json={'batch_no':1,'utterances':shared}).status_code==201
assert c.post(f'/uploads/{u2}/batches',headers=H,json={'batch_no':1,'utterances':shared}).status_code==201
v1=c.get(f'/uploads/{u1}',headers=H).json();v2=c.get(f'/uploads/{u2}',headers=H).json()
assert v1['row_count']==2000 and v2['row_count']==2000 and v1['id']!=v2['id']
assert c.post(f'/uploads/{u1}/publish',headers=H,json={'total_batches':1}).status_code==200
assert c.post(f'/uploads/{u2}/publish',headers=H,json={'total_batches':1}).status_code==200

# 原小快照直传仍可用（历史路径）
r=c.post('/snapshots',headers=H,json={'name':'legacy','kind':'reference','filename':'l.json','description':'','utterances':rows('L',50)})
assert r.status_code==201,r.text
# 校验：特征缺失/类型错误/保留名
bad_rows=[{'reference':'X','features':{'duration':'not-a-number','noise':1,'token_rate':1,'language':'zh','device':'mobile'}}]
r=c.post('/snapshots',headers=H,json={'name':'bad1','kind':'candidate','filename':'b.json','description':'','utterances':bad_rows});assert r.status_code==422
reserved=[{'reference':'X','features':{'duration':1,'noise':1,'token_rate':1,'language':'__NEW__','device':'mobile'}}]
r=c.post('/snapshots',headers=H,json={'name':'bad2','kind':'candidate','filename':'b.json','description':'','utterances':reserved});assert r.status_code==422

# 上传进度列表可见
ups=c.get('/uploads',headers=H).json();assert len(ups)>=4 and all('received_batches' in u for u in ups)
print('HTTP 端到端：鉴权/分批/重复拒绝/并发隔离/小快照兼容 全部通过，共',len(ups),'个上传会话')
print('ALL HTTP TESTS PASSED')
