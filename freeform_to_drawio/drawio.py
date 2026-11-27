"""Editable native paths, text, and embedded images in draw.io XML."""
import base64, collections, html, json, math, mimetypes, pathlib, struct, subprocess, tempfile, zlib
import xml.etree.ElementTree as E
from .coherence import get, point, wire, first
from . import __version__
def pair(b):
 return (0,0) if not b else struct.unpack('<ff' if len(b)==8 else '<ee',b)
def commands(source):
 bez=get(source,6) or get(source,8,1)
 if not bez:return [],None
 raw=get(bez,0,'bytes');natural=point(get(bez,1))
 result=[]
 for v in wire(bytes.fromhex(raw))[1]:
  d=wire(v);kind=first(d,1,0);p=pair(first(d,2,b''))
  if kind==2:
   c1=pair(first(d,3,b''));c2=pair(first(d,4,b''));result.append(('curve',p[0]+c1[0],p[1]+c1[1],p[0]+c2[0],p[1]+c2[1],*p))
  elif kind in (0,1):result.append(('move' if kind==0 else 'line',*p))
  elif kind==3:result.append(('close',))
  else:raise ValueError(kind)
 return result,natural
def color(c):
 rgb=get(c,1)
 if rgb:return '#'+''.join(f'{round(max(0,min(1,get(rgb,k) or 0))*255):02x}' for k in (1,2,3))
 mono=get(c,3,0)
 if mono is not None:return '#'+f'{round(mono*255):02x}'*3
 return '#000000'
def style(i):
 n=get(i,'specific','a','a','a') if i['kind']=='sticky' else get(i,'specific','a','a');n=n or {};fill=get(n,'a');stroke=get(n,'b')
 return color(get(fill,1)) if fill else 'none',color(get(stroke,1)) if stroke else 'none',get(stroke,2) or 1

def stroke_options(item):
    node=get(item,'specific','a','a','a') if item['kind']=='sticky' else get(item,'specific','a','a')
    stroke=get(node,'b') or {}
    dash=get(stroke,6,0) or []
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in dash):
        raise ValueError('Invalid native dash pattern')
    if dash and not any(dash):raise ValueError('Empty native dash cycle')
    return {'dash':dash,'phase':get(stroke,6,1) or 0,
            'cap':{0:'flat',1:'round',2:'square'}.get(get(stroke,3),'flat'),
            'join':{0:'miter',1:'round',2:'bevel'}.get(get(stroke,4),'miter'),
            'miter':get(stroke,5) or 4}


def paint_color(paint):
    """Return RGB/gray color and alpha for a CRLFillData color alternative."""
    native=get(paint,1)
    return color(native),get(native,0) if get(native,0) is not None else 1.0


def escaped_text(text):
    return html.escape(text).replace('\r\n','\n').replace('\r','\n').replace('\u2028','\n').replace('\u2029','\n').replace('\n','<br>')


def colored_text(item):
    """Keep per-run color; attribute ranges use UTF-16, not Python indices."""
    default=paint_color(get(item,'text_preset',6))
    encoded=item['text'].encode('utf-16-le');offset=0;parts=[];colors=[]
    def append(text,paint):
        value=escaped_text(text);hexcolor,alpha=paint
        if not math.isfinite(alpha) or not 0<=alpha<=1:raise ValueError('Invalid text opacity')
        if paint!=default or alpha!=1:
            css=f'color:{hexcolor};'
            if alpha!=1:css+=f'opacity:{alpha:g};'
            value=f'<span style="{css}">{value}</span>'
        parts.append(value)
        if text:colors.append(paint)
    runs=item.get('text_attributes',[])
    for run in runs:
        length=run['length']
        if not isinstance(length,int) or length<0:raise ValueError('Invalid UTF-16 run length')
        end=offset+length*2
        if end>len(encoded):raise ValueError('Text run exceeds UTF-16 string')
        try:text=encoded[offset:end].decode('utf-16-le')
        except UnicodeDecodeError as exc:raise ValueError('Text run splits a UTF-16 surrogate pair') from exc
        fill=run['attributes'].get('characterFill')
        append(text,paint_color(fill) if fill is not None else default)
        offset=end
    if offset<len(encoded):append(encoded[offset:].decode('utf-16-le'),default)
    # Set the cell's fallback color too, so a uniformly colored label remains
    # colored when edited. Individual spans preserve mixed-color labels.
    fontcolor=colors[0][0] if colors and len(set(colors))==1 else default[0]
    return ''.join(parts),fontcolor,any(c!='#000000' for c,a in colors)


