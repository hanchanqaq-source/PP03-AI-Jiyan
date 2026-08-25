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
  kind?: "built_in" | "custom";
}

export interface PageTagState {
  version: 2;
  ids: string[];
  activeId: string;
  order: string[];
}

export interface PageTagStateInput {
  ids: string[];
  activeId: string;
  order?: string[];
}

export interface CustomTagCatalogItem {
  id: string;
  name: string;
  kind: "custom";
}

export interface CustomTagCatalogState {
  version: 1;
  items: CustomTagCatalogItem[];
}

export interface CustomTagCreateResult {
  tag: TagDefinition;
  created: boolean;
}
