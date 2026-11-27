"""Command line entry point. Conversion always operates on a working snapshot."""
import argparse
import collections
import datetime
import html
import json
import pathlib
import re
import sys
import sqlite3

from .drawio import export_board, write_file
from .store import DEFAULT_STORE, read_boards, snapshot

def safe_name(title):
    name=re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', title).strip(' .')[:100]
    return name or 'Untitled'

def main(argv=None):
    parser=argparse.ArgumentParser(description='Copy an Apple Freeform SQLite store and export editable draw.io diagrams locally.')
    parser.add_argument('--source',type=pathlib.Path,default=DEFAULT_STORE,help='Directory containing boards.db and Assets (default: local macOS Freeform store)')
    parser.add_argument('--output',type=pathlib.Path,default=pathlib.Path('exports'),help='New output directory; existing directories are never overwritten')
    parser.add_argument('--board',help='Export one board by exact UUID or title')
    parser.add_argument('--list',action='store_true',help='List boards from the snapshot without exporting')
    args=parser.parse_args(argv)
    try:
        source=args.source.expanduser().resolve();out=args.output.expanduser().resolve()
        if not (source/'boards.db').is_file():raise ValueError('boards.db not found in '+str(source))
        if source==out or source in out.parents or out in source.parents:raise ValueError('Output and source must not overlap')
        out.mkdir(parents=True,exist_ok=False)
        copied=snapshot(source,out/'snapshot')
        boards=read_boards(copied)
        if args.board:
            boards=[b for b in boards if b['id']==args.board or b['title']==args.board]
            if not boards:raise ValueError('No board matched --board')
        if args.list:
            for b in boards:print(b['id'],b['title'],len(b['items']),sep='\t')
            return 0
        diagrams=[];reports=[];files=out/'diagrams';files.mkdir()
        for board in boards:
            diagram,report=export_board(board,copied)
            filename=f"{safe_name(board['title'])}--{board['id']}.drawio"
            write_file([diagram],files/filename);diagrams.append(diagram);report['file']='diagrams/'+filename;reports.append(report)
            print(f"{board['title']}: {sum(report['counts'].get(k,0) for k in ('paths','images','texts'))} objects; {len(report['warnings'])} warnings")
        write_file(diagrams,out/'all-boards.drawio')
        totals=collections.Counter()
        for r in reports:totals.update(r['counts'])
        report={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'boards':reports,'totals':dict(totals),
                'limitations':['Experimental private Apple format; verify visual results.', 'Groups flattened; layer order approximated.', 'Connectors preserve editable contours, without automatic routing.', 'Mixed text formatting reduced to dominant values.', 'Parametric paths, crops, masks, drawing and ink may require manual repair.']}
        (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        rows=[]
        for r in reports:
            messages=''.join('<li>'+html.escape(w['item'][:8]+': '+w['message'])+'</li>' for w in r['warnings'])
            rows.append('<tr><td><a href="'+html.escape(r['file'],quote=True)+'">'+html.escape(r['title'])+'</a></td><td>'+str(r['source_items'])+'</td><td>'+str(len(r['warnings']))+'</td><td><details><summary>Details</summary><ul>'+messages+'</ul></details></td></tr>')
        (out/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>Freeform export</title><style>body{font:16px system-ui;max-width:1200px;margin:40px auto;padding:20px}td,th{padding:10px;border-bottom:1px solid #ddd;text-align:left}</style><h1>Freeform → draw.io</h1><p><a href="all-boards.drawio">All boards in one file</a> · <a href="report.json">Full report</a></p><p>Experimental export: review warnings and compare with Freeform.</p><table><tr><th>Board</th><th>Source items</th><th>Warnings</th><th>Details</th></tr>'+''.join(rows)+'</table>',encoding='utf-8')
        print(f'Exported {len(reports)} boards to {out}\nTotals: {dict(totals)}')
        return 0
    except (OSError,ValueError,sqlite3.Error) as exc:
        print('Error: '+str(exc),file=sys.stderr)
        return 1
