"""Synthetic data only. Never reads the developer's Freeform library."""
import base64
import contextlib
import io
import json
import math
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
from freeform_to_drawio.drawio import export_board, stencil, colored_text, drawing_order
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

    def test_native_rotation_turns_down_right_not_up_right(self):
        # Native 315 degrees must become draw.io +45 degrees. Both use a
        # centered frame, so a 100pt horizontal line then runs down/right.
        item=shape();item['geometry']['rotation']=315
        diagram,_=export_board({'id':'b','title':'Rotation','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        c=diagram.find('.//mxCell[@id="shape"]')
        style=dict(part.split('=',1) for part in c.get('style').split(';') if '=' in part)
        angle=math.radians(float(style['rotation']))
        self.assertAlmostEqual(math.cos(angle)*100,math.sqrt(5000))
        self.assertAlmostEqual(math.sin(angle)*100,math.sqrt(5000))
        item['geometry']['rotation']=270
        diagram,_=export_board({'id':'b','title':'Rotation','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        c=diagram.find('.//mxCell[@id="shape"]')
        style=dict(part.split('=',1) for part in c.get('style').split(';') if '=' in part)
        self.assertAlmostEqual(math.sin(math.radians(float(style['rotation']))),1)

    def test_head_and_tail_arrowheads_follow_opposite_tangents(self):
        item=shape()
        triangle=pb(1,b'')+pb(1,pv(1,1)+pb(2,struct.pack('<ff',3,6)))+pb(1,pv(1,1)+pb(2,struct.pack('<ff',6,0)))+pb(1,pv(1,3))
        desc={'0':{'bytes':triangle.hex()},'2':point(3,0),'4':'simple arrow'}
        item['specific']['a']['a'].update(c=desc,d=desc)
        diagram,report=export_board({'id':'b','title':'Arrows','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        c=diagram.find('.//mxCell[@id="shape"]');encoded=c.get('style').split('stencil(',1)[1].split(')',1)[0]
        paths=E.fromstring(zlib.decompress(base64.b64decode(encoded),-15)).findall('./foreground/path')
        self.assertEqual(report['counts']['arrowheads'],2);self.assertEqual(len(paths),3)
        head=[(float(n.get('x')),float(n.get('y'))) for n in paths[1] if 'x' in n.attrib]
        tail=[(float(n.get('x')),float(n.get('y'))) for n in paths[2] if 'x' in n.attrib]
        # Tip at endpoint; the head base is to its left, tail base to its right.
        self.assertEqual(max(x for x,y in head),100)
        self.assertEqual(min(x for x,y in tail),0)
        self.assertAlmostEqual(min(x for x,y in head),94)
        self.assertAlmostEqual(max(x for x,y in tail),6)
        self.assertEqual(len(E.fromstring(zlib.decompress(base64.b64decode(encoded),-15)).findall('./foreground/fillstroke')),2)
        # A filled node must not hide an arrow that intentionally ends inside it.
        node=shape(id='node',width=80,height=80)
        node['specific']['a']['a']['a']={'0':0,'1':{'1':{'0':0,'1':.5,'2':.5,'3':1}}}
        diagram,_=export_board({'id':'b','title':'Layers','items':[item,node],'warnings':[]},pathlib.Path('/nonexistent'))
        ids=[c.get('id') for c in diagram.findall('.//mxCell')]
        self.assertLess(ids.index('node'),ids.index('shape'))
        item['specific']['a']['a']['c']={'4':'none'}
        _,report=export_board({'id':'b','title':'Tail only','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        self.assertEqual(report['counts']['arrowheads'],1)
        self.assertEqual(report['warnings'],[])

    def test_unicode_paragraph_separators_become_visible_line_breaks(self):
        item=shape(text='First\u2028Second\u2029Third',width=100,height=50)
        diagram,_=export_board({'id':'b','title':'Text','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        self.assertEqual(diagram.find('.//mxCell[@id="shape-text"]').get('value'),'First<br>Second<br>Third')

    def test_dotted_stencil_keeps_relative_pattern_and_round_caps(self):
        item=shape()
        stroke=item['specific']['a']['a']['b']
        stroke.update({'2':3,'3':1,'6':{'0':[.001,2.0],'1':0.0}})
        diagram,report=export_board({'id':'b','title':'Dots','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        cell=diagram.find('.//mxCell[@id="shape"]')
        encoded=cell.get('style').split('stencil(',1)[1].split(')',1)[0]
        foreground=E.fromstring(zlib.decompress(base64.b64decode(encoded),-15)).find('foreground')
        self.assertEqual(foreground.find('dashpattern').get('pattern'),'0.001 2')
        self.assertEqual(foreground.find('dashed').get('dashed'),'1')
        self.assertEqual(foreground.find('linecap').get('cap'),'round')
        self.assertIn('strokeWidth=3;',cell.get('style'))
        self.assertEqual(report['counts']['dashed_paths'],1)

    def test_dash_does_not_apply_to_arrowhead(self):
        node=E.fromstring(zlib.decompress(base64.b64decode(stencil(
            [('move',0,0),('line',100,0)],(100,1),'none',
            [[('move',94,-3),('line',100,0),('line',94,3),('close',)]],
            '#000000',{'dash':[1,1],'cap':'flat','join':'miter','miter':4})), -15))
        dashed=[n.get('dashed') for n in node.findall('./foreground/dashed')]
        self.assertEqual(dashed,['1','0'])

    def test_text_colors_preserve_utf16_runs_and_html_escaping(self):
        def paint(r,g,b):return {'0':0,'1':{'1':{'0':0,'1':r,'2':g,'3':b}}}
        item=shape(text='A😀B<&',width=100,height=50)
        purple=paint(120/255,44/255,246/255);orange=paint(239/255,140/255,0)
        item['text_attributes']=[{'length':3,'attributes':{'characterFill':purple}},
                                 {'length':3,'attributes':{'characterFill':orange}}]
        value,fallback,colored=colored_text(item)
        self.assertEqual(value,'<span style="color:#782cf6;">A😀</span><span style="color:#ef8c00;">B&lt;&amp;</span>')
        self.assertEqual(fallback,'#000000');self.assertTrue(colored)
        diagram,report=export_board({'id':'b','title':'Color','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        self.assertEqual(diagram.find('.//mxCell[@id="shape-text"]').get('value'),value)
        self.assertEqual(report['counts']['colored_texts'],1)
        item['text_attributes'][0]['length']=2
        with self.assertRaisesRegex(ValueError,'surrogate pair'):colored_text(item)

    def test_preset_color_and_run_opacity(self):
        item=shape(text='Plain',width=100,height=50)
        item['text_preset']['6']={'0':0,'1':{'1':{'0':0,'1':1,'2':.5,'3':0}}}
        value,fallback,colored=colored_text(item)
        self.assertEqual(value,'Plain');self.assertEqual(fallback,'#ff8000');self.assertTrue(colored)
        item['text_attributes']=[{'length':5,'attributes':{'characterFill':{'0':0,'1':{'0':.5,'1':{'0':0,'1':0,'2':0,'3':0}}}}}]
        self.assertIn('opacity:0.5;',colored_text(item)[0])

    def test_ordered_set_tree_order_and_persistent_tombstone(self):
        a,b=uuid.UUID(int=1),uuid.UUID(int=2)
        constants=pb(1,uuid.UUID(int=99).bytes+a.bytes+b.bytes)
        doc=Document(b'crdt\x06\0\0\0'+pb(6,constants))
        entries=pb(4,pb(1,pv(6,1)))+pb(4,pb(1,pv(6,2)))
        # The sequence tree orders the third segment before the second.
        # The middle segment has no added stamp, and is elided in SQLite.
        root=pb(4,bytes([3,1,2]))
        tombstone=pv(2,7)
        first=pv(2,1)+pb(3,pv(2,1))
        second=pv(2,1)+pb(3,pv(2,2))
        sequence=b''.join(pb(3,v) for v in (root,tombstone,first,second))+pb(4,b'')
        ordered=pb(1,entries)+pb(2,sequence)+pb(3,pv(2,1))+pb(3,pv(2,2))
        self.assertEqual(doc.ordered_values(ordered),[str(b),str(a)])

    def test_ordered_set_resolves_duplicate_timestamps(self):
        a,b=uuid.UUID(int=1),uuid.UUID(int=2)
        doc=Document(b'crdt\x06\0\0\0'+pb(6,pb(1,uuid.UUID(int=99).bytes+a.bytes+b.bytes)))
        entries=b''.join(pb(4,pb(1,pv(6,k))) for k in (1,2,1))
        sequence=pb(3,pv(2,3)+pb(3,pv(2,1)))
        ordered=pb(1,entries)+pb(2,sequence)+b''.join(pb(3,pv(2,n)) for n in (1,2,3))
        self.assertEqual(doc.ordered_values(ordered),[str(b),str(a)])

    def test_ordered_set_version_vector_removes_visible_content(self):
        a,b=uuid.UUID(int=1),uuid.UUID(int=2)
        doc=Document(b'crdt\x06\0\0\0'+pb(6,pb(1,uuid.UUID(int=99).bytes+a.bytes+b.bytes)))
        entries=b''.join(pb(4,pb(1,pv(6,k))) for k in (1,2))
        removed=pb(1,b'\0')+pb(2,pb(2,vi(0)+vi(2)))
        sequence=pb(3,pb(4,b'\x01\x02'))+pb(3,pv(2,1)+pb(3,pv(2,1))+pb(5,removed))+pb(3,pv(2,1)+pb(3,pv(2,2)))
        ordered=pb(1,entries)+pb(2,sequence)+pb(3,pv(2,1))+pb(3,pv(2,2))
        self.assertEqual(doc.ordered_values(ordered),[str(b)])

    def test_native_group_order_wins_over_item_type_sorting(self):
        root={'id':'root','kind':'container','parent':None,'child_ids':['image','nested']}
        group={'id':'nested','kind':'container','parent':'root','child_ids':['overlay','label']}
        image={'id':'image','kind':'image','parent':'root'}
        overlay={'id':'overlay','kind':'shape','parent':'nested'}
        label={'id':'label','kind':'shape','parent':'nested'}
        ordered,mode,warnings=drawing_order({'root_container':'root','items':[label,group,overlay,image,root]},lambda i:i['id'])
        self.assertEqual([i['id'] for i in ordered],['root','image','nested','overlay','label'])
        self.assertEqual(mode,'native');self.assertEqual(warnings,[])
        group['child_ids']=['overlay']
        _,mode,warnings=drawing_order({'root_container':'root','items':[label,group,overlay,image,root]},lambda i:i['id'])
        self.assertEqual(mode,'partial');self.assertTrue(warnings)

    def test_fill_and_stroke_opacity_are_independent(self):
        item=shape(width=100,height=50)
        native=item['specific']['a']['a']
        native['a']={'0':0,'1':{'0':.25,'1':{'0':0,'1':1,'2':0,'3':0}}}
        native['b']['1']['0']=.5
        diagram,report=export_board({'id':'b','title':'Opacity','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        style=diagram.find('.//mxCell[@id="shape"]').get('style')
        self.assertIn('fillOpacity=25;',style);self.assertIn('strokeOpacity=50;',style)
        self.assertEqual(report['counts']['transparent_fills'],1)
        self.assertEqual(report['counts']['transparent_strokes'],1)
        native['a']['1']['0']=0
        diagram,_=export_board({'id':'b','title':'Invisible fill','items':[item],'warnings':[]},pathlib.Path('/nonexistent'))
        self.assertIn('fillOpacity=0;',diagram.find('.//mxCell[@id="shape"]').get('style'))

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
