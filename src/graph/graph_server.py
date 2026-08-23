"""Serve the typedness graph visualizer for the benchmark suite."""
import argparse
import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from benchmarking.utilities.load_source import CONFIG, ROOT

from .simple_graph_view import graph_data


PAGE = r'''<!doctype html><meta charset="utf-8"><title>Typedness graph</title>
<style>
html,body{margin:0;height:100%;overflow:hidden;background:#0d1117;color:#d7dee7;font:16px system-ui}
.bar{position:fixed;z-index:6;inset-inline:0;display:flex;align-items:center;gap:18px;padding:12px 18px;background:#171d25f5;border-bottom:1px solid #3a4655}#toolbar{top:0}#randomizer{top:55px}
label{display:flex;align-items:center;gap:7px}select,input,button{color:#d7dee7;background:#0d1117;border:1px solid #526070;border-radius:5px;padding:6px 8px;font:14px ui-monospace,monospace}button{cursor:pointer}button:hover{border-color:#8ba0b5}#mask{width:220px}#counts{margin-left:auto;color:#aab6c4}#error{color:#ff7b72}#proportion{padding:0;width:180px}#proportion-value{width:3em;color:#aab6c4}
#source{position:fixed;inset:0;overflow:auto;padding:155px 7vw 130px;font:20px/3.2 ui-monospace,monospace}.line{display:block;white-space:pre;min-height:30px;border-radius:5px}.line.hot{background:#263142}.no{display:inline-block;width:3.5em;color:#526070;user-select:none}.text{position:relative;z-index:3;background:#0d1117cc;pointer-events:none}
svg{position:fixed;inset:0;width:100%;height:100%;z-index:2;pointer-events:none}.edge{fill:none;stroke:#78889b;stroke-width:2;opacity:.28;marker-mid:url(#arrow)}.edge.hot{stroke:#ffd166;opacity:1;marker-mid:url(#arrow-hot)}body.selected .edge:not(.hot){opacity:.05}.cell{pointer-events:stroke;cursor:pointer;stroke-width:4;stroke-linecap:round}.type{stroke:#56b4ff}.context{stroke:#ff9561}
#info{position:fixed;z-index:5;bottom:12px;left:12px;white-space:pre-wrap;max-width:720px;background:#171d25ee;border:1px solid #3a4655;border-radius:8px;padding:10px}
</style>
<div id="toolbar" class="bar"><label>Benchmark <select id="benchmark"></select></label><label>Variant <select id="variant"><option>advanced</option><option>shallow</option><option>untyped</option></select></label><label><input id="functions" type="checkbox"> Function-level mask</label><label>Mask <input id="mask" value="0" inputmode="numeric" spellcheck="false"></label><span id="error"></span><span id="counts"></span></div>
<div id="randomizer" class="bar"><span>Random mask</span><label><input id="specify-proportion" type="checkbox"> Specify proportion</label><label id="proportion-wrap" hidden>Proportion <input id="proportion" type="range" min="0" max="100" value="50"><span id="proportion-value">50%</span></label><button id="generate">Generate</button></div>
<div id="source"></div><svg><defs><marker id="arrow" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#78889b"/></marker><marker id="arrow-hot" viewBox="0 0 10 10" refX="5" refY="5" markerUnits="userSpaceOnUse" markerWidth="9" markerHeight="9" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="#ffd166"/></marker></defs><g id="edges"></g><g id="cells"></g></svg><div id="info">Click a cell to inspect it. Blue is produced type; orange is demanded context.</div>
<script>
const source=document.querySelector('#source'),edges=document.querySelector('#edges'),cells=document.querySelector('#cells'),info=document.querySelector('#info'),selectBox=document.querySelector('#benchmark'),variantBox=document.querySelector('#variant'),maskBox=document.querySelector('#mask'),functionBox=document.querySelector('#functions'),counts=document.querySelector('#counts'),error=document.querySelector('#error'),proportionCheck=document.querySelector('#specify-proportion'),proportionWrap=document.querySelector('#proportion-wrap'),proportion=document.querySelector('#proportion'),proportionValue=document.querySelector('#proportion-value'),generate=document.querySelector('#generate'),NS='http://www.w3.org/2000/svg';let data=null,current=null,timer=null,generation=0;
const storageKey='typedness-graph-settings-v1';
function savedSettings(){let saved={};try{const stored=JSON.parse(localStorage.getItem(storageKey)||'{}');if(stored&&typeof stored==='object'&&!Array.isArray(stored))saved=stored}catch(e){}const query=new URLSearchParams(location.search);for(const key of ['benchmark','variant','functions','mask','proportionEnabled','proportion'])if(query.has(key))saved[key]=query.get(key);return saved}
function restoreSettings(saved,names){if(names.includes(saved.benchmark))selectBox.value=saved.benchmark;if([...variantBox.options].some(option=>option.value===saved.variant))variantBox.value=saved.variant;if(saved.functions!==undefined)functionBox.checked=saved.functions===true||saved.functions==='1';if(typeof saved.mask==='string')maskBox.value=saved.mask;if(saved.proportionEnabled!==undefined)proportionCheck.checked=saved.proportionEnabled===true||saved.proportionEnabled==='1';const amount=Number(saved.proportion);if(Number.isFinite(amount))proportion.value=Math.max(Number(proportion.min),Math.min(Number(proportion.max),amount));proportionWrap.hidden=!proportionCheck.checked;proportionValue.textContent=proportion.value+'%'}
function persistSettings(){const saved={benchmark:selectBox.value,variant:variantBox.value,functions:functionBox.checked?'1':'0',mask:maskBox.value,proportionEnabled:proportionCheck.checked?'1':'0',proportion:proportion.value};try{localStorage.setItem(storageKey,JSON.stringify(saved))}catch(e){}const query=new URLSearchParams(saved);history.replaceState(null,'',location.pathname+'?'+query+location.hash)}
function el(tag,attrs){const e=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))e.setAttribute(k,v);return e}
function position(n,w){const row=document.querySelector(`.line[data-line="${n.line}"]`),body=row.querySelector('.text'),r=body.getBoundingClientRect(),x1=r.left+n.col*w,x2=r.left+Math.max(n.col+1,n.endline===n.line?n.endcol:n.col+1)*w,center=(r.top+r.bottom)/2,gap=7*(n.lane||0);return{x:(x1+x2)/2,x1,x2,y:n.slot==='type'?center+15+gap:center-15-gap}}
function draw(){if(!data)return;edges.replaceChildren();cells.replaceChildren();const sample=source.querySelector('.text'),probe=document.createElement('canvas').getContext('2d');probe.font=getComputedStyle(sample).font;const width=probe.measureText('M').width,pos=new Map(data.nodes.map(n=>[n.id,position(n,width)]));for(const [i,e] of data.edges.entries()){const a=pos.get(e[0]),b=pos.get(e[1]);if(!a||!b)continue;const dx=b.x-a.x,dy=b.y-a.y,len=Math.hypot(dx,dy)||1,bend=Math.max(24,Math.min(100,len/3)),sign=dx<0?-1:1,mid={x:(a.x+b.x)/2,y:(a.y+b.y)/2};edges.append(el('path',{d:`M${a.x},${a.y} Q${a.x+bend*sign},${a.y} ${mid.x},${mid.y} Q${b.x-bend*sign},${b.y} ${b.x},${b.y}`,class:'edge','data-edge':i}))}for(const n of data.nodes){const p=pos.get(n.id);if(!p)continue;const line=el('line',{x1:p.x1,y1:p.y,x2:p.x2,y2:p.y,class:`cell ${n.slot}`});line.onclick=()=>inspect(n);cells.append(line)}if(current)inspect(current)}
function inspect(n){current=n;document.body.classList.add('selected');document.querySelectorAll('.hot').forEach(e=>e.classList.remove('hot'));document.querySelector(`.line[data-line="${n.line}"]`)?.classList.add('hot');for(const [i,e] of data.edges.entries())if(e.includes(n.id))document.querySelector(`[data-edge="${i}"]`)?.classList.add('hot');info.textContent=n.label+'\nvalue: '+(n.values.join(' | ')||'∅')+'\nblue = produced type, orange = demanded context'}
function render(next){data=next;current=null;document.body.classList.remove('selected');source.replaceChildren();for(const [i,text] of data.source.entries()){const row=document.createElement('span'),no=document.createElement('span'),body=document.createElement('span');row.className='line';row.dataset.line=i+1;no.className='no';no.textContent=i+1;body.className='text';body.textContent=text||' ';row.append(no,body);source.append(row)}for(const slot of ['type','context']){const lines=new Map;for(const n of data.nodes.filter(n=>n.slot===slot)){if(!lines.has(n.line))lines.set(n.line,[]);lines.get(n.line).push(n)}for(const group of lines.values()){const ends=[];group.sort((a,b)=>a.col-b.col||b.endcol-a.endcol);for(const n of group){const end=n.endline===n.line?n.endcol:Infinity;let lane=ends.findIndex(value=>n.col>=value);if(lane<0)lane=ends.length;n.lane=lane;ends[lane]=end}}}counts.textContent=`${data.annotation_units} annotation units · ${data.function_units} function units`;info.textContent='Click a cell to inspect it. Blue is produced type; orange is demanded context.';requestAnimationFrame(draw)}
async function load(){persistSettings();const request=++generation,bits=maskBox.value.trim();if(!/^[01]*$/.test(bits)){error.textContent='Mask must be binary';return}error.textContent='';const query=new URLSearchParams({benchmark:selectBox.value,variant:variantBox.value,functions:functionBox.checked?'1':'0',mask:bits||'0'});try{const response=await fetch('/graph?'+query),body=await response.json();if(request!==generation)return;if(!response.ok)throw new Error(body.error);render(body)}catch(e){if(request===generation)error.textContent=e.message}}
function schedule(){persistSettings();clearTimeout(timer);timer=setTimeout(load,180)}
function randomMask(){if(!data)return;const size=functionBox.checked?data.function_units:data.annotation_units,bits=Array(size).fill('0');if(proportionCheck.checked){const count=Math.round(size*Number(proportion.value)/100),indices=Array.from({length:size},(_,i)=>i);for(let i=indices.length-1;i>0;i--){const j=Math.floor(Math.random()*(i+1));[indices[i],indices[j]]=[indices[j],indices[i]]}for(const index of indices.slice(0,count))bits[index]='1'}else{for(let i=0;i<size;i++)bits[i]=Math.random()<.5?'1':'0'}maskBox.value=bits.join('')||'0';load()}
fetch('/benchmarks').then(r=>r.json()).then(names=>{for(const name of names)selectBox.add(new Option(name,name));restoreSettings(savedSettings(),names);load()});selectBox.onchange=load;variantBox.onchange=load;functionBox.onchange=load;maskBox.oninput=schedule;proportionCheck.onchange=()=>{proportionWrap.hidden=!proportionCheck.checked;persistSettings()};proportion.oninput=()=>{proportionValue.textContent=proportion.value+'%';persistSettings()};generate.onclick=randomMask;source.onscroll=draw;onresize=draw;
</script>'''


