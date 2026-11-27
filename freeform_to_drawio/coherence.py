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
 def node(self,b):
  d=wire(b)
  if 1 in d:
   v=first(wire(first(d,1)),2);return self.value(v) if v is not None else None
  if 4 in d:
   s=wire(first(d,4));keys=packed(first(s,1,b''));vals=s[2]
   if len(keys)!=len(vals):raise ValueError('composite key mismatch')
   return {self.strings[k]:self.node(v) for k,v in zip(keys,vals)}
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
