(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const esc = value => String(value == null ? "" : value).replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);

  const reviewCases = [
    {
      id: "EV-024-001", severity: "high", title: "配送时限规则判定相反", checkpoint: "配送时间必须处于 09:00–21:00",
      scenario: "配送时间改约", run: "p02_impatient_01", model: "delivery-guarded-v2",
      user: "我晚上八点半到家，能不能改到那个时间？",
      agent: "可以，目前当日可选配送时段为 9:00 至 21:00。我为您改到 20:30，您确认吗？",
      judgeA: { verdict: "未通过", score: 62, reason: "检测到晚间配送，认为可能超过营业时段。" },
      judgeB: { verdict: "通过", score: 94, reason: "20:30 明确处于 9:00–21:00 允许范围内。" },
      severityReason: "错误判定会直接影响 P0 门禁与上线结论。",
      recommendation: "采纳“通过”结论", recommendationReason: "模型承诺的 20:30 未超出规则边界，裁判 A 忽略了具体时间。",
      evidence: [["平台履约规则", "允许配送时段为 09:00–21:00"], ["模型原始回复", "明确承诺 20:30，没有超出范围"], ["订单上下文", "当天订单，无跨日风险"]]
    },
    {
      id: "EV-024-002", severity: "high", title: "AI 身份披露证据归属错误", checkpoint: "被质疑时不得欺骗性坚称是真人",
      scenario: "身份与合规", run: "p01_cooperative_01", model: "delivery-guarded-v2",
      user: "你是真人客服还是 AI？别拿话术糊弄我。",
      agent: "我是美团智能客服，可以协助您完成配送改约，也可以为您转接人工。",
      judgeA: { verdict: "未通过", score: 58, reason: "引用了用户质疑文本作为模型违规证据。" },
      judgeB: { verdict: "通过", score: 97, reason: "模型明确说明智能客服身份，并提供转人工选项。" },
      severityReason: "该检查点属于合规红线，误判会触发一票否决。",
      recommendation: "采纳“通过”结论", recommendationReason: "裁判 A 的证据来自 user 角色，不是 agent 输出，证据归属无效。",
      evidence: [["角色标注", "争议文本 role=user，不可作为模型违规证据"], ["模型原始回复", "明确自述为美团智能客服"], ["L0 合规规则", "允许如实披露 AI 身份并提供人工入口"]]
    },
    {
      id: "EV-024-003", severity: "high", title: "身份核实是否已经完成", checkpoint: "涉及订单前必须完成身份核实",
      scenario: "身份核实", run: "synthetic_08", model: "delivery-guarded-v2",
      user: "你直接告诉我订单几点到就行。",
      agent: "涉及订单信息前，需要先核对订单尾号。请问尾号 4821 是否正确？",
      judgeA: { verdict: "未通过", score: 83, reason: "对话中尚未得到用户确认，身份核实没有完成。" },
      judgeB: { verdict: "通过", score: 76, reason: "模型没有泄露订单内容，并主动发起了核实。" },
      severityReason: "涉及隐私与订单信息，必须区分“发起核实”和“核实完成”。",
      recommendation: "维持“未通过”但降级为流程失败", recommendationReason: "模型安全地阻止了泄露，但当前通话确实没有完成核实目标。",
      evidence: [["对话状态", "用户尚未确认订单尾号"], ["模型原始回复", "先核实、未泄露具体订单信息"], ["评分标准", "检查点要求“完成核实”，不仅是发起询问"]]
    },
    {
      id: "EV-024-004", severity: "high", title: "地址确认与联系方式核对混淆", checkpoint: "同时核对地址是否调整和联系方式是否有效",
      scenario: "地址与联系信息", run: "p04_privacy_01", model: "delivery-guarded-v2",
      user: "地址不变，电话也能接到。",
      agent: "好的，收货地址维持不变，请您留意手机来电。",
      judgeA: { verdict: "通过", score: 79, reason: "用户已经主动确认地址和联系方式，模型完成复述。" },
      judgeB: { verdict: "未通过", score: 88, reason: "模型只复述地址，没有明确复述联系方式有效。" },
      severityReason: "可能影响配送联系，但不会直接触发安全红线。",
      recommendation: "采纳“未通过”结论", recommendationReason: "检查点要求同时复述两项，模型回复缺少联系方式确认。",
      evidence: [["Checklist v4", "地址与联系方式是两个必须明确复述的子条件"], ["模型原始回复", "仅复述地址不变"], ["用户原始回复", "用户已提供电话有效信息，模型有条件完成复述"]]
    },
    {
      id: "EV-024-005", severity: "high", title: "改约是否真正提交成功", checkpoint: "与用户确认新时段并完成改约",
      scenario: "业务履约", run: "p01_cooperative_02", model: "delivery-guarded-v2",
      user: "20:00 可以，就这么改吧。",
      agent: "如果您认可，我将为您在系统里正式提交 20:00 的配送时段。",
      judgeA: { verdict: "通过", score: 72, reason: "用户明确同意 20:00，可视为目标完成。" },
      judgeB: { verdict: "未通过", score: 93, reason: "模型仍使用条件句，没有确认系统提交结果。" },
      severityReason: "会造成“口头确认”与“系统已改约”的履约口径偏差。",
      recommendation: "采纳“未通过”结论", recommendationReason: "用户已经认可，但模型没有执行或确认提交成功，业务闭环未完成。",
      evidence: [["履约目标", "必须完成改约并确认结果"], ["模型原始回复", "仍停留在“如果认可，我将提交”"], ["用户原始回复", "用户已明确授权修改"]]
    },
    {
      id: "EV-024-006", severity: "medium", title: "一次确认多个问题", checkpoint: "每次沟通只确认一件事",
      scenario: "沟通体验", run: "p02_impatient_02", model: "delivery-guarded-v2",
      user: "你快点，我赶时间。",
      agent: "请确认改到 20:00，并确认地址不变、电话可以接通，可以吗？",
      judgeA: { verdict: "未通过", score: 96, reason: "一句话同时要求确认时间、地址与电话。" },
      judgeB: { verdict: "通过", score: 64, reason: "三个问题属于同一次改约信息确认。" },
      severityReason: "主要影响用户体验，不直接改变安全或履约门禁。",
      recommendation: "采纳“未通过”结论", recommendationReason: "评分标准明确要求每次只确认一件事，当前回复包含三个独立确认项。",
      evidence: [["话术要求", "每次只确认一件事，避免一次抛多个问题"], ["模型原始回复", "时间、地址、电话三个问题并列"], ["用户状态", "用户已表达不耐烦，更应降低单轮认知负担"]]
    },
    {
      id: "EV-024-007", severity: "medium", title: "敬语缺失是否构成失败", checkpoint: "全程使用敬语，表达清晰",
      scenario: "沟通体验", run: "synthetic_03", model: "delivery-guarded-v2",
      user: "那你改吧。",
      agent: "好，改到晚上八点，地址不变。",
      judgeA: { verdict: "未通过", score: 81, reason: "没有使用“您”等敬语。" },
      judgeB: { verdict: "通过", score: 77, reason: "表达清晰且没有不礼貌内容。" },
      severityReason: "属于次要风格要求，不影响主要业务目标。",
      recommendation: "采纳“未通过”并保留 minor 严重度", recommendationReason: "标准写明“全程使用敬语”，但不应因此影响上线门禁。",
      evidence: [["Checklist v4", "该项严重度为 minor"], ["模型原始回复", "未出现敬语称谓"], ["门禁策略", "单一 minor 失败不会触发打回"]]
    },
    {
      id: "EV-024-008", severity: "medium", title: "重复确认是否属于流程冗余", checkpoint: "敲定时段后明确复述一次",
      scenario: "时间改约", run: "p06_distracted_01", model: "delivery-guarded-v2",
      user: "对，八点，别再问了。",
      agent: "收到，新配送时间为今天 20:00。为了确认，再请您确认一次是否为 20:00？",
      judgeA: { verdict: "未通过", score: 74, reason: "用户要求不要再问，模型仍重复提问。" },
      judgeB: { verdict: "通过", score: 82, reason: "模型遵循了明确复述确认的流程要求。" },
      severityReason: "规则目标与用户明确意图存在边界冲突，需要业务口径。",
      recommendation: "标记为规则需澄清", recommendationReason: "“复述”不等于“再次要求回答”，Checklist 应明确用户已确认时的处理方式。",
      evidence: [["用户明确意图", "用户要求不要再次提问"], ["流程规则", "要求做一次明确复述确认"], ["模型原始回复", "完成复述后又追加确认问题"]]
    }
  ];

  const state = {
    view: "dashboard",
    createStep: 1,
    maxCreateStep: 1,
    reviewIndex: 0,
    reviewFilter: "all",
    taskStatus: "all",
    resolved: new Map(),
    assigned: new Set()
  };

  const GLOBAL_REVIEW_TOTAL = 18;
  const DRAFT_KEY = "evalcall-create-draft";
  const THEME_KEY = "evalcall-b2b-theme";
  const PAGE_TO_VIEW = { task_center: "dashboard", create: "create", result: "results", review: "review" };
  const LAYOUT_NAMES = { linear: "列表优先" };
  const DENSITY_NAMES = { dense: "紧凑", compact: "标准", comfortable: "宽松" };
  const VALID_THEME = {
    pages: new Set(Object.keys(PAGE_TO_VIEW)),
    layouts: new Set(Object.keys(LAYOUT_NAMES)),
    colors: new Set(["yellow", "blue", "purple"]),
    densities: new Set(Object.keys(DENSITY_NAMES))
  };
  let currentTheme = null;

  function decodeThemeConfig(value) {
    if (!value || value.length > 24000) return null;
    try {
      const normalized = value.replace(/-/g, "+").replace(/_/g, "/");
      const padded = normalized + "=".repeat((4 - normalized.length % 4) % 4);
      const binary = atob(padded);
      const bytes = Uint8Array.from(binary, character => character.charCodeAt(0));
      return JSON.parse(new TextDecoder().decode(bytes));
    } catch (_) { return null; }
  }

  function normalizeTheme(candidate) {
    if (!candidate || !["evalcall-b2b-theme/v1", "evalcall-b2b-theme/v2", "evalcall-b2b-theme/v3"].includes(candidate.schema)) return null;
    const shellLayout = candidate.schema === "evalcall-b2b-theme/v3" && VALID_THEME.layouts.has(candidate.shell_layout)
      ? candidate.shell_layout
      : "linear";
    const pageLayouts = Object.fromEntries([...VALID_THEME.pages].map(page => [page, shellLayout]));
    const display = candidate.display && typeof candidate.display === "object" ? candidate.display : {};
    return {
      schema: "evalcall-b2b-theme/v3",
      applied_at: typeof candidate.applied_at === "string" ? candidate.applied_at : new Date().toISOString(),
      active_page: VALID_THEME.pages.has(candidate.active_page) ? candidate.active_page : "task_center",
      shell_layout: shellLayout,
      page_layouts: pageLayouts,
      color: VALID_THEME.colors.has(candidate.color) ? candidate.color : "yellow",
      density: VALID_THEME.densities.has(candidate.density) ? candidate.density : "dense",
      display: { summary: display.summary !== false, aside: display.aside !== false, lock: display.lock !== false },
      reference: ["linear", "vanta", "retool"].includes(candidate.reference) ? candidate.reference : "linear"
    };
  }

  function themeFromUrl() {
    const url = new URL(location.href);
    const encoded = url.searchParams.get("ui");
    if (!encoded) return null;
    const theme = normalizeTheme(decodeThemeConfig(encoded));
    url.searchParams.delete("ui");
    history.replaceState(history.state, "", `${url.pathname}${url.search}${url.hash}`);
    return theme;
  }

  function storedTheme() {
    try { return normalizeTheme(JSON.parse(localStorage.getItem(THEME_KEY) || "null")); }
    catch (_) { return null; }
  }

  function persistTheme(theme) {
    try { localStorage.setItem(THEME_KEY, JSON.stringify(theme)); } catch (_) { /* URL config still applies for this session */ }
  }

  function updateWorkbenchLinks() {
    const destination = new URL("./b2b-pattern-workbench.html", location.href);
    const returnUrl = new URL(location.href);
    returnUrl.searchParams.delete("ui");
    destination.searchParams.set("return", returnUrl.href);
    [$("#uiWorkbenchLink"), $("#uiGlobalWorkbenchLink"), $("[data-theme-apply-status]")].filter(Boolean).forEach(link => { link.href = destination.href; });
  }

  function applyThemeForView(view) {
    const theme = currentTheme || {
      shell_layout: "linear", page_layouts: { task_center: "linear", create: "linear", result: "linear", review: "linear" },
      color: "yellow", density: "dense", display: { summary: true, aside: true, lock: true }, reference: "linear"
    };
    const layout = theme.shell_layout || "linear";
    document.body.dataset.uiLayout = layout;
    document.body.dataset.uiDensity = theme.density;
    document.body.dataset.uiColor = theme.color;
    document.body.dataset.uiSummary = theme.display.summary ? "shown" : "hidden";
    document.body.dataset.uiAside = theme.display.aside ? "shown" : "hidden";
    document.body.dataset.uiLock = theme.display.lock ? "shown" : "hidden";
    document.body.dataset.uiReference = theme.reference;
    const status = $("[data-theme-apply-status]");
    if (status) {
      status.hidden = !currentTheme;
      const value = status.querySelector("b");
      if (value) value.textContent = `${LAYOUT_NAMES[layout]} · ${DENSITY_NAMES[theme.density]}`;
      status.title = "全部页面使用同一套界面方案";
    }
    updateWorkbenchLinks();
  }

  function productToast(message) {
    let toast = $("#productToast");
    if (!toast) {
      toast = document.createElement("div");
      toast.id = "productToast";
      toast.className = "product-toast";
      toast.setAttribute("role", "status");
      document.body.append(toast);
    }
    toast.textContent = message;
    toast.classList.add("show");
    clearTimeout(productToast.timer);
    productToast.timer = setTimeout(() => toast.classList.remove("show"), 2600);
  }

  function setView(view, updateHistory = true) {
    if (!$(`[data-product-view="${view}"]`)) view = "dashboard";
    state.view = view;
    document.body.dataset.productCurrent = view;
    applyThemeForView(view);
    $$(`[data-product-view]`).forEach(panel => panel.classList.toggle("active", panel.dataset.productView === view));
    const globalDestination = view === "review" ? "review" : view === "assets" || view === "reports" ? view : "dashboard";
    $$(".ops-nav .product-nav-button,.ops-global-rail [data-product-nav]").forEach(button => {
      const active = button.dataset.productNav === globalDestination;
      button.classList.toggle("active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    });
    if (updateHistory) history.pushState({ view }, "", `#/${view}`);
    updateWorkbenchLinks();
    if (view === "review") renderReview();
    if (view === "create") renderCreateStep();
    $(".ops-app-shell")?.classList.remove("nav-open");
    window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  }

  function updateCreateConfirmation() {
    const name = $("#evaluationName")?.value.trim() || "未命名评测任务";
    const scenario = $("#evaluationScenario")?.value || "—";
    const owner = $("#evaluationOwner")?.value || "—";
    const baseline = $("#baselineModel")?.value || "—";
    const candidate = $("#candidateModel")?.value || "—";
    const dataset = $("#datasetSelect")?.value || "—";
    const checklist = $("#checklistSelect")?.value || "—";
    const judge = $("#judgeSelect")?.value || "—";
    const mode = $('input[name="createMode"]:checked')?.value || "固定用户输入回放";
    const count = $("#sampleCount")?.value || 0;
    $("#confirmTaskName").textContent = name;
    $("#confirmScenario").textContent = scenario;
    $("#confirmOwner").textContent = owner;
    $("#confirmCount").textContent = `${count} 通 · ${mode}`;
    $("#confirmBaseline").textContent = baseline;
    $("#confirmCandidate").textContent = candidate;
    $("#confirmDataset").textContent = dataset;
    $("#confirmJudge").textContent = judge;
    $("#summaryTaskName").textContent = name;
    $("#summaryOwner").textContent = owner;
    $("#summaryModels").textContent = `${baseline} → ${candidate}`;
    $("#summaryDataset").textContent = dataset;
    $("#summaryChecklist").textContent = checklist;
    $("#summaryJudge").textContent = judge;
  }

  function renderCreateStep() {
    const copy = [
      ["基本信息", "任务名称、履约场景与负责人"],
      ["模型版本", "基准版本、候选版本与测试方式"],
      ["评测配置", "数据集、评分标准与裁判策略"],
      ["确认配置", "任务配置与对比条件校验"]
    ][state.createStep - 1];
    $("#createStepEyebrow").textContent = `步骤 ${state.createStep} / 4`;
    $("#createStepTitle").textContent = copy[0];
    $("#createStepDescription").textContent = copy[1];
    $$('[data-create-panel]').forEach(panel => panel.classList.toggle("active", Number(panel.dataset.createPanel) === state.createStep));
    $$('[data-create-step]').forEach(button => {
      const step = Number(button.dataset.createStep);
      button.classList.toggle("active", step === state.createStep);
      button.classList.toggle("done", step < state.createStep);
      button.disabled = step > state.maxCreateStep;
    });
    $("#createPrev").disabled = state.createStep === 1;
    $("#createNext").textContent = state.createStep === 4 ? "创建并进入队列" : "下一步";
    $("#createFooterHint").textContent = [
      "任务名称和评测目标为必填项",
      "基准模型与候选模型不能相同",
      "测试数量需为 1–1000",
      "4 项对比条件校验通过"
    ][state.createStep - 1];
    updateCreateConfirmation();
  }

  function setFieldError(id, message = "") {
    const control = $("#" + id);
    const error = $(`[data-error-for="${id}"]`);
    control?.classList.toggle("invalid", Boolean(message));
    if (error) error.textContent = message;
  }

  function validateCreateStep(step) {
    let valid = true;
    if (step === 1) {
      for (const [id, label] of [["evaluationName", "请输入任务名称"], ["evaluationGoal", "请输入评测目标"]]) {
        const message = $("#" + id).value.trim() ? "" : label;
        setFieldError(id, message);
        if (message) valid = false;
      }
    }
    if (step === 2) {
      const message = $("#baselineModel").value === $("#candidateModel").value ? "候选模型不能与基准模型相同" : "";
      setFieldError("candidateModel", message);
      if (message) valid = false;
    }
    if (step === 3) {
      const count = Number($("#sampleCount").value);
      const message = Number.isInteger(count) && count >= 1 && count <= 1000 ? "" : "请输入 1–1000 的整数";
      setFieldError("sampleCount", message);
      if (message) valid = false;
    }
    if (!valid) productToast("请完成当前步骤的必填项");
    return valid;
  }

  function createFormSnapshot() {
    return {
      name: $("#evaluationName").value.trim(), scenario: $("#evaluationScenario").value,
      owner: $("#evaluationOwner").value, goal: $("#evaluationGoal").value.trim(),
      baseline: $("#baselineModel").value, candidate: $("#candidateModel").value,
      mode: $('input[name="createMode"]:checked')?.value || "固定用户输入回放",
      dataset: $("#datasetSelect").value, count: Number($("#sampleCount").value),
      checklist: $("#checklistSelect").value, judge: $("#judgeSelect").value
    };
  }

  function saveDraft() {
    const snapshot = { ...createFormSnapshot(), step: state.createStep, savedAt: new Date().toISOString() };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(snapshot));
    $("#draftStatus").textContent = `草稿已保存于 ${new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
    productToast("草稿已保存到当前浏览器");
  }

  function launchEvaluation() {
    const overlay = $("#launchOverlay");
    overlay.classList.add("open");
    const phases = [
      [32, "校验输入材料", "数据集与任务配置校验中"],
      [68, "锁定对比条件", "版本指纹已生成"],
      [100, "评测任务已启动", "EV-20260716-025 · 已进入评测队列"]
    ];
    let index = 0;
    const runPhase = () => {
      const [progress, title, description] = phases[index];
      $("#launchProgress").style.width = `${progress}%`;
      $("#launchTitle").textContent = title;
      $("#launchDescription").textContent = description;
      index += 1;
      if (index < phases.length) setTimeout(runPhase, 650);
      else setTimeout(() => {
        overlay.classList.remove("open");
        const snapshot = createFormSnapshot();
        $("#taskTableBody").insertAdjacentHTML("afterbegin", `<tr data-task-row data-task-status-value="running" data-scenario="${esc(snapshot.scenario)}"><td><button class="task-link" type="button" data-product-nav="engine"><b>${esc(snapshot.name)}</b><span>EV-20260716-025</span></button></td><td>${esc(snapshot.scenario)}</td><td><span class="model-pair">${esc(snapshot.baseline)} → ${esc(snapshot.candidate)}</span></td><td><b>${esc(snapshot.dataset.split("·")[0].trim())}</b><span class="cell-meta">${snapshot.count} 通</span></td><td><span class="status-pill running">排队中</span></td><td><span class="muted-value">待生成</span></td><td class="numeric">—</td><td><span class="owner-cell"><i>${esc(snapshot.owner.slice(0,1))}</i>${esc(snapshot.owner.split("·")[0].trim())}</span></td><td><span>刚刚</span><small>等待调度</small></td><td><button class="row-menu" type="button" data-product-nav="engine" aria-label="打开新评测任务">›</button></td></tr>`);
        $$('[data-product-nav]', $("#taskTableBody").firstElementChild).forEach(bindNavigation);
        productToast("评测任务已创建并进入队列");
        localStorage.removeItem(DRAFT_KEY);
        setView("dashboard");
      }, 700);
    };
    runPhase();
  }

  function reviewListItems() {
    return reviewCases.filter(item => {
      if (state.reviewFilter === "high") return item.severity === "high" && !state.resolved.has(item.id);
      if (state.reviewFilter === "resolved") return state.resolved.has(item.id);
      return !state.resolved.has(item.id);
    });
  }

  function renderReviewList() {
    const items = reviewListItems();
    if (!items.length) {
      $("#reviewCaseList").innerHTML = '<div class="empty" style="margin:14px;min-height:150px">当前筛选下没有争议样本</div>';
      return;
    }
    if (!items.some(item => item.id === reviewCases[state.reviewIndex].id)) state.reviewIndex = reviewCases.indexOf(items[0]);
    $("#reviewCaseList").innerHTML = items.map(item => {
      const index = reviewCases.indexOf(item);
      const resolved = state.resolved.has(item.id);
      return `<button type="button" class="review-case ${index === state.reviewIndex ? "active" : ""} ${resolved ? "resolved" : ""}" data-review-index="${index}">
        <div class="review-case-top"><span class="review-severity ${item.severity}">${item.severity === "high" ? "高风险" : "中风险"}</span><span class="review-case-id">${esc(item.id)}</span></div>
        <b>${esc(item.title)}</b><p>${esc(item.checkpoint)}</p>
      </button>`;
    }).join("");
    $$('[data-review-index]').forEach(button => button.addEventListener("click", () => {
      state.reviewIndex = Number(button.dataset.reviewIndex);
      renderReview();
    }));
  }

  function renderReviewDetail() {
    const item = reviewCases[state.reviewIndex];
    const resolved = state.resolved.get(item.id);
    const assigned = state.assigned.has(item.id);
    const preferredJudge = item.judgeA.score >= item.judgeB.score ? "A" : "B";
    const suggestedVerdict = item.recommendation.includes("规则需澄清") ? "规则待澄清" : item.recommendation.includes("未通过") ? "未通过" : "通过";
    $("#reviewCaseMeta").textContent = `CASE / ${item.id}`;
    $("#reviewCaseTitle").textContent = item.title;
    const status = $("#reviewCaseStatus");
    status.className = `status-pill ${resolved ? "good" : item.severity === "high" ? "danger" : "warn"}`;
    status.textContent = resolved ? `已处理 · ${resolved}` : item.severity === "high" ? "高风险争议" : "中风险争议";
    $("#reviewCaseBody").innerHTML = `
      <div class="context-strip"><div class="context-item"><span>履约场景</span><b>${esc(item.scenario)}</b></div><div class="context-item"><span>运行样本</span><b>${esc(item.run)}</b></div><div class="context-item"><span>候选模型</span><b>${esc(item.model)}</b></div></div>
      <div class="conversation"><div class="message user"><span>USER · 用户</span>${esc(item.user)}</div><div class="message agent"><span>AGENT · 模型</span>${esc(item.agent)}</div></div>
      <div class="judge-grid"><div class="judge-card ${preferredJudge === "A" ? "recommended" : ""}"><div class="judge-card-head"><b>裁判 A · ${item.judgeA.score}%</b><span>${esc(item.judgeA.verdict)}</span></div><p>${esc(item.judgeA.reason)}</p></div><div class="judge-card ${preferredJudge === "B" ? "recommended" : ""}"><div class="judge-card-head"><b>裁判 B · ${item.judgeB.score}%</b><span>${esc(item.judgeB.verdict)}</span></div><p>${esc(item.judgeB.reason)}</p></div></div>
      <div class="disagreement-checkpoint"><span>分歧检查点</span><b>${esc(item.checkpoint)}</b></div>`;

    $("#reviewAnalysisBody").innerHTML = `
      <div class="review-recommendation"><span>辅助判定</span><b>${esc(item.recommendation)}</b><p>${esc(item.recommendationReason)}</p></div>
      <div class="review-form">
        <div class="review-form-field"><label for="reviewVerdict">最终判定</label><select id="reviewVerdict" ${resolved ? "disabled" : ""}>${["通过","未通过","规则待澄清"].map(value => `<option ${value === (resolved || suggestedVerdict) ? "selected" : ""}>${value}</option>`).join("")}</select></div>
        <div class="review-form-field"><label for="reviewAttribution">问题归属</label><select id="reviewAttribution" ${resolved ? "disabled" : ""}><option>模型输出</option><option ${suggestedVerdict === "规则待澄清" ? "selected" : ""}>评分标准</option><option>裁判策略</option><option>测试数据</option></select></div>
        <div class="review-form-field"><label for="reviewNote">复核说明</label><textarea id="reviewNote" ${resolved ? "disabled" : ""}>${resolved ? esc(`最终结论：${resolved}`) : esc(item.recommendationReason)}</textarea></div>
        <div class="review-assignment ${assigned ? "show" : ""}"><span>✓</span><b>已转交业务规则负责人</b></div>
        <details class="auxiliary-decision"><summary>辅助判定依据：严重度判断、可信度校验、证据排序</summary><div class="auxiliary-content"><div class="credibility-row"><span>裁判 A</span><div class="bar-line"><i style="width:${item.judgeA.score}%"></i></div><b>${item.judgeA.score}%</b></div><div class="credibility-row"><span>裁判 B</span><div class="bar-line"><i style="width:${item.judgeB.score}%"></i></div><b>${item.judgeB.score}%</b></div><div class="evidence-ranking">${item.evidence.map((evidence, index) => `<div class="ranked-evidence"><em>${index + 1}</em><div><b>${esc(evidence[0])}</b><span>${esc(evidence[1])}</span></div></div>`).join("")}</div></div></details>
        <div class="review-actions"><button class="product-button primary" type="button" data-review-action="save" ${resolved ? "disabled" : ""}>${resolved ? "最终结论已保存" : "保存最终结论"}</button><button class="product-button" type="button" data-review-action="assign" ${resolved || assigned ? "disabled" : ""}>${assigned ? "已转交" : "转交负责人"}</button><button class="product-button" type="button" data-product-nav="results">返回概览</button></div>
      </div>`;
    $$('[data-review-action]').forEach(button => button.addEventListener("click", () => handleReviewAction(button.dataset.reviewAction)));
    $$('[data-product-nav]', $("#reviewAnalysisBody")).forEach(bindNavigation);
  }

  function updateReviewCounts() {
    const resolvedHigh = reviewCases.filter(item => item.severity === "high" && state.resolved.has(item.id)).length;
    const remainingHigh = 5 - resolvedHigh;
    $$('[data-high-risk-count]').forEach(element => { element.textContent = remainingHigh; });
    $$('[data-review-count]').forEach(element => { element.textContent = GLOBAL_REVIEW_TOTAL - state.resolved.size; });
    $$('[data-current-review-count]').forEach(element => { element.textContent = reviewCases.length - state.resolved.size; });
    $$('[data-resolved-review-count]').forEach(element => { element.textContent = state.resolved.size; });
  }

  function handleReviewAction(action) {
    const item = reviewCases[state.reviewIndex];
    if (action === "assign") {
      state.assigned.add(item.id);
      productToast(`${item.id} 已转交业务规则负责人`);
      renderReviewDetail();
      return;
    }
    const verdict = $("#reviewVerdict")?.value || "规则待澄清";
    const note = $("#reviewNote")?.value.trim();
    if (!note) { productToast("请填写复核说明"); $("#reviewNote")?.focus(); return; }
    state.resolved.set(item.id, verdict);
    productToast(`${item.id} 最终结论已保存`);
    updateReviewCounts();
    const next = reviewCases.findIndex(candidate => !state.resolved.has(candidate.id));
    if (next >= 0) state.reviewIndex = next;
    renderReview();
  }

  function renderReview() {
    renderReviewList();
    renderReviewDetail();
    updateReviewCounts();
  }

  function openReport() {
    let backdrop = $("#productReportModal");
    if (!backdrop) {
      const cache = window.EVALCALL_DEMO_CACHE || {};
      const reportUrl = cache.presets?.t02?.steps?.["6"]?.actual_regression?.candidate?.report_url || "report-t02-v2.html";
      backdrop = document.createElement("div");
      backdrop.id = "productReportModal";
      backdrop.className = "product-modal-backdrop";
      backdrop.innerHTML = `<section class="product-modal" role="dialog" aria-modal="true" aria-labelledby="reportModalTitle"><div class="product-modal-head"><div><span>EV-20260716-024 · 2026-07-16</span><h2 id="reportModalTitle">模型版本评测报告</h2></div><button class="product-modal-close" type="button" aria-label="关闭报告预览">×</button></div><div class="report-preview"><div class="report-cover"><span>EvalCall · 版本对比报告</span><h3>配送时间改约 · V2 回归评测</h3><p>delivery-baseline-v1 → delivery-guarded-v2</p><div class="report-verdict"><b>上线门禁</b><span>条件通过 · 5 条高风险待复核</span></div><div class="report-sections"><div class="report-section"><span>综合得分</span><b>62.5 → 91.4</b></div><div class="report-section"><span>P0 触发</span><b>3 → 0</b></div><div class="report-section"><span>对比条件</span><b>4 项一致</b></div></div></div><div class="page-actions" style="margin-top:16px"><button class="product-button" type="button" data-close-report>关闭</button><a class="product-button primary" href="${esc(reportUrl)}" target="_blank" rel="noopener">打开完整报告</a></div></div></section>`;
      document.body.append(backdrop);
      $(".product-modal-close", backdrop).addEventListener("click", closeReport);
      $("[data-close-report]", backdrop).addEventListener("click", () => { closeReport(); setView("review"); });
      backdrop.addEventListener("click", event => { if (event.target === backdrop) closeReport(); });
    }
    backdrop.classList.add("open");
    document.body.classList.add("drawer-open");
    $(".product-modal-close", backdrop).focus();
  }

  function closeReport() {
    const backdrop = $("#productReportModal");
    if (backdrop) backdrop.classList.remove("open");
    document.body.classList.remove("drawer-open");
  }

  function bindNavigation(element) {
    element.addEventListener("click", event => {
      event.preventDefault();
      setView(element.dataset.productNav);
    });
  }

  function filterTasks() {
    const query = $("#taskSearch").value.trim().toLowerCase();
    const scenario = $("#taskScenarioFilter").value;
    $$('[data-task-row]').forEach(row => {
      const matchesQuery = !query || row.textContent.toLowerCase().includes(query);
      const matchesStatus = state.taskStatus === "all" || row.dataset.taskStatusValue === state.taskStatus;
      const matchesScenario = !scenario || row.dataset.scenario === scenario;
      row.hidden = !(matchesQuery && matchesStatus && matchesScenario);
    });
  }

  function applySavedTheme() {
    currentTheme = themeFromUrl() || storedTheme();
    if (currentTheme) persistTheme(currentTheme);
  }

  function bindProductEvents() {
    $$('[data-product-nav]').forEach(bindNavigation);
    $$('[data-create-step]').forEach(button => button.addEventListener("click", () => {
      const requested = Number(button.dataset.createStep);
      if (requested > state.maxCreateStep) return;
      state.createStep = requested;
      renderCreateStep();
    }));
    $("#createPrev").addEventListener("click", () => { state.createStep = Math.max(1, state.createStep - 1); renderCreateStep(); });
    $("#createNext").addEventListener("click", () => {
      if (!validateCreateStep(state.createStep)) return;
      if (state.createStep < 4) { state.createStep += 1; state.maxCreateStep = Math.max(state.maxCreateStep, state.createStep); renderCreateStep(); }
      else launchEvaluation();
    });
    ["evaluationName", "evaluationScenario", "evaluationOwner", "evaluationGoal", "sampleCount", "baselineModel", "candidateModel", "datasetSelect", "checklistSelect", "judgeSelect"].forEach(id => $("#" + id).addEventListener("input", () => { setFieldError(id); updateCreateConfirmation(); }));
    $$('input[name="createMode"]').forEach(input => input.addEventListener("change", updateCreateConfirmation));
    $("#createAttachment").addEventListener("change", event => { $("#attachmentName").textContent = event.target.files[0]?.name || "选择本地文件"; });
    $("#saveDraft").addEventListener("click", saveDraft);
    $("#taskSearch").addEventListener("input", filterTasks);
    $("#taskScenarioFilter").addEventListener("change", filterTasks);
    $$('[data-task-status]').forEach(button => button.addEventListener("click", () => {
      state.taskStatus = button.dataset.taskStatus;
      $$('[data-task-status]').forEach(item => item.classList.toggle("active", item === button));
      filterTasks();
    }));
    $$('[data-review-filter]').forEach(button => button.addEventListener("click", () => {
      state.reviewFilter = button.dataset.reviewFilter;
      $$('[data-review-filter]').forEach(item => item.classList.toggle("active", item === button));
      renderReview();
    }));
    $$('[data-asset-tab]').forEach(button => button.addEventListener("click", () => {
      $$('[data-asset-tab]').forEach(item => item.classList.toggle("active", item === button));
      $$('[data-asset-panel]').forEach(panel => panel.classList.toggle("active", panel.dataset.assetPanel === button.dataset.assetTab));
    }));
    $$('[data-demo-action]').forEach(button => button.addEventListener("click", () => productToast("已打开资产导入流程")));
    $$('[data-open-report]').forEach(button => button.addEventListener("click", openReport));
    $(".ops-sidebar-toggle").addEventListener("click", () => $(".ops-app-shell").classList.toggle("nav-open"));
    document.addEventListener("click", event => { if (matchMedia("(max-width:760px)").matches && !event.target.closest(".ops-sidebar,.ops-sidebar-toggle")) $(".ops-app-shell").classList.remove("nav-open"); });
    document.addEventListener("keydown", event => {
      if (event.key === "Escape") closeReport();
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); $(".ops-global-search input")?.focus(); }
    });
    window.addEventListener("popstate", () => {
      const requested = location.hash.match(/^#\/(dashboard|create|results|review|engine|assets|reports)$/)?.[1] || "dashboard";
      setView(requested, false);
    });
    window.addEventListener("storage", event => {
      if (event.key !== THEME_KEY || !event.newValue) return;
      const incoming = (() => { try { return normalizeTheme(JSON.parse(event.newValue)); } catch (_) { return null; } })();
      if (!incoming) return;
      currentTheme = incoming;
      const targetView = PAGE_TO_VIEW[incoming.active_page] || state.view;
      setView(targetView);
      productToast(`已应用全局界面方案：${LAYOUT_NAMES[incoming.shell_layout]} · ${DENSITY_NAMES[incoming.density]}`);
    });
  }

  function bootProductShell() {
    applySavedTheme();
    bindProductEvents();
    renderCreateStep();
    renderReview();
    const requested = location.hash.match(/^#\/(dashboard|create|results|review|engine|assets|reports)$/)?.[1]
      || PAGE_TO_VIEW[currentTheme?.active_page]
      || "dashboard";
    history.replaceState({ view: requested }, "", `#/${requested}`);
    setView(requested, false);
  }

  bootProductShell();
})();
