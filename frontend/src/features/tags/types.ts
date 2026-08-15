export type TagType = "一级行业" | "二级行业" | "细分产业链" | "投资主题" | "市场范围" | "自定义标签";

export type PageKey = "market_news" | "industry_research";

export interface TagDefinition {
  id: string;
  name: string;
  slug: string;
  type: TagType;
  parent_id: string | null;
  aliases: string[];
  keywords: string[];
  description: string;
  sort_order: number;
  enabled: boolean;
  news_track?: string;
  report_template?: "semiconductor" | "storage" | "robotics";
}

export interface PageTagState {
  ids: string[];
  activeId: string;
}
