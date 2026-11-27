"""Generate an artificial example, with no real Freeform content."""
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from freeform_to_drawio.drawio import cell, write_file
import xml.etree.ElementTree as E

page=E.Element('diagram',id='synthetic-example',name='Synthetic demo')
model=E.SubElement(page,'mxGraphModel',adaptiveColors='none',background='#ffffff',page='0')
root=E.SubElement(model,'root');E.SubElement(root,'mxCell',id='0');E.SubElement(root,'mxCell',id='1',parent='0')
cell(root,'source','Source','rounded=1;fillColor=#5ac4f6;strokeColor=#000000;fontColor=#000000;fontSize=18;',(40,60,140,60))
cell(root,'target','Destination','rounded=1;fillColor=#ef8c00;strokeColor=#000000;fontColor=#000000;fontSize=18;',(300,60,140,60))
edge=E.SubElement(root,'mxCell',id='link',edge='1',parent='1',source='source',target='target',style='endArrow=block;strokeColor=#000000;')
E.SubElement(edge,'mxGeometry',relative='1',attrib={'as':'geometry'})
cell(root,'label','Artificial example · Пример','text;html=0;fontColor=#000000;strokeColor=none;fillColor=none;fontSize=16;',(40,160,400,40))
write_file([page],pathlib.Path(__file__).with_name('demo.drawio'))
