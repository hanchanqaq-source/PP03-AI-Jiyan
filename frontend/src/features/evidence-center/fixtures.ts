export type VerificationStatus = "已核验" | "多源印证" | "待核验" | "存在冲突" | "已证伪" | "已更正";

export interface EvidenceFixture {
  eventId: string;
  title: string;
  publishedAt: string;
  verifiedAt: string;
  status: VerificationStatus;
  claim: string;
  fields: Array<{ label: string; value: string; status: "已核验" | "待核验" | "存在冲突" }>;
  primaryCount: number;
  independentCount: number;
  reprintCount: number;
  conflictCount: number;
  impact: number;
  holdingsRelation: "直接关联" | "行业关联" | "待核验相关";
  primaryEvidence: string[];
  independentChains: string[];
  conflictNote: string;
  history: string[];
}

// Frontend-only demo fixtures. They never represent online verification or a real company event.
export const evidenceFixtures: EvidenceFixture[] = [
  {
    eventId: "galaxy-compute-center", title: "星河科技发布算力中心建设公告", publishedAt: "2026-08-18 13:20", verifiedAt: "2026-08-18 13:46", status: "已核验",
    claim: "星河科技正式公告建设新的算力中心。", fields: [
      { label: "核心主张", value: "正式公告建设算力中心", status: "已核验" }, { label: "投资金额", value: "12亿元", status: "已核验" },
      { label: "公告日期", value: "2026-08-18", status: "已核验" }, { label: "建设周期", value: "三年", status: "待核验" },
    ], primaryCount: 1, independentCount: 2, reprintCount: 6, conflictCount: 0, impact: 99, holdingsRelation: "直接关联",
    primaryEvidence: ["公司正式公告 · 演示证据", "交易所公告页面 · 演示证据"], independentChains: ["独立来源链 A · 行业公开采访记录（演示）", "独立来源链 B · 独立研究机构观察（演示）"],
    conflictNote: "当前未发现可靠冲突证据", history: ["13:20 待核验", "13:45 取得公司正式公告", "13:46 升级为已核验"],
  },
  {
    eventId: "yunling-capacity-plan", title: "云岭半导体产能规划获多源印证", publishedAt: "2026-08-18 14:05", verifiedAt: "2026-08-18 14:40", status: "多源印证",
    claim: "云岭半导体的扩产规划获得两条独立来源链支持。", fields: [
      { label: "核心主张", value: "扩产规划获独立印证", status: "已核验" }, { label: "规划日期", value: "2026-08-18", status: "已核验" }, { label: "具体产能", value: "待后续披露", status: "待核验" },
    ], primaryCount: 1, independentCount: 2, reprintCount: 5, conflictCount: 0, impact: 82, holdingsRelation: "行业关联",
    primaryEvidence: ["公司公开说明 · 演示证据"], independentChains: ["独立来源链 A · 行业协会公开材料（演示）", "独立来源链 B · 供应链公开访谈（演示）"],
    conflictNote: "当前未发现可靠冲突证据；转载来源不重复计入独立来源。", history: ["14:05 待核验", "14:30 取得第一条独立来源链", "14:40 升级为多源印证"],
  },
  {
    eventId: "yunling-memory-pricing", title: "云岭半导体披露存储产品价格调整计划", publishedAt: "2026-08-18 12:10", verifiedAt: "2026-08-18 15:10", status: "已证伪",
    claim: "云岭半导体计划调整部分存储产品报价。", fields: [
      { label: "核心主张", value: "官方更正为不成立", status: "已核验" }, { label: "调整比例", value: "不适用", status: "已核验" }, { label: "生效日期", value: "不适用", status: "已核验" },
    ], primaryCount: 1, independentCount: 1, reprintCount: 4, conflictCount: 0, impact: 76, holdingsRelation: "待核验相关",
    primaryEvidence: ["公司更正说明 · 演示证据"], independentChains: ["独立来源链 A · 供应链公开访谈（演示）", "独立来源链 B · 尚未形成"],
    conflictNote: "已取得官方更正或反证；此前传播的价格调整说法不得作为确定事实。", history: ["12:10 待核验", "12:35 记录独立线索，保持待核验", "15:10 取得官方更正，升级为已证伪"],
  },
  {
    eventId: "beichen-robot-policy", title: "北辰机器人产业政策解读出现分歧", publishedAt: "2026-08-17 16:30", verifiedAt: "2026-08-18 09:20", status: "存在冲突",
    claim: "北辰机器人相关政策将于本季度落地。", fields: [
      { label: "核心主张", value: "本季度落地", status: "存在冲突" }, { label: "实施范围", value: "不同来源说法不一致", status: "存在冲突" }, { label: "发布时间", value: "2026-08-17", status: "已核验" },
    ], primaryCount: 1, independentCount: 2, reprintCount: 3, conflictCount: 2, impact: 64, holdingsRelation: "行业关联",
    primaryEvidence: ["政策公开征求意见稿 · 演示证据"], independentChains: ["独立来源链 A · 公开会议纪要（演示）", "独立来源链 B · 行业协会公开说明（演示）"],
    conflictNote: "两条可靠来源对落地时间存在不同说法，前端不替用户选择结论。", history: ["16:30 多源印证", "09:20 发现可靠来源分歧", "09:21 降级为存在冲突"],
  },
  {
    eventId: "beichen-supply-plan", title: "北辰机器人供应计划仍待核验", publishedAt: "2026-08-18 10:05", verifiedAt: "2026-08-18 10:30", status: "待核验",
    claim: "北辰机器人计划扩充关键零部件供应。", fields: [
      { label: "核心主张", value: "计划扩充供应", status: "待核验" }, { label: "数量", value: "未确认", status: "待核验" }, { label: "日期", value: "未确认", status: "待核验" },
    ], primaryCount: 0, independentCount: 1, reprintCount: 2, conflictCount: 0, impact: 48, holdingsRelation: "行业关联",
    primaryEvidence: ["尚未取得一手证据 · 演示状态"], independentChains: ["独立来源链 A · 行业公开观察（演示）", "独立来源链 B · 尚未形成"],
    conflictNote: "当前证据不足，不能据此生成投资影响结论", history: ["10:05 待核验", "10:30 记录独立线索，保持待核验"],
  },
  {
    eventId: "galaxy-compute-center-correction", title: "星河科技更正算力中心公告细节", publishedAt: "2026-08-18 16:05", verifiedAt: "2026-08-18 16:20", status: "已更正",
    claim: "原始公告的建设周期说明已被更正。", fields: [
      { label: "核心主张", value: "建设周期说明已更正", status: "已核验" }, { label: "原披露日期", value: "2026-08-18", status: "已核验" }, { label: "更正后周期", value: "分阶段披露", status: "已核验" },
    ], primaryCount: 1, independentCount: 1, reprintCount: 2, conflictCount: 0, impact: 58, holdingsRelation: "直接关联",
    primaryEvidence: ["公司更正公告 · 演示证据"], independentChains: ["独立来源链 A · 公开披露复核（演示）", "独立来源链 B · 尚未形成"],
    conflictNote: "原始公告的建设周期说明已被更正，旧表述不应继续作为确定事实。", history: ["13:46 已核验", "16:05 取得更正公告", "16:20 更新为已更正"],
  },
];

export const correctionFixtures = [
  { eventId: "galaxy-compute-center", title: "星河科技发布算力中心建设公告", from: "待核验", to: "已核验", type: "取得一手证据", at: "2026-08-18 13:46", source: "公司正式公告（演示）" },
  { eventId: "beichen-robot-policy", title: "北辰机器人产业政策解读出现分歧", from: "多源印证", to: "存在冲突", type: "可靠来源分歧", at: "2026-08-18 09:21", source: "行业协会公开说明（演示）" },
  { eventId: "yunling-memory-pricing", title: "云岭半导体披露存储产品价格调整计划", from: "待核验", to: "已证伪", type: "官方更正", at: "2026-08-18 15:10", source: "更正说明（演示）" },
  { eventId: "galaxy-compute-center-correction", title: "星河科技更正算力中心公告细节", from: "已核验", to: "已更正", type: "公告细节更正", at: "2026-08-18 16:20", source: "公司更正公告（演示）" },
];
