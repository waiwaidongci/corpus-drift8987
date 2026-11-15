"""百万级快照的流式比较：语义与 services.comparison.compare_snapshots 完全一致。

内存策略（每边两遍扫描，任一刻只保留定界数据）：
- 统计遍：Core 行服务器端分块读取（yield_per），连续值进 np.float64 分块数组
  （8字节/值），类别只留 去重类别→计数，缺失/总量为标量；
- 指纹遍：行以紧凑 JSONL 落盘做外部归并排序，逐行喂入 SHA-256，
  切片 id 以 np.int64 分块累积（8字节/值）；
- 结果 JSON 流式写盘，百万 id 不物化 Python list。
统计口径（分位箱、桶计数、PSI/JS、KS、BH、指纹）与内存版逐函数复用，不另造算法。
"""
import gc
import json
import os
import shutil
import tempfile
import numpy as np
from scipy.stats import ks_2samp
from sqlalchemy import select,func
from src.constants.features import FEATURES,NUMERIC
from src.models import Utterance
from .binning import freeze_numeric,freeze_categorical,count_bins
from .distributions import divergence
from .multiple_testing import benjamini_hochberg
from .fingerprint_stream import FingerprintHasher
from .external_sort import RowSpool

CHUNK=2000
ID_CHUNK=20000
SORT_CHUNK=5000
TABLE=Utterance.__table__


def _release_memory():
 """阶段边界归还 glibc 已释放的 arena，避免百万行逐行分配后 RSS 只涨不退。"""
 gc.collect()
 if os.getenv('MALLOC_TRIM','1')!='0':
  try:
   import ctypes
   ctypes.CDLL('libc.so.6').malloc_trim(0)
  except OSError:
   pass


def _selected(features_obj,config):
 return all(config.get('slice_'+key) in (None,'',features_obj[key]) for key in ('language','device'))


class _Acc:
 """切片统计累加器：定界内存，不保留任何逐行 Python 对象。"""
 def __init__(self,features):
  self.numeric=[f for f in features if f in NUMERIC]
  self.categorical=[f for f in features if f not in NUMERIC]
  self._chunks={f:[] for f in self.numeric}
  self._buf={f:np.empty(CHUNK,dtype=np.float64) for f in self.numeric}
  self._len={f:0 for f in self.numeric}
  self.cat_counts={f:{} for f in self.categorical}
  self.missing={f:0 for f in features}
  self.n=0

 def add(self,features_obj):
  self.n+=1
  for f in self.numeric:
   v=features_obj[f]
   if v is None:self.missing[f]+=1
   else:
    i=self._len[f];self._buf[f][i]=float(v);self._len[f]=i+1
    if self._len[f]==CHUNK:
     self._chunks[f].append(self._buf[f].copy());self._len[f]=0
  for f in self.categorical:
   v=features_obj[f]
   if v is None:self.missing[f]+=1
   else:self.cat_counts[f][v]=self.cat_counts[f].get(v,0)+1

 def take_numeric(self,feature):
  """取出单特征的连续数组并立即释放其分块，避免多特征数组与源分块同时驻留。"""
  parts=list(self._chunks[feature])
  if self._len[feature]:parts.append(self._buf[feature][:self._len[feature]])
  out=np.concatenate(parts) if parts else np.empty(0,dtype=np.float64)
  self._chunks[feature]=[];self._buf[feature]=np.empty(0,dtype=np.float64);self._len[feature]=0
  return out


def _stats_pass(db,snapshot_id,config,features):
 acc=_Acc(features)
 # stream_results：MySQL/PyMySQL 下启用 SSCursor 服务器端游标，避免整结果集驻留客户端
 stmt=select(TABLE.c.features).where(TABLE.c.snapshot_id==snapshot_id).execution_options(yield_per=CHUNK,stream_results=True)
 total=0
 for (features_obj,) in db.execute(stmt):
  total+=1
  if _selected(features_obj,config):acc.add(features_obj)
 if total==0:raise ValueError('参考和候选快照均不得为空')
 return total,acc


