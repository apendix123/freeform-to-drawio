"""Decode a subset of copied SQLite CRDT records using libfreeform 2.1 schemas.
Schema reference: https://github.com/can1357/libfreeform (MIT license; see THIRD_PARTY_NOTICES).
Unknown CRDT/value alternatives are retained as hex, not guessed.
"""
import collections, json, math, pathlib, sqlite3, struct, uuid
def varint(b,i):
 n=s=0
 while i<len(b) and s<70:
  v=b[i];i+=1;n|=(v&127)<<s
  if v<128:return n,i
  s+=7
 raise ValueError('invalid varint')
def wire(b):
 out=collections.defaultdict(list);i=0
 while i<len(b):
  k,i=varint(b,i);t,w=k>>3,k&7
  if not t:raise ValueError('zero field')
  if w==0:v,i=varint(b,i)
  elif w in (1,5):
   n=8 if w==1 else 4;v=b[i:i+n];i+=n
   if len(v)!=n:raise ValueError('truncated fixed field')
  elif w==2:
   n,i=varint(b,i);v=b[i:i+n];i+=n
   if len(v)!=n:raise ValueError('truncated bytes')
  else:raise ValueError('unsupported wire')
  out[t].append(v)
 return out
def first(d,k,default=None):return d.get(k,[default])[0]
def packed(b):
 out=[];i=0
 while i<len(b):v,i=varint(b,i);out.append(v)
 return out
def uid(b):return str(uuid.UUID(bytes=b))
class Document:
 def __init__(self,b):
  if b[:4]!=b'crdt' or int.from_bytes(b[4:8],'little') not in (6,7):raise ValueError('unsupported CRDT')
  self.body=wire(b[8:]);c=wire(first(self.body,6,b''));self.strings=[x.decode() for x in c[2]];self.uuids=first(c,1,b'')
 def value(self,b):
  d=wire(b)
  if 1 in d:return first(d,1)
  if 2 in d:return struct.unpack('<d',first(d,2))[0]
  if 3 in d:return self.strings[first(d,3)]
  if 4 in d:return {'bytes':first(d,4).hex()}
  if 5 in d:
   v=first(d,5);return (v>>1)^-(v&1)
  if 6 in d:
   i=first(d,6)*16;return uid(self.uuids[i:i+16])
  if 9 in d:
   v=first(wire(first(d,9)),1);return self.value(v) if v is not None else None
  if 11 in d:return [self.value(x) for x in wire(first(d,11))[1]]
  if 14 in d:
   s=wire(first(d,14));vals=s[2];keys=packed(first(s,1,b'')) if 1 in s else list(range(len(vals)))
   if len(keys)!=len(vals):raise ValueError('custom key mismatch')
   return {str(k):self.value(v) for k,v in zip(keys,vals)}
  if 15 in d:return struct.unpack('<f',first(d,15))[0]
  return {'unknown_value':b.hex()}
 def replica(self,index):
  start=index*16;value=self.uuids[start:start+16]
  if len(value)!=16:raise ValueError('UUID constant out of range')
  return value
 def removed(self,version,timestamp):
  version=wire(version);stamp=wire(timestamp)
  target=self.replica(first(stamp,1,0));counter=first(stamp,2,0)
  uuids=packed(first(version,1,b''));sets=version[2]
  if len(uuids)!=len(sets):raise ValueError('Version vector length mismatch')
  for replica,ranges in zip(uuids,sets):
   values=packed(first(wire(ranges),2,b''));end=0
   if len(values)%2:raise ValueError('Incomplete version counter range')
   for gap,length in zip(values[::2],values[1::2]):
    start=end+gap;end=start+length
    if self.replica(replica)==target and start<=counter<end:return True
  return False
 def ordered_values(self,b):
  ordered=wire(b)
  if first(ordered,4,0)!=0:raise ValueError('Ordered dictionary is not a set')
  insertions=wire(first(ordered,1,b''))[4]
  keys=[]
  for insertion in insertions:
   key=first(wire(insertion),1)
   if key is None:raise ValueError('Missing ordered-set key')
   keys.append(key)
  segments=[];consumed=0
  sequence=wire(first(ordered,2,b''))
  persistent=4 in sequence or 5 in sequence
  for raw in sequence[3]:
   seg=wire(raw);length=first(seg,2,0);option=first(seg,6,0)
   if option not in (0,1):raise ValueError('Unknown sequence content option')
   # Persistent stores elide removed ranges by omitting their added stamp;
   # clipboard snapshots can instead use the explicit content=1 alternative.
   elided=option==1 or (persistent and 3 not in seg)
   end=consumed+(0 if elided else length)
   if end>len(keys):raise ValueError('Sequence exceeds content')
   gone=3 in seg and 5 in seg and self.removed(first(seg,5),first(seg,3))
   segments.append((list(range(consumed,end)),packed(first(seg,4,b'')),not gone));consumed=end
  if consumed!=len(keys):raise ValueError('Sequence content length mismatch')
  order=[];stack=[0] if segments else [];seen=set()
  while stack:
   index=stack.pop()
   if index in seen or index>=len(segments):raise ValueError('Invalid sequence tree')
   seen.add(index);content,children,visible=segments[index]
   if visible:order.extend(content)
   stack.extend(reversed(children))
  if len(seen)!=len(segments):raise ValueError('Unreachable sequence segment')
  timestamps=ordered[3]
  if len(timestamps)!=len(keys):raise ValueError('Ordered-set timestamp count mismatch')
  stamps=[]
  for stamp in timestamps:
   stamp=wire(stamp);stamps.append((first(stamp,2,0),self.replica(first(stamp,1,0))))
  winners={}
  for index in order:
   key=keys[index]
   if key not in winners or stamps[index]>stamps[winners[key]]:winners[key]=index
  return [self.value(keys[index]) for index in order if winners[keys[index]]==index]
 def node(self,b):
  d=wire(b)
  if 1 in d:
   v=first(wire(first(d,1)),2);return self.value(v) if v is not None else None
  if 4 in d:
   s=wire(first(d,4));keys=packed(first(s,1,b''));vals=s[2]
   if len(keys)!=len(vals):raise ValueError('composite key mismatch')
   return {self.strings[k]:self.node(v) for k,v in zip(keys,vals)}
  if 9 in d:
   try:return {'ordered_values':self.ordered_values(first(d,9))}
   except ValueError as exc:return {'unknown_crdt':b.hex(),'ordered_error':str(exc)}
  if 5 in d:
   s=wire(first(d,5));return {'attributed_text':first(s,1,b'').decode(),'attribute_runs_raw':[x.hex() for x in s[6]]}
  return {'unknown_crdt':b.hex()}
 def root(self):
  b=first(self.body,1);return self.node(b) if b is not None else {'unsupported_capsule':True}
def get(v,*path):
 for k in path:
  if not isinstance(v,dict):return None
  v=v.get(str(k))
 return v
def point(v):
 if not isinstance(v,dict) or 'bytes' not in v:return None
 d=wire(bytes.fromhex(v['bytes']))
 return [struct.unpack('<f',first(d,k,b'\0'*4))[0] for k in (1,2)]
