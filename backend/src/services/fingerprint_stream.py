import json
import hashlib
from src.constants.features import FEATURES

class FingerprintHasher:
 """与 services.fingerprint.fingerprint 字节一致的流式 SHA-256。

 原实现：json.dumps({'reference':rows(ref),'candidate':rows(cand),'config':config,
 'algorithm':...},sort_keys=True,allow_nan=False,separators=(',',':'))。
 默认 ensure_ascii=True；sort_keys 使顶层键序为 algorithm,candidate,config,reference。
 行 JSON 由 RowSpool 预序列化（同样 sort_keys/ensure_ascii=True/紧凑分隔符），
 归并排序后按 reference 顺序逐行喂入，避免任何一侧的全量 dict 驻留内存。
 """
 def __init__(self,config):
  self.config=config
  self.h=hashlib.sha256()
  self._first={'reference':True,'candidate':True}

 def begin(self):
  self.h.update(b'{"algorithm":"frozen-quantile-psi-js-ks-bh-v1","candidate":[')

 def feed_line(self,side,line:str):
  if not self._first[side]:self.h.update(b',')
  self.h.update(line.encode())
  self._first[side]=False

 def feed(self,side,row):
  payload={'id':row['id'],'reference':row['reference'],'features':{k:row['features'][k] for k in sorted(FEATURES)}}
  self.feed_line(side,json.dumps(payload,sort_keys=True,allow_nan=False,separators=(',',':')))

 def switch_to_reference(self):
  self.h.update(b'],"config":')
  self.h.update(json.dumps(self.config,sort_keys=True,allow_nan=False,separators=(',',':')).encode())
  self.h.update(b',"reference":[')

 def finish(self):
  self.h.update(b']}')
  return self.h.hexdigest()
