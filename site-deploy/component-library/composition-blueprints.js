(function(){
  "use strict";
  const evalcall={
    schema:"design-composition/v1",
    id:"evalcall",
    name:"EvalCall 六步评测",
    page:{context:"RUN · DELIVERY-BASELINE-V1",title:"03 · 测试外呼模型",status:"实时执行 · 42%"},
    navigation:["配置任务","评分标准","测试模型","评测报告","失败归因","同尺回归"],
    layout:{direction:"columns",tracks:[42,58],gap:12},
    regions:[
      {id:"materials",label:"输入与输出",type:"stack",role:"supporting",items:[
        {type:"panel",tone:"input",label:"输入",meta:"TEST PACKAGE",items:[
          {type:"field",label:"被测模型",value:"delivery-baseline-v1"},
          {type:"field",label:"任务指令",value:"配送时间改约 SOP"},
          {type:"field",label:"Persona",value:"拒绝 · 质疑 · 追问"}
        ]},
        {type:"panel",tone:"output",label:"输出",meta:"LIVE RESULT",items:[
          {type:"metric",label:"已完成",value:"4/10"},
          {type:"metric",label:"覆盖率",value:"19%"},
          {type:"status",label:"21 项逐项判定"}
        ]}
      ]},
      {id:"audit",label:"动态审计信号树",type:"flow",role:"primary",orientation:"vertical",meta:"STEP 2 / 4",stages:[
        {label:"INPUT",tone:"input",nodes:["Persona","Target model","Checklist"]},
        {label:"PROCESS",tone:"process",nodes:["生成压力响应","模型多轮回复","Judge 引用证据"]},
        {label:"OUTPUT",tone:"output",nodes:["测试对话","逐项判定","覆盖盲区"]}
      ]}
    ]
  };
  const approval={
    schema:"design-composition/v1",
    id:"approval",
    name:"企业审批控制台",
    page:{context:"WORKSPACE · FINANCE",title:"费用审批中心",status:"12 项待处理"},
    navigation:["待我审批","我发起的","抄送我的","规则配置"],
    layout:{direction:"columns",tracks:[64,36],gap:16},
    regions:[
      {id:"queue",label:"审批队列",type:"table",role:"primary",columns:["申请人","事项","金额","状态"],rows:[
        ["王悦","差旅报销","¥4,280","待审批"],
        ["陈晨","供应商付款","¥32,000","需复核"],
        ["赵宇","市场活动","¥8,600","待审批"]
      ]},
      {id:"detail",label:"当前申请",type:"stack",role:"supporting",items:[
        {type:"panel",tone:"input",label:"申请信息",meta:"REQUEST #0248",items:[
          {type:"field",label:"部门",value:"城市运营"},
          {type:"field",label:"预算科目",value:"市场推广"},
          {type:"field",label:"风险等级",value:"中风险"}
        ]},
        {type:"panel",tone:"output",label:"审批动作",meta:"NEXT ACTION",items:[
          {type:"status",label:"同意并流转至财务"},
          {type:"status",label:"退回补充材料"}
        ]}
      ]}
    ]
  };
  const analytics={
    schema:"design-composition/v1",
    id:"analytics",
    name:"经营分析看板",
    page:{context:"ANALYTICS · JULY",title:"城市经营总览",status:"数据更新于 10:32"},
    navigation:["总览","交易","履约","用户","异常"],
    layout:{direction:"rows",tracks:[38,62],gap:14},
    regions:[
      {id:"metrics",label:"核心指标",type:"cards",role:"supporting",items:[
        {type:"metric",label:"交易额",value:"¥4.82M"},
        {type:"metric",label:"履约率",value:"96.4%"},
        {type:"metric",label:"客诉率",value:"0.31%"},
        {type:"metric",label:"活跃商户",value:"12,840"}
      ]},
      {id:"trend",label:"指标传导",type:"flow",role:"primary",orientation:"horizontal",meta:"WEEK 28",stages:[
        {label:"流量",tone:"input",nodes:["曝光","访问","搜索"]},
        {label:"转化",tone:"process",nodes:["下单","支付","复购"]},
        {label:"履约",tone:"output",nodes:["接单","送达","评价"]}
      ]}
    ]
  };
  window.COMPOSITION_BLUEPRINTS={schema:"design-composition/v1",examples:[evalcall,approval,analytics]};
})();
