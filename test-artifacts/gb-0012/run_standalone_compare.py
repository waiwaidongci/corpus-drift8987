import json,resource,sys,time
sys.path.insert(0,'/workspace/backend');sys.path.insert(0,'/home/node/.local/lib/python3.11/site-packages')
from sqlalchemy import create_engine,event
from sqlalchemy.orm import sessionmaker
from src.services.comparison_stream import compare_snapshots_streaming
db_file,out_dir,cache=sys.argv[1],sys.argv[2],int(sys.argv[3])
engine=create_engine('sqlite:///'+db_file)
@event.listens_for(engine,'connect')
def _set(c,_):
 cur=c.cursor();cur.execute(f'PRAGMA cache_size=-{cache}');cur.close()
Session=sessionmaker(engine)
cfg={'features':['duration','noise','token_rate','language','device'],'bins':6,'smoothing':0.5,'fdr_alpha':0.05,'slice_language':None,'slice_device':None}
t=time.time()
with Session() as db:
 result,findings=compare_snapshots_streaming(db,1,2,cfg,out_dir+'/out.json',out_dir)
peak=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024.0
disk=json.load(open(out_dir+'/out.json'))
assert len(disk['reference_ids'])==1_000_000 and len(disk['candidate_ids'])==1_000_000
for f in disk['findings']:assert sum(f['metrics']['reference_counts'])==1_000_000
print('[compare] cache_kb=%d seconds=%.1f peakRSS_MB=%.0f fp=%s'%(cache,time.time()-t,peak,result['fingerprint'][:16]))
