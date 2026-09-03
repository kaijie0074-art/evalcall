(function(){
  "use strict";
  const sources=["Voiceflow","Retool","Vanta","LaunchDarkly","Linear","PostHog","Incident","Dovetail","Grafbase","Seline","Pirsch","Officevibe","Dub","Harvest","Adaptive ML","Sequence"];
  const chapters=[
    {id:"foundations",name:"基础语言",file:"design-library-foundations.html",description:"字体、颜色、表面、边界与空间。"},
    {id:"controls",name:"操作与输入",file:"design-library-controls.html",description:"按钮、表单、筛选和上传。"},
    {id:"content",name:"内容与数据",file:"design-library-content.html",description:"卡片、指标、列表、表格和引用。"},
    {id:"feedback",name:"状态与反馈",file:"design-library-feedback.html",description:"状态、告警、进度、空白与加载。"},
    {id:"flow",name:"流程与关系",file:"design-library-flow.html",description:"节点、连接、分支、时间线与对比。"}
  ];
  const V=(id,name,source,description,html)=>({id,name,source,description,html});
  const E=(id,name,chapter,description,tags,variants)=>({id,name,chapter,description,tags,variants});
  const elements=[
    E("type-hierarchy","文字层级","foundations","用字号、字重和字距建立阅读顺序，而不是依赖装饰。",["Typography","Hierarchy"],[
      V("type-quiet","克制无衬线","Linear","紧凑、理性、适合高密度工具。",`<div style="width:260px"><div style="font:900 9px var(--aa-data);letter-spacing:.15em;color:#8792a1">WORKSPACE / 04</div><div style="font-size:27px;font-weight:560;letter-spacing:-.045em;line-height:1.05;margin-top:8px">Build with precision.</div><p style="font-size:9px;color:#778292;margin:8px 0 0">Quiet type lets the interface carry the weight.</p></div>`),
      V("type-editorial","编辑部衬线","Incident","像报告、法律文件或出版物，强调可信度。",`<div style="width:260px"><div style="font:700 10px var(--aa-data);color:#777">STATUS REPORT</div><div style="font:400 29px/1.05 var(--aa-editorial);margin-top:8px">Trust is a typographic decision.</div><p style="font:400 10px/1.5 var(--aa-editorial);color:#555">A calm voice can feel more authoritative than a loud one.</p></div>`),
      V("type-mono","工程等宽","Retool","强化版本、代码、编号和技术对象。",`<div style="width:260px;font-family:var(--aa-data)"><div style="font-size:9px;color:#8490a0">DEPLOYMENT_024</div><div style="font-size:22px;font-weight:900;margin-top:8px">READY / 98.7%</div><div style="font-size:9px;color:#13775f;margin-top:7px">all systems operational</div></div>`)
    ]),
    E("accent-strategy","强调色策略","foundations","颜色的价值来自稀缺。一个页面只让一种主色承担注意力。",["Color","Accent"],[
      V("accent-signal","信号黄","Pirsch","明亮但克制，适合动作与关键节点。",`<div style="display:flex;gap:10px;align-items:center"><span style="width:58px;height:58px;border-radius:15px;background:#ffd100"></span><div><b style="font-size:11px">Signal yellow</b><span style="display:block;font:800 8px var(--aa-data);color:#788394;margin-top:4px">#FFD100 · ACTION</span></div></div>`),
      V("accent-blue","秩序蓝","Dub","用于链接、选中和信息关系，冷静可靠。",`<div style="display:flex;gap:10px;align-items:center"><span style="width:58px;height:58px;border-radius:15px;background:#2c62a0"></span><div><b style="font-size:11px">Order blue</b><span style="display:block;font:800 8px var(--aa-data);color:#788394;margin-top:4px">#2C62A0 · NAVIGATION</span></div></div>`),
      V("accent-semantic","语义四色","Vanta","只在状态系统中同时使用绿、黄、橙、红。",`<div style="display:flex;gap:7px"><span style="width:37px;height:58px;border-radius:10px;background:#13775f"></span><span style="width:37px;height:58px;border-radius:10px;background:#ffd100"></span><span style="width:37px;height:58px;border-radius:10px;background:#ee6a2c"></span><span style="width:37px;height:58px;border-radius:10px;background:#c63b34"></span></div>`)
    ]),
    E("surface-treatment","表面层级","foundations","通过背景亮度、边框或阴影建立深度。",["Surface","Depth"],[
      V("surface-flat","边框平面","Pirsch","没有投影，依靠清晰边界。",`<article class="aa-card"><label>FLAT SURFACE</label><h4>Quiet structure</h4><p>One border is enough to define the object.</p></article>`),
      V("surface-raised","柔光浮层","Seline","轻柔投影把少数重点从画布抬起。",`<article class="aa-card raised"><label>RAISED SURFACE</label><h4>Focused object</h4><p>Use elevation only for a real hierarchy change.</p></article>`),
      V("surface-inset","深色内嵌","Retool","像控制台或工作台内部区域。",`<article class="aa-card dark"><label>INSET SURFACE</label><h4>System console</h4><p>Darkness makes technical content feel contained.</p></article>`)
    ]),
    E("border-language","边界语言","foundations","边框可以是结构、强调，也可以是一种几乎不可见的分隔。",["Border","Structure"],[
      V("border-hairline","发丝线","Linear","0.5–1px 低对比边界，适合精密工具。",`<div style="width:230px;border:1px solid #cfd6df;padding:18px;border-radius:8px"><b style="font-size:11px">Hairline frame</b><p style="font-size:8.5px;color:#788394;margin:6px 0 0">Structure without visual weight.</p></div>`),
      V("border-accent","强调边","Officevibe","只让一条边承担视觉焦点。",`<div style="width:230px;border:1px solid #d7dee7;border-top:5px solid #ffd100;padding:15px;border-radius:10px"><b style="font-size:11px">Accent edge</b><p style="font-size:8.5px;color:#788394;margin:6px 0 0">One line changes the whole priority.</p></div>`),
      V("border-ink","墨色硬边","Incident","黑色硬边有印刷物和编辑部感。",`<div style="width:230px;border:1px solid #111;padding:16px;background:#f4f1ea"><b style="font:700 12px var(--aa-editorial)">Editorial rule</b><p style="font:400 9px var(--aa-editorial);margin:6px 0 0">The border reads like a printed document.</p></div>`)
    ]),
    E("radius-language","圆角语言","foundations","圆角决定产品气质：精密、友好或强工具感。",["Radius","Shape"],[
      V("radius-compact","精密小圆角","Linear","4–8px，专业、紧凑、少装饰。",`<div style="width:210px;height:82px;border:1px solid #cfd6df;border-radius:6px;background:#fff;display:grid;place-items:center;font-size:10px;font-weight:850">6px precision</div>`),
      V("radius-soft","柔和中圆角","Seline","12–16px，现代而不幼态。",`<div style="width:210px;height:82px;border:1px solid #dce2e9;border-radius:15px;background:#fff;display:grid;place-items:center;font-size:10px;font-weight:850;box-shadow:0 8px 22px rgba(17,26,44,.06)">15px calm</div>`),
      V("radius-pill","完全胶囊","Retool","只用于动作、标签和身份，不用于所有容器。",`<div style="height:44px;border-radius:999px;background:#111a2c;color:#fff;padding:0 22px;display:grid;place-items:center;font-size:10px;font-weight:900">999px decisive action</div>`)
    ]),
    E("spacing-density","空间密度","foundations","空间不是越大越高级；应匹配任务频率和信息密度。",["Spacing","Density"],[
      V("density-compact","紧凑控制台","Retool","小间距，高频使用时扫描更快。",`<div style="width:250px;display:grid;gap:4px">${[1,2,3,4].map(i=>`<div style="height:28px;border:1px solid #dce2e9;border-radius:5px;background:#fff;padding:6px 8px;font-size:8px">Row ${i} · compact</div>`).join("")}</div>`),
      V("density-balanced","均衡工作台","Vanta","信息密度与呼吸感保持平衡。",`<div style="width:250px;display:grid;gap:9px">${[1,2,3].map(i=>`<div style="height:36px;border:1px solid #dce2e9;border-radius:9px;background:#fff;padding:10px;font-size:8px">Item ${i} · balanced</div>`).join("")}</div>`),
      V("density-editorial","宽松阅读型","Incident","适合报告、决策和低频内容。",`<div style="width:250px;border-top:1px solid #111;border-bottom:1px solid #111;padding:18px 0;font:400 10px/1.7 var(--aa-editorial)">Generous spacing lets the reader pause and decide.</div>`)
    ]),

    E("primary-button","主按钮","controls","每个视图最重要的动作，只允许一个主视觉。",["Button","Action"],[
      V("button-signal","信号色实心","Pirsch","动作明确，适合浅色界面。",`<button class="aa-button signal">Create workspace</button>`),
      V("button-ink","深色胶囊","Retool","高对比、成熟，适合全局动作。",`<button class="aa-button ink">Publish changes</button>`),
      V("button-paper","纸张描边","Incident","硬边与错位阴影，带编辑部感。",`<button class="aa-button paper">View full report</button>`)
    ]),
    E("secondary-button","次按钮","controls","存在但不抢夺注意力，通常与主按钮成对出现。",["Button","Secondary"],[
      V("secondary-soft","浅灰填充","Linear","低干扰、适合工具栏。",`<button class="aa-button soft">Save draft</button>`),
      V("secondary-outline","中性描边","Vanta","边框建立可点击性，不引入第二主色。",`<button class="aa-button" style="background:#fff;border:1px solid #cbd3dd;color:#4d596b">Review details</button>`),
      V("secondary-text","纯文字动作","Dovetail","最轻层级，用于查看和跳转。",`<button class="aa-button" style="background:transparent;color:#2c62a0;padding-inline:4px">Open evidence →</button>`)
    ]),
    E("icon-button","图标按钮","controls","将频繁但次要的动作压缩为小面积控件。",["Button","Icon"],[
      V("icon-neutral","中性方形","Linear","稳定、工具化。",`<button class="aa-icon-button" aria-label="Settings">⚙</button>`),
      V("icon-dark","深色工具","Retool","适合深色控制区域。",`<button class="aa-icon-button dark" aria-label="Command">⌘</button>`),
      V("icon-round","信号圆形","Officevibe","适合强调一个独立快捷操作。",`<button class="aa-icon-button round" aria-label="Add">＋</button>`)
    ]),
    E("text-input","文本输入","controls","输入框通过填充、边框或下划线表达不同密度。",["Input","Form"],[
      V("input-outline","边框输入","Vanta","通用、清晰、适合表单。",`<input class="aa-input" placeholder="Workspace name">`),
      V("input-filled","填充输入","Linear","在高密度界面中减少边框噪声。",`<input class="aa-input filled" placeholder="Filter by owner">`),
      V("input-underline","下划线输入","Incident","低密度、编辑部式页面。",`<input class="aa-input underline" placeholder="Report title">`)
    ]),
    E("search-field","搜索框","controls","搜索框可强调快捷、范围或结果语境。",["Search","Command"],[
      V("search-command","命令搜索","Linear","快捷键提示强化专业工具感。",`<label class="aa-search"><span>⌕</span><input placeholder="Search anything"><kbd>⌘K</kbd></label>`),
      V("search-soft","柔和搜索","Dub","轻量、高频、不打断页面。",`<label class="aa-search" style="background:#f1f4f7;border-color:transparent"><span>⌕</span><input style="background:transparent" placeholder="Search links"></label>`),
      V("search-dark","深色搜索","Retool","嵌入控制台或命令面板。",`<label class="aa-search" style="background:#121c30;border-color:#344159;color:#fff"><span>⌕</span><input style="background:transparent;color:#fff" placeholder="Search resources"><kbd style="background:#26334a;border-color:#425069">/</kbd></label>`)
    ]),
    E("select-menu","选择器","controls","用于稳定枚举；视觉重量应低于主动作。",["Select","Form"],[
      V("select-standard","标准选择器","Harvest","清晰、熟悉、适合大多数表单。",`<select class="aa-select"><option>All projects</option><option>Active projects</option></select>`),
      V("select-compact","紧凑选择器","Linear","小尺寸适合表格上方工具栏。",`<select class="aa-select" style="height:34px;min-width:170px;border-radius:7px"><option>Last 30 days</option></select>`),
      V("select-dark","深色选择器","Retool","适合控制台和开发者工具。",`<select class="aa-select dark"><option>Production</option><option>Staging</option></select>`)
    ]),
    E("toggle-switch","开关","controls","只表示真实布尔状态，不代替普通选择。",["Switch","Boolean"],[
      V("switch-semantic","语义绿色","LaunchDarkly","绿色表达已启用或健康。",`<button class="aa-switch on" data-aa="switch"><i></i></button>`),
      V("switch-neutral","中性灰","Linear","不强调语义，仅显示关闭状态。",`<button class="aa-switch" data-aa="switch"><i></i></button>`),
      V("switch-signal","品牌黄色","Pirsch","适合非状态性的偏好开关。",`<button class="aa-switch on" data-aa="switch" style="background:#ffd100"><i></i></button>`)
    ]),
    E("filter-chip","筛选标签","controls","用形状和轻色差表达临时筛选条件。",["Filter","Chip"],[
      V("chip-outline","轮廓胶囊","Dovetail","最通用，适合多选筛选。",`<button class="aa-chip active" data-aa="chip">● Active</button>`),
      V("chip-square","紧凑方标签","PostHog","更像工具，不像营销标签。",`<button class="aa-chip square" data-aa="chip">Feature flag</button>`),
      V("chip-dark","深色筛选","Retool","适合深色面板。",`<button class="aa-chip" data-aa="chip" style="background:#172238;color:#fff;border-color:#33405a">Production</button>`)
    ]),
    E("upload-zone","文件投放区","controls","投放区应清楚表达可接受内容和动作。",["Upload","Dropzone"],[
      V("upload-dashed","标准虚线","PostHog","易理解、适合工具页。",`<div class="aa-upload"><b>Drop files here</b><span>PDF, CSV or JSON up to 10MB</span></div>`),
      V("upload-paper","黄色纸张","Pirsch","强调一次重要导入动作。",`<div class="aa-upload paper"><b>Import a data source</b><span>Drag or choose a file</span></div>`),
      V("upload-compact","紧凑行式","Linear","适合侧栏或表单内部。",`<div class="aa-upload" style="padding:11px;display:flex;align-items:center;gap:9px;text-align:left"><b style="white-space:nowrap">＋ Add file</b><span style="margin:0">or drag here</span></div>`)
    ]),

    E("content-card","内容卡片","content","卡片应表达真实分组，而不是把每段文字都装进容器。",["Card","Container"],[
      V("card-flat","平面细边","Pirsch","克制、安静、适合大量并列内容。",`<article class="aa-card"><label>PROJECT</label><h4>Design system audit</h4><p>12 components reviewed this week.</p></article>`),
      V("card-raised","柔光浮层","Seline","只抬高少量关键对象。",`<article class="aa-card raised"><label>INSIGHT</label><h4>Conversion improved</h4><p>New onboarding increased completion by 18%.</p></article>`),
      V("card-accent","顶部强调边","Officevibe","一条颜色即可建立优先级。",`<article class="aa-card accent"><label>ATTENTION</label><h4>Review required</h4><p>Three items need a decision today.</p></article>`)
    ]),
    E("metric-block","指标块","content","数字的类型和上下文决定它是报告、监控还是经营视图。",["Metric","Number"],[
      V("metric-technical","工程数字","Linear","等宽、紧凑、精确。",`<div class="aa-metric"><b>98.7%</b><span>Availability</span></div>`),
      V("metric-editorial","编辑部数字","Seline","大号衬线数字更像报告。",`<div class="aa-metric editorial"><b>24,891</b><span>Monthly readers</span></div>`),
      V("metric-dark","深色监控","Retool","适合实时控制台。",`<div class="aa-metric dark"><b>42ms</b><span>Median latency</span></div>`)
    ]),
    E("list-row","列表行","content","列表行承载对象、辅助信息与行级状态。",["List","Row"],[
      V("list-avatar","身份型列表","Officevibe","头像帮助快速识别对象。",`<div class="aa-list-row"><i>AJ</i><div><b>Alex Johnson</b><span>Product design</span></div><em>Online</em></div>`),
      V("list-icon","图标型列表","Linear","统一图标强化任务分类。",`<div class="aa-list-row"><i>◇</i><div><b>Homepage refresh</b><span>Updated 2 hours ago</span></div><em>In review</em></div>`),
      V("list-number","编号型列表","Sequence","编号适合步骤、对象和开发流程。",`<div class="aa-list-row"><i style="font-family:var(--aa-data)">07</i><div><b>Build deployment</b><span>commit f47a19</span></div><em>Passed</em></div>`)
    ]),
    E("data-table","数据表格","content","表格应通过密度、对齐和状态帮助比较。",["Table","Data"],[
      V("table-quiet","低噪声表格","Dub","浅边界和留白，适合经营数据。",`<div class="aa-table"><div><b>Landing page</b><span>12,490</span><span>4.8%</span></div><div><b>Pricing</b><span>8,201</span><span>6.2%</span></div><div><b>Docs</b><span>6,310</span><span>3.9%</span></div></div>`),
      V("table-dense","紧凑工具表格","Retool","更小行高，适合高频管理。",`<div class="aa-table" style="border-radius:5px"><div style="padding-block:6px"><b>prod-api</b><span>active</span><span>42ms</span></div><div style="padding-block:6px"><b>worker-02</b><span>active</span><span>51ms</span></div><div style="padding-block:6px"><b>cache-eu</b><span>idle</span><span>—</span></div></div>`),
      V("table-status","状态主导表格","Vanta","状态色只出现于需要处置的单元格。",`<div class="aa-table"><div><b>Access review</b><span><i class="aa-badge green">Ready</i></span><span>Today</span></div><div><b>Vendor risk</b><span><i class="aa-badge red">Action</i></span><span>2d</span></div></div>`)
    ]),
    E("badge-tag","标签与徽章","content","标签负责分类，徽章负责状态；不要混用。",["Badge","Tag"],[
      V("badge-success","成功状态","Vanta","绿色仅表示通过或健康。",`<span class="aa-badge green">● Verified</span>`),
      V("badge-critical","关键状态","Incident","红色表示必须处理。",`<span class="aa-badge red">Critical</span>`),
      V("badge-category","分类标签","PostHog","黄色只做分类或强调，不冒充成功。",`<span class="aa-badge yellow">Experiment</span>`)
    ]),
    E("identity-chip","身份单元","content","紧凑展示人、组织或角色及其辅助信息。",["Identity","Avatar"],[
      V("identity-person","人物身份","Officevibe","头像、姓名、角色三层信息。",`<span class="aa-identity"><i>AL</i><b>Alex Lee</b><span>Designer</span></span>`),
      V("identity-team","团队身份","Dovetail","图标可以替代真实头像。",`<span class="aa-identity"><i>◇</i><b>Research ops</b><span>8 members</span></span>`),
      V("identity-system","系统身份","Linear","等宽标记更适合机器人、服务和版本。",`<span class="aa-identity"><i style="font-family:var(--aa-data)">AI</i><b style="font-family:var(--aa-data)">agent_04</b><span>active</span></span>`)
    ]),
    E("quote-block","引用块","content","引用可以是证据、观点或编辑部式强调。",["Quote","Evidence"],[
      V("quote-evidence","证据引用","Dovetail","蓝色边界区分来源与结论。",`<blockquote class="aa-quote"><p>“The onboarding was clear, but I could not find billing settings.”</p><footer>Interview 07 · 12:43</footer></blockquote>`),
      V("quote-editorial","编辑部引语","Incident","衬线与墨色边界适合观点和报告。",`<blockquote class="aa-quote editorial"><p>“Clarity is a form of trust.”</p><footer>Quarterly review</footer></blockquote>`),
      V("quote-dark","深色代码引用","Retool","适合系统输出、日志或机器证据。",`<blockquote class="aa-quote" style="background:#121c30;border-color:#ffd100"><p style="color:#dfe6f0;font-family:var(--aa-data)">status: healthy<br>latency_p95: 84ms</p><footer style="color:#8290a5">system.log</footer></blockquote>`)
    ]),
    E("accordion","折叠项","content","折叠用于隐藏可选细节，不应用来掩盖核心信息。",["Accordion","Disclosure"],[
      V("accordion-plain","标准折叠","Dovetail","边框清楚，内容按需展开。",`<div class="aa-accordion" data-aa="accordion"><button>How was this calculated?</button><div>Calculated from the last 30 days of verified events.</div></div>`),
      V("accordion-editorial","编辑部折叠","Incident","硬边、无圆角，更像报告目录。",`<div class="aa-accordion" data-aa="accordion" style="border-color:#111;border-radius:0"><button style="font-family:var(--aa-editorial)">Methodology and sources</button><div style="font-family:var(--aa-editorial)">Primary and secondary evidence are listed here.</div></div>`),
      V("accordion-soft","柔和折叠","Officevibe","浅底与大圆角更亲和。",`<div class="aa-accordion" data-aa="accordion" style="background:#f7f4ff;border-color:#e1d8ff;border-radius:14px"><button style="background:#f7f4ff">Suggested next steps</button><div style="background:#fff">Invite the team to review three open actions.</div></div>`)
    ]),

    E("live-status","实时状态","feedback","状态点应该表达系统是否正在工作，而不是装饰。",["Status","Live"],[
      V("status-live","扩散状态点","LaunchDarkly","周期外扩表达持续在线。",`<span class="aa-status">System operational</span>`),
      V("status-static","静态状态点","Vanta","适合稳定列表，不制造额外动效。",`<span class="aa-status" style="color:#2c62a0"><span style="width:8px;height:8px;border-radius:50%;background:currentColor"></span>Review pending</span>`),
      V("status-mono","等宽系统状态","Retool","适合控制台和开发工具。",`<span class="aa-status" style="font-family:var(--aa-data);color:#13775f">● service.ready</span>`)
    ]),
    E("alert-banner","告警条","feedback","告警必须说明发生了什么以及下一步做什么。",["Alert","Banner"],[
      V("alert-critical","关键告警","Incident","红色只用于必须处理的问题。",`<div class="aa-alert"><i>!</i><div><b>Service disruption</b><span>Checkout is unavailable. Open incident details.</span></div></div>`),
      V("alert-neutral","中性提醒","Pirsch","黄色适合注意事项，不制造危机感。",`<div class="aa-alert neutral"><i>i</i><div><b>Data is still processing</b><span>New results will appear in a few minutes.</span></div></div>`),
      V("alert-dark","深色系统告警","Retool","用于控制台或高密度操作区。",`<div class="aa-alert" style="background:#121c30;border-color:#4b3f1a"><i style="background:#ffd100;color:#211b00">!</i><div><b style="color:#ffe36a">Build warning</b><span style="color:#aeb8c8">Two optional checks were skipped.</span></div></div>`)
    ]),
    E("toast-message","轻提示","feedback","提示反馈动作结果，出现后应自动退出注意力。",["Toast","Message"],[
      V("toast-success","成功提示","Officevibe","绿色图标、白色浮层，亲和清楚。",`<div class="aa-toast">Changes saved successfully</div>`),
      V("toast-compact","紧凑工具提示","Linear","更小、更短，适合高频动作。",`<div class="aa-toast" style="padding:8px 10px;border-radius:7px;box-shadow:0 9px 20px rgba(17,26,44,.13)">Issue copied</div>`),
      V("toast-dark","深色提示","Retool","控制台内保持一致的暗色表面。",`<div class="aa-toast" style="background:#121c30;color:#fff;border-color:#344159">Deployment queued</div>`)
    ]),
    E("progress-indicator","进度","feedback","进度条应连接任务状态，不只是等待动画。",["Progress","Loading"],[
      V("progress-signal","品牌色进度","Pirsch","单色、清楚、适合确定任务。",`<div class="aa-progress"><header><b>Uploading assets</b><span>68%</span></header><div><i></i></div></div>`),
      V("progress-gradient","渐变进度","Adaptive ML","适合长过程或模型计算。",`<div class="aa-progress gradient"><header><b>Training model</b><span>68%</span></header><div><i></i></div></div>`),
      V("progress-steps","分段进度","Voiceflow","离散阶段比连续百分比更真实。",`<div style="display:flex;gap:5px;width:250px">${[1,2,3,4,5].map((i)=>`<span style="flex:1;height:8px;border-radius:99px;background:${i<4?'#ffd100':'#e2e7ed'}"></span>`).join("")}</div>`)
    ]),
    E("skeleton-loader","骨架加载","feedback","在结构已知但数据未到达时保留布局稳定。",["Skeleton","Loading"],[
      V("skeleton-lines","文本骨架","Linear","低对比扫光，避免强存在感。",`<div class="aa-skeleton"><i></i><i></i><i></i></div>`),
      V("skeleton-card","卡片骨架","Seline","保留媒体和正文结构。",`<div style="width:240px;border:1px solid #e0e5eb;border-radius:12px;background:#fff;padding:12px"><div style="height:68px;border-radius:8px;background:#e8ebef"></div><div class="aa-skeleton" style="width:auto;margin-top:10px"><i></i><i></i></div></div>`),
      V("skeleton-table","表格骨架","Retool","紧凑行适合数据工具。",`<div class="aa-skeleton" style="gap:5px"><i style="height:20px"></i><i style="height:20px;width:100%"></i><i style="height:20px;width:100%"></i><i style="height:20px;width:100%"></i></div>`)
    ]),
    E("empty-state","空状态","feedback","空状态应该提供方向，而不是只宣布没有内容。",["Empty","State"],[
      V("empty-guided","带指引空状态","PostHog","明确下一步操作。",`<div class="aa-empty"><i>⌁</i><b>No results yet</b><span>Connect a source to create the first report.</span></div>`),
      V("empty-editorial","编辑部空状态","Incident","用一句克制文字保持低密度。",`<div style="width:250px;border-top:1px solid #111;border-bottom:1px solid #111;padding:20px 0;text-align:center;font:400 12px var(--aa-editorial)">Nothing requires your attention.</div>`),
      V("empty-dark","深色空状态","Retool","适合控制台内部。",`<div class="aa-empty" style="background:#121c30;border-color:#3b4960;color:#98a4b7"><i>◇</i><b style="color:#fff">No deployments</b><span>Create a build to begin.</span></div>`)
    ]),
    E("severity-level","严重度","feedback","严重度使用稳定的语义系统，而不是随页面变化。",["Severity","Semantic"],[
      V("severity-critical","关键","Vanta","红色表示阻断和立即处置。",`<span class="aa-badge red">P0 · Critical</span>`),
      V("severity-warning","警告","Incident","橙黄表示需要关注但未阻断。",`<span class="aa-badge yellow" style="background:#fff3df;color:#9a5a00">P1 · Warning</span>`),
      V("severity-healthy","健康","Vanta","绿色表示通过或已解决。",`<span class="aa-badge green">Healthy</span>`)
    ]),

    E("workflow-node","流程节点","flow","节点由身份、任务、状态组成，是流程图最小有意义单元。",["Workflow","Node"],[
      V("node-signal","信号边节点","Voiceflow","黄色边界突出当前处理节点。",`<div class="aa-node"><i>2</i><div><b>Validate input</b><span>3 checks</span></div><em>RUNNING</em></div>`),
      V("node-dark","深色系统节点","Retool","控制台式，高技术感。",`<div class="aa-node dark"><i style="background:#26334a;color:#ffd100">$</i><div><b>Build package</b><span>worker_07</span></div><em>READY</em></div>`),
      V("node-soft","柔和业务节点","Officevibe","浅色面与大圆角更亲和。",`<div class="aa-node" style="border-radius:15px;background:#f7f4ff;border-color:#e1d8ff"><i style="background:#e9e1ff;color:#7356d8">◇</i><div><b>Request review</b><span>Team approval</span></div><em style="color:#7356d8">OPEN</em></div>`)
    ]),
    E("connector-line","连接线","flow","连接线表达方向和状态，不承载额外文案。",["Connector","Motion"],[
      V("connector-pulse","流动信号","Voiceflow","信号点沿路径传导，适合真实运行。",`<div class="aa-connector"><i></i></div>`),
      V("connector-static","静态箭头","Sequence","文档和架构图使用稳定方向。",`<div style="width:200px;height:3px;background:#cbd3dd;position:relative"><span style="position:absolute;right:0;top:-4px;width:10px;height:10px;border-right:3px solid #aeb8c4;border-bottom:3px solid #aeb8c4;transform:rotate(-45deg)"></span></div>`),
      V("connector-dashed","虚线关系","Dovetail","表示间接、待确认或弱关系。",`<div style="width:200px;border-top:2px dashed #9ca8b7;position:relative"><span style="position:absolute;right:-1px;top:-5px;width:8px;height:8px;border-right:2px solid #9ca8b7;border-bottom:2px solid #9ca8b7;transform:rotate(-45deg)"></span></div>`)
    ]),
    E("branch-structure","分支","flow","分支展示互斥结果、并行路径或弱强关系。",["Branch","Logic"],[
      V("branch-semantic","语义分支","Vanta","绿/红分别表达通过与处置。",`<div class="aa-branch semantic"><span>Approved</span><span>Needs action</span></div>`),
      V("branch-neutral","中性并行","Grafbase","适合架构、治理和多路输出。",`<div class="aa-branch"><span>API layer</span><span>Data layer</span></div>`),
      V("branch-priority","主次分支","Voiceflow","用黄色强调首选路径。",`<div class="aa-branch"><span style="border-color:#d4ae00;background:#fff8d5">Primary path</span><span style="opacity:.62">Fallback</span></div>`)
    ]),
    E("timeline-event","时间线事件","flow","时间线将变化、责任和因果放在同一轴上。",["Timeline","History"],[
      V("timeline-signal","黄色节点","LaunchDarkly","适合版本、发布和里程碑。",`<div class="aa-timeline"><i></i><div><b>14:32 · Version published</b><p>Release v2.4 is now available to all users.</p></div></div>`),
      V("timeline-critical","红色事件","Incident","关键故障使用红色节点。",`<div class="aa-timeline"><i style="--x:red"></i><div><b style="color:#9a2c25">09:18 · Incident opened</b><p>Checkout errors exceeded the alert threshold.</p></div></div>`),
      V("timeline-editorial","编辑部记录","Incident","黑色规则和衬线适合正式报告。",`<div style="width:260px;border-left:1px solid #111;padding-left:14px"><b style="font:700 11px var(--aa-editorial)">12 July</b><p style="font:400 9px/1.5 var(--aa-editorial);margin:4px 0 0">The review was completed and archived.</p></div>`)
    ]),
    E("system-log","系统日志","flow","日志呈现时间、事件和状态，避免堆叠无意义文本。",["Log","Console"],[
      V("log-line","单行日志","Retool","紧凑显示关键运行事件。",`<div class="aa-log"><time>14:31:08</time><i></i><span>build completed</span><em>100%</em></div>`),
      V("log-stack","日志堆栈","PostHog","连续事件构成清晰运行轨迹。",`<div style="border-radius:9px;overflow:hidden">${["job queued","worker started","result stored"].map((x,i)=>`<div class="aa-log"><time>14:31:0${i}</time><i></i><span>${x}</span><em>${i===2?'DONE':'OK'}</em></div>`).join("")}</div>`),
      V("log-paper","纸张式记录","Pirsch","浅色日志适合非技术用户。",`<div style="width:285px;border:1px solid #111;border-radius:8px;background:#fff8e8;padding:10px;font:800 8px/1.7 var(--aa-data)"><span style="color:#9b7d00">14:31</span> Report generated<br><span style="color:#9b7d00">14:32</span> Email delivered</div>`)
    ]),
    E("before-after","前后对比","flow","对比必须固定其他条件，只强调发生变化的对象。",["Compare","Version"],[
      V("compare-semantic","结果对比","LaunchDarkly","红绿边界表达失败与通过。",`<div class="aa-compare"><article><b>VERSION 1</b><strong>Blocked</strong></article><i></i><article><b>VERSION 2</b><strong>Ready</strong></article></div>`),
      V("compare-metric","数字对比","Seline","用数字和差值表达变化。",`<div class="aa-compare"><article><b>BEFORE</b><strong>62.5</strong></article><i></i><article><b>AFTER</b><strong>91.4</strong></article></div>`),
      V("compare-visual","表面对比","Officevibe","左低对比、右高亮，强化改善方向。",`<div class="aa-compare"><article style="opacity:.55"><b>OLD</b><strong>Manual</strong></article><i></i><article style="box-shadow:0 10px 24px rgba(17,26,44,.11)"><b>NEW</b><strong>Automated</strong></article></div>`)
    ]),
    E("step-navigation","步骤导航","flow","步骤导航表达顺序和当前进度，不只是编号装饰。",["Stepper","Sequence"],[
      V("step-active","当前步骤","Voiceflow","信号色突出当前任务。",`<div class="aa-step"><i>03</i><div><b>Review details</b><span>Current step</span></div></div>`),
      V("step-complete","完成步骤","Vanta","绿色确认状态。",`<div class="aa-step"><i style="background:#e7f5f0;color:#13775f">✓</i><div><b>Connect source</b><span>Completed</span></div></div>`),
      V("step-compact","紧凑步骤","Linear","适合工具栏或窄侧栏。",`<div class="aa-step"><i style="width:25px;height:25px;border-radius:6px;background:#eff2f5">04</i><div><b style="font-family:var(--aa-data)">deploy</b><span>pending</span></div></div>`)
    ]),
    E("workspace-layout","页面主编排","flow","先决定信息区域之间的关系，再进入卡片、节点和颜色等原子细节。",["Layout","Composition"],[
      V("layout-balanced","标准双栏","Linear","左侧 42% 输入输出，右侧 58% 流程树，信息与过程均衡。",`<div style="width:270px;height:112px;display:grid;grid-template-columns:42% 58%;gap:7px"><div style="display:grid;grid-template-rows:1fr 1fr;gap:6px"><i style="border:1px solid #aeb8c4;border-radius:6px;background:#fff"></i><i style="border:1px solid #aeb8c4;border-radius:6px;background:#fff"></i></div><div style="border:1px solid #aeb8c4;border-radius:6px;background:#f7f9fb;display:grid;place-items:center"><span style="width:60%;height:68%;border-left:2px solid #ffd100;border-right:2px solid #ffd100"></span></div></div>`),
      V("layout-process","流程主导","Retool","左侧压缩为 32% 摘要，右侧放大为 68% 运行画布。",`<div style="width:270px;height:112px;display:grid;grid-template-columns:32% 68%;gap:7px"><div style="border:1px solid #344159;border-radius:4px;background:#121c30"></div><div style="border:1px solid #344159;border-radius:4px;background:#17233a;display:grid;place-items:center"><span style="width:72%;height:3px;background:#ffd100;box-shadow:0 -24px #4b5870,0 24px #4b5870"></span></div></div>`),
      V("layout-stacked","上下联动","Incident","输入输出位于上方，流程横跨下方，适合窄屏与讲解型页面。",`<div style="width:270px;height:112px;display:grid;grid-template-rows:38% 62%;gap:7px"><div style="display:grid;grid-template-columns:1fr 1fr;gap:7px"><i style="border:1px solid #111;background:#fff"></i><i style="border:1px solid #111;background:#fff8e8"></i></div><div style="border:1px solid #111;background:#fff;display:grid;place-items:center"><span style="width:72%;border-top:2px solid #ffd100"></span></div></div>`)
    ]),
    E("io-arrangement","输入输出组合","flow","输入和输出既是独立内容块，也需要作为一个整体与流程树建立稳定关系。",["Input","Output","Grouping"],[
      V("io-vertical","纵向堆叠","Linear","输入在上、输出在下，顺着阅读方向完成因果闭环。",`<div style="width:220px;display:grid;gap:9px"><div style="height:48px;border:1px solid #cbd3dd;border-radius:7px;background:#fff;padding:8px"><b style="font:850 8px var(--aa-data)">INPUT</b></div><div style="height:48px;border:1px solid #cbd3dd;border-radius:7px;background:#fff;padding:8px"><b style="font:850 8px var(--aa-data);color:#13775f">OUTPUT</b></div></div>`),
      V("io-tabs","标签切换","Incident","输入与输出共享容器，通过标签强调当前阅读对象。",`<div style="width:220px;height:112px;border:1px solid #111;background:#fff"><div style="display:flex;border-bottom:1px solid #111"><b style="padding:8px 16px;background:#ffd100;font:800 8px var(--aa-data)">INPUT</b><b style="padding:8px 16px;font:800 8px var(--aa-data)">OUTPUT</b></div><div style="margin:14px;height:40px;border:1px solid #d5d5d5"></div></div>`),
      V("io-compact","紧凑摘要","Retool","输入输出压缩为高密度运行摘要，为流程画布让出空间。",`<div style="width:220px;border:1px solid #344159;border-radius:5px;background:#121c30;color:#e9eef7;padding:9px;font:800 8px/2 var(--aa-data)"><div>IN&nbsp;&nbsp; SOP_V3 · MODEL_V1</div><div style="border-top:1px solid #344159;color:#5de0ad">OUT&nbsp; 21 CHECKS · READY</div></div>`)
    ]),
    E("flow-orientation","流程树方向","flow","流程图的方向决定评委能否一眼看清输入如何经过处理形成输出。",["Flow","Tree","Orientation"],[
      V("flow-vertical","纵向传导树","Voiceflow","节点自上而下逐层点亮，适合展示真实运行过程。",`<div style="width:240px;height:118px;display:grid;place-items:center"><div style="height:110px;width:2px;background:#cbd3dd;position:relative"><i style="position:absolute;top:0;left:-35px;width:72px;height:22px;border:1px solid #9eabb9;border-radius:6px;background:#fff"></i><i style="position:absolute;top:43px;left:-47px;width:96px;height:24px;border:1px solid #d0aa00;border-radius:6px;background:#fff8d7"></i><i style="position:absolute;bottom:0;left:-35px;width:72px;height:22px;border:1px solid #86bcae;border-radius:6px;background:#eef9f5"></i></div></div>`),
      V("flow-horizontal","横向管线","Sequence","输入、处理、输出从左向右推进，适合并列步骤。",`<div style="width:250px;display:flex;align-items:center;gap:8px"><i style="width:58px;height:42px;border:1px solid #111;background:#fff"></i><span style="flex:1;border-top:2px solid #ffd100"></span><i style="width:68px;height:48px;border:1px solid #d0aa00;background:#fff8d7"></i><span style="flex:1;border-top:2px solid #ffd100"></span><i style="width:58px;height:42px;border:1px solid #13775f;background:#eef9f5"></i></div>`),
      V("flow-swimlane","责任泳道","Retool","按 Simulator、Model、Judge 分道，强调责任边界。",`<div style="width:260px;border:1px solid #344159;border-radius:5px;overflow:hidden;background:#121c30;color:#fff;font:800 7px var(--aa-data)">${["SIMULATOR","TARGET MODEL","EVIDENCE JUDGE"].map((x,i)=>`<div style="height:32px;display:grid;grid-template-columns:72px 1fr;align-items:center;border-bottom:${i<2?'1px solid #344159':'0'}"><b style="padding-left:8px">${x}</b><i style="width:${55+i*25}%;height:5px;background:${i===1?'#ffd100':'#53617a'}"></i></div>`).join("")}</div>`)
    ])
  ];
  const sourceMap=Object.fromEntries(sources.map(source=>[source,elements.filter(element=>element.variants.some(variant=>variant.source===source)).map(element=>element.id)]));
  const elementMap=Object.fromEntries(elements.map(element=>[element.id,{chapter:element.chapter,variants:element.variants.map(variant=>variant.id),sources:[...new Set(element.variants.map(variant=>variant.source))]}]));
  window.AESTHETIC_ATLAS={sources,chapters,elements,sourceMap,elementMap};
})();
