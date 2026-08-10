"""Render simple_type_graph as a dependency-free interactive HTML canvas."""
import argparse
import ast
import json
import webbrowser
from pathlib import Path

from get_ast_data import get_ast_data
from simple_type_graph import build_binding_graph

HTML = r'''<!doctype html><meta charset="utf-8"><title>Type/context source graph</title>
<style>html,body{margin:0;height:100%;overflow:hidden;background:#0d1117;color:#d7dee7;font:18px system-ui}#source{position:fixed;inset:0;overflow:auto;padding:90px 7vw 130px;font:20px/3.2 ui-monospace,monospace}.line{display:block;white-space:pre;min-height:30px;border-radius:5px}.line.hot{background:#263142}.no{display:inline-block;width:3.5em;color:#526070;user-select:none}.text{position:relative;z-index:3;background:#0d1117cc;pointer-events:none}svg{position:fixed;inset:0;width:100%;height:100%;z-index:2;pointer-events:none}.edge{fill:none;stroke:#78889b;stroke-width:2;opacity:.28;marker-mid:url(#arrow)}.edge.hot{stroke:#ffd166;stroke-width:2;opacity:1;marker-mid:url(#arrow-hot)}body.selected .edge:not(.hot){opacity:.05}.cell{pointer-events:stroke;cursor:pointer;stroke-width:4;stroke-linecap:round}.type{stroke:#56b4ff}.context{stroke:#ff9561}#help,#info{position:fixed;z-index:5;background:#171d25ee;border:1px solid #3a4655;border-radius:8px;padding:10px}#help{top:12px;left:12px}#info{bottom:12px;left:12px;white-space:pre-wrap;max-width:720px}</style>
<div id="source"></div><svg><defs><marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#78889b"/></marker><marker id="arrow-hot" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#ffd166"/></marker></defs><g id="edges"></g><g id="cells"></g></svg><div id="help">Edges are drawn directly over their source locations<br><b style="color:#56b4ff">type underline</b> · <b style="color:#ff9561">context overline</b> · click a cell</div><div id="info">Click a cell to inspect its value and dependencies.</div>
<script>const data=__DATA__,source=document.querySelector('#source'),edges=document.querySelector('#edges'),cells=document.querySelector('#cells'),info=document.querySelector('#info'),NS='http://www.w3.org/2000/svg',byId=new Map(data.nodes.map(n=>[n.id,n]));
for(const slot of ['type','context']){const lines=new Map;for(const n of data.nodes.filter(n=>n.slot===slot)){if(!lines.has(n.line))lines.set(n.line,[]);lines.get(n.line).push(n)}for(const group of lines.values()){const ends=[];group.sort((a,b)=>a.col-b.col||b.endcol-a.endcol);for(const n of group){const end=n.endline===n.line?n.endcol:Infinity;let lane=ends.findIndex(value=>n.col>=value);if(lane<0)lane=ends.length;n.lane=lane;ends[lane]=end}}}
data.source.forEach((text,i)=>{const row=document.createElement('span');row.className='line';row.dataset.line=i+1;const no=document.createElement('span');no.className='no';no.textContent=i+1;const body=document.createElement('span');body.className='text';body.textContent=text||' ';row.append(no,body);source.append(row)});
function el(tag,attrs){const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e}function position(n){const row=document.querySelector(`.line[data-line="${n.line}"]`),body=row.querySelector('.text'),r=body.getBoundingClientRect(),style=getComputedStyle(body),probe=document.createElement('canvas').getContext('2d');probe.font=style.font;const w=probe.measureText('M').width,x1=r.left+n.col*w,x2=r.left+Math.max(n.col+1,n.endline===n.line?n.endcol:n.col+1)*w;const center=(r.top+r.bottom)/2,gap=7*(n.lane||0);return{x:(x1+x2)/2,x1,x2,y:n.slot==='type'?center+15+gap:center-15-gap}}
function draw(){edges.replaceChildren();cells.replaceChildren();const pos=new Map(data.nodes.map(n=>[n.id,position(n)]));for(const [i,e] of data.edges.entries()){const a=pos.get(e[0]),b=pos.get(e[1]),dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy)||1,end=b,bend=Math.max(24,Math.min(100,len/3)),sign=dx<0?-1:1;const mid={x:(a.x+end.x)/2,y:(a.y+end.y)/2},path=el('path',{d:`M${a.x},${a.y} Q${a.x+bend*sign},${a.y} ${mid.x},${mid.y} Q${end.x-bend*sign},${end.y} ${end.x},${end.y}`,class:'edge','data-edge':i});edges.append(path)}for(const n of data.nodes){const p=pos.get(n.id),line=el('line',{x1:p.x1,y1:p.y,x2:p.x2,y2:p.y,class:`cell ${n.slot}`,'data-id':n.id});line.onclick=()=>select(n);cells.append(line)}}
function select(n){document.body.classList.add('selected');document.querySelectorAll('.hot').forEach(e=>e.classList.remove('hot'));document.querySelector(`.line[data-line="${n.line}"]`)?.classList.add('hot');for(const [i,e] of data.edges.entries())if(e.includes(n.id))document.querySelector(`[data-edge="${i}"]`)?.classList.add('hot');info.textContent=n.label+'\nvalue: '+(n.values.join(' | ')||'∅')+'\nblue = produced type, orange = demanded context'}source.onscroll=draw;onresize=draw;requestAnimationFrame(draw);</script>'''


def graph_data(source):
    bound = get_ast_data(ast.parse(source))
    graph = build_binding_graph(bound)
    cells = {
        cell
        for edge in graph.edges
        for cell in (edge.source, edge.target)
    }
    def anchor(cell):
        node = cell[0]
        return (getattr(node, "annotation", None)
                or getattr(node, "returns", None) or node)

    def label(cell):
        node = anchor(cell)
        text = ast.unparse(node).replace("\n", " ")[:48]
        return f"{getattr(node, 'lineno', 0)}: {text} · {cell[1]}"

    ordered = sorted(cells, key=lambda cell: (
        getattr(anchor(cell), "lineno", 0), cell[1], label(cell)))
    ids = {cell: index for index, cell in enumerate(ordered)}

    def value(cell):
        table = bound.types if cell[1] == "type" else bound.type_contexts
        return table.get(cell[0])
    nodes = [{"id": ids[cell], "slot": cell[1],
              "line": getattr(anchor(cell), "lineno", 0),
              "col": getattr(anchor(cell), "col_offset", 0),
              "endline": getattr(anchor(cell), "end_lineno", 0),
              "endcol": getattr(anchor(cell), "end_col_offset", 0),
              "label": label(cell),
              "values": [str(value(cell))] if value(cell) is not None else []}
             for cell in ordered]
    edges = [[ids[edge.source], ids[edge.target]] for edge in graph.edges]
    return {"nodes": nodes, "edges": edges, "source": source.splitlines()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("-o", "--output", default="type-graph.html")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    data = json.dumps(graph_data(Path(args.source).read_text())).replace("<", "\\u003c")
    output = Path(args.output).resolve()
    output.write_text(HTML.replace("__DATA__", data))
    print(output)
    if args.open:
        webbrowser.open(output.as_uri())


if __name__ == "__main__":
    main()
