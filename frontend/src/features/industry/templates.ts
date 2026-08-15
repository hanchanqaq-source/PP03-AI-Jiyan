import type { DataField, IndustryReportTemplate, TruthStatus } from "./types";

export const INCOMPLETE_REPORT_MESSAGE = "该行业报告模板正在完善";

const field = (label: string, note = "等待接入可核验数据源", status: TruthStatus = "unavailable"): DataField => ({
  label,
  value: null,
  status,
  source: status === "development" ? "PP03 任务书模板" : "暂无可靠数据源",
  updatedAt: null,
  note,
});

const summary = (): DataField[] => [
  field("当前行业状态", "需要价格、库存和需求数据联合验证", "development"),
  field("当前周期阶段", "需要历史周期序列定位", "development"),
  field("景气度"),
  field("市场热度"),
  field("资金关注度"),
  field("风险水平", "需要风险指标和事件数据验证", "development"),
];

const capital = (): DataField[] => [
  field("板块资金流"),
  field("ETF份额变化"),
  field("机构资金数据"),
  field("估值水平"),
  field("历史估值分位"),
  field("市场关注度变化"),
];

const templates: IndustryReportTemplate[] = [
  {
    id: "storage",
    name: "存储",
    subtitle: "DRAM、NAND 与 HBM 的供需、价格、库存和资本开支跟踪模板",
    summary: summary(),
    judgement: "待真实数据验证：本页先建立存储周期判断框架，不输出未经核验的周期结论。",
    basis: ["DRAM 与 NAND 产品价格趋势", "主要厂商库存与产能利用率", "HBM 与服务器需求", "厂商资本开支与扩产计划"],
    confidence: "未评估（缺少连续真实数据）",
    invalidatingConditions: ["产品价格方向与预期相反", "服务器或消费电子需求显著偏离", "主要厂商扩产节奏发生变化"],
    cycle: [field("需求趋势"), field("库存趋势"), field("产品价格趋势"), field("资本开支趋势"), field("历史阶段对比")],
    chain: [
      { name: "设备与材料", description: "晶圆制造、封装所需设备与关键材料", representatives: [], status: "development" },
      { name: "存储设计与制造", description: "DRAM、NAND、HBM 设计与晶圆制造", representatives: [], status: "development" },
      { name: "封装测试", description: "传统封测、先进封装与 HBM 堆叠", representatives: [], status: "development" },
      { name: "终端应用", description: "服务器、手机、PC、汽车与工业终端", representatives: [], status: "development" },
    ],
    metrics: ["DRAM价格", "NAND价格", "HBM需求", "库存水平", "产能利用率", "厂商资本开支", "服务器需求", "消费电子需求"].map((name) => field(name)),
    capital: capital(),
    companies: [],
    funds: [],
    catalysts: ["价格与库存数据出现可持续改善", "服务器需求兑现", "先进存储产能验证"],
    risks: ["供给恢复过快", "下游需求不及预期", "产品价格再次下行", "数据源时效与口径不一致"],
    reverseSignals: ["库存重新累积", "资本开支快速扩张但需求未跟上", "主要产品价格持续走弱"],
  },
  {
    id: "semiconductor",
    name: "半导体",
    subtitle: "设备、材料、设计、制造、封装测试与终端需求的全链条跟踪模板",
    summary: summary(),
    judgement: "待真实数据验证：本页不以单一市场表现代替产业景气判断。",
    basis: ["设备订单与资本开支", "材料需求与国产替代进度", "设计公司库存与订单", "晶圆厂利用率", "终端需求与政策变化"],
    confidence: "未评估（缺少统一行业数据）",
    invalidatingConditions: ["终端需求显著转弱", "资本开支计划下修", "供应链或政策条件变化"],
    cycle: [field("终端需求"), field("库存周期"), field("晶圆厂利用率"), field("资本开支"), field("政策影响")],
    chain: [
      { name: "设备与材料", description: "制造与封装环节的生产工具和耗材", representatives: [], status: "development" },
      { name: "芯片设计", description: "模拟、数字、功率与专用芯片设计", representatives: [], status: "development" },
      { name: "晶圆制造", description: "成熟与先进制程晶圆制造", representatives: [], status: "development" },
      { name: "封装测试", description: "传统封测与先进封装", representatives: [], status: "development" },
      { name: "终端需求", description: "消费、汽车、工业、通信与计算", representatives: [], status: "development" },
    ],
    metrics: ["设备订单", "材料需求", "设计库存", "晶圆厂利用率", "先进封装需求", "终端需求", "国产替代进度", "资本开支"].map((name) => field(name)),
    capital: capital(), companies: [], funds: [],
    catalysts: ["资本开支验证", "终端需求改善", "国产替代项目兑现"],
    risks: ["需求波动", "库存去化不及预期", "技术迭代与贸易政策变化"],
    reverseSignals: ["订单和利用率同步下行", "库存连续累积", "资本开支大幅下调"],
  },
  {
    id: "robotics",
    name: "机器人",
    subtitle: "整机、核心零部件、订单、量产进度与下游应用跟踪模板",
    summary: summary(),
    judgement: "待真实数据验证：首版区分产业进展、订单和量产，不把概念热度当作销量。",
    basis: ["整机产量与销量", "订单和交付", "减速器与伺服系统供应", "量产进度", "下游应用和国产化率"],
    confidence: "未评估（缺少可比订单与量产口径）",
    invalidatingConditions: ["量产节点延期", "核心零部件成本不降", "下游应用验证不及预期"],
    cycle: [field("订单趋势"), field("产量趋势"), field("销量趋势"), field("量产进度"), field("下游渗透率")],
    chain: [
      { name: "核心零部件", description: "减速器、伺服系统、控制器、传感器与执行器", representatives: [], status: "development" },
      { name: "整机集成", description: "工业、协作与人形机器人整机", representatives: [], status: "development" },
      { name: "软件与机器视觉", description: "控制软件、感知、机器视觉与具身模型", representatives: [], status: "development" },
      { name: "下游应用", description: "制造、物流、服务与特种场景", representatives: [], status: "development" },
    ],
    metrics: ["产量", "销量", "订单", "减速器", "伺服系统", "传感器", "机器视觉", "量产进度", "国产化率"].map((name) => field(name)),
    capital: capital(), companies: [], funds: [],
    catalysts: ["明确订单与交付", "核心零部件降本", "下游规模化应用验证"],
    risks: ["量产延期", "成本过高", "订单口径不可比", "应用场景验证不足"],
    reverseSignals: ["订单取消或延期", "样机进展未转化为量产", "核心零部件供应受限"],
  },
];

const byId = new Map(templates.map((template) => [template.id, template]));

export function getIndustryTemplate(tagId: string): IndustryReportTemplate | undefined {
  return byId.get(tagId as IndustryReportTemplate["id"]);
}
