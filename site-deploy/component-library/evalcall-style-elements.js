(function(){
  "use strict";
  const categories=[
    {id:"composition",name:"00 · 页面编排与区域关系",description:"决定输入、输出与流程图如何共同构成一页，而不是只设计孤立控件。"},
    {id:"shell",name:"01 · 品牌与页面骨架",description:"决定第一眼的气质、空间尺度与产品可信度。"},
    {id:"navigation",name:"02 · 导航与阶段定位",description:"帮助用户理解现在在哪、接下来去哪里。"},
    {id:"flow",name:"03 · 动态流程与信号树",description:"把输入、处理与输出的传导过程变成主视觉。"},
    {id:"input",name:"04 · 输入与任务配置",description:"承载模式、素材、模型与参数的选择。"},
    {id:"content",name:"05 · 数据与证据表达",description:"把指标、判定、问题与产物组织成可扫描信息。"},
    {id:"feedback",name:"06 · 状态反馈与覆盖层",description:"解释运行状态、错误、证据详情和临时反馈。"}
  ];
  const C=(id,name,category,selector,atlasElement,description,location)=>({id,name,category,selector,atlasElement,description,location});
  const components=[
    C("stage-layout","页面主编排","composition",".stage-layout","workspace-layout","决定左侧输入输出与右侧流程树的宽度、层级和主次。","每一步内容主舞台"),
    C("io-stack","输入输出组合","composition",".io-stack","io-arrangement","决定输入与输出是纵向堆叠、并列切换还是紧凑摘要。","每一步左侧"),
    C("flow-orientation","流程树方向","composition",".flow-map","flow-orientation","决定处理节点采用纵向传导、横向管线或泳道编排。","每一步右侧"),

    C("brand-mark","品牌标记","shell",".brand-mark","icon-button","EvalCall 的最小识别符号。","全局顶栏"),
    C("topbar","全局顶栏","shell",".topbar","surface-treatment","承载品牌、模式和跨页面入口。","所有步骤"),
    C("top-controls","顶栏操作组","shell",".top-controls","spacing-density","决定模式切换与辅助动作的排列密度。","全局顶栏右侧"),
    C("library-link","图鉴入口","shell",".library-link","secondary-button","从工作台进入设计资产库的辅助动作。","全局顶栏"),
    C("truth-badge","结果真实性标识","shell",".truth-badge","live-status","区分缓存结果与现场实时结果。","全局顶栏"),
    C("page-heading","页面标题组","shell",".title-copy","type-hierarchy","建立产品名、定位和说明的阅读顺序。","工作台上方"),
    C("formula","闭环公式","shell",".formula","quote-block","用一句短公式解释产品运行逻辑。","页面标题右侧"),
    C("workbench","工作台容器","shell",".workbench","surface-treatment","容纳六步流程的主舞台。","页面主体"),

    C("step-navigation","六步导航","navigation",".steps","step-navigation","表达顺序、完成状态和当前步骤。","工作台顶部"),
    C("step-index","步骤编号","navigation",".step-index","badge-tag","强化当前、完成与核心步骤的差异。","六步导航内部"),
    C("stage-heading","阶段标题","navigation",".stage-head","type-hierarchy","明确当前阶段及当前任务。","每一步顶部"),
    C("run-context","运行标识","navigation",".run-context","identity-chip","展示运行 ID 与产物上下文。","阶段标题右侧"),
    C("footer-navigation","前后步导航","navigation",".footer .nav","secondary-button","控制六步之间的前进与返回。","每一步底部"),

    C("flow-shell","信号树画布","flow",".flow-map","surface-treatment","承载动态审计过程的主视觉画布。","每一步右侧"),
    C("flow-header","信号树标题栏","flow",".flow-map-head","content-card","说明流程名称与当前执行状态。","信号树顶部"),
    C("flow-progress","步骤内进度","flow",".flow-progress","progress-indicator","展示当前小步进度与里程碑。","信号树顶部"),
    C("flow-input-node","输入叶节点","flow",".flow-column.input .flow-node","workflow-node","显示进入当前步骤的原始材料。","信号树输入区"),
    C("flow-process-node","处理主干节点","flow",".flow-column.process .flow-node","workflow-node","逐项点亮系统正在执行的内部动作。","信号树处理区"),
    C("flow-output-node","输出分支节点","flow",".flow-column.output .flow-node","workflow-node","显示当前步骤形成的结果与产物。","信号树输出区"),
    C("flow-connector","流程连接线","flow",".flow-link","connector-line","表达输入、处理和输出之间的传导关系。","信号树节点之间"),
    C("flow-pulse","运行信号脉冲","flow",".flow-pulse","live-status","用运动强调系统正在真实执行。","信号树连接线"),

    C("io-card","输入输出卡片","input",".io-card","content-card","承载每一步的材料和结果明细。","每一步左侧"),
    C("io-header","输入输出标题栏","input",".io-head","border-language","区分输入、输出及其类型。","输入输出卡片顶部"),
    C("execute-button","执行按钮","input",".execute","primary-button","触发当前步骤，是页面唯一主动作。","输入与输出之间"),
    C("entry-switch","测试入口切换","input",".entry-switch","toggle-switch","切换模拟测试与已有日志质检。","第一步"),
    C("preset-card","样例素材卡","input",".preset","content-card","选择一套预装任务与对话材料。","第一步"),
    C("material-library","内置素材库","input",".material-library","accordion","组织随 Demo 部署的 SOP 与对话。","第一步"),
    C("material-select","素材选择器","input",".material-select","select-menu","从已部署素材中选择任务文件。","第一步"),
    C("text-input","模型与数量输入","input",".text-input","text-input","编辑模型调用名与测试数量。","第一步实时模式"),
    C("mode-note","模式说明条","input",".mode-note","alert-banner","解释当前模式真实执行了什么。","第一至三步"),
    C("empty-state","未执行空状态","input",".empty","empty-state","告诉用户当前为什么还没有结果。","每一步输出区"),

    C("section-label","内容分区标题","content",".section-label","type-hierarchy","把长结果拆成可扫描的信息区块。","结果卡片内部"),
    C("metric-card","指标卡","content",".metric","metric-block","呈现履约率、P0、覆盖率等关键数字。","第二至六步"),
    C("status-line","状态说明行","content",".status-line","live-status","展示健康状态、哈希和执行说明。","结果卡片内部"),
    C("excerpt","原文摘要","content",".excerpt","quote-block","展示 SOP、Persona 或日志原文片段。","输入卡片内部"),
    C("artifact-link","产物链接","content",".artifact","secondary-button","下载或打开 Checklist、报告与回归请求。","输出卡片底部"),
    C("list-row","规则与动作列表项","content",".item","list-row","组织检查点、优化动作与验收条件。","第二、六步"),
    C("chip","判定与严重度标签","content",".chip","badge-tag","用紧凑语义标记通过、失败、P0 和置信度。","多处结果列表"),
    C("detail-table","逐项判定表","content",".detail-table","data-table","展示运行、检查点、判定和证据。","第三、四步"),
    C("problem-row","失败问题排行","content",".problem","progress-indicator","用排序和失败率条展示主要问题。","第四步"),
    C("gate-card","上线门禁卡","content",".gate","severity-level","把可上线或打回结论提升为视觉焦点。","第四、五步"),
    C("root-card","根因卡","content",".root","content-card","展示首要根因、置信度、证据和负责人。","第五、六步"),
    C("regression-loop","同尺回归闭环","content",".loop","timeline-event","表达优化、返回与再次验证的闭环。","第六步"),

    C("progress-block","执行进度块","feedback",".progress","progress-indicator","反馈后端任务的阶段与百分比。","实时执行中"),
    C("toast","轻提示","feedback",".toast","toast-message","反馈素材载入、保存和错误。","全局底部"),
    C("evidence-trigger","查看证据按钮","feedback",".evidence-trigger","secondary-button","打开当前判定的原文证据。","逐项判定表"),
    C("drawer","证据抽屉","feedback",".evidence-drawer","surface-treatment","在不中断流程的情况下复核详细证据。","页面右侧覆盖层"),
    C("drawer-evidence","证据引用块","feedback",".drawer-evidence","quote-block","突出 Judge 使用的原文依据。","证据抽屉内部"),
    C("drawer-backdrop","抽屉遮罩","feedback",".drawer-backdrop","surface-treatment","降低背景干扰并强调当前复核任务。","证据抽屉打开时")
  ];
  const elementMap=Object.fromEntries(components.map(component=>[component.id,{category:component.category,selector:component.selector,atlasElement:component.atlasElement,location:component.location}]));
  const selectorMap=Object.fromEntries(components.map(component=>[component.selector,component.id]));
  window.EVALCALL_STYLE_CATALOG={categories,components,elementMap,selectorMap};
})();