def _fingerprint_pass(db,snapshot_id,spool,id_chunks):
 stmt=select(TABLE.c.id,TABLE.c.reference,TABLE.c.features).where(TABLE.c.snapshot_id==snapshot_id).execution_options(yield_per=CHUNK,stream_results=True)
 ids=[]
 for row_id,reference,features_obj in db.execute(stmt):
  spool.add(row_id,reference,features_obj);ids.append(row_id)
  if len(ids)>=ID_CHUNK:
   id_chunks.append(np.asarray(ids,dtype=np.int64));ids.clear()
 if ids:id_chunks.append(np.asarray(ids,dtype=np.int64))


def _categorical_counts(cat_counts,missing,spec):
 """与 count_bins(逐行值,spec) 等价，但输入是去重计数；缺失并入末桶 __MISSING__。"""
 counts=[0]*len(spec['labels']);cats=spec['categories']
 for value,n in cat_counts.items():
  idx=cats.index(value) if value in cats else len(counts)-2
  counts[idx]+=n
 counts[-1]+=missing
 return counts


def _build_findings(ref_acc,cand_acc,config):
 """与 comparison.compare_snapshots 同序、同函数构造 findings。

 连续特征逐特征取数组、算完即释放，任一时刻只保留当前特征的两边数组，
 避免三个特征数组与其源分块同时驻留造成的瞬时峰值。
 """
 features=config['features']
 findings=[];continuous=[]
 for feature in features:
  if feature in NUMERIC:
   a=ref_acc.take_numeric(feature);b=cand_acc.take_numeric(feature)
   spec=freeze_numeric(a,config['bins'])
   # 累加器仅存非缺失 np.float64；缺失数单列并计入末桶 __MISSING__，与原 count_bins(含None) 等价
   ac=count_bins(a,spec);bc=count_bins(b,spec)
   ac[-1]+=ref_acc.missing[feature];bc[-1]+=cand_acc.missing[feature]
  else:
   a=b=None
   spec=freeze_categorical(list(ref_acc.cat_counts[feature]))
   ac=_categorical_counts(ref_acc.cat_counts[feature],ref_acc.missing[feature],spec)
   bc=_categorical_counts(cand_acc.cat_counts[feature],cand_acc.missing[feature],spec)
  metrics=divergence(ac,bc,config['smoothing'])
  metrics.update({'binning':spec,'reference_counts':ac,'candidate_counts':bc,
   'reference_n':ref_acc.n,'candidate_n':cand_acc.n,
   'reference_missing':ref_acc.missing[feature],'candidate_missing':cand_acc.missing[feature],
   'ks_statistic':None,'p_value':None,'q_value':None,'fdr_reject':False})
  if feature in NUMERIC:
   if len(a)<2 or len(b)<2:raise ValueError('连续特征有效值不足，无法KS检验')
   ks=ks_2samp(a,b,alternative='two-sided',method='auto')
   metrics.update(ks_statistic=float(ks.statistic),p_value=float(ks.pvalue),ks_reference_n=len(a),ks_candidate_n=len(b))
   continuous.append(len(findings))
   del a,b;_release_memory()
  findings.append({'feature':feature,'status':'quiet','metrics':metrics})
 qvalues=benjamini_hochberg([findings[i]['metrics']['p_value'] for i in continuous])
 for i,q in zip(continuous,qvalues):findings[i]['metrics'].update(q_value=q,fdr_reject=bool(q<=config['fdr_alpha']))
 for f in findings:
  m=f['metrics'];f['status']='review' if m['fdr_reject'] or m['psi']>=.2 or m['js']>=.05 else 'quiet'
 return findings,continuous


