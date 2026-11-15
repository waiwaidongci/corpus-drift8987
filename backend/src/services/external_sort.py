"""轻量行外排缓冲：只存 (reference, json_bytes) 元组，避免 dict 与重复序列化。"""
import heapq
import json
import os
import tempfile


class RowSpool:
 def __init__(self,temp_dir:str,chunk_rows:int=20000):
  os.makedirs(temp_dir,exist_ok=True)
  self.temp_dir=temp_dir
  self.chunk_rows=chunk_rows
  self._buf:list[tuple[str,str]]=[]
  self._paths:list[str]=[]

 def add(self,row_id:int,reference:str,features:dict):
  # 与指纹行内 JSON 完全同构：{"features":{...},"id":..,"reference":..}（sort_keys 后键序）
  payload={'id':row_id,'reference':reference,'features':features}
  self._buf.append((reference,json.dumps(payload,sort_keys=True,allow_nan=False,separators=(',',':'))))
  if len(self._buf)>=self.chunk_rows:self._spill()

 def _spill(self):
  if not self._buf:return
  self._buf.sort(key=lambda x:x[0])
  fd,path=tempfile.mkstemp(prefix='spool-',suffix='.jsonl',dir=self.temp_dir)
  with os.fdopen(fd,'w',encoding='utf-8') as f:
   for _,line in self._buf:f.write(line);f.write('\n')
  self._paths.append(path);self._buf=[]

 @staticmethod
 def _read(path):
  with open(path,'r',encoding='utf-8') as f:
   for line in f:
    obj=json.loads(line)
    yield obj['reference'],line.rstrip('\n')

 def iter_sorted_lines(self):
  """逐行产出已排序的指纹 JSON 字符串（含 sort_keys 的行结构）。"""
  if not self._paths:
   self._buf.sort(key=lambda x:x[0])
   for _,line in self._buf:yield line
   self._buf=[]
   return
  if self._buf:self._spill()
  streams=[self._read(p) for p in self._paths]
  try:
   for _,line in heapq.merge(*streams,key=lambda x:x[0]):yield line
  finally:
   for s in streams:s.close()
   for p in self._paths:
    try:os.remove(p)
    except OSError:pass
   self._paths=[]
