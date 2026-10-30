const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = 'app/static/js/nutrition.js';
const digits = text => text.replace(/[.,  ]/g,'');
const tick = async () => { for (let n=0;n<60;n++) await Promise.resolve(); };
class Element {
  constructor(id='') { this.id=id; this.children=[]; this.dataset={}; this.listeners={}; this.style={}; this.disabled=false; this.hidden=false; this.value=''; this._text=''; this._html=''; this.classList={toggle(){},add(){},remove(){}}; }
  set textContent(value) { this._text=String(value); this._html=''; this.children=[]; }
  get textContent() { return this._text+this.children.map(item=>item.textContent||'').join(''); }
  set innerHTML(value) { this._html=String(value); this._text=''; this.children=[]; }
  get innerHTML() { return this._html || this._text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this._text=''; this._html=''; this.children=nodes; }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(key,handler) { this.listeners[key]=handler; }
  focus() { this.focused=true; }
  remove() {}
}
function setup(targets, {transportError=null, recipes=[], detail=null}={}) {
  const nodes=new Map(), reads=[], writes=[], events={};
  const node=id=>{ if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  const pills=['all','protein'].map(name=>{ const item=new Element(); item.dataset.pill=name; return item; });
  const endpoints={
    '/api/nutrition/recipes':{recipes},
    '/api/nutrition/supplements':{products:[],today:[]},
    '/api/nutrition/supplements/adherence?days=7':{active_products:0,rows:[]},
  };
  async function fetchJSON(url) {
    reads.push(url);
    if(url==='/api/nutrition/targets') {
      if(transportError) throw new Error(transportError);
      return {result:targets};
    }
    if(detail&&url.startsWith('/api/nutrition/recipe/')) return detail;
    assert.ok(Object.hasOwn(endpoints,url),`Unexpected read: ${url}`);
    return endpoints[url];
  }
  async function fetch(url,options) {
    writes.push({url,body:JSON.parse(options.body)});
    throw new Error('The Nutrition page must not write');
  }
  const context={
    document:{getElementById:node,querySelector:()=>({content:'csrf'}),querySelectorAll:selector=>selector==='#nPills .pill'?pills:[],createElement:()=>new Element()},
    window:{HermesCharts:{fetchJSON,pal:()=>({}),gaugeOpt:options=>({series:[{}],options}),lineOpt:()=>({series:[{}]})},HermesUI:{range:key=>key==='nutrition'?'Week':'All meals',onRange(){},onTab(){}},addEventListener:(name,handler)=>{events[name]=handler;}},
    echarts:{init:()=>({setOption(){},dispose(){},resize(){}})},fetch,
    location:{assign(){}},requestAnimationFrame:handler=>handler(),setTimeout:()=>0,console,
  };
  vm.runInNewContext(fs.readFileSync(source,'utf8'),context,{filename:source});
  return {node,nodes,reads,writes,events};
}
async function unavailableTargets(reason) {
  const h=setup({status:'insufficient_data',reason,targets:{water_ml:{target:3000}}});

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,false);
  assert.equal(h.node('nTgtReason').textContent,reason);
  assert.equal(h.node('nTgtReasonLabel').textContent,'Reason');
  assert.equal(h.node('nTgtReload').hidden,true);
  assert.equal(h.node('nTgtCal').textContent,'—');
  assert.equal(digits(h.node('nTgtWater').textContent),'3000 ml');
  assert.ok(!h.node('nTgtReason').innerHTML.includes('<script>'));
  assert.equal(h.reads.filter(url=>url==='/api/nutrition/targets').length,1);
  assert.deepEqual(h.writes,[]);
}
async function targetTransportError() {
  const h=setup(null,{transportError:'Fictional broker is unavailable'});

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,false);
  assert.equal(h.node('nTgtReasonLabel').textContent,'Request error');
  assert.equal(h.node('nTgtReason').textContent,'Fictional broker is unavailable');
  assert.equal(h.node('nTgtReload').hidden,false);
  assert.equal(h.node('nTgtWater').textContent,'—');
}
async function readyTargets() {
  const h=setup({status:'ok',phase:{phase:'maintain',set:true,started:'2026-01-01'},targets:{kcal:{target:2000,band_low:1800,band_high:2200},protein_g:{target:120},carbs_g:{target:200},fat_g:{target:70},water_ml:{target:3000},micros:[]}});

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,true);
  assert.equal(digits(h.node('nTgtCal').textContent),'2000 kcal · band 1800–2200');
  assert.equal(h.node('nTgtProtein').textContent,'120 g');
  assert.equal(digits(h.node('nTgtWater').textContent),'3000 ml');
  assert.equal(h.node('nTgtFibre').textContent,'target not configured');
  assert.equal(h.node('nPhaseMaintain')['aria-pressed'],'true');
  assert.ok(h.node('nPhaseCaption').textContent.includes('2026-01-01'));
  assert.deepEqual(h.writes,[]);
}
async function mealShowsShareOfGoal() {
  const h=setup({status:'ok',phase:{phase:'maintain',set:true,started:'2026-01-01'},targets:{kcal:{target:2000,band_low:1800,band_high:2200},protein_g:{target:100},carbs_g:{target:200},fat_g:{target:70},water_ml:{target:3000},micros:[]}},
    {recipes:[{recipe_id:'fictional-stew',name:'Fictional stew',grams_per_portion:400,meal_type:null}],
     detail:{kcal_per_portion:600,macros:[{name:'Protein',amount:50},{name:'Carbs',amount:40},{name:'Fat',amount:14}]}});

  await tick();

  const card=h.node('nMenu').children[0];
  assert.ok(card.innerHTML.includes('600 kcal · 30% of daily goal'));
  assert.ok(card.innerHTML.includes('50 g · 50% of daily goal'));
  assert.ok(!card.innerHTML.includes('freezer'));
  assert.deepEqual(h.writes,[]);
}
const scenarios={
  missing_profile:()=>unavailableTargets('user profile incomplete — profile-set height_cm/sex/dob'),
  missing_weight:()=>unavailableTargets('no weight_kg in body_metrics — log a weight first'),
  missing_configuration:()=>unavailableTargets('nutrient targets are not configured <script>fictional()</script>'),
  transport_error:targetTransportError,
  ready:readyTargets,
  meal_share_of_goal:mealShowsShareOfGoal,
};
const scenario=process.argv[2];
Promise.resolve().then(()=>scenarios[scenario]()).then(()=>console.log(`PASS ${scenario}`)).catch(error=>{console.error(error);process.exitCode=1;});
