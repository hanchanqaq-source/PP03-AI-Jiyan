import { useCallback, useMemo, useState } from "react";
import {
  createOrReuseCustomTag,
  deleteCustomTag,
  loadCustomTagCatalog,
  loadPageTagState,
  moveTag,
  resolveTag,
  savePageTagState,
} from "./preferences";
import type { PageKey, PageTagState, PageTagStateInput } from "./types";

export function usePageTags(pageKey: PageKey) {
  const [state, setState] = useState<PageTagState>(() => loadPageTagState(pageKey));

  const update = useCallback((next: PageTagStateInput) => {
    const saved = savePageTagState(pageKey, next);
    setState(saved);
  }, [pageKey]);

  const replace = useCallback((ids: string[]) => {
    const unique = Array.from(new Set(ids.filter((id) => !!resolveTag(id))));
    update({
      ids: unique,
      order: unique,
      activeId: unique.includes(state.activeId) ? state.activeId : unique[0] || "",
    });
  }, [state.activeId, update]);

  const remove = useCallback((id: string) => {
    if (resolveTag(id)?.kind === "custom") {
      deleteCustomTag(id);
      setState(loadPageTagState(pageKey));
      return;
    }
    replace(state.order.filter((tagId) => tagId !== id));
  }, [pageKey, replace, state.order]);
  const activate = useCallback((id: string) => {
    if (state.ids.includes(id)) update({ ...state, activeId: id });
  }, [state, update]);
  const reorder = useCallback((sourceId: string, targetId: string) => {
    const order = moveTag(state.order, sourceId, targetId);
    update({ ...state, ids: order, order });
  }, [state, update]);

  const create = useCallback((name: string) => {
    const result = createOrReuseCustomTag(name);
    const order = [result.tag.id, ...state.order.filter((id) => id !== result.tag.id)];
    update({ ids: order, order, activeId: result.tag.id });
    return result;
  }, [state.order, update]);

  const tags = useMemo(() => state.order.map(resolveTag).filter((tag) => tag !== undefined), [state]);
  const customTags = useMemo(() => loadCustomTagCatalog().items
    .map((tag) => resolveTag(tag.id))
    .filter((tag) => tag !== undefined), [state]);

  return {
    state,
    tags,
    customTags,
    activeTag: resolveTag(state.activeId),
    replace,
    remove,
    activate,
    reorder,
    create,
  };
}