def native_path(source):
    """Also handle editable Béziers; approximate parametric outlines explicitly."""
    cmd,natural=commands(source)
    if cmd:return cmd,natural,None
    editable=get(source,9)
    if editable:
        cmd=[]
        for sub in get(editable,1) or []:
            nodes=get(sub,0) or []
            if not nodes:continue
            start=point(get(nodes[0],1));cmd.append(('move',*start))
            pairs=list(zip(nodes,nodes[1:]))
            if get(sub,1):pairs.append((nodes[-1],nodes[0]))
            for a,b in pairs:
                p=point(get(b,1));out=point(get(a,2));inc=point(get(b,0))
                cmd.append(('line',*p) if out==point(get(a,1)) and inc==p else ('curve',*out,*inc,*p))
            if get(sub,1):cmd.append(('close',))
        return cmd,point(get(editable,2)),None
    for key,sizekey in ((4,2),(5,2),(7,4)):
        value=get(source,key)
        if value:
            size=point(get(value,sizekey))
            if size:
                w,h=size
                return [('move',0,0),('line',w,0),('line',w,h),('line',0,h),('close',)],size,'Parametric shape approximated by bounding rectangle'
    return [],None,'Unsupported or absent path source'

def smooth_connection(cmd,source):
    if get(source,8,0)!=0 or len(cmd)<3 or not all(c[0] in ('move','line') for c in cmd):return cmd
    pts=[c[1:] for c in cmd];out=[('move',*pts[0])];start=pts[0]
    for j,control in enumerate(pts[1:-1],1):
        end=pts[-1] if j==len(pts)-2 else tuple((a+b)/2 for a,b in zip(control,pts[j+1]))
        c1=tuple(a+2*(b-a)/3 for a,b in zip(start,control));c2=tuple(a+2*(b-a)/3 for a,b in zip(end,control))
        out.append(('curve',*c1,*c2,*end));start=end
    return out

def text_metrics(item,natural):
    preset=item.get('text_preset',{});runs=item.get('text_attributes',[])
    attrs=collections.Counter()
    for r in runs:
        for k,v in r['attributes'].items():
            if isinstance(v,(str,int,float)):attrs[(k,v)]+=max(r['length'],1)
    def attribute(key,default):
        values=[(n,v) for (k,v),n in attrs.items() if k==key]
        return max(values,key=lambda x:x[0])[1] if values else default
    fs=attribute('fontSize',get(preset,1) or 18)
    bold=attribute('bold',bool(get(preset,2)));italic=attribute('italic',bool(get(preset,3)))
    underline=attribute('underline',get(preset,4) or 0);strike=attribute('strikethrough',get(preset,5) or 0)
    # Freeform caches a text outline even when automatic layout stores zero frame size.
    if not any(k=='fontSize' for k,v in attrs) and natural and item['geometry']['width']==0:
        lines=max(len(item.get('text','').splitlines()),1)
        fs=max(5,(natural[1]-8)/(1.17*lines))
    align={0:'left',1:'right',2:'center',3:'left',4:'left'}.get(attribute('paragraphAlignment',get(preset,7)), 'center')
    fontstyle=int(bool(bold))+2*int(bool(italic))+4*int(bool(underline))+8*int(bool(strike))
    return float(fs),fontstyle,align

def stencil(cmd,natural,fill,markers=(),marker_color=None,stroke_settings=None):
    w,h=natural;s=E.Element('shape',w=str(w),h=str(h),aspect='variable',strokewidth='inherit')
    E.SubElement(s,'background');fg=E.SubElement(s,'foreground')
    if stroke_settings:
        E.SubElement(fg,'linecap',cap=stroke_settings['cap'])
        E.SubElement(fg,'linejoin',join=stroke_settings['join'])
        E.SubElement(fg,'miterlimit',limit=str(stroke_settings['miter']))
        if stroke_settings['dash']:
            E.SubElement(fg,'dashpattern',pattern=' '.join(f'{v:g}' for v in stroke_settings['dash']))
            E.SubElement(fg,'dashed',dashed='1')
    p=E.SubElement(fg,'path')
    names={'move':['x','y'],'line':['x','y'],'curve':['x1','y1','x2','y2','x3','y3'],'close':[]}
    for c in cmd:E.SubElement(p,c[0],dict(zip(names[c[0]],map(str,c[1:]))))
    E.SubElement(fg,'fillstroke' if fill!='none' else 'stroke')
    for marker in markers:
        E.SubElement(fg,'save')
        E.SubElement(fg,'dashed',dashed='0')
        E.SubElement(fg,'fillcolor',color=marker_color or '#000000')
        E.SubElement(fg,'strokecolor',color=marker_color or '#000000')
        p=E.SubElement(fg,'path')
        for c in marker:E.SubElement(p,c[0],dict(zip(names[c[0]],map(str,c[1:]))))
        E.SubElement(fg,'fillstroke')
        E.SubElement(fg,'restore')
    co=zlib.compressobj(wbits=-15)
    return base64.b64encode(co.compress(E.tostring(s))+co.flush()).decode()

