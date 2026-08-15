import { useCallback, useMemo, useState } from "react";
import { getTag } from "./catalog";
import { loadPageTagState, moveTag, savePageTagState } from "./preferences";
import type { PageKey, PageTagState } from "./types";

export function usePageTags(pageKey: PageKey) {
  const [state, setState] = useState<PageTagState>(() => loadPageTagState(pageKey));

  const update = useCallback((next: PageTagState) => {
    const saved = savePageTagState(pageKey, next);
    setState(saved);
  }, [pageKey]);

  const replace = useCallback((ids: string[]) => {
    const unique = Array.from(new Set(ids.filter((id) => !!getTag(id))));
    update({
      ids: unique,
      activeId: unique.includes(state.activeId) ? state.activeId : unique[0] || "",
    });
  }, [state.activeId, update]);

  const remove = useCallback((id: string) => replace(state.ids.filter((tagId) => tagId !== id)), [replace, state.ids]);
  const activate = useCallback((id: string) => {
    if (state.ids.includes(id)) update({ ...state, activeId: id });
  }, [state, update]);
  const reorder = useCallback((sourceId: string, targetId: string) => {
    update({ ...state, ids: moveTag(state.ids, sourceId, targetId) });
  }, [state, update]);

  const tags = useMemo(() => state.ids.map(getTag).filter((tag) => tag !== undefined), [state.ids]);

  return { state, tags, activeTag: getTag(state.activeId), replace, remove, activate, reorder };
}
