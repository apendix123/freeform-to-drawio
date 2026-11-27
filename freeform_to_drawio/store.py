"""Read copied SQLite stores; snapshot a live store without modifying its files."""
import contextlib
import collections
import datetime
import hashlib
import json
import pathlib
import shutil
import sqlite3

from .coherence import Document, get, point, uid

DEFAULT_STORE = pathlib.Path.home() / 'Library/Containers/com.apple.freeform/Data/Library/Freeform/Boards'
KINDS = {2:'container',3:'shape',4:'connector',5:'image',6:'movie',7:'file',8:'url',9:'usdz',10:'drawing',11:'ink',12:'sticky'}

def readonly(path):
    return sqlite3.connect(pathlib.Path(path).resolve().as_uri() + '?mode=ro', uri=True)

def snapshot(source, destination):
    source, destination = pathlib.Path(source).resolve(), pathlib.Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError('Snapshot and source directories must not overlap')
    destination.mkdir(parents=True, exist_ok=False)
    # SQLite backup merges WAL content consistently; the live connection is read-only.
    with contextlib.closing(readonly(source / 'boards.db')) as src, contextlib.closing(sqlite3.connect(destination / 'boards.db')) as dst:
        src.backup(dst)
        dst.execute('PRAGMA journal_mode=DELETE')
    if (source / 'side.db').exists():
        with contextlib.closing(readonly(source / 'side.db')) as src, contextlib.closing(sqlite3.connect(destination / 'side.db')) as dst:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
    if (source / 'Assets').exists():
        shutil.copytree(source / 'Assets', destination / 'Assets', symlinks=False)
    with readonly(destination / 'boards.db') as db:
        check = db.execute('PRAGMA quick_check').fetchone()[0]
    if check != 'ok':
        raise ValueError(f'Snapshot integrity check failed: {check}')
    manifest = {'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
                'database_sha256':hashlib.sha256((destination/'boards.db').read_bytes()).hexdigest(),
                'note':'Database is a consistent SQLite backup; close Freeform for stable asset files.'}
    (destination/'snapshot.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    return destination

def read_boards(store):
    boards = []
    with readonly(pathlib.Path(store)/'boards.db') as db:
        for bid, data in db.execute('SELECT board_identifier,data FROM boards WHERE tombstoned=0 ORDER BY hex(board_identifier)'):
            metadata = Document(data).root()
            board = {'id':uid(bid),'title':get(metadata,'c') or 'Untitled','items':[],'warnings':[]}
            for iid,parent,typ,common,specific in db.execute('SELECT item_uuid,parent_uuid,item_type,common_data,specific_data FROM board_items WHERE board_identifier=? AND tombstoned=0 ORDER BY hex(item_uuid)',(bid,)):
                item = {'id':uid(iid),'parent':uid(parent) if parent else None,'type':typ,'kind':KINDS.get(typ,'unknown')}
                try:
                    co=Document(common).root() if common else {}
                    spdoc=Document(specific) if specific else None
                    sp=spdoc.root() if spdoc else {}
                    item.update(common=co,specific=sp)
                    affinity=get(co,'b');general=get(affinity,0) if typ in (3,4,11,12) else affinity
                    geom=get(general,1);pos=point(get(geom,0));size=point(get(geom,1))
                    if pos is not None and size is not None:
                        item['geometry']={'x':pos[0],'y':pos[1],'width':size[0],'height':size[1],
                                          'rotation':get(geom,2) or 0,'flipH':bool(get(geom,5)),'flipV':bool(get(geom,6))}
                    if typ in (3,12):
                        shape=sp if typ==3 else get(sp,'a')
                        item['text']=get(shape,'b','a','attributed_text')
                        item['text_preset']=get(shape,'d') or {}
                        item['text_style']=get(shape,'c') or {}
                        item['text_attributes']=[]
                        for raw in get(shape,'b','a','attribute_runs_raw') or []:
                            from .coherence import wire, first
                            run=wire(bytes.fromhex(raw));attrs={}
                            for a in run[2]:
                                a=wire(a);attrs[spdoc.strings[first(a,1,0)]]=spdoc.value(first(a,2,b''))
                            item['text_attributes'].append({'length':first(run,1,0),'attributes':attrs})
                    if typ==4:item['connection']={'from':get(sp,'b'),'to':get(sp,'c')}
                except (ValueError,IndexError,KeyError,TypeError) as exc:
                    item['error']=str(exc)
                    board['warnings'].append({'item':item['id'],'message':'Decode failed: '+str(exc)})
                board['items'].append(item)
            boards.append(board)
    return boards
