import { storageGet, storageSet } from "@/lib/storage";
import { getTag } from "./catalog";
import type { PageKey, PageTagState } from "./types";

export const DEFAULT_TAG_IDS = ["semiconductor", "storage", "robotics"];
const DEFAULT_ACTIVE = "storage";
const keyFor = (pageKey: PageKey) => `vr-page-tags:${pageKey}`;

const fallback = (): PageTagState => ({ ids: [...DEFAULT_TAG_IDS], activeId: DEFAULT_ACTIVE });

function normalize(value: unknown): PageTagState {
  if (!value || typeof value !== "object") return fallback();
  const raw = value as Partial<PageTagState>;
  if (!Array.isArray(raw.ids)) return fallback();
  const ids = Array.from(new Set(raw.ids.filter((id): id is string => typeof id === "string" && !!getTag(id))));
  if (ids.length === 0) return { ids: [], activeId: "" };
  const activeId = typeof raw.activeId === "string" && ids.includes(raw.activeId) ? raw.activeId : ids[0];
  return { ids, activeId };
}

export function loadPageTagState(pageKey: PageKey): PageTagState {
  const raw = storageGet(keyFor(pageKey));
  if (!raw) return fallback();
  try {
    return normalize(JSON.parse(raw));
  } catch {
    return fallback();
  }
}

export function savePageTagState(pageKey: PageKey, state: PageTagState): PageTagState {
  const normalized = normalize(state);
  storageSet(keyFor(pageKey), JSON.stringify(normalized));
  return normalized;
}

export function moveTag(ids: string[], sourceId: string, targetId: string): string[] {
  const sourceIndex = ids.indexOf(sourceId);
  const targetIndex = ids.indexOf(targetId);
  if (sourceIndex < 0 || targetIndex < 0 || sourceIndex === targetIndex) return [...ids];
  const next = [...ids];
  next.splice(sourceIndex, 1);
  next.splice(next.indexOf(targetId), 0, sourceId);
  return next;
}
