import type { TagDefinition } from "./types";

const root = (id: string, name: string, order: number, aliases: string[] = []): TagDefinition => ({
  id,
  name,
  slug: id,
  type: "一级行业",
  parent_id: null,
  aliases,
  keywords: [name, ...aliases],
  description: `${name}行业标签根节点`,
  sort_order: order,
  enabled: true,
});

const child = (
  id: string,
  name: string,
  parentId: string,
  order: number,
  options: Partial<Pick<TagDefinition, "type" | "aliases" | "keywords" | "description" | "news_track" | "report_template">> = {},
): TagDefinition => ({
  id,
  name,
  slug: id,
  type: options.type || "二级行业",
  parent_id: parentId,
  aliases: options.aliases || [],
  keywords: options.keywords || [name, ...(options.aliases || [])],
  description: options.description || `${name}行业与产业链标签`,
  sort_order: order,
  enabled: true,
  news_track: options.news_track,
  report_template: options.report_template,
});

export const TAG_CATALOG: TagDefinition[] = [
  root("technology", "科技", 10),
  child("semiconductor", "半导体", "technology", 11, {
    aliases: ["芯片", "集成电路"],
    keywords: ["半导体", "芯片", "集成电路", "晶圆", "封装"],
    news_track: "semi",
    report_template: "semiconductor",
  }),
  child("storage", "存储", "semiconductor", 12, {
    type: "细分产业链",
    aliases: ["存储器", "DRAM", "NAND", "HBM"],
    keywords: ["存储", "存储器", "DRAM", "NAND", "HBM", "memory chip", "memory"],
    news_track: "semi",
    report_template: "storage",
  }),
  child("chip-design", "芯片设计", "semiconductor", 13, { type: "细分产业链", aliases: ["IC设计"] }),
  child("semiconductor-equipment", "半导体设备", "semiconductor", 14, { type: "细分产业链" }),
  child("semiconductor-materials", "半导体材料", "semiconductor", 15, { type: "细分产业链" }),
  child("advanced-packaging", "先进封装", "semiconductor", 16, { type: "细分产业链", aliases: ["Chiplet"] }),
  child("pcb", "PCB", "semiconductor", 17, { type: "细分产业链", aliases: ["印制电路板"] }),
  child("artificial-intelligence", "人工智能", "technology", 20, { aliases: ["AI"], news_track: "ai" }),
  child("ai-computing", "AI算力", "artificial-intelligence", 21, { type: "细分产业链", aliases: ["算力"] }),
  child("optical-module", "光模块", "artificial-intelligence", 22, { type: "细分产业链", aliases: ["CPO"] }),
  child("liquid-cooling", "液冷", "artificial-intelligence", 23, { type: "细分产业链" }),
  child("data-center", "数据中心", "artificial-intelligence", 24, { type: "细分产业链" }),
  child("robotics", "机器人", "technology", 30, {
    aliases: ["人形机器人", "具身智能", "自动化"],
    keywords: ["机器人", "人形机器人", "具身智能", "robot", "robotics", "automation"],
    news_track: "robot",
    report_template: "robotics",
  }),
  child("reducer", "减速器", "robotics", 31, { type: "细分产业链", aliases: ["谐波减速器"] }),
  child("servo-system", "伺服系统", "robotics", 32, { type: "细分产业链" }),
  child("sensor", "传感器", "robotics", 33, { type: "细分产业链" }),
  child("machine-vision", "机器视觉", "robotics", 34, { type: "细分产业链" }),

  root("healthcare", "医疗", 100, ["医药", "生物医药"]),
  child("innovative-drug", "创新药", "healthcare", 101, { news_track: "bio" }),
  child("medical-device", "医疗器械", "healthcare", 102),
  root("consumer", "消费", 200),
  child("consumer-electronics", "消费电子", "consumer", 201, { news_track: "consumer" }),
  child("food-beverage", "食品饮料", "consumer", 202),
  root("finance", "金融", 300),
  child("banking", "银行", "finance", 301),
  child("insurance", "保险", "finance", 302),
  root("new-energy", "新能源", 400),
  child("new-energy-vehicle", "新能源汽车", "new-energy", 401, { news_track: "auto" }),
  child("energy-storage", "储能", "new-energy", 402, { news_track: "energy" }),
  child("photovoltaic", "光伏", "new-energy", 403, { news_track: "energy" }),
  root("advanced-manufacturing", "高端制造", 500),
  child("industrial-automation", "工业自动化", "advanced-manufacturing", 501, { news_track: "robot" }),
  child("commercial-space", "商业航天", "advanced-manufacturing", 502, { news_track: "space" }),
  root("defense", "军工", 600),
  root("cyclical-resources", "周期资源", 700),
  child("nonferrous", "有色金属", "cyclical-resources", 701),
  child("coal", "煤炭", "cyclical-resources", 702),
  root("media", "传媒", 800),
  root("agriculture", "农业", 900),
  root("utilities", "公用事业", 1000),
  root("real-estate", "房地产", 1100),
  root("transportation", "交通运输", 1200),
  root("overseas-market", "海外市场", 1300),
  child("us-market", "美国市场", "overseas-market", 1301, { type: "市场范围", aliases: ["美股"] }),
  child("hk-market", "香港市场", "overseas-market", 1302, { type: "市场范围", aliases: ["港股"] }),
];

const byId = new Map(TAG_CATALOG.map((tag) => [tag.id, tag]));

export function getTag(id: string): TagDefinition | undefined {
  return byId.get(id);
}

export function rootTags(): TagDefinition[] {
  return TAG_CATALOG.filter((tag) => tag.parent_id === null && tag.enabled)
    .sort((a, b) => a.sort_order - b.sort_order);
}

export function childrenOf(parentId: string): TagDefinition[] {
  return TAG_CATALOG.filter((tag) => tag.parent_id === parentId && tag.enabled)
    .sort((a, b) => a.sort_order - b.sort_order);
}

export function descendantsOf(parentId: string): TagDefinition[] {
  const direct = childrenOf(parentId);
  return direct.flatMap((tag) => [tag, ...descendantsOf(tag.id)]);
}

export function searchTags(query: string): TagDefinition[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return TAG_CATALOG.filter((tag) => tag.enabled);
  return TAG_CATALOG.filter((tag) => {
    const haystack = [tag.name, tag.slug, tag.description, ...tag.aliases, ...tag.keywords]
      .join(" ")
      .toLocaleLowerCase();
    return tag.enabled && haystack.includes(needle);
  });
}