def benchmark_sources():
    with CONFIG.open() as stream:
        locations = json.load(stream)
    required = {"advanced", "shallow", "untyped"}
    return {name: {variant: ROOT / variants[variant]
                   for variant in required}
            for name, variants in locations.items()
            if required <= variants.keys()}


class GraphHandler(BaseHTTPRequestHandler):
    sources = benchmark_sources()

    def send(self, status, body, content_type="application/json"):
        payload = body if isinstance(body, bytes) else body.encode()
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        request = urlparse(self.path)
        if request.path == "/":
            return self.send(200, PAGE, "text/html")
        if request.path == "/benchmarks":
            return self.send(200, json.dumps(sorted(self.sources)))
        if request.path != "/graph":
            return self.send(404, json.dumps({"error": "not found"}))
        try:
            query = parse_qs(request.query)
            benchmark = query.get("benchmark", [""])[0]
            if benchmark not in self.sources:
                raise ValueError("unknown benchmark")
            variant = query.get("variant", [""])[0]
            if variant not in self.sources[benchmark]:
                raise ValueError("unknown variant")
            bits = query.get("mask", ["0"])[0]
            if not bits or any(bit not in "01" for bit in bits):
                raise ValueError("mask must be a binary string")
            mask = int(bits, 2)
            functions = query.get("functions", ["0"])[0] == "1"
            source = self.sources[benchmark][variant].read_text()
            result = graph_data(source, mask, functions)
            self.send(200, json.dumps(result))
        except Exception as error:
            self.send(400, json.dumps({"error": str(error)}))

    def log_message(self, format, *args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), GraphHandler)
    url = f"http://{args.host}:{server.server_port}"
    print(url)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
