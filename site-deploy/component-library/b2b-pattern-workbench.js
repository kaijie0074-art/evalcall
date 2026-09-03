(() => {
  "use strict";

  const STORAGE_KEY = "evalcall-b2b-workbench";
  const THEME_KEY = "evalcall-b2b-theme";
  const defaults = {
    workbench_schema: "evalcall-b2b-workbench/v2",
    page: "task_center",
    reference: "linear",
    density: "dense",
    viewport: "desktop",
    color: "yellow",
    mode: "design",
    shell_layout: "linear",
    layouts: { task_center: "linear", create: "linear", result: "linear", review: "linear" },
    display: { summary: true, aside: true, lock: true }
  };
  const pageNames = { task_center: "评测任务", create: "创建评测", result: "评测结果", review: "争议复核" };
  const pageRoutes = { task_center: "dashboard", create: "create", result: "results", review: "review" };
  const layoutNames = { linear: "列表优先" };
  const colorNames = { yellow: "中性黄", blue: "安静蓝", purple: "治理紫" };
  const densityNames = { dense: "紧凑", compact: "标准", comfortable: "宽松" };
  const viewportSizes = { desktop: [1440, 900], tablet: [1024, 800], mobile: [390, 844] };
  const referenceSizes = { linear: [1920, 868], vanta: [1888, 980], retool: [1920, 1148] };
  const valid = {
    page: Object.keys(pageNames), reference: ["linear", "vanta", "retool"], density: ["dense", "compact", "comfortable"],
    viewport: Object.keys(viewportSizes), color: Object.keys(colorNames), mode: ["design", "reference"], layout: Object.keys(layoutNames)
  };

  const $ = selector => document.querySelector(selector);
  const $$ = selector => [...document.querySelectorAll(selector)];
  const clone = value => JSON.parse(JSON.stringify(value));
  let patternLibrary = null;
  let toastTimer = 0;

  function encodeThemeConfig(value) {
    const bytes = new TextEncoder().encode(JSON.stringify(value));
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
  }

  function resolveDemoUrl(applied) {
    const requestedReturn = new URLSearchParams(location.search).get("return");
    let target = new URL("./app.html", location.href);
    if (requestedReturn) {
      try {
        const candidate = new URL(requestedReturn, location.href);
        const allowedHost = candidate.origin === location.origin
          || candidate.hostname === "127.0.0.1"
          || candidate.hostname === "localhost"
          || candidate.hostname.endsWith(".trycloudflare.com")
          || candidate.hostname.endsWith(".github.io");
        if (["http:", "https:"].includes(candidate.protocol) && allowedHost) target = candidate;
      } catch (_) { /* use the same-origin Demo */ }
    }
    const transport = { ...applied };
    delete transport.tokens;
    target.searchParams.set("ui", encodeThemeConfig(transport));
    target.hash = `#/${pageRoutes[state.page]}`;
    return target;
  }

  function loadState() {
    let saved = {};
    try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch (_) { saved = {}; }
    const next = clone(defaults);
    for (const key of ["page", "reference", "density", "viewport", "color", "mode"]) {
      if (valid[key].includes(saved[key])) next[key] = saved[key];
    }
    const savedLayout = saved.workbench_schema === defaults.workbench_schema && valid.layout.includes(saved.shell_layout)
      ? saved.shell_layout
      : "linear";
    next.shell_layout = savedLayout;
    for (const page of valid.page) next.layouts[page] = savedLayout;
    if (saved.display && typeof saved.display === "object") {
      for (const key of Object.keys(next.display)) if (typeof saved.display[key] === "boolean") next.display[key] = saved.display[key];
    }
    return next;
  }

  let state = loadState();

  const tasks = [
    { id:"EV-20260716-042", name:"配送时效问答回归", versions:"v3.4.1 → v3.5.0", scene:"即时配送", owner:"陈序", conclusion:"待复核", status:"运行中", tone:"active", updated:"2 分钟前", action:"查看运行" },
    { id:"EV-20260716-041", name:"退款规则模型门禁", versions:"v2.8.6 → v2.9.0", scene:"售后履约", owner:"周岚", conclusion:"条件通过", status:"待复核", tone:"warning", updated:"18 分钟前", action:"处理 5 条" },
    { id:"EV-20260716-040", name:"到店核销异常回归", versions:"v1.9.2 → v1.9.3", scene:"到店履约", owner:"许衡", conclusion:"通过", status:"已完成", tone:"success", updated:"10:42", action:"查看结果" },
    { id:"EV-20260716-039", name:"骑手改派策略检查", versions:"v5.1.0 → v5.1.1", scene:"配送调度", owner:"林澈", conclusion:"未生成", status:"排队中", tone:"", updated:"09:58", action:"查看任务" },
    { id:"EV-20260715-038", name:"商家催单回复评测", versions:"v2.3.7 → v2.4.0", scene:"商家履约", owner:"江宜", conclusion:"打回", status:"门禁打回", tone:"danger", updated:"昨天 22:16", action:"查看原因" },
    { id:"EV-20260715-037", name:"配送地址纠错抽检", versions:"v4.6.1 → v4.6.2", scene:"地址治理", owner:"叶川", conclusion:"通过", status:"已完成", tone:"success", updated:"昨天 20:31", action:"查看结果" },
    { id:"EV-20260715-036", name:"恶劣天气承诺校验", versions:"v1.4.8 → v1.5.0", scene:"特殊天气", owner:"苏禾", conclusion:"草稿", status:"未开始", tone:"", updated:"昨天 18:09", action:"继续配置" },
    { id:"EV-20260715-035", name:"预约单时间窗回归", versions:"v3.0.0 → v3.0.2", scene:"预约配送", owner:"唐宁", conclusion:"通过", status:"已完成", tone:"success", updated:"昨天 16:48", action:"查看结果" }
  ];

  function status(text, tone = "") { return `<span class="ec-status ${tone}">${text}</span>`; }

  function productSidebar(page) {
    const items = [
      ["task_center", "▤", "评测任务", ""], ["create", "＋", "创建评测", ""],
      ["result", "◫", "评测结果", ""], ["review", "◇", "争议复核", "18"]
    ];
    return `<aside class="ec-sidebar">
      <div class="ec-product"><span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span><span>EvalCall</span></div>
      <div class="ec-workspace"><span>履约模型评测</span><span>⌄</span></div>
      <div class="ec-nav-label">工作区</div>
      <nav>${items.map(item => `<button class="ec-nav-item ${page === item[0] ? "is-active" : ""}" type="button" data-preview-page="${item[0]}"><i>${item[1]}</i><span>${item[2]}</span>${item[3] ? `<span class="ec-nav-count">${item[3]}</span>` : ""}</button>`).join("")}</nav>
      <div class="ec-nav-label">管理</div>
      <button class="ec-nav-item" type="button"><i>⌁</i><span>评测集</span></button>
      <button class="ec-nav-item" type="button"><i>⚙</i><span>规则与裁判</span></button>
      <div class="ec-sidebar-foot"><div class="ec-user"><span class="ec-avatar">CX</span><span>陈序<small>模型评测运营</small></span></div></div>
    </aside>`;
  }

  function globalRail(page) {
    return `<aside class="ec-global-rail" aria-label="全局导航"><div class="ec-rail-logo">E</div><span class="ec-rail-button ${page === "task_center" ? "is-active" : ""}">▤</span><span class="ec-rail-button ${page === "review" ? "is-active" : ""}">◇</span><span class="ec-rail-button">⌁</span><span class="ec-rail-button">⚙</span><span class="ec-avatar ec-rail-avatar">CX</span></aside>`;
  }

  function shell(content, page) {
    return `<div class="ec-shell">${globalRail(page)}${productSidebar(page)}<main class="ec-main">${content}</main></div>`;
  }

  function sameScaleLock() {
    return `<div class="ec-lock"><span><i></i>用户输入集 · DS-0716</span><span><i></i>SOP · 2026.07</span><span><i></i>Checklist · CL-19</span><span><i></i>Judge · J-3.2</span></div>`;
  }

  function taskCenterPage() {
    return `<section class="ec-page ec-page-task">
      <header class="ec-page-head"><div><h1>评测任务</h1><p>履约模型评测 · 更新于 11:24</p></div><button class="ec-button primary" type="button" data-preview-page="create">＋ 创建评测</button></header>
      <div class="ec-summary"><span class="ec-summary-item">运行中 <strong>1</strong></span><span class="ec-summary-item">待复核（全局） <strong class="warn">18</strong></span><span class="ec-summary-item">门禁打回 <strong>1</strong></span><span class="ec-summary-item">今日完成 <strong>3</strong></span></div>
      ${sameScaleLock()}
      <div class="ec-toolbar"><div class="ec-tabs"><button class="ec-tab is-active">全部任务</button><button class="ec-tab">我负责的</button><button class="ec-tab">待我处理</button></div><input class="ec-search" aria-label="搜索任务" placeholder="⌕  搜索名称或任务编号"><button class="ec-filter">状态⌄</button><button class="ec-filter">履约场景⌄</button><button class="ec-filter">负责人⌄</button><span class="ec-toolbar-spacer"></span><button class="ec-button ec-icon-button" title="配置列">▥</button></div>
      <div class="ec-work-layout"><section class="ec-table-panel"><table class="ec-table"><colgroup><col style="width:24%"><col style="width:15%"><col style="width:11%"><col style="width:8%"><col style="width:10%"><col style="width:10%"><col style="width:10%"><col style="width:12%"></colgroup><thead><tr><th>任务</th><th>模型版本</th><th>履约场景</th><th>负责人</th><th>结论</th><th>状态</th><th>更新时间</th><th>操作</th></tr></thead><tbody>${tasks.map(task => `<tr><td><span class="ec-task-cell"><b>${task.name}</b><small>${task.id}</small></span></td><td class="ec-version">${task.versions}</td><td>${task.scene}</td><td>${task.owner}</td><td>${task.conclusion}</td><td>${status(task.status,task.tone)}</td><td>${task.updated}</td><td><button class="ec-table-action" type="button" data-task-action="${task.action}">${task.action}</button></td></tr>`).join("")}</tbody></table><footer class="ec-table-footer"><span>共 42 个任务 · 当前 1–8</span><span class="ec-pagination"><span>‹</span><span class="current">1</span><span>2</span><span>3</span><span>›</span></span></footer></section>
      <aside class="ec-aside"><div class="ec-aside-head"><b>我的待办</b><span>5 项</span></div><div class="ec-action-item"><span><i class="ec-risk-dot"></i>11:18</span><b>5 条高风险样本待复核</b><a href="#" data-preview-page="review">开始复核 →</a></div><div class="ec-action-item"><span>任务已完成 <em>10:42</em></span><b>到店核销异常回归</b><a href="#" data-preview-page="result">查看结果 →</a></div><div class="ec-action-item"><span>门禁打回 <em>昨天</em></span><b>商家催单回复评测</b><a href="#">查看原因 →</a></div><div class="ec-action-item"><span>草稿 <em>昨天</em></span><b>恶劣天气承诺校验</b><a href="#" data-preview-page="create">继续配置 →</a></div></aside></div>
    </section>`;
  }

  function createPage() {
    return `<section class="ec-page">
      <header class="ec-page-head"><div><div class="ec-breadcrumb">评测任务 <span>›</span> 创建评测</div><h1>创建评测</h1><p>草稿已保存于 11:22</p></div><div class="ec-head-context"><button class="ec-button">保存草稿</button><button class="ec-button primary">创建并运行</button></div></header>
      <div class="ec-create-grid"><section class="ec-form-panel"><div class="ec-panel-head"><b>任务配置</b><small>必填 8 / 8</small></div><div class="ec-form-section"><h2>基本信息</h2><div class="ec-form-row"><label>任务名称</label><div class="ec-form-control">配送时效问答回归</div></div><div class="ec-form-row"><label>履约场景</label><div class="ec-form-control">即时配送 <small>⌄</small></div></div><div class="ec-form-row"><label>负责人</label><div class="ec-form-control">陈序 <small>⌄</small></div></div></div><div class="ec-form-section"><h2>版本对比</h2><div class="ec-form-row"><label>基准版本</label><div class="ec-form-control"><span class="ec-version">delivery-qa · v3.4.1</span><small>⌄</small></div></div><div class="ec-form-row"><label>候选版本</label><div class="ec-form-control"><span class="ec-version">delivery-qa · v3.5.0</span><small>⌄</small></div></div></div><div class="ec-form-section"><h2>评测条件</h2><div class="ec-form-row"><label>测试数据</label><div class="ec-form-control">履约问答回归集 · 2,480 条 <small>更换</small></div></div><div class="ec-form-row"><label>裁判策略</label><div class="ec-choice-grid"><div class="ec-choice is-selected"><b>双裁判 + 分歧复核</b><small>Judge A / Judge B 独立裁判</small></div><div class="ec-choice"><b>单裁判快速评测</b><small>适合日常冒烟检查</small></div></div></div><div class="ec-form-row"><label>门禁规则</label><div class="ec-form-control">履约生产门禁 · GATE-07 <small>查看 6 项规则</small></div></div></div></section>
      <aside class="ec-summary-panel"><div class="ec-panel-head"><b>运行摘要</b><small>预计 18 分钟</small></div><dl class="ec-summary-list"><div><dt>样本数</dt><dd>2,480</dd></div><div><dt>请求数</dt><dd>4,960</dd></div><div><dt>裁判调用</dt><dd>9,920</dd></div><div><dt>高风险阈值</dt><dd>P0 / P1</dd></div><div><dt>并发数</dt><dd>20</dd></div><div><dt>预计费用</dt><dd>¥ 38.60</dd></div></dl>${sameScaleLock()}</aside></div>
      <footer class="ec-sticky-action"><button class="ec-button">返回评测任务</button><button class="ec-button primary">创建并运行</button></footer>
    </section>`;
  }

  function resultPage() {
    const metrics = [["有效样本","2,475","2,475"],["业务准确率","91.8%","93.4% <span class=\"ec-delta\">+1.6%</span>"],["高风险错误","3","5"],["裁判一致率","94.2%","92.7%"],["平均响应时延","812 ms","768 ms <span class=\"ec-delta\">-44 ms</span>"]];
    return `<section class="ec-page">
      <header class="ec-page-head"><div><div class="ec-breadcrumb">评测任务 <span>›</span> EV-20260716-041</div><div class="ec-object-status"><h1>退款规则模型门禁</h1>${status("待复核","warning")}</div><p>v2.8.6 → v2.9.0 · 售后履约 · 周岚</p></div><div class="ec-head-context"><button class="ec-button">导出报告</button><button class="ec-button primary" data-preview-page="review">复核 5 条高风险样本</button></div></header>
      <div class="ec-gate-bar"><span class="ec-gate-icon">!</span><div><b>条件通过 · 需完成高风险复核</b><small>5 条高风险样本尚未确认，灰度入口保持关闭</small></div><div class="ec-gate-meta"><span>完成时间<strong>11:06</strong></span><span>有效样本<strong>2,475</strong></span><span>任务范围争议<strong>8</strong></span></div></div>
      ${sameScaleLock()}
      <div class="ec-result-grid"><section class="ec-result-panel"><div class="ec-panel-head"><b>版本对比</b><small>候选版本相对基准版本</small></div><div class="ec-comparison"><div class="head">指标</div><div class="head">基准 · v2.8.6</div><div class="head">候选 · v2.9.0</div>${metrics.map(row => `<div class="metric">${row[0]}</div><div><strong>${row[1]}</strong></div><div><strong>${row[2]}</strong></div>`).join("")}</div></section><section class="ec-result-panel"><div class="ec-panel-head"><b>风险队列</b><small>8 条争议</small></div><div class="ec-risk-list"><div class="ec-risk-row"><span><b>退款时效承诺错误</b><small>CASE-00942 · 退款到账</small></span><span class="ec-risk-level high">P0</span>${status("待复核","warning")}</div><div class="ec-risk-row"><span><b>优惠券返还条件缺失</b><small>CASE-01108 · 权益返还</small></span><span class="ec-risk-level high">P1</span>${status("待复核","warning")}</div><div class="ec-risk-row"><span><b>原路退款描述不完整</b><small>CASE-01631 · 退款路径</small></span><span class="ec-risk-level high">P1</span>${status("待复核","warning")}</div><div class="ec-risk-row"><span><b>举证期限回答分歧</b><small>CASE-02144 · 申诉举证</small></span><span class="ec-risk-level">P2</span>${status("已确认","success")}</div></div></section></div>
    </section>`;
  }

  function reviewPage() {
    const cases = [["CASE-00942","退款时效承诺错误","P0 · 双裁判分歧"],["CASE-01108","优惠券返还条件缺失","P1 · 双裁判分歧"],["CASE-01631","原路退款描述不完整","P1 · 证据不一致"],["CASE-01862","拒收订单退款范围","P1 · 双裁判分歧"],["CASE-02087","申诉结果时间承诺","P2 · 低置信度"],["CASE-02211","部分退款计算口径","P2 · 双裁判分歧"]];
    return `<section class="ec-page">
      <header class="ec-page-head"><div><div class="ec-breadcrumb">评测结果 <span>›</span> EV-20260716-041 <span>›</span> 争议复核</div><div class="ec-object-status"><h1>争议复核</h1>${status("8 条任务范围","warning")}</div></div><div class="ec-head-context"><button class="ec-button">上一个</button><button class="ec-button">下一个</button></div></header>
      <div class="ec-review-grid"><aside class="ec-review-queue"><div class="ec-panel-head"><b>争议队列</b><small>1 / 8</small></div><div class="ec-review-filter"><input placeholder="⌕  搜索样本"></div><div class="ec-case-list">${cases.map((item,index) => `<div class="ec-case ${index === 0 ? "is-active" : ""}"><span class="ec-case-head"><span>${item[0]}</span><span>${index < 4 ? "待复核" : "未处理"}</span></span><b>${item[1]}</b><small>${item[2]}</small></div>`).join("")}</div></aside>
      <section class="ec-review-detail"><div class="ec-panel-head"><b>样本与裁判结论</b><small>退款到账 · P0</small></div><div class="ec-review-body"><div class="ec-sample"><label>用户问题</label><p>外卖订单取消后，用银行卡支付的金额多久能退回？如果使用了优惠券，会一起退吗？</p></div><div class="ec-sample" style="margin-top:8px"><label>候选模型回答 · v2.9.0</label><p>订单取消后款项通常会立即原路退回。银行卡到账时间以银行处理为准；已使用的优惠券也会自动返还到账户。</p></div><div class="ec-judge-grid"><article class="ec-judge"><label>裁判 A</label><div class="ec-judge-head"><b>不通过</b><span class="ec-score">42</span></div><p>“立即原路退回”构成确定性时效承诺；优惠券是否返还取决于订单与券规则。</p></article><article class="ec-judge is-preferred"><label>裁判 B</label><div class="ec-judge-head"><b>不通过 · 高置信</b><span class="ec-score">31</span></div><p>回答缺少银行卡预计到账范围，并错误承诺全部优惠券自动返还。</p></article></div></div></section>
      <aside class="ec-review-evidence"><div class="ec-panel-head"><b>证据与结论</b><small>按相关度排序</small></div><div class="ec-evidence-block"><label>规则证据</label><ol><li>银行卡退款：平台发起后，预计 1–7 个工作日到账。</li><li>优惠券：未过期且满足返还条件时退回账户。</li><li>P0：不得对资金到账时间作确定性承诺。</li></ol></div><div class="ec-evidence-block"><label>版本指纹</label><code>dataset  DS-0716-01\nsop      SOP-2026.07\ncheck    CL-19\njudge   J-3.2</code></div><div class="ec-review-actions"><label>最终结论</label><div class="ec-decision"><button class="is-active">不通过</button><button>通过</button></div><button class="ec-button primary">保存最终结论</button></div></aside></div>
    </section>`;
  }

  function pageContent() {
    if (state.page === "create") return createPage();
    if (state.page === "result") return resultPage();
    if (state.page === "review") return reviewPage();
    return taskCenterPage();
  }

  function saveState() { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }

  function syncControls() {
    $("#pageSelect").value = state.page;
    $("#referenceSelect").value = state.reference;
    $("#densitySelect").value = state.density;
    $("#viewportSelect").value = state.viewport;
    $$('[data-page]').forEach(button => button.classList.toggle("is-active", button.dataset.page === state.page));
    $$('[data-reference]').forEach(button => button.classList.toggle("is-active", button.dataset.reference === state.reference && state.mode === "reference"));
    $$('input[name="layout"]').forEach(input => { input.checked = input.value === state.shell_layout; });
    $$('input[name="color"]').forEach(input => { input.checked = input.value === state.color; });
    $$('[data-density]').forEach(button => button.classList.toggle("is-active", button.dataset.density === state.density));
    $("#summaryToggle").checked = state.display.summary;
    $("#asideToggle").checked = state.display.aside;
    $("#lockToggle").checked = state.display.lock;
    $("#replicaToggle").setAttribute("aria-pressed", String(state.mode === "reference"));
    $("#replicaToggle").textContent = state.mode === "reference" ? "返回 EvalCall" : "查看参考复刻";
    $$('[data-mode]').forEach(button => button.classList.toggle("is-active", button.dataset.mode === state.mode));
    const densityMetrics = { dense:[44,32,16], compact:[50,34,20], comfortable:[58,38,24] }[state.density];
    $("#rowHeightReadout").textContent = `${densityMetrics[0]} px`;
    $("#controlHeightReadout").textContent = `${densityMetrics[1]} px`;
    $("#pagePaddingReadout").textContent = `${densityMetrics[2]} px`;
  }

  function renderPreview() {
    const preview = $("#evalcallPreview");
    preview.dataset.layout = state.shell_layout;
    preview.dataset.color = state.color;
    preview.dataset.density = state.density;
    preview.classList.toggle("hide-summary", !state.display.summary);
    preview.classList.toggle("hide-aside", !state.display.aside);
    preview.classList.toggle("hide-lock", !state.display.lock);
    preview.innerHTML = shell(pageContent(), state.page);
    const viewport = $("#canvasViewport");
    viewport.classList.toggle("is-reference", state.mode === "reference");
    viewport.classList.remove("viewport-desktop","viewport-tablet","viewport-mobile");
    viewport.classList.add(`viewport-${state.viewport}`);
    const frame = $("#referenceFrame");
    const frameUrl = `./reference-replicas.html?view=${state.reference}`;
    if (!frame.src.endsWith(frameUrl.replace("./", ""))) frame.src = frameUrl;
    $("#previewTitle").textContent = state.mode === "reference" ? `${$("#referenceSelect").selectedOptions[0].textContent} · 复刻` : pageNames[state.page];
    $("#previewMeta").textContent = state.mode === "reference" ? "参考页面 · 原始结构比例" : `${layoutNames[state.shell_layout]} · ${colorNames[state.color]} · ${densityNames[state.density]}`;
    resizeCanvas();
  }

  function targetSize() {
    if (state.mode === "reference" && state.viewport === "desktop") return referenceSizes[state.reference];
    return viewportSizes[state.viewport];
  }

  function resizeCanvas() {
    const stage = $("#canvasStage");
    const viewport = $("#canvasViewport");
    if (!stage || !viewport) return;
    const [width,height] = targetSize();
    viewport.style.width = `${width}px`;
    viewport.style.height = `${height}px`;
    const stageWidth = stage.clientWidth;
    const stageHeight = stage.clientHeight;
    const scale = Math.min((stageWidth - 30) / width, (stageHeight - 36) / height, 1);
    const safeScale = Math.max(scale, .18);
    viewport.style.transform = `translateX(-50%) scale(${safeScale})`;
    $("#previewScale").textContent = `${Math.round(safeScale * 100)}%`;
  }

  function render() {
    syncControls();
    renderPreview();
    saveState();
  }

  function setPage(page) {
    if (!valid.page.includes(page)) return;
    state.page = page;
    state.mode = "design";
    render();
  }

  function setReference(reference, open = false) {
    if (!valid.reference.includes(reference)) return;
    state.reference = reference;
    if (open) state.mode = "reference";
    render();
  }

  function showToast(message) {
    const toast = $("#toast");
    toast.textContent = message;
    toast.classList.add("is-visible");
    clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => toast.classList.remove("is-visible"), 2200);
  }

  function exportConfig() {
    const exportData = {
      schema: "evalcall-b2b-theme/v3",
      exported_at: new Date().toISOString(),
      source_library: { schema: patternLibrary?.schema || "b2b-operations-design/v1", version: patternLibrary?.version || "unknown" },
      selection: clone(state),
      resolved: {
        page: pageNames[state.page], layout: layoutNames[state.shell_layout], scope: "全部页面", color: colorNames[state.color], density: densityNames[state.density],
        reference_source: patternLibrary?.reference_sources?.find(source => source.id.startsWith(state.reference))?.id || state.reference
      }
    };
    const blob = new Blob([`${JSON.stringify(exportData,null,2)}\n`], {type:"application/json"});
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `evalcall-b2b-theme-${new Date().toISOString().slice(0,10)}.json`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    showToast("方案 JSON 已导出");
  }

  function applyToDemo() {
    const applied = {
      schema: "evalcall-b2b-theme/v3",
      applied_at: new Date().toISOString(),
      active_page: state.page,
      shell_layout: state.shell_layout,
      page_layouts: Object.fromEntries(valid.page.map(page => [page, state.shell_layout])),
      color: state.color,
      density: state.density,
      display: clone(state.display),
      reference: state.reference,
      tokens: patternLibrary?.evalcall_target_tokens || null
    };
    try { localStorage.setItem(THEME_KEY, JSON.stringify(applied)); } catch (_) { /* URL bootstrap remains available */ }
    window.dispatchEvent(new CustomEvent("evalcall-theme-applied", {detail:applied}));
    showToast(`正在应用：全部页面 · ${layoutNames[state.shell_layout]}`);
    const target = resolveDemoUrl(applied);
    window.setTimeout(() => window.location.assign(target.href), 180);
  }

  function bindEvents() {
    $("#pageSelect").addEventListener("change", event => setPage(event.target.value));
    $("#referenceSelect").addEventListener("change", event => setReference(event.target.value, state.mode === "reference"));
    $("#densitySelect").addEventListener("change", event => { state.density = event.target.value; render(); });
    $("#viewportSelect").addEventListener("change", event => { state.viewport = event.target.value; render(); });
    $("#replicaToggle").addEventListener("click", () => { state.mode = state.mode === "reference" ? "design" : "reference"; render(); });
    $("#propertiesToggle").addEventListener("click", () => {
      const panel = $(".properties-panel");
      panel.classList.toggle("is-open");
      $("#propertiesToggle").setAttribute("aria-expanded", String(panel.classList.contains("is-open")));
    });
    $("#exportButton").addEventListener("click", exportConfig);
    $("#applyButton").addEventListener("click", applyToDemo);
    $("#resetButton").addEventListener("click", () => { state = clone(defaults); render(); showToast("已恢复默认方案"); });
    $("#pageDirectory").addEventListener("click", event => { const target = event.target.closest("[data-page]"); if (target) setPage(target.dataset.page); });
    $$(".reference-item").forEach(button => button.addEventListener("click", () => setReference(button.dataset.reference,true)));
    $$("[data-mode]").forEach(button => button.addEventListener("click", () => { state.mode = button.dataset.mode; render(); }));
    $$('input[name="layout"]').forEach(input => input.addEventListener("change", () => {
      state.shell_layout = input.value;
      for (const page of valid.page) state.layouts[page] = input.value;
      render();
    }));
    $$('input[name="color"]').forEach(input => input.addEventListener("change", () => { state.color = input.value; render(); }));
    $$('[data-density]').forEach(button => button.addEventListener("click", () => { state.density = button.dataset.density; render(); }));
    $("#summaryToggle").addEventListener("change", event => { state.display.summary = event.target.checked; render(); });
    $("#asideToggle").addEventListener("change", event => { state.display.aside = event.target.checked; render(); });
    $("#lockToggle").addEventListener("change", event => { state.display.lock = event.target.checked; render(); });
    $("#evalcallPreview").addEventListener("click", event => {
      const navigation = event.target.closest("[data-preview-page]");
      if (navigation) { event.preventDefault(); setPage(navigation.dataset.previewPage); return; }
      const action = event.target.closest("[data-task-action]");
      if (action) showToast(`${action.dataset.taskAction} · 预览交互`);
    });
    window.addEventListener("resize", resizeCanvas, {passive:true});
  }

  async function boot() {
    try {
      const response = await fetch("./component-library/b2b-operations-patterns.json");
      if (response.ok) patternLibrary = await response.json();
    } catch (_) { patternLibrary = null; }
    bindEvents();
    render();
  }

  boot();
})();
