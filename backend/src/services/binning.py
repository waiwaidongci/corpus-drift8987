import numpy as np
from src.constants.features import MISSING,NEW

def freeze_numeric(values,bins):
 # 兼容 Python list 与 np.ndarray；内部统一为 numpy 以支持百万级流式调用
 valid=np.asarray([float(v) for v in values if v is not None]) if not isinstance(values,np.ndarray) else np.asarray(values,dtype=np.float64)
 if len(valid)<2 or not np.isfinite(valid).all() or len(np.unique(valid))<2:raise ValueError('参考数值特征缺失过多或为常量，无法冻结分位箱')
 edges=np.unique(np.quantile(valid,np.linspace(0,1,bins+1)[1:-1])).tolist()
 bounds=[None]+edges+[None];labels=[]
 for i in range(len(bounds)-1):labels.append(f"[{bounds[i] if bounds[i] is not None else '-inf'}, {bounds[i+1] if bounds[i+1] is not None else '+inf'})")
 return {'kind':'numeric','internal_edges':edges,'labels':labels+[MISSING],'fitted_from':'reference_only','interval':'left_closed_right_open; last includes +inf'}
def freeze_categorical(values):
 categories=sorted(set(v for v in values if v is not None))
 if not categories:raise ValueError('参考类别没有有效值')
 return {'kind':'categorical','categories':categories,'labels':categories+[NEW,MISSING],'fitted_from':'reference_only'}
def count_bins(values,spec):
 counts=[0]*len(spec['labels'])
 if spec['kind']=='numeric':
  if isinstance(values,np.ndarray):
   # 流式路径：数组保证只含非缺失 np.float64，零 Python 对象物化
   arr=values.astype(np.float64,copy=False)
   missing=0
  else:
   raw=list(values)
   missing=sum(v is None for v in raw)
   arr=np.asarray([float(v) for v in raw if v is not None],dtype=np.float64)
  if len(arr) and not np.isfinite(arr).all():raise ValueError('数值含非有限值')
  # searchsorted 向量化：与逐行 side='right' 完全等价；缺失值入末桶 __MISSING__
  idx=np.searchsorted(np.asarray(spec['internal_edges'],dtype=np.float64),arr,side='right')
  hist=np.bincount(idx,minlength=len(counts));hist[-1]+=missing
  return [int(x) for x in hist]
 for v in values:
  if v is None:index=len(counts)-1
  else:index=spec['categories'].index(v) if v in spec['categories'] else len(counts)-2
  counts[index]+=1
 return counts