def path_endpoints(cmd):
    """Return endpoint positions and outward unit tangents for an open path."""
    segments=[];current=None
    for command in cmd:
        if command[0]=='move':current=command[1:]
        elif command[0] in ('line','curve') and current is not None:
            end=command[-2:]
            controls=[command[1:3],command[3:5]] if command[0]=='curve' else []
            segments.append((current,end,controls));current=end
    if not segments:return None
    start,end,controls=segments[0]
    following=next((p for p in controls+[end] if math.dist(start,p)>1e-9),None)
    before,last,controls=segments[-1]
    previous=next((p for p in list(reversed(controls))+[before] if math.dist(last,p)>1e-9),None)
    if following is None or previous is None:return None
    def unit(a,b):
        length=math.dist(a,b)
        return ((b[0]-a[0])/length,(b[1]-a[1])/length)
    return (start,unit(following,start)),(last,unit(previous,last))


def line_markers(item,cmd,natural,frame):
    """Place separately archived arrowhead paths along physical endpoint tangents.

    Native line-end paths point along +Y; their coding key 2 is the base anchor.
    Their furthest +Y coordinate is the tip. Geometry scale is cancelled so that
    line-end dimensions remain in points, independently of the body frame.
    """
    style_node=get(item,'specific','a','a','a') if item['kind']=='sticky' else get(item,'specific','a','a')
    ends=path_endpoints(cmd)
    if not ends:return [],[]
    sx=frame[0]/natural[0];sy=frame[1]/natural[1]
    result=[];warnings=[]
    for key,index in (('c',1),('d',0)):
        descriptor=get(style_node,key)
        if not descriptor or get(descriptor,4)=='none':continue
        raw=get(descriptor,0,'bytes')
        if not raw:
            warnings.append('Line end has no supported archived path')
            continue
        marker,_=commands({'6':{'0':{'bytes':raw},'1':{'bytes':''}}})
        if not marker:continue
        anchor=point(get(descriptor,2)) or [0,0]
        coordinates=[c[k:k+2] for c in marker for k in range(1,len(c),2)]
        tip_y=max(p[1] for p in coordinates)
        endpoint,tangent=ends[index]
        dx,dy=tangent[0]*sx,tangent[1]*sy;length=math.hypot(dx,dy)
        if length<1e-9:continue
        ux,uy=dx/length,dy/length;nx,ny=-uy,ux
        transformed=[]
        for c in marker:
            values=[]
            for k in range(1,len(c),2):
                across=c[k]-anchor[0];along=c[k+1]-tip_y
                values.extend((endpoint[0]+(nx*across+ux*along)/sx,
                               endpoint[1]+(ny*across+uy*along)/sy))
            transformed.append((c[0],*values))
        result.append(transformed)
    return result,warnings


def cell(root,id,value,sty,box):
    x,y,w,h=box
    if not all(math.isfinite(v) for v in box) or w<=0 or h<=0:raise ValueError('Invalid output geometry')
    c=E.SubElement(root,'mxCell',id=id,value=value,style=sty,vertex='1',parent='1')
    E.SubElement(c,'mxGeometry',x=str(x),y=str(y),width=str(w),height=str(h),attrib={'as':'geometry'})
    return c

def assets_index(store):
    return {p.stem.lower():p for p in (pathlib.Path(store)/'Assets').glob('*') if p.is_file()}

