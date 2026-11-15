"""端到端口径一致性测试：内存管线 vs 流式管线（SQLite 模拟 MySQL）。

运行：PYTHONPATH=/workspace/backend python3 -I test_equivalence.py
"""
import json
import os
import sys
import tempfile
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0,'/workspace/backend')
from src.config.database import Base
from src.models import CorpusSnapshot,Utterance
from src.services.comparison import compare_snapshots
from src.services.comparison_stream import compare_snapshots_streaming
from src.services.snapshots import get_utterances

FEATURES=['duration','noise','token_rate','language','device']
CONFIG={'features':FEATURES,'bins':6,'smoothing':0.5,'fdr_alpha':0.05,'slice_language':None,'slice_device':None}

def make_rows(rng,n,prefix,lang_p,dev_p,missing_p=0.03):
 out=[]
 for i in range(n):
  out.append({'reference':f'{prefix}-{i:08d}',
   'features':{
    'duration':None if rng.random()<missing_p else round(float(rng.lognormal(0.9,0.5)),5),
    'noise':round(float(rng.normal(10,3)),5),
    'token_rate':None if rng.random()<missing_p else round(float(rng.normal(5,0.9)),5),
    'language':None if rng.random()<0.01 else str(rng.choice(['zh','en','ja','ko'],p=lang_p)),
    'device':None if rng.random()<0.01 else str(rng.choice(['studio','mobile','field','wearable'],p=dev_p))}})
 return out

def main():
 work=tempfile.mkdtemp(prefix='eq-test-')
 db_path=os.path.join(work,'t.db')
 engine=create_engine('sqlite:///'+db_path)
 Base.metadata.create_all(engine)
 Session=sessionmaker(engine,expire_on_commit=False)
 rng=np.random.default_rng(113)
 ref_rows=make_rows(rng,3000,'REF',[0.65,0.25,0.1,0.0],[0.55,0.3,0.15,0.0])
 cand_rows=make_rows(rng,3000,'NEW',[0.5,0.35,0.1,0.05],[0.15,0.65,0.15,0.05])
 with Session() as db:
  ref=CorpusSnapshot(name='ref',kind='reference',filename='r.json',status='ready')
  cand=CorpusSnapshot(name='cand',kind='candidate',filename='c.json',status='ready')
  db.add_all([ref,cand]);db.flush()
  db.add_all([Utterance(snapshot_id=ref.id,reference=r['reference'],features=r['features']) for r in ref_rows])
  db.add_all([Utterance(snapshot_id=cand.id,reference=r['reference'],features=r['features']) for r in cand_rows])
  db.commit();rid,cid=ref.id,cand.id
 with Session() as db:
  mem=compare_snapshots(get_utterances(db,rid),get_utterances(db,cid),CONFIG)
  art=os.path.join(work,'art.json')
  result,findings=compare_snapshots_streaming(db,rid,cid,CONFIG,art,work)
  disk=json.load(open(art))
  # 1) 指纹一致
  assert mem['fingerprint']==result['fingerprint']==disk['fingerprint'],'fingerprint mismatch'
  # 2) findings 全字段一致
  assert len(mem['findings'])==len(findings)
  for a,b in zip(mem['findings'],findings):
   assert a['feature']==b['feature'] and a['status']==b['status']
   for k in a['metrics']:
    av,bv=a['metrics'][k],b['metrics'][k]
    if isinstance(av,float):assert np.isclose(av,bv,rtol=1e-12,atol=1e-15),(a['feature'],k,av,bv)
    else:assert av==bv,(a['feature'],k,av,bv)
  # 3) artifact 中 id 数组与内存版一致
  assert disk['reference_ids']==mem['reference_ids']
  assert disk['candidate_ids']==mem['candidate_ids']
  assert disk['findings']==mem['findings']
  assert disk['test_family']==mem['test_family']
  print('full-config equivalence OK; fingerprint',mem['fingerprint'][:16])
  # 4) 切片 + 部分特征场景
  cfg2=dict(CONFIG);cfg2['features']=['duration','language'];cfg2['slice_language']='zh';cfg2['bins']=4;cfg2['smoothing']=1.0;cfg2['fdr_alpha']=0.1
  mem2=compare_snapshots(get_utterances(db,rid),get_utterances(db,cid),cfg2)
  art2=os.path.join(work,'art2.json')
  res2,find2=compare_snapshots_streaming(db,rid,cid,cfg2,art2,work)
  assert mem2['fingerprint']==res2['fingerprint'],'sliced fingerprint mismatch'
  for a,b in zip(mem2['findings'],find2):
   assert a['status']==b['status']
   for k in a['metrics']:
    av,bv=a['metrics'][k],b['metrics'][k]
    if isinstance(av,float):assert np.isclose(av,bv,rtol=1e-12,atol=1e-15)
    else:assert av==bv
  assert mem2['reference_n']==res2['reference_n'] and mem2['candidate_n']==res2['candidate_n']
  print('slice+partial-feature equivalence OK; n=',res2['reference_n'],res2['candidate_n'])
  # 5) 小数据常量参考应失败，口径一致
  const=CorpusSnapshot(name='const',kind='reference',filename='x.json',status='ready')
  db.add(const);db.flush()
  db.add_all([Utterance(snapshot_id=const.id,reference=f'K{i}',features={'duration':1.0,'noise':2.0,'token_rate':3.0,'language':'zh','device':'mobile'}) for i in range(30)])
  db.commit()
  for pipe in ('mem','stream'):
   try:
    if pipe=='mem':compare_snapshots(get_utterances(db,const.id),get_utterances(db,cid),CONFIG)
    else:compare_snapshots_streaming(db,const.id,cid,CONFIG,os.path.join(work,'x.json'),work)
    raise AssertionError('应失败：参考常量')
   except ValueError as e:
    assert '常量' in str(e) or '分位箱' in str(e),str(e)
  print('constant-reference failure path OK')
 print('ALL EQUIVALENCE TESTS PASSED')

if __name__=='__main__':main()
