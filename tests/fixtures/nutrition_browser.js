const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = 'app/static/js/nutrition.js';
const digits = text => text.replace(/[.,  ]/g,'');
const tick = async () => { for (let n=0;n<200;n++) await Promise.resolve(); };
class Element {
  constructor(id='') { this.id=id; this.children=[]; this.dataset={}; this.listeners={}; this.style={setProperty(){}}; this.disabled=false; this.hidden=false; this.value=''; this._text=''; this._html=''; this.actButtons=[]; this.classList={toggle(){},add(){},remove(){}}; }
  set textContent(value) { this._text=String(value); this._html=''; this.children=[]; }
  get textContent() { return this._text+this.children.map(item=>item.textContent||'').join(''); }
  set innerHTML(value) { this._html=String(value); this._text=''; this.children=[]; }
  get innerHTML() { return this._html || this._text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.children.push(node); return node; }
  replaceChildren(...nodes) { this._text=''; this._html=''; this.children=nodes; }
  setAttribute(key,value) { this[key]=value; }
  addEventListener(key,handler) { this.listeners[key]=handler; }
  // Meal cards expose their action buttons so a test can click them.
  querySelectorAll(selector) {
    if (selector==='[data-act]') {
      if (this._buttonsFor!==this._html) {
        this._buttonsFor=this._html;
        this.actButtons=[...this._html.matchAll(/data-act="(\w+)"/g)].map(match=>{ const b=new Element(); b.dataset.act=match[1]; return b; });
      }
      return this.actButtons;
    }
    return [];
  }
  focus() { this.focused=true; }
  scrollIntoView() {}
  reset() {}
  remove() {}
}
const OK_TARGETS = {status:'ok',phase:{phase:'maintain',set:true,started:'2026-01-01'},targets:{kcal:{target:2000,band_low:1800,band_high:2200},protein_g:{target:120},carbs_g:{target:200},fat_g:{target:70},water_ml:{target:3000},micros:[{nutrient:'iron',target:8,unit:'mg'},{nutrient:'fibre',target:null,unit:'g'}]}};
function setup(targets, {transportError=null, recipes=[], detail=null, profile=null}={}) {
  const nodes=new Map(), reads=[], writes=[], events={};
  const node=id=>{ if(!nodes.has(id)) nodes.set(id,new Element(id)); return nodes.get(id); };
  const pills=['all','protein'].map(name=>{ const item=new Element(); item.dataset.pill=name; return item; });
  const endpoints={
    '/api/profile':profile||{ok:true,per_user_overlay_supported:true,catalog:{micro_target_names:['iron','zinc']}},
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
    return {status:200,ok:true,json:async()=>({ok:true})};
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
  assert.equal(h.node('nGoalEditBtn').hidden,true);
  assert.equal(h.node('nTgtWater').textContent.replace(',','.'),'3.0 L');
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
  const h=setup(OK_TARGETS);

  await tick();

  assert.equal(h.node('nTgtBanner').hidden,true);
  assert.equal(digits(h.node('nTgtCal').textContent),'2000');
  assert.equal(h.node('nTgtProtein').textContent,'120 g');
  assert.ok(digits(h.node('nTgtBand').textContent).includes('Maintenance band 1800–2200'));
  assert.equal(h.node('nPhaseMaintain')['aria-pressed'],'true');
  assert.ok(h.node('nPhaseCaption').textContent.includes('2026-01-01'));
  assert.equal(h.node('nGoalEditBtn').hidden,false);
  assert.equal(h.node('nMicroEditBtn').hidden,false);
  assert.equal(h.node('nMicroChips').children[0].textContent,'Iron · 8 mg');
  assert.equal(h.node('nMicroChips').children.length,1);  // a micro without a target is not shown
  assert.ok(h.node('nPhaseHelp').textContent.includes('maintenance'));
  assert.ok(h.node('nRing')['aria-label'].includes('protein'));
  assert.deepEqual(h.writes,[]);
}
async function ownerCannotEditMicros() {
  const h=setup(OK_TARGETS,{profile:{ok:true,per_user_overlay_supported:false,catalog:{micro_target_names:[]}}});

  await tick();

  assert.equal(h.node('nMicroEditBtn').hidden,true);
  assert.ok(h.node('nMicroNote').textContent.includes("can't be edited"));
}
async function goalsAreEditable() {
  const h=setup(OK_TARGETS);
  await tick();

  await h.node('nGoalEditBtn').listeners.click();
  assert.equal(h.node('nGoalForm').hidden,false);
  assert.equal(h.node('nGoalKcal').value,2000);
  h.node('nGoalKcal').value='2200';
  h.node('nGoalProtein').value='120';
  await h.node('nGoalForm').listeners.submit({preventDefault(){}});
  await tick();

  assert.deepEqual(h.writes,[{url:'/api/profile/targets',body:{name:'kcal',target:2200}}]);
  assert.equal(h.node('nGoalForm').hidden,true);
  assert.equal(h.reads.filter(url=>url==='/api/nutrition/targets').length,2);

  h.writes.length=0;
  await h.node('nGoalEditBtn').listeners.click();
  h.node('nGoalKcal').value='100';
  await h.node('nGoalForm').listeners.submit({preventDefault(){}});
  assert.ok(h.node('nGoalStatus').textContent.includes('between 500 and 10000'));
  assert.deepEqual(h.writes,[]);
}
async function phaseIsSwitchable() {
  const h=setup(OK_TARGETS);
  await tick();

  const button={dataset:{phase:'cut'},getAttribute:()=>'false'};
  await h.node('nPhaseSeg').listeners.click({target:{closest:()=>button}});
  await tick();

  assert.deepEqual(h.writes,[{url:'/api/profile/phase',body:{phase:'cut'}}]);
}
async function microTargetsAreEditable() {
  const h=setup(OK_TARGETS);
  await tick();

  await h.node('nMicroEditBtn').listeners.click();
  assert.equal(h.node('nMicroForm').hidden,false);
  assert.equal(h.node('nMicroRows').children.length,1);
  h.node('nMicroRows').children[0].querySelector=()=>({value:'10'});
  await h.node('nMicroForm').listeners.submit({preventDefault(){}});
  await tick();

  assert.deepEqual(h.writes,[{url:'/api/profile/nutrition-config',body:{nutrient_targets:{iron:10}}}]);
}
const STEW={recipe_id:'fictional-stew',name:'Fictional stew',grams_per_portion:400,meal_type:'dinner'};
const STEW_DETAIL={recipe:{recipe_id:'fictional-stew',name:'Fictional stew',batch_grams:2000,grams_per_portion:400,portions:5},
  kcal_per_portion:600,macros:[{name:'Protein',amount:50},{name:'Carbs',amount:40},{name:'Fat',amount:14}],
  micronutrients:[{name:'Iron',amount:4,unit:'mg'}],ingredients:[{name:'oats',amount:60,unit:'g'}]};
function natInputs(h) {
  const inputs=['kcal','protein_g','carbs_g','fat_g','fiber_g','vitamin_d_ug','magnesium_mg','iron_mg','zinc_mg'].map(key=>{ const el=new Element(); el.dataset.nut=key; return el; });
  h.node('nMealForm').querySelectorAll=selector=>selector==='input[data-nut]'?inputs:[];
  return inputs;
}
async function mealShowsShareOfGoal() {
  const h=setup(OK_TARGETS,{recipes:[STEW],detail:STEW_DETAIL});

  await tick();

  const html=h.node('nMenu').children[0].innerHTML;
  assert.ok(html.includes('600'));
  assert.ok(html.includes('30% of daily goal'));
  assert.ok(html.includes('data-w="'));
  assert.ok(html.includes('Iron 4 mg'));
  assert.ok(!html.includes('style="'));
  assert.ok(!html.includes('freezer'));
  assert.deepEqual(h.writes,[]);
}
async function mealCanBeCreated() {
  const h=setup(OK_TARGETS);
  await tick();
  const inputs=natInputs(h);

  h.node('nMealNew').listeners.click();
  assert.equal(h.node('nMealForm').hidden,false);
  h.node('mf-name').value='Fictional bowl';
  h.node('mf-type').value='breakfast';
  h.node('mf-gpp').value='300';
  h.node('mf-portions').value='2';
  h.node('mf-ingredients').value='60 g oats\n\n250 ml milk';
  const set=(key,value)=>{ inputs.find(el=>el.dataset.nut===key).value=value; };

  // calories, protein, carbs and fat are required for a new meal
  set('kcal','450');
  await h.node('nMealForm').listeners.submit({preventDefault(){}});
  assert.ok(h.node('mf-status').textContent.includes('required'));
  assert.deepEqual(h.writes,[]);

  set('protein_g','20'); set('carbs_g','60'); set('fat_g','12'); set('iron_mg','3.5');
  await h.node('nMealForm').listeners.submit({preventDefault(){}});
  await tick();

  assert.deepEqual(h.writes,[{url:'/api/nutrition/meal',body:{
    name:'Fictional bowl',meal_type:'breakfast',grams_per_portion:300,portions:2,
    per_portion:{kcal:450,protein_g:20,carbs_g:60,fat_g:12,iron_mg:3.5},
    ingredients:['60 g oats','250 ml milk']}}]);
  assert.equal(h.node('nMealForm').hidden,true);
}
async function editSendsOnlyChangedFields() {
  const h=setup(OK_TARGETS,{recipes:[STEW],detail:STEW_DETAIL});
  await tick();
  const inputs=natInputs(h);

  h.node('nMenu').children[0].querySelectorAll('[data-act]');
  h.node('nMenu').children[0].actButtons.find(b=>b.dataset.act==='edit').listeners.click();
  assert.equal(h.node('nMealForm').hidden,false);
  assert.equal(h.node('mf-name').value,'Fictional stew');
  assert.equal(h.node('mf-portions').value,5);
  assert.equal(inputs.find(el=>el.dataset.nut==='protein_g').value,50);
  assert.equal(inputs.find(el=>el.dataset.nut==='iron_mg').value,4);
  assert.equal(inputs.find(el=>el.dataset.nut==='zinc_mg').value,'');
  assert.equal(h.node('mf-ingredients').value,'60 g oats');

  inputs.find(el=>el.dataset.nut==='protein_g').value='55';
  await h.node('nMealForm').listeners.submit({preventDefault(){}});
  await tick();

  // only the changed nutrient is sent, and the ingredients are left alone
  assert.deepEqual(h.writes,[{url:'/api/nutrition/meal',body:{
    recipe_id:'fictional-stew',name:'Fictional stew',meal_type:'dinner',grams_per_portion:400,portions:5,
    per_portion:{protein_g:55}}}]);
}
async function deleteNeedsConfirmation() {
  const h=setup(OK_TARGETS,{recipes:[STEW],detail:STEW_DETAIL});
  await tick();

  const card=()=>{ const c=h.node('nMenu').children[0]; c.querySelectorAll('[data-act]'); return c; };
  card().actButtons.find(b=>b.dataset.act==='delete').listeners.click();
  assert.deepEqual(h.writes,[]);
  assert.ok(card().innerHTML.includes('Delete this meal?'));

  await card().actButtons.find(b=>b.dataset.act==='confirm').listeners.click();
  await tick();

  assert.deepEqual(h.writes,[{url:'/api/nutrition/meal/fictional-stew/delete',body:{}}]);
}
const scenarios={
  missing_profile:()=>unavailableTargets('user profile incomplete — profile-set height_cm/sex/dob'),
  missing_weight:()=>unavailableTargets('no weight_kg in body_metrics — log a weight first'),
  missing_configuration:()=>unavailableTargets('nutrient targets are not configured <script>fictional()</script>'),
  transport_error:targetTransportError,
  ready:readyTargets,
  owner_micros_read_only:ownerCannotEditMicros,
  goals_editable:goalsAreEditable,
  phase_switch:phaseIsSwitchable,
  micro_targets_editable:microTargetsAreEditable,
  meal_share_of_goal:mealShowsShareOfGoal,
  meal_create:mealCanBeCreated,
  meal_edit_changed_fields:editSendsOnlyChangedFields,
  meal_delete_confirms:deleteNeedsConfirmation,
};
const scenario=process.argv[2];
Promise.resolve().then(()=>scenarios[scenario]()).then(()=>console.log(`PASS ${scenario}`)).catch(error=>{console.error(error);process.exitCode=1;});