def _write_artifact(path,result,findings,ref_ids,cand_ids):
 """流式写JSON：id数组按块输出，避免百万id一次性物化成Python list。"""
 def write_ids(f,chunks):
  f.write('[');first=True
  for ch in chunks:
   for v in ch.tolist():
    if not first:f.write(',')
    f.write(str(v));first=False
  f.write(']')
 with open(path,'w',encoding='utf-8') as f:
  head={k:v for k,v in result.items()}
  f.write('{')
  f.write(','.join(json.dumps(k,ensure_ascii=False)+':'+json.dumps(v,ensure_ascii=False,allow_nan=False) for k,v in head.items()))
  f.write(',"findings":');f.write(json.dumps(findings,ensure_ascii=False,allow_nan=False))
  f.write(',"reference_ids":');write_ids(f,ref_ids)
  f.write(',"candidate_ids":');write_ids(f,cand_ids)
  f.write('}')


def compare_snapshots_streaming(db,ref_snapshot_id,cand_snapshot_id,config,artifact_path,spool_root):
 features=config['features']
 if not features or len(set(features))!=len(features) or not set(features)<=set(FEATURES):raise ValueError('未知或重复特征')
 if not 2<=config['bins']<=10 or not 0<config['fdr_alpha']<=.2:raise ValueError('分箱数/FDR范围不合法')
 if not (isinstance(config.get('smoothing'),(int,float)) and np.isfinite(config['smoothing']) and config['smoothing']>0):raise ValueError('平滑伪计数必须为有限正数')

 os.makedirs(spool_root,exist_ok=True)
 ref_dir=tempfile.mkdtemp(prefix='ref-',dir=spool_root);cand_dir=tempfile.mkdtemp(prefix='cand-',dir=spool_root)
 try:
  # ---- 统计遍（先候选后参考，顺序不影响统计；仅持累加器）----
  cand_total,cand_acc=_stats_pass(db,cand_snapshot_id,config,features)
  ref_total,ref_acc=_stats_pass(db,ref_snapshot_id,config,features)
  if ref_acc.n<5 or cand_acc.n<5:raise ValueError('空切片或样本不足：参考和候选切片各至少5片段')
  findings,continuous=_build_findings(ref_acc,cand_acc,config)
  ref_slice_n,cand_slice_n=ref_acc.n,cand_acc.n
  # 释放数值数组，指纹遍只需 spool/id；归还 arena 控制 RSS
  del ref_acc,cand_acc;_release_memory()
  # ---- 指纹遍：外排 JSONL + id 分块 ----
  ref_spool=RowSpool(ref_dir,SORT_CHUNK);cand_spool=RowSpool(cand_dir,SORT_CHUNK)
  ref_ids=[];cand_ids=[]
  _fingerprint_pass(db,cand_snapshot_id,cand_spool,cand_ids)
  _release_memory()
  _fingerprint_pass(db,ref_snapshot_id,ref_spool,ref_ids)
  _release_memory()
  hasher=FingerprintHasher(config);hasher.begin()
  for line in cand_spool.iter_sorted_lines():hasher.feed_line('candidate',line)
  hasher.switch_to_reference()
  for line in ref_spool.iter_sorted_lines():hasher.feed_line('reference',line)
  digest=hasher.finish()
 finally:
  shutil.rmtree(ref_dir,ignore_errors=True);shutil.rmtree(cand_dir,ignore_errors=True)

 result={'fingerprint':digest,'reference_n':ref_slice_n,'candidate_n':cand_slice_n,
  'test_family':{'method':'Benjamini-Hochberg','m':len(continuous),'alpha':config['fdr_alpha'],'features':[findings[i]['feature'] for i in continuous]},
  'method':'Reference-only frozen quantile/category bins; additive pseudocount; PSI natural log; JS log2; two-sided KS nonmissing; BH across selected numeric features',
  'interpretation':'Distribution difference is not evidence of downstream model quality degradation; review markers combine FDR and explicit heuristic effect-size thresholds.'}
 _write_artifact(artifact_path,result,findings,ref_ids,cand_ids)
 return result,findings