def image_data(path):
    mime=mimetypes.guess_type(path.name)[0]
    if mime in ('image/png','image/jpeg','image/gif','image/svg+xml','image/webp'):
        data=path.read_bytes()
    elif path.suffix.lower() in ('.tiff','.tif','.heic'):
        with tempfile.TemporaryDirectory(prefix='freeform-image-') as temp:
            dest=pathlib.Path(temp)/'image.png'
            subprocess.run(['/usr/bin/sips','-s','format','png',str(path),'--out',str(dest)],check=True,capture_output=True)
            data=dest.read_bytes();mime='image/png'
    else:raise ValueError('Unsupported image format: '+path.suffix)
    return 'data:'+mime+','+base64.b64encode(data).decode()

def export_board(board,store):
    diagram=E.Element('diagram',name=board['title'],id=board['id'])
    model=E.SubElement(diagram,'mxGraphModel',grid='1',gridSize='10',page='0',adaptiveColors='none',background='#ffffff')
    root=E.SubElement(model,'root');E.SubElement(root,'mxCell',id='0');E.SubElement(root,'mxCell',id='1',parent='0')
    warnings=list(board['warnings']);stats=collections.Counter();assets=assets_index(store)
    items={i['id']:i for i in board['items']}
    def warn(i,msg):warnings.append({'item':i['id'],'message':msg})
    def ancestors(item):
        chain=[];seen={item['id']};parent=item['parent']
        while parent and parent in items:
            if parent in seen:raise ValueError('Cyclic group ancestry')
            seen.add(parent);p=items[parent]
            if p['parent'] is not None:chain.append(p)
            parent=p['parent']
        return chain
    def world(item,x,y):
        angle=-item['geometry'].get('rotation',0)
        for group in ancestors(item):
            g=group.get('geometry')
            if not g:continue
            # Native group coordinates are affine local coordinates; unit group frames
            # do not provide bounds. Preserve translation and rotation, flatten hierarchy.
            theta=math.radians(-g.get('rotation',0));x,y=x*math.cos(theta)-y*math.sin(theta)+g['x'],x*math.sin(theta)+y*math.cos(theta)+g['y'];angle-=g.get('rotation',0)
        return x,y,angle
    def layer(i):
        if i.get('text') and i.get('geometry',{}).get('width')==0:return 3
        # Decorated line endpoints can deliberately lie inside filled nodes.
        # Draw their body and marker above fills instead of hiding arrow tips.
        style_node=get(i,'specific','a','a','a') if i['kind']=='sticky' else get(i,'specific','a','a')
        if get(style_node,'c') or get(style_node,'d'):return 2.5
        if i['kind']=='connector':return 1
        if i['kind']=='image':return 2
        return 2 if style(i)[0]!='none' else 0
    for item in sorted(board['items'],key=layer):
        if item['kind']=='container':stats['containers_flattened']+=1;continue
        try:
            if item.get('error'):raise ValueError(item['error'])
            g=item.get('geometry')
            if not g:raise ValueError('Missing geometry')
            x,y,rotation=world(item,g['x'],g['y']);w,h=g['width'],g['height']
            transform=f'rotation={rotation};'
            if g.get('flipH'):transform+='flipH=1;'
            if g.get('flipV'):transform+='flipV=1;'
            if item['kind']=='image':
                aid=get(item,'specific','a',0,0);p=assets.get(str(aid).lower())
                if not p:raise ValueError('Image asset not found: '+str(aid))
                data=image_data(p)
                cell(root,item['id'],'',f'shape=image;image={data};imageAspect=0;strokeColor=none;fillColor=none;'+transform,(x,y,w,h));stats['images']+=1
                mask=get(item,'specific','c')
                if mask:warn(item,'Image crop/mask may differ; source image embedded in frame')
                continue
            if item['kind'] not in ('shape','sticky','connector','ink'):
                cell(root,item['id'],f"[{item['kind']} - unsupported]",'rounded=1;fillColor=#fff2cc;strokeColor=#d6b656;fontColor=#000000;', (x,y,max(w,120),max(h,40)))
                stats['placeholders']+=1;warn(item,'Unsupported item retained as labelled placeholder');continue
            source=get(item,'common','b',1);cmd,natural,notice=native_path(source)
            if notice:warn(item,notice)
            if not cmd or not natural:
                if not item.get('text'):raise ValueError('No supported path or text')
                natural=[max(30,len(item['text'])*10),30]
            if item['kind']=='connector':cmd=smooth_connection(cmd,source)
            zero=w==0 and h==0
            if zero:
                w,h=natural;x-=w/2;y-=h/2
            # A straight native line legitimately has a zero extent on one axis.
            # Give its stencil a one-point box and center that axis, without dropping it.
            if not zero and (w==0 or h==0 or natural[0]==0 or natural[1]==0):
                zw=natural[0]==0;zh=natural[1]==0
                adjusted=[]
                for c in cmd:
                    values=list(c[1:])
                    for k in range(len(values)):
                        if (k%2==0 and zw) or (k%2==1 and zh):values[k]+=.5
                    adjusted.append((c[0],*values))
                cmd=adjusted;natural=[max(natural[0],1),max(natural[1],1)]
                if w==0:x-=.5;w=1
                if h==0:y-=.5;h=1
            if (w<=0 or h<=0) and item.get('text'):
                size=get(item,'text_preset',1) or 18
                w=max(30,max(map(len,item['text'].splitlines()),default=1)*size*.55+8)
                h=max(1,len(item['text'].splitlines()))*size*1.17+8
                natural=[w,h];x=g['x']-w/2;y=g['y']-h/2
                x,y,rotation=world(item,x,y)
                warn(item,'Missing cached text bounds; estimated from font size')
            if w<=0 or h<=0:raise ValueError('Nonpositive frame')
            fill,stroke,width=style(item)
            if cmd and not zero:
                pathtransform=transform
                if get(source,0):pathtransform+='flipH=1;'
                if get(source,1):pathtransform+='flipV=1;'
                markers,marker_warnings=line_markers(item,cmd,natural,(w,h))
                for message in marker_warnings:warn(item,message)
                if markers:stats['arrowheads']+=len(markers)
                stroke_settings=stroke_options(item)
                if stroke_settings['dash']:stats['dashed_paths']+=1
                if stroke_settings['phase']:warn(item,'Nonzero dash phase is not supported')
                sty=f'shape=stencil({stencil(cmd,natural,fill,markers,stroke,stroke_settings)});fillColor={fill};strokeColor={stroke};strokeWidth={width};'+pathtransform
                cell(root,item['id'],'',sty,(x,y,w,h));stats['paths']+=1
            if zero and item.get('text') == '':
                cell(root,item['id'],'','text;fillColor=none;strokeColor=none;fontColor=#000000;',(x,y,w,h))
                stats['empty_labels']+=1
            if item.get('text'):
                size,fs,align=text_metrics(item,natural)
                if not zero and get(item,'text_style','c'):
                    # Approximate shrink-to-fit only; exact Apple font metrics are unavailable.
                    inset=get(item,'text_style','a') or 0
                    longest=max(map(len,item['text'].splitlines()),default=1)
                    size=min(size,max(5,(w-2*inset)/(max(longest,1)*.55)),max(5,(h-2*inset)/(max(len(item['text'].splitlines()),1)*1.2)))
                # HTML preserves explicit line breaks without rewrapping standalone labels.
                try:val,fontcolor,is_colored=colored_text(item)
                except ValueError as exc:
                    warn(item,str(exc));val=escaped_text(item['text']);fontcolor='#000000';is_colored=False
                if is_colored:stats['colored_texts']+=1
                sty=f'text;html=1;whiteSpace=nowrap;overflow=visible;strokeColor=none;fillColor=none;fontColor={fontcolor};fontFamily=Helvetica;fontSize={size};fontStyle={fs};align={align};verticalAlign=middle;spacing=0;'+transform
                cell(root,item['id']+'-text',val,sty,(x,y,w,h));stats['texts']+=1
        except (ValueError,IndexError,KeyError,TypeError,struct.error,subprocess.SubprocessError,OSError) as exc:
            stats['failed']+=1;warn(item,str(exc))
    # Shift all cells into a positive canvas, preserving relative positions.
    geometries=root.findall('./mxCell/mxGeometry')
    dx=40-min((float(g.get('x')) for g in geometries),default=0)
    dy=40-min((float(g.get('y')) for g in geometries),default=0)
    for g in geometries:g.set('x',str(float(g.get('x'))+dx));g.set('y',str(float(g.get('y'))+dy))
    return diagram,{'id':board['id'],'title':board['title'],'source_items':len(board['items']),'counts':dict(stats),'warnings':warnings}

def write_file(diagrams,path):
    root=E.Element('mxfile',host='freeform-to-drawio',version=__version__)
    for d in diagrams:root.append(d)
    E.indent(root);E.ElementTree(root).write(path,encoding='utf-8',xml_declaration=True)
