import json
from pathlib import Path
from sqlalchemy import select,func
from celery import Celery
from src.config.database import SessionLocal
from src.config.settings import REDIS_URL,ARTIFACT_DIR,SPOOL_DIR,STREAM_THRESHOLD
from src.models import ComparisonRun,DriftFinding,CorpusSnapshot,Utterance
from src.services.snapshots import get_utterances
from src.services.comparison import compare_snapshots
from src.services.comparison_stream import compare_snapshots_streaming
from src.services.audit import record
app=Celery('corpus_drift',broker=REDIS_URL,backend=REDIS_URL)
app.conf.update(task_track_started=True,worker_prefetch_multiplier=1,task_acks_late=True)
@app.task(name='corpus.compare',soft_time_limit=900,time_limit=960)
def compare_run(id):
 with SessionLocal() as db:
  run=db.get(ComparisonRun,id)
  if not run or run.status=='succeeded':return
  run.status='running';db.commit()
  try:
   Path(ARTIFACT_DIR).mkdir(parents=True,exist_ok=True)
   artifact_path=str(Path(ARTIFACT_DIR,f'comparison-{id}.json'))
   ref_count=db.scalar(select(func.count()).select_from(Utterance).where(Utterance.snapshot_id==run.reference_id))
   cand_count=db.scalar(select(func.count()).select_from(Utterance).where(Utterance.snapshot_id==run.candidate_id))
   if max(ref_count or 0,cand_count or 0)>STREAM_THRESHOLD:
    result,findings=compare_snapshots_streaming(db,run.reference_id,run.candidate_id,run.config,artifact_path,SPOOL_DIR)
   else:
    result=compare_snapshots(get_utterances(db,run.reference_id),get_utterances(db,run.candidate_id),run.config)
    findings=result['findings']
    Path(artifact_path).write_text(json.dumps(result,ensure_ascii=False,allow_nan=False))
    result={k:v for k,v in result.items() if k!='findings'}
   for f in findings:
    if not db.scalar(select(DriftFinding).where(DriftFinding.run_id==id,DriftFinding.feature==f['feature'])):db.add(DriftFinding(run_id=id,**f))
   db.flush()
   if 'findings' in result:result={k:v for k,v in result.items() if k!='findings'}
   run.result=result;run.status='succeeded';run.error=None;db.commit();record(db,'worker','comparison.succeeded',{'run_id':id,'findings':len(findings),'mode':'streaming' if max(ref_count or 0,cand_count or 0)>STREAM_THRESHOLD else 'in_memory'})
  except Exception as e:
   db.rollback();run=db.get(ComparisonRun,id);run.status='failed';run.error=str(e)[:1000];db.commit();record(db,'worker','comparison.failed',{'run_id':id,'error':run.error})
