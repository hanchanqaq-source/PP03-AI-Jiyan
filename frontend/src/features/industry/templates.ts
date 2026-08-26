import type { IndustryReportTemplate } from "./types";

export const INCOMPLETE_REPORT_MESSAGE = "标签已保存，报告模板建设中；系统不会根据标签名称自动补造行业数据";

const capitalLabels = ["板块资金", "ETF 份额", "估值水平", "历史分位"];
const templates: IndustryReportTemplate[] = [
  {
    id: "storage", name: "存储", subtitle: "DRAM、NAND 与 HBM 的供需、库存、价格和资本开支证据报告",
    cycleLabels: ["DRAM 价格", "NAND 价格", "HBM 需求", "库存水平", "产能利用率", "厂商资本开支", "服务器需求", "消费电子需求"],
    chainLabels: ["设备与材料", "存储设计与制造", "封装测试", "模组与控制器", "服务器 / 手机 / PC / 汽车终端"],
    metricLabels: ["DRAM 价格", "NAND 价格", "HBM 需求", "库存水平", "产能利用率", "厂商资本开支", "服务器需求", "消费电子需求"],
    capitalLabels,
  },
  {
    id: "semiconductor", name: "半导体", subtitle: "设备材料、设计、晶圆制造、封装测试与终端需求证据报告",
    cycleLabels: ["设备订单", "材料需求", "设计库存", "晶圆厂利用率", "先进封装需求", "终端需求", "国产替代进度", "资本开支"],
    chainLabels: ["设备与材料", "芯片设计", "晶圆制造", "封装测试", "终端需求"],
    metricLabels: ["设备订单", "材料需求", "设计库存", "晶圆厂利用率", "先进封装需求", "终端需求", "国产替代进度", "资本开支"],
    capitalLabels,
  },
  {
    id: "robotics", name: "机器人", subtitle: "核心零部件、整机、软件视觉与应用落地证据报告；区分样机、订单、交付和量产",
    cycleLabels: ["样机进展", "订单", "交付", "量产进度", "产量", "销量", "核心零部件成本", "下游应用"],
    chainLabels: ["核心零部件", "整机", "软件与机器视觉", "应用"],
    metricLabels: ["样机进展", "订单", "交付", "量产进度", "减速器", "伺服系统", "机器视觉", "下游应用"],
    capitalLabels,
  },
];

const byId = new Map(templates.map((template) => [template.id, template]));

export function getIndustryTemplate(tagId: string): IndustryReportTemplate | undefined {
  return byId.get(tagId as IndustryReportTemplate["id"]);
}
