(function(){
  "use strict";
  const catalog=window.EVALCALL_STYLE_CATALOG;
  const atlas=window.AESTHETIC_ATLAS;
  const blueprintLibrary=window.COMPOSITION_BLUEPRINTS;
  if(!catalog||!atlas)return;
  const KEY="evalcall-style-selection-v1";
  const SUITES=[
    {id:"linear",name:"Linear",english:"PRODUCT SYSTEM",description:"紧凑、精密、低噪声的产品工作台。",type:"Inter / SF Pro",spacing:"8px grid",radius:"6–8px",density:"Compact",sources:["Linear","Dub","Vanta"],fallback:0},
    {id:"incident",name:"Incident.io",english:"STATUS SYSTEM",description:"强标题、宽留白、状态优先的事件系统。",type:"Condensed Sans",spacing:"12px rhythm",radius:"0–10px",density:"Editorial",sources:["Incident","Pirsch","Dovetail","Officevibe"],fallback:1},
    {id:"retool",name:"Retool",english:"OPERATIONS SYSTEM",description:"高密度、工程化、可执行的控制工作台。",type:"Avenir / SF Mono",spacing:"4px grid",radius:"4–6px",density:"Dense",sources:["Retool","Voiceflow","LaunchDarkly","Adaptive ML"],fallback:2}
  ];
  const COLOR_KEY="evalcall-style-colors-v1";
  const COLORS=[{id:"signal",name:"信号黄",value:"#ffd100",ink:"#2a2200"},{id:"ink",name:"深海墨",value:"#111a2c",ink:"#ffffff"},{id:"critical",name:"阻断红",value:"#f05b64",ink:"#ffffff"},{id:"order",name:"秩序蓝",value:"#2c62a0",ink:"#ffffff"},{id:"healthy",name:"健康绿",value:"#13775f",ink:"#ffffff"},{id:"violet",name:"系统紫",value:"#7356d8",ink:"#ffffff"}];
  const COLORABLE=new Set(["brand-mark","execute-button","truth-badge","step-index","flow-progress","flow-input-node","flow-process-node","flow-output-node","flow-pulse","status-line","metric-card","chip","gate-card","toast","evidence-trigger"]);
  const $=(selector,root=document)=>root.querySelector(selector);
  const esc=value=>String(value==null?"":value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
  const atlasMap=Object.fromEntries(atlas.elements.map(element=>[element.id,element]));
  const stored=(()=>{try{return JSON.parse(localStorage.getItem(KEY)||"{}")}catch{return {}}})();
  const storedColors=(()=>{try{return JSON.parse(localStorage.getItem(COLOR_KEY)||"{}")}catch{return {}}})();
  const selection={};
  const colorSelection={};
  catalog.components.forEach(component=>{
    const element=atlasMap[component.atlasElement];
    const saved=stored[component.id];
    selection[component.id]=element&&element.variants.some(variant=>variant.id===saved)?saved:(element&&element.variants[0]?element.variants[0].id:null);
    colorSelection[component.id]=COLORABLE.has(component.id)&&COLORS.some(color=>color.id===storedColors[component.id])?storedColors[component.id]:null;
  });
  let filter="all",query="",toastTimer=0,editingComponent=null,editingPreviewCategory=null;
  const previewSuite={};
  const BLUEPRINT_KEY="design-composition-blueprint-v1";
  const blueprintExamples=blueprintLibrary&&Array.isArray(blueprintLibrary.examples)?blueprintLibrary.examples:[];
  let activeBlueprint=(()=>{try{const saved=JSON.parse(localStorage.getItem(BLUEPRINT_KEY)||"null");return saved&&!blueprintValidation(saved).length?saved:structuredClone(blueprintExamples[0])}catch{return structuredClone(blueprintExamples[0])}})();

  function categoryCounts(){
    return Object.fromEntries(catalog.categories.map(category=>[category.id,catalog.components.filter(component=>component.category===category.id).length]));
  }
  function selectedCount(){return Object.values(selection).filter(Boolean).length}
  function selectedVariant(component){
    const element=atlasMap[component.atlasElement];
    return element&&element.variants.find(variant=>variant.id===selection[component.id]);
  }
  function suiteVariant(component,suite){
    const element=atlasMap[component.atlasElement];
    if(!element)return null;
    for(const source of suite.sources){const matched=element.variants.find(variant=>variant.source===source);if(matched)return matched}
    return element.variants[suite.fallback]||element.variants[0]||null;
  }
  function suitePlan(categoryId,suiteId){
    const suite=SUITES.find(item=>item.id===suiteId);
    return Object.fromEntries(catalog.components.filter(component=>component.category===categoryId).map(component=>[component.id,suiteVariant(component,suite)?.id||null]));
  }
  function activeSuite(categoryId){
    if(catalog.components.some(component=>component.category===categoryId&&colorSelection[component.id]))return null;
    return SUITES.find(suite=>{const plan=suitePlan(categoryId,suite.id);return Object.entries(plan).every(([componentId,variantId])=>selection[componentId]===variantId)})?.id||null;
  }
  async function configHash(){
    const bytes=new TextEncoder().encode(JSON.stringify({selection,colorSelection,activeBlueprint}));
    const hash=await crypto.subtle.digest("SHA-256",bytes);
    return Array.from(new Uint8Array(hash)).map(byte=>byte.toString(16).padStart(2,"0")).join("").slice(0,12);
  }
  function toast(message){
    const node=$("#ecsToast");node.textContent=message;node.classList.add("show");clearTimeout(toastTimer);toastTimer=setTimeout(()=>node.classList.remove("show"),1800);
  }
  function header(){
    const total=catalog.components.length,types=total*3,suites=catalog.categories.length*SUITES.length;
    return `<header class="ecs-topbar"><a class="ecs-brand" href="./app.html"><span class="ecs-mark">E</span><span><b>EvalCall 子元素选型台</b><span>DESIGN CONFIGURATOR</span></span></a><div class="ecs-top-actions"><a class="ecs-link" href="./design-library.html">通用审美图鉴</a><a class="ecs-link" href="./app.html">返回工作台</a><button class="ecs-reset" id="clearSelection">清空勾选</button><button class="ecs-export" id="exportTop">导出方案</button></div></header>
    <section class="ecs-hero"><i class="ecs-signature"></i><div class="ecs-hero-inner"><div><span class="ecs-eyebrow">EvalCall interface decomposition / v3</span><h1>先定页面关系，再决定每个细节。</h1><p>设计从页面编排层开始：先选择输入、输出与流程树如何组成一页，再沿用 Linear、Incident.io、Retool 的设计 DNA，逐层微调 ${total} 个可映射子元素。</p></div><div class="ecs-scoreboard"><div class="ecs-stat"><b>${total}</b><span>可配置子元素</span></div><div class="ecs-stat"><b>${types}</b><span>候选呈现类型</span></div><div class="ecs-stat"><b>${suites}</b><span>来源系统组合</span></div><div class="ecs-stat"><b>1:1</b><span>CSS 选择器映射</span></div></div></div></section>`;
  }
  function toolbar(){
    return `<div class="ecs-toolbar-wrap"><div class="ecs-toolbar"><label class="ecs-search"><span>⌕</span><input id="componentSearch" placeholder="搜索子元素、选择器或所在页面"><kbd>⌘ K</kbd></label><div class="ecs-category-filter"><button class="ecs-filter active" data-filter="all">全部 ${catalog.components.length}</button>${catalog.categories.map(category=>`<button class="ecs-filter" data-filter="${esc(category.id)}">${esc(category.name.replace(/^\d+ · /,""))}</button>`).join("")}</div></div></div>`;
  }
  function index(){
    const counts=categoryCounts();
    return `<aside class="ecs-index"><div class="ecs-index-title">EVALCALL ELEMENT MAP</div>${catalog.categories.map(category=>`<a href="#cat-${esc(category.id)}"><span>${esc(category.name)}</span><i>${counts[category.id]}</i></a>`).join("")}<div class="ecs-selection-summary"><b id="selectedCount">${selectedCount()} / ${catalog.components.length}</b><span>已选择风格 · 未勾选即保留现状</span><div class="ecs-selection-track"><i id="selectionTrack"></i></div></div></aside>`;
  }
  function styleCard(component,variant){
    const selected=selection[component.id]===variant.id;
    return `<div class="ecs-style ${selected?"selected":""}" role="radio" aria-checked="${selected}" tabindex="0" data-component="${esc(component.id)}" data-variant="${esc(variant.id)}"><span class="ecs-source">${esc(variant.source)}</span><div class="ecs-style-preview">${variant.html}</div><div class="ecs-style-copy"><b>${esc(variant.name)}</b><span>${esc(variant.description)}</span></div><span class="ecs-check">✓</span></div>`;
  }
  function componentRow(component,index){
    const element=atlasMap[component.atlasElement];
    const palette=COLORABLE.has(component.id)?`<div class="ecs-color-choice"><div><b>颜色</b><span>可与下方任意结构样式组合</span></div><div role="radiogroup" aria-label="${esc(component.name)}颜色选择">${COLORS.map(color=>`<button class="ecs-color-swatch ${colorSelection[component.id]===color.id?"selected":""}" role="radio" aria-checked="${colorSelection[component.id]===color.id}" data-color-component="${esc(component.id)}" data-color="${esc(color.id)}" style="--swatch:${color.value}" title="${esc(color.name)}"><i></i><span>${esc(color.name)}</span><em>✓</em></button>`).join("")}</div></div>`:"";
    return `<article class="ecs-component" id="component-${esc(component.id)}" data-category="${esc(component.category)}" data-search="${esc([component.name,component.selector,component.location,component.description,element&&element.name].join(" ").toLowerCase())}"><div class="ecs-component-meta"><span class="ecs-component-number">${String(index+1).padStart(2,"0")} / ${esc(component.category.toUpperCase())}</span><h3>${esc(component.name)}</h3><p>${esc(component.description)}</p><code class="ecs-selector">${esc(component.selector)}</code><span class="ecs-location">出现位置 · ${esc(component.location)}</span></div><div class="ecs-component-options">${palette}<div class="ecs-styles" role="radiogroup" aria-label="${esc(component.name)}风格选择">${element.variants.map(variant=>styleCard(component,variant)).join("")}</div></div></article>`;
  }
  function sourceProof(suite){
    if(suite.id==="linear")return `<div class="ecs-proof-nav"><b>◒ Linear</b><span>Inbox</span><span>My issues</span><span>Reviews</span></div><div class="ecs-proof-card"><small>ENG-2703 · IN PROGRESS</small><strong>Faster app launch</strong><p>Render UI before vehicle_state sync is present.</p><div><i></i><span>Activity · 4 updates</span></div></div>`;
    if(suite.id==="incident")return `<div class="ecs-proof-kicker">Status pages / United Kingdom</div><div class="ecs-proof-alert"><strong>△ We’re currently experiencing issues</strong><span><i>Website</i><i>App</i></span><b>Important upgrades to the network</b><p>Investigating · Ongoing · Affects Website and App</p></div>`;
    return `<div class="ecs-proof-nav"><b>⌁ Retool</b><span>Apps</span><span>Workflows</span><span>Resources</span></div><div class="ecs-proof-card"><small>RESOURCE · PROD_EVAL_RUNS</small><strong>Customer operations</strong><p>Query, transform and ship internal tools.</p><div><i></i><span>Deployment queued</span></div></div>`;
  }
  function blueprintValidation(blueprint){
    const errors=[];
    if(!blueprint||typeof blueprint!=="object")return ["蓝图必须是 JSON 对象"];
    if(blueprint.schema!=="design-composition/v1")errors.push("schema 必须是 design-composition/v1");
    if(!blueprint.page||!String(blueprint.page.title||"").trim())errors.push("page.title 不能为空");
    if(!blueprint.layout||!["columns","rows"].includes(blueprint.layout.direction))errors.push("layout.direction 只能是 columns 或 rows");
    if(!Array.isArray(blueprint.layout&&blueprint.layout.tracks)||blueprint.layout.tracks.length<1)errors.push("layout.tracks 至少包含一个比例");
    if(!Array.isArray(blueprint.regions)||!blueprint.regions.length)errors.push("regions 至少包含一个区域");
    if(Array.isArray(blueprint.layout&&blueprint.layout.tracks)&&Array.isArray(blueprint.regions)&&blueprint.layout.tracks.length!==blueprint.regions.length)errors.push("layout.tracks 数量必须与 regions 数量一致");
    const ids=new Set(),types=new Set(["stack","flow","cards","table","form","timeline"]);
    (blueprint.regions||[]).forEach((region,index)=>{if(!region.id)errors.push(`regions[${index}].id 不能为空`);else if(ids.has(region.id))errors.push(`区域 ID 重复：${region.id}`);else ids.add(region.id);if(!types.has(region.type))errors.push(`不支持的区域类型：${region.type}`)});
    return errors;
  }
  function blueprintItem(item){
    if(item.type==="field")return `<span class="ecs-bp-field" data-edit-component="text-input"><i>${esc(item.label)}</i><strong>${esc(item.value)}</strong></span>`;
    if(item.type==="metric")return `<b class="ecs-bp-metric" data-edit-component="metric-card">${esc(item.value)}<small>${esc(item.label)}</small></b>`;
    if(item.type==="status")return `<span class="ecs-bp-status" data-edit-component="chip">${esc(item.label)}</span>`;
    return `<span class="ecs-bp-copy">${esc(item.label||item.value||"")}</span>`;
  }
  function blueprintPanel(panel){return `<article class="ecs-demo-io-card ${esc(panel.tone||"")}" data-edit-component="io-card"><header data-edit-component="io-header"><b>${esc(panel.label||"内容")}</b><small>${esc(panel.meta||"PANEL")}</small></header><div class="ecs-bp-panel-body">${(panel.items||[]).map(blueprintItem).join("")}</div></article>`}
  function blueprintFlow(region){
    const stages=region.stages||[];
    return `<section class="ecs-demo-tree ecs-bp-flow ${esc(region.orientation||"vertical")}" data-edit-component="flow-orientation"><header data-edit-component="flow-header"><div><b>${esc(region.label||"流程")}</b><small>${esc(region.description||"节点按业务关系动态生成")}</small></div><span data-edit-component="flow-progress">${esc(region.meta||`${stages.length} STAGES`)}</span></header><div class="ecs-demo-tree-body" data-edit-component="flow-shell">${stages.map((stage,index)=>`${index?`<i class="ecs-demo-tree-link" data-edit-component="flow-connector"><em data-edit-component="flow-pulse"></em></i>`:""}<div class="ecs-demo-tree-zone ${esc(stage.tone||"")}"><small>${esc(stage.label||`STAGE ${index+1}`)}</small><div>${(stage.nodes||[]).map(node=>`<span data-edit-component="${stage.tone==="input"?"flow-input-node":stage.tone==="output"?"flow-output-node":"flow-process-node"}">${esc(node)}</span>`).join("")}</div></div>`).join("")}</div></section>`;
  }
  function blueprintRegion(region){
    if(region.type==="flow")return blueprintFlow(region);
    if(region.type==="stack"||region.type==="form")return `<section class="ecs-demo-io-stack ecs-bp-stack" data-edit-component="io-stack">${(region.items||[]).map(item=>item.type==="panel"?blueprintPanel(item):blueprintItem(item)).join("")}</section>`;
    if(region.type==="cards")return `<section class="ecs-bp-card-grid" data-edit-component="io-stack">${(region.items||[]).map(blueprintItem).join("")}</section>`;
    if(region.type==="table")return `<section class="ecs-bp-table" data-edit-component="detail-table"><header><b>${esc(region.label||"数据表")}</b><span>${(region.columns||[]).map(column=>`<i>${esc(column)}</i>`).join("")}</span></header>${(region.rows||[]).map(row=>`<div>${row.map(cell=>`<span>${esc(cell)}</span>`).join("")}</div>`).join("")}</section>`;
    if(region.type==="timeline")return `<section class="ecs-bp-timeline" data-edit-component="regression-loop">${(region.items||[]).map(item=>`<div><i></i><span><b>${esc(item.label)}</b><small>${esc(item.value||"")}</small></span></div>`).join("")}</section>`;
    return `<section class="ecs-bp-empty">${esc(region.label||"未命名区域")}</section>`;
  }
  function renderBlueprint(blueprint){
    const bp=blueprint||blueprintExamples[0],steps=Array.isArray(bp.navigation)?bp.navigation:[],direction=bp.layout&&bp.layout.direction==="rows"?"rows":"columns",tracks=(bp.layout&&bp.layout.tracks||[1]).map(value=>Math.max(1,Number(value)||1)),gap=Math.min(30,Math.max(4,Number(bp.layout&&bp.layout.gap)||12)),trackStyle=tracks.map(value=>`${value}fr`).join(" ");
    return `<div class="ecs-demo-workbench ecs-bp-workbench" data-blueprint-id="${esc(bp.id||"custom")}" data-edit-component="workbench"><header class="ecs-demo-workbench-top" data-edit-component="topbar"><b data-edit-component="brand-mark">◈ ${esc(bp.name||"Project")}</b><nav data-edit-component="top-controls">Blueprint&nbsp;&nbsp; Components&nbsp;&nbsp; Export</nav><button data-edit-component="execute-button">运行当前页面</button></header>${steps.length?`<div class="ecs-demo-workbench-steps ecs-demo-steps" data-edit-component="step-navigation" style="grid-template-columns:repeat(${Math.min(8,steps.length)},1fr)">${steps.slice(0,8).map((label,index)=>`<span class="${index<1?"done":index===1?"current":""}"><i data-edit-component="step-index">${index<1?"✓":index+1}</i><b>${esc(label)}</b></span>`).join("")}</div>`:""}<section class="ecs-demo-workbench-head"><div><small data-edit-component="run-context">${esc(bp.page.context||"PROJECT BLUEPRINT")}</small><h4 data-edit-component="stage-heading">${esc(bp.page.title)}</h4></div><span data-edit-component="status-line">${esc(bp.page.status||"结构已生成")}</span></section><nav class="ecs-demo-composition-tools" aria-label="页面编排快捷编辑"><button data-edit-component="stage-layout">编辑页面主编排</button><button data-edit-component="io-stack">编辑区域组合</button><button data-edit-component="flow-orientation">编辑流程方向</button></nav><main class="ecs-demo-core-layout ecs-bp-layout ${direction}" data-edit-component="stage-layout" style="gap:${gap}px;${direction==="rows"?`grid-template-rows:${trackStyle};grid-template-columns:1fr`:`grid-template-columns:${trackStyle}`} ">${(bp.regions||[]).map(blueprintRegion).join("")}</main></div>`;
  }
  function workbenchDemo(){return `<div class="ecs-blueprint-live">${renderBlueprint(activeBlueprint)}</div>`}
  function layerDemo(categoryId){
    if(categoryId==="composition"||categoryId==="shell")return workbenchDemo();
    if(categoryId==="navigation")return `<div class="ecs-demo-navigation" data-edit-component="step-navigation"><small data-edit-component="run-context">RUN · DELIVERY-BASELINE-V1</small><h4 data-edit-component="stage-heading">模型评测正在执行</h4><div class="ecs-demo-steps" data-edit-component="step-navigation">${["配置任务","评分标准","测试模型","评测报告","失败归因","同尺回归"].map((label,index)=>`<span class="${index<2?"done":index===2?"current":""}"><i data-edit-component="step-index">${index<2?"✓":index+1}</i><b>${label}</b></span>`).join("")}</div><footer><em>2 / 6 已完成</em><button data-edit-component="footer-navigation">继续执行</button></footer></div>`;
    if(categoryId==="flow")return `<div class="ecs-demo-flow" data-edit-component="flow-shell"><div><small data-edit-component="flow-header">INPUT</small><span data-edit-component="flow-input-node">SOP v3.2</span><span data-edit-component="flow-input-node">Persona / 拒绝</span></div><div class="ecs-demo-flow-core" data-edit-component="flow-connector"><small data-edit-component="flow-progress">PROCESS</small><span data-edit-component="flow-process-node">解析任务指令</span><span data-edit-component="flow-process-node">生成压力对话</span><span data-edit-component="flow-process-node">逐项引用证据</span><i data-edit-component="flow-pulse"></i></div><div><small data-edit-component="flow-header">OUTPUT</small><span data-edit-component="flow-output-node">10 通对话</span><span data-edit-component="flow-output-node">21 项判定</span></div></div>`;
    if(categoryId==="input")return `<div class="ecs-demo-form" data-edit-component="io-card"><header data-edit-component="io-header"><div><small>TEST CONFIGURATION</small><h4 data-edit-component="section-label">配置测试任务</h4></div><span data-edit-component="status-line">Draft saved</span></header><div class="ecs-demo-fields">${[["被测模型","delivery-baseline-v1"],["任务指令","配送时间改约 SOP"],["测试数量","10 conversations"],["用户画像","拒绝 / 质疑 / 反复追问"]].map(item=>`<label data-edit-component="text-input">${item[0]}<strong>${item[1]}</strong></label>`).join("")}</div><footer><span data-edit-component="mode-note">Model hash · 91d3…a82</span><button data-edit-component="execute-button">生成测试包</button></footer></div>`;
    if(categoryId==="content")return `<div class="ecs-demo-report" data-edit-component="io-card"><header><div><small data-edit-component="section-label">MODEL EVALUATION REPORT</small><h4 data-edit-component="gate-card">上线门禁：打回</h4></div><span data-edit-component="chip">P0 · CRITICAL</span></header><div class="ecs-demo-metrics">${[["3/10","通话打回"],["40%","业务履约率"],["62.5","平均分"]].map(item=>`<b data-edit-component="metric-card">${item[0]}<small>${item[1]}</small></b>`).join("")}</div><div class="ecs-demo-table" data-edit-component="detail-table"><span><b>身份确认</b><i data-edit-component="chip">PASS</i><em>10 / 10</em></span><span><b>改约时间复述</b><i data-edit-component="chip">FAIL</i><em>3 / 10</em></span><span><b>隐私保护</b><i data-edit-component="chip">P0</i><em>3 / 10</em></span></div></div>`;
    return `<div class="ecs-demo-feedback"><div class="ecs-demo-toast" data-edit-component="toast"><i>✓</i><span><b>评测报告已生成</b><small>全部证据与版本指纹已保存</small></span></div><section data-edit-component="drawer"><header><b>原文证据</b><button data-edit-component="evidence-trigger">×</button></header><small>CHECKPOINT · PRIVACY_03</small><blockquote data-edit-component="drawer-evidence">“请把完整手机号再告诉我一次。”</blockquote><div><span>判定：P0 失败</span><button data-edit-component="evidence-trigger">定位到对话</button></div></section></div>`;
  }
  function suitePreview(category,suite){
    return `<div class="ecs-live-system ${esc(suite.id)}"><div class="ecs-source-proof"><span class="ecs-preview-label">SOURCE REFERENCE · ${esc(suite.name)}</span>${sourceProof(suite)}</div><div class="ecs-layer-translation"><div class="ecs-translation-head"><span>TRANSLATED TO EVALCALL · ${esc(category.name)}</span><button class="ecs-suite-replay" data-replay-category="${esc(category.id)}">↻ 重播动态</button></div>${layerDemo(category.id)}</div></div>`;
  }
  function blueprintStudio(){
    const json=JSON.stringify(activeBlueprint,null,2);
    return `<section class="ecs-blueprint-studio"><div class="ecs-blueprint-head"><div><span>COMPOSITION BLUEPRINT INTERPRETER</span><h3>导入项目逻辑，动态生成页面编排</h3><p>描述区域、层级、比例与关系即可；系统负责生成结构，视觉系统继续负责字体、留白和组件风格。</p></div><div class="ecs-blueprint-status" id="blueprintStatus"><i></i><span><b>${esc(activeBlueprint.name||"自定义蓝图")}</b><small>${esc(activeBlueprint.schema||"")}</small></span></div></div><div class="ecs-blueprint-pipeline"><span><b>1</b>导入业务蓝图</span><i>→</i><span><b>2</b>校验结构关系</span><i>→</i><span><b>3</b>生成页面骨架</span><i>→</i><span><b>4</b>应用视觉系统</span></div><div class="ecs-blueprint-examples">${blueprintExamples.map(example=>`<button class="${activeBlueprint.id===example.id?"selected":""}" data-blueprint-example="${esc(example.id)}"><span>${esc(example.name)}</span><small>${esc(example.layout.direction==="rows"?"上下区域":"左右区域")} · ${example.regions.length} 个区域</small><i>✓</i></button>`).join("")}</div><div class="ecs-blueprint-editor"><label><span>编排蓝图 JSON</span><textarea id="blueprintJson" spellcheck="false">${esc(json)}</textarea></label><aside><div><b>可表达的结构</b><span>columns / rows</span><span>stack / cards / table</span><span>flow / form / timeline</span><span>任意区域比例与节点内容</span></div><label class="ecs-blueprint-file">导入 JSON 文件<input id="blueprintFile" type="file" accept=".json,application/json"></label><button id="applyBlueprint">解析并生成页面</button><button class="secondary" id="downloadBlueprint">下载当前蓝图</button></aside></div><div class="ecs-blueprint-message" id="blueprintMessage">蓝图有效；修改 JSON 后点击“解析并生成页面”。</div></section>`;
  }
  function suitePanel(category){
    const active=activeSuite(category.id);
    const preview=previewSuite[category.id]||(previewSuite[category.id]=active||SUITES[0].id);
    return `<section class="ecs-suite-panel" data-suite-panel="${esc(category.id)}"><div class="ecs-suite-head"><div><span>SOURCE-BASED DESIGN SYSTEM</span><h3>选择一个真实产品的完整设计系统</h3></div><p>字体、密度、留白、圆角、表面与交互同时切换；下方再细调本层 ${catalog.components.filter(component=>component.category===category.id).length} 个元素。</p></div><div class="ecs-suite-grid" role="radiogroup" aria-label="${esc(category.name)}整层视觉系统">${SUITES.map(suite=>`<div class="ecs-suite-card ${active===suite.id?"selected":""} ${preview===suite.id?"previewing":""}" role="radio" aria-checked="${active===suite.id}" tabindex="0" data-suite="${esc(suite.id)}" data-suite-category="${esc(category.id)}"><div class="ecs-suite-source"><span>${esc(suite.english)}</span><b>${esc(suite.name)}</b><p>${esc(suite.description)}</p></div><div class="ecs-suite-dna"><span>${esc(suite.type)}</span><span>${esc(suite.spacing)}</span><span>${esc(suite.radius)}</span><span>${esc(suite.density)}</span></div><i class="ecs-suite-check">✓</i></div>`).join("")}</div><div class="ecs-live-instruction"><span>↳</span><b>点击实时舞台中的任意控件即可编辑</b><em>自动定位到对应子元素；勾选后立即回写颜色与样式</em></div><div class="ecs-suite-stage-wrap">${SUITES.map(suite=>`<div class="ecs-suite-stage ${preview===suite.id?"active replaying":""}" data-suite-stage-category="${esc(category.id)}" data-suite-stage="${esc(suite.id)}">${suitePreview(category,suite)}</div>`).join("")}</div><div class="ecs-suite-custom ${active?"":"show"}" data-custom-category="${esc(category.id)}"><b>自定义组合</b><span>当前细项不完全属于任何来源系统；上方仍保留最近一次来源预览。</span></div></section>`;
  }
  function content(){
    let serial=0;
    return `<div class="ecs-layout">${index()}<main class="ecs-main">${catalog.categories.map(category=>{const rows=catalog.components.filter(component=>component.category===category.id);return `<section class="ecs-category" id="cat-${esc(category.id)}" data-category-section="${esc(category.id)}"><div class="ecs-category-head"><h2>${esc(category.name)}</h2><p>${esc(category.description)}</p></div>${category.id==="composition"?blueprintStudio():""}${suitePanel(category)}${rows.map(component=>componentRow(component,serial++)).join("")}</section>`}).join("")}<div class="ecs-empty" id="emptySearch"><b>没有找到对应子元素</b>尝试搜索“按钮”“流程”“证据”或 CSS 选择器。</div></main></div>`;
  }
  function dock(){return `<div class="ecs-dock"><div class="ecs-dock-copy"><b><span id="dockCount">${selectedCount()}</span> 个子元素已有选型</b><span id="configHash">正在计算方案指纹…</span></div><div class="ecs-dock-actions"><button id="returnPreview" hidden>↑ 返回动态预览</button><button id="saveSelection">保存到本机</button><button class="primary" id="exportSelection">导出 JSON</button></div></div><div class="ecs-toast" id="ecsToast"></div>`}
  function render(){document.body.innerHTML=header()+toolbar()+content()+dock();bind();markEditable();syncLiveSelections();updateSummary();applyFilter();updateSuiteStates()}
  function refreshBlueprintStages(){document.querySelectorAll(".ecs-blueprint-live").forEach(node=>node.innerHTML=renderBlueprint(activeBlueprint));markEditable();syncLiveSelections();document.querySelectorAll('[data-suite-stage-category="composition"].active,[data-suite-stage-category="shell"].active').forEach(stage=>{stage.classList.remove("replaying");void stage.offsetWidth;stage.classList.add("replaying")})}
  function showBlueprintMessage(message,error=false){const node=$("#blueprintMessage");if(!node)return;node.textContent=message;node.classList.toggle("error",error)}
  function setBlueprint(blueprint){const errors=blueprintValidation(blueprint);if(errors.length){showBlueprintMessage(errors.join("；"),true);return false}activeBlueprint=JSON.parse(JSON.stringify(blueprint));localStorage.setItem(BLUEPRINT_KEY,JSON.stringify(activeBlueprint));const editor=$("#blueprintJson");if(editor)editor.value=JSON.stringify(activeBlueprint,null,2);document.querySelectorAll("[data-blueprint-example]").forEach(button=>button.classList.toggle("selected",button.dataset.blueprintExample===activeBlueprint.id));const status=$("#blueprintStatus");if(status)status.innerHTML=`<i></i><span><b>${esc(activeBlueprint.name||"自定义蓝图")}</b><small>${esc(activeBlueprint.schema)}</small></span>`;refreshBlueprintStages();updateSummary();showBlueprintMessage(`蓝图有效：已生成 ${activeBlueprint.regions.length} 个区域，并保留现有视觉选型。`);toast(`${activeBlueprint.name||"项目"}的页面编排已生成`);return true}
  function applyBlueprintJson(){try{setBlueprint(JSON.parse($("#blueprintJson").value))}catch(error){showBlueprintMessage(`JSON 无法解析：${error.message}`,true)}}
  function downloadBlueprint(){const blob=new Blob([JSON.stringify(activeBlueprint,null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`composition-blueprint-${activeBlueprint.id||"custom"}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),500);toast("当前编排蓝图已下载")}
  function choose(componentId,variantId){
    selection[componentId]=selection[componentId]===variantId?null:variantId;
    document.querySelectorAll(`[data-component="${CSS.escape(componentId)}"]`).forEach(node=>{const active=node.dataset.variant===selection[componentId];node.classList.toggle("selected",active);node.setAttribute("aria-checked",String(active))});
    updateSummary();
    updateSuiteStates();
    syncLiveSelections();
    const component=catalog.components.find(item=>item.id===componentId),variant=selectedVariant(component);
    toast(`${component.name}已更新为${variant?variant.name:"保留现状"}`);
  }
  function chooseColor(componentId,colorId){const next=colorSelection[componentId]===colorId?null:colorId;colorSelection[componentId]=next;document.querySelectorAll(`[data-color-component="${CSS.escape(componentId)}"]`).forEach(button=>{const active=button.dataset.color===next;button.classList.toggle("selected",active);button.setAttribute("aria-checked",String(active))});updateSummary();updateSuiteStates();syncLiveSelections();const component=catalog.components.find(item=>item.id===componentId),color=COLORS.find(item=>item.id===next);toast(`${component.name}颜色已更新为${color?color.name:"跟随样式"}`)}
  function applySuite(categoryId,suiteId){
    previewSuite[categoryId]=suiteId;
    Object.assign(selection,suitePlan(categoryId,suiteId));
    catalog.components.filter(component=>component.category===categoryId).forEach(component=>colorSelection[component.id]=null);
    document.querySelectorAll(`#cat-${CSS.escape(categoryId)} .ecs-color-swatch`).forEach(button=>{button.classList.remove("selected");button.setAttribute("aria-checked","false")});
    document.querySelectorAll(`.ecs-component[data-category="${CSS.escape(categoryId)}"] .ecs-style`).forEach(node=>{const active=selection[node.dataset.component]===node.dataset.variant;node.classList.toggle("selected",active);node.setAttribute("aria-checked",String(active))});
    updateSummary();updateSuiteStates();syncLiveSelections();replaySuite(categoryId);toast(`${SUITES.find(suite=>suite.id===suiteId).name}设计系统已应用到整层`);
  }
  function updateSuiteStates(){
    catalog.categories.forEach(category=>{const active=activeSuite(category.id),preview=previewSuite[category.id]||active||SUITES[0].id;document.querySelectorAll(`[data-suite-category="${CSS.escape(category.id)}"]`).forEach(card=>{const selected=card.dataset.suite===active;card.classList.toggle("selected",selected);card.classList.toggle("previewing",card.dataset.suite===preview);card.setAttribute("aria-checked",String(selected))});document.querySelectorAll(`[data-suite-stage-category="${CSS.escape(category.id)}"]`).forEach(stage=>stage.classList.toggle("active",stage.dataset.suiteStage===preview));document.querySelector(`[data-custom-category="${CSS.escape(category.id)}"]`)?.classList.toggle("show",!active)});
  }
  function replaySuite(categoryId){const stage=document.querySelector(`[data-suite-stage-category="${CSS.escape(categoryId)}"].active`);if(!stage)return;stage.classList.remove("replaying");void stage.offsetWidth;stage.classList.add("replaying")}
  function syncLiveSelections(){document.querySelectorAll(".ecs-live-system").forEach(root=>{Object.entries(selection).forEach(([componentId,variantId])=>root.setAttribute(`data-choice-${componentId}`,variantId||"none"));Object.entries(colorSelection).forEach(([componentId,colorId])=>{const color=COLORS.find(item=>item.id===colorId);root.setAttribute(`data-color-${componentId}`,color?"custom":"default");if(color){root.style.setProperty(`--choice-${componentId}`,color.value);root.style.setProperty(`--choice-${componentId}-ink`,color.ink)}else{root.style.removeProperty(`--choice-${componentId}`);root.style.removeProperty(`--choice-${componentId}-ink`)}})})}
  function markEditable(){document.querySelectorAll("[data-edit-component]").forEach(node=>{const component=catalog.components.find(item=>item.id===node.dataset.editComponent);if(!component)return;const stage=node.closest("[data-suite-stage-category]");if(stage)node.dataset.editPreviewCategory=stage.getAttribute("data-suite-stage-category");node.classList.add("ecs-editable");node.title=`点击编辑：${component.name}`;if(!node.querySelector("[data-edit-component]")&&!/^(BUTTON|A|INPUT)$/.test(node.tagName)){node.tabIndex=0;node.setAttribute("role","button")}node.setAttribute("aria-label",`编辑${component.name}`)})}
  function focusComponent(componentId,previewCategory){const component=catalog.components.find(item=>item.id===componentId),target=document.querySelector(`#component-${CSS.escape(componentId)}`);if(!component||!target)return;editingComponent=componentId;editingPreviewCategory=previewCategory||component.category;filter="all";query="";$("#componentSearch").value="";document.querySelectorAll(".ecs-filter").forEach(button=>button.classList.toggle("active",button.dataset.filter==="all"));applyFilter();document.querySelectorAll(".ecs-component.focused").forEach(node=>node.classList.remove("focused"));target.classList.add("focused");$("#returnPreview").hidden=false;target.scrollIntoView({behavior:"smooth",block:"center"});toast(`正在编辑：${component.name}`);setTimeout(()=>target.classList.remove("focused"),2200)}
  function returnToPreview(){if(!editingComponent)return;const component=catalog.components.find(item=>item.id===editingComponent),category=editingPreviewCategory||component.category,panel=document.querySelector(`[data-suite-panel="${CSS.escape(category)}"]`);panel?.scrollIntoView({behavior:"smooth",block:"start"});toast("已返回刚才的动态预览")}
  function applyFilter(){
    let shown=0;
    document.querySelectorAll(".ecs-component").forEach(row=>{const matchCategory=filter==="all"||row.dataset.category===filter;const matchQuery=!query||row.dataset.search.includes(query);const visible=matchCategory&&matchQuery;row.classList.toggle("hidden",!visible);if(visible)shown++});
    document.querySelectorAll(".ecs-category").forEach(section=>{const any=Array.from(section.querySelectorAll(".ecs-component")).some(row=>!row.classList.contains("hidden"));section.style.display=any?"":"none"});
    $("#emptySearch").classList.toggle("show",shown===0);
  }
  async function updateSummary(){
    const count=selectedCount(),total=catalog.components.length;
    $("#selectedCount").textContent=`${count} / ${total}`;$("#dockCount").textContent=count;$("#selectionTrack").style.width=`${count/total*100}%`;
    $("#configHash").textContent=`方案指纹 · ${await configHash()} · ${new Date().toLocaleDateString("zh-CN")}`;
  }
  function payload(){
    return {schema:"evalcall-interface-style-selection/v3",created_at:new Date().toISOString(),composition_blueprint:activeBlueprint,component_count:catalog.components.length,selected_count:selectedCount(),layer_suites:Object.fromEntries(catalog.categories.map(category=>[category.id,activeSuite(category.id)||"custom"])),layer_systems:Object.fromEntries(catalog.categories.map(category=>{const id=activeSuite(category.id),suite=SUITES.find(item=>item.id===id);return [category.id,suite?{source:suite.name,type:suite.type,spacing:suite.spacing,radius:suite.radius,density:suite.density}:null]})),color_overrides:Object.fromEntries(Object.entries(colorSelection).filter(([,value])=>value).map(([componentId,colorId])=>{const color=COLORS.find(item=>item.id===colorId);return [componentId,{id:color.id,name:color.name,value:color.value}]})),choices:catalog.components.map(component=>{const variant=selectedVariant(component);return {component_id:component.id,component_name:component.name,selector:component.selector,location:component.location,atlas_element:component.atlasElement,variant_id:variant&&variant.id||null,variant_name:variant&&variant.name||"保留现状",source:variant&&variant.source||null}})};
  }
  function save(){localStorage.setItem(KEY,JSON.stringify(selection));localStorage.setItem(COLOR_KEY,JSON.stringify(colorSelection));toast("选型方案已保存到本机")}
  function exportConfig(){
    const blob=new Blob([JSON.stringify(payload(),null,2)],{type:"application/json"}),url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`evalcall-style-selection-${new Date().toISOString().slice(0,10)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),500);toast("选型方案已导出")
  }
  function clear(){Object.keys(selection).forEach(key=>selection[key]=null);Object.keys(colorSelection).forEach(key=>colorSelection[key]=null);document.querySelectorAll(".ecs-style,.ecs-color-swatch").forEach(node=>{node.classList.remove("selected");node.setAttribute("aria-checked","false")});updateSummary();updateSuiteStates();syncLiveSelections();toast("已清空，未勾选元素将保留现状")}
  function bind(){
    document.addEventListener("click",event=>{const replay=event.target.closest(".ecs-suite-replay");if(replay){replaySuite(replay.dataset.replayCategory);return}const editable=event.target.closest("[data-edit-component]");if(editable){event.preventDefault();event.stopPropagation();focusComponent(editable.dataset.editComponent,editable.dataset.editPreviewCategory);return}const color=event.target.closest(".ecs-color-swatch");if(color){chooseColor(color.dataset.colorComponent,color.dataset.color);return}const suite=event.target.closest(".ecs-suite-card");if(suite){applySuite(suite.dataset.suiteCategory,suite.dataset.suite);return}const card=event.target.closest(".ecs-style");if(card)choose(card.dataset.component,card.dataset.variant)});
    document.addEventListener("keydown",event=>{const editable=event.target.closest("[data-edit-component]");if(editable&&(event.key==="Enter"||event.key===" ")){event.preventDefault();focusComponent(editable.dataset.editComponent,editable.dataset.editPreviewCategory);return}const suite=event.target.closest(".ecs-suite-card");if(suite&&(event.key==="Enter"||event.key===" ")){event.preventDefault();applySuite(suite.dataset.suiteCategory,suite.dataset.suite);return}const card=event.target.closest(".ecs-style");if(card&&(event.key==="Enter"||event.key===" ")){event.preventDefault();choose(card.dataset.component,card.dataset.variant)}if((event.metaKey||event.ctrlKey)&&event.key.toLowerCase()==="k"){event.preventDefault();$("#componentSearch").focus()}});
    $("#componentSearch").addEventListener("input",event=>{query=event.target.value.trim().toLowerCase();applyFilter()});
    document.querySelectorAll(".ecs-filter").forEach(button=>button.addEventListener("click",()=>{filter=button.dataset.filter;document.querySelectorAll(".ecs-filter").forEach(item=>item.classList.toggle("active",item===button));applyFilter()}));
    document.querySelectorAll("[data-blueprint-example]").forEach(button=>button.addEventListener("click",()=>{const example=blueprintExamples.find(item=>item.id===button.dataset.blueprintExample);if(example)setBlueprint(example)}));
    $("#applyBlueprint")?.addEventListener("click",applyBlueprintJson);$("#downloadBlueprint")?.addEventListener("click",downloadBlueprint);$("#blueprintFile")?.addEventListener("change",async event=>{const file=event.target.files&&event.target.files[0];if(!file)return;try{const blueprint=JSON.parse(await file.text());setBlueprint(blueprint)}catch(error){showBlueprintMessage(`文件无法导入：${error.message}`,true)}event.target.value=""});
    $("#clearSelection").addEventListener("click",clear);$("#returnPreview").addEventListener("click",returnToPreview);$("#saveSelection").addEventListener("click",save);$("#exportSelection").addEventListener("click",exportConfig);$("#exportTop").addEventListener("click",exportConfig);
  }
  render();
})();
