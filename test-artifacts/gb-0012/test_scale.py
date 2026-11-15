"""百万级规模与内存测试：1,000,000 行/快照，流式管线峰值内存与正确性抽检。

运行：JWT_SECRET=t PYTHONPATH=... python3 -P test_scale.py [rows]
"""
import json
import os
import resource
import sys
import tempfile
import time
import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0,'/workspace/backend')
from src.config.database import Base
from src.models import CorpusSnapshot,Utterance
from src.services.comparison_stream import compare_snapshots_streaming

N=int(sys.argv[1]) if len(sys.argv)>1 else 1_000_000
FEATURES=['duration','noise','token_rate','language','device']
CONFIG={'features':FEATURES,'bins':6,'smoothing':0.5,'fdr_alpha':0.05,'slice_language':None,'slice_device':None}

def peak_mb():
 return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.0

def main():
 work=tempfile.mkdtemp(prefix='scale-')
 db_file=os.path.join(work,'scale.db')
 engine=create_engine('sqlite:///'+db_file)
 Base.metadata.create_all(engine)
 Session=sessionmaker(engine)
 rng=np.random.default_rng(2026)
 t0=time.time()
 with Session() as db:
  rid={}
  for kind,prefix in (('reference','REF'),('candidate','NEW')):
   s=CorpusSnapshot(name=kind,kind=kind,filename=prefix+'.json',status='ready');db.add(s);db.flush();sid=s.id;rid[kind]=sid
   buf=[]
   langs=np.array(['zh','en','ja','ko']);devs=np.array(['studio','mobile','field','wearable'])
   for start in range(0,N,20000):
    m=min(20000,N-start);idx=np.arange(start,start+m)
    dur=np.round(rng.lognormal(0.9,0.5,m),5);noise=np.round(rng.normal(10,3,m),5);rate=np.round(rng.normal(5,0.9,m),5)
    lang=rng.choice(langs,m,p=[0.6,0.3,0.08,0.02]);dev=rng.choice(devs,m,p=[0.4,0.4,0.15,0.05])
    dmiss=rng.random(m)<0.03;lmiss=rng.random(m)<0.01;vmiss=rng.random(m)<0.01
    buf=[Utterance(snapshot_id=sid,reference=f'{prefix}-{start+k:09d}',
      features={'duration':None if dmiss[k] else float(dur[k]),'noise':float(noise[k]),'token_rate':None if vmiss[k] else float(rate[k]),
       'language':None if lmiss[k] else str(lang[k]),'device':None if vmiss[k] else str(dev[k])}) for k in range(m)]
    db.bulk_save_objects(buf);db.commit();buf=None
 print(f'insert {N*2} rows in {time.time()-t0:.1f}s, db size {os.path.getsize(db_file)/1e6:.0f}MB, peakRSS {peak_mb():.0f}MB')
 t1=time.time()
 with Session() as db:
  art=os.path.join(work,'comparison.json')
  result,findings=compare_snapshots_streaming(db,rid['reference'],rid['candidate'],CONFIG,art,work)
 dt=time.time()-t1
 print(f'streaming compare in {dt:.1f}s, peakRSS {peak_mb():.0f}MB')
 print('fingerprint',result['fingerprint'])
 for f in findings:print(' ',f['feature'],f['status'],'psi=%.5f js=%.5f p=%s q=%s'%(f['metrics']['psi'],f['metrics']['js'],f['metrics']['p_value'],f['metrics']['q_value']))
 disk=json.load(open(art))
 assert len(disk['reference_ids'])==N and len(disk['candidate_ids'])==N
 assert len(disk['findings'])==5
 # 计数守恒：每特征桶计数之和等于切片总量
 for f in disk['findings']:
  assert sum(f['metrics']['reference_counts'])==N and sum(f['metrics']['candidate_counts'])==N,f['feature']
 print('artifact size %.0fMB; count conservation OK'% (os.path.getsize(art)/1e6))
 print('SCALE OK rows=%d peakRSS=%.0fMB compare_s=%.1f'%(N,peak_mb(),dt))

if __name__=='__main__':main()
