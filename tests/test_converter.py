"""Synthetic data only. Never reads the developer's Freeform library."""
import base64
import contextlib
import io
import json
import pathlib
import sqlite3
import struct
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as E
import zlib

from freeform_to_drawio.cli import main
from freeform_to_drawio.coherence import Document, wire
from freeform_to_drawio.drawio import export_board, stencil
from freeform_to_drawio.store import snapshot

def vi(n):
    out=bytearray()
    while n>=128:out.append((n&127)|128);n>>=7
    out.append(n);return bytes(out)
def pb(k,v):return vi(k<<3|2)+vi(len(v))+v
def pv(k,v):return vi(k<<3)+vi(v)
def document_string(text):
    # Root register with string constant 0.
    return b'crdt\x06\0\0\0'+pb(1,pb(1,pb(2,pv(3,0))))+pb(6,pb(2,text.encode()))
def title_doc(title):
    # Root composite key c -> register(string 1).
    return b'crdt\x06\0\0\0'+pb(1,pb(4,pb(1,b'\0')+pb(2,pb(1,pb(2,pv(3,1))))))+pb(6,pb(2,b'c')+pb(2,title.encode()))
def point(x,y):return {'bytes':(b'\x0d'+struct.pack('<f',x)+b'\x15'+struct.pack('<f',y)).hex()}
def shape(id='shape',text=None,width=100,height=0):
    path=pb(1,b'')+pb(1,pv(1,1)+pb(2,struct.pack('<ff',width,height)))
    return {'id':id,'parent':None,'kind':'shape','type':3,
            'geometry':{'x':0,'y':0,'width':width,'height':height,'rotation':0},
            'common':{'b':{'1':{'6':{'0':{'bytes':path.hex()},'1':point(width,height)}}}},
            'specific':{'a':{'a':{'b':{'0':0,'1':{'1':{'0':0,'1':0,'2':0,'3':0}},'2':1}}}},
            'text':text,'text_preset':{},'text_style':{},'text_attributes':[]}

class ConverterTests(unittest.TestCase):
    def test_unicode_constant_and_truncated_wire(self):
        self.assertEqual(Document(document_string('Привет')).root(),'Привет')
        with self.assertRaises(ValueError):wire(b'\x0a\x04a')
        with self.assertRaises(ValueError):Document(b'crdt\x09\0\0\0')

    def test_zero_extent_line_is_not_dropped(self):
        board={'id':'board','title':'Synthetic','items':[shape()],'warnings':[]}
        diagram,report=export_board(board,pathlib.Path('/nonexistent'))
        self.assertEqual(report['counts']['paths'],1)
        self.assertEqual(report['counts'].get('failed',0),0)
        model=diagram.find('mxGraphModel');self.assertEqual(model.get('adaptiveColors'),'none')
        cell=diagram.find('.//mxCell[@id="shape"]');style=cell.get('style')
        encoded=style.split('stencil(',1)[1].split(')',1)[0]
        self.assertGreater(float(E.fromstring(zlib.decompress(base64.b64decode(encoded),-15)).get('h')),0)

    def test_multiline_html_is_escaped_and_explicit_black(self):
        item=shape(text='A & <B>\nВторая строка',width=100,height=50)
        board={'id':'board','title':'Synthetic','items':[item],'warnings':[]}
        diagram,report=export_board(board,pathlib.Path('/nonexistent'))
        cell=diagram.find('.//mxCell[@id="shape-text"]')
        self.assertEqual(cell.get('value'),'A &amp; &lt;B&gt;<br>Вторая строка')
        self.assertIn('fontColor=#000000;',cell.get('style'));self.assertIn('whiteSpace=nowrap;',cell.get('style'))

    def test_image_is_embedded_without_external_url(self):
        with tempfile.TemporaryDirectory() as td:
            store=pathlib.Path(td);(store/'Assets').mkdir();(store/'Assets/abc.png').write_bytes(b'synthetic-image')
            item=shape();item['kind']='image';item['geometry']['height']=50;item['specific']={'a':{'0':{'0':'abc'}}}
            diagram,report=export_board({'id':'b','title':'Images','items':[item],'warnings':[]},store)
            style=diagram.find('.//mxCell[@id="shape"]').get('style')
            self.assertIn('image=data:image/png,',style);self.assertNotIn('https:',style);self.assertEqual(report['counts']['images'],1)

    def test_snapshot_includes_uncheckpointed_wal_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);source=root/'source';source.mkdir();db=sqlite3.connect(source/'boards.db')
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA wal_autocheckpoint=0');db.execute('CREATE TABLE sample(value)');db.execute('INSERT INTO sample VALUES (42)');db.commit()
            before={p.name:p.read_bytes() for p in source.iterdir() if not p.name.endswith('-shm')}
            dest=snapshot(source,root/'copy')
            with sqlite3.connect(dest/'boards.db') as copied:self.assertEqual(copied.execute('SELECT value FROM sample').fetchone()[0],42)
            self.assertEqual(before,{p.name:p.read_bytes() for p in source.iterdir() if not p.name.endswith('-shm')})
            with self.assertRaises(ValueError):snapshot(source,source/'nested')
            db.close()

    def test_cli_duplicate_names_and_existing_output(self):
        with tempfile.TemporaryDirectory() as td:
            root=pathlib.Path(td);source=root/'source';source.mkdir()
            with sqlite3.connect(source/'boards.db') as db:
                db.execute('CREATE TABLE boards(board_identifier BLOB,data BLOB,tombstoned INTEGER)')
                db.execute('CREATE TABLE board_items(item_uuid BLOB,parent_uuid BLOB,board_identifier BLOB,item_type INTEGER,common_data BLOB,specific_data BLOB,tombstoned INTEGER)')
                for n in (1,2):db.execute('INSERT INTO boards VALUES(?,?,0)',(uuid.UUID(int=n).bytes,title_doc('Same / title')))
            out=root/'export'
            with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(main(['--source',str(source),'--output',str(out)]),0)
            files=list((out/'diagrams').glob('*.drawio'));self.assertEqual(len(files),2)
            self.assertEqual(len(E.parse(out/'all-boards.drawio').findall('diagram')),2)
            before=(out/'all-boards.drawio').read_bytes()
            with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(['--source',str(source),'--output',str(out)]),1)
            self.assertEqual(before,(out/'all-boards.drawio').read_bytes())

if __name__=='__main__':unittest.main()
