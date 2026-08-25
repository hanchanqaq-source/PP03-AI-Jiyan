import { useCallback, useMemo, useState } from "react";
import {
  createAndSelectTag,
  deleteCustomTag,
  loadPageTagState,
  loadTagCatalogView,
  moveTag,
  moveTagByOffset,
  savePageTagState,
} from "./preferences";
import type { PageKey, PageTagState, PageTagStateInput } from "./types";

export function usePageTags(pageKey: PageKey) {
  const [state, setState] = useState<PageTagState>(() => loadPageTagState(pageKey));
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const catalog = useMemo(() => loadTagCatalogView(), [state]);

  const update = useCallback((next: PageTagStateInput) => {
    const saved = savePageTagState(pageKey, next);
    setState(saved);
    setErrorMessage(null);
    return saved;
  }, [pageKey]);

  const replace = useCallback((ids: string[]) => {
    try {
      const unique = Array.from(new Set(ids.filter((id) => catalog.byId.has(id))));
      update({
        ids: unique,
        order: unique,
        activeId: unique.includes(state.activeId) ? state.activeId : unique[0] || "",
      });
      return true;
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "标签选择保存失败，请稍后重试");
      return false;
    }
  }, [catalog.byId, state.activeId, update]);

  const remove = useCallback((id: string) => {
    try {
      if (catalog.byId.get(id)?.kind === "custom") {
        deleteCustomTag(id);
        setState(loadPageTagState(pageKey));
        setErrorMessage(null);
        return true;
      }
      return replace(state.order.filter((tagId) => tagId !== id));
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "标签删除失败，请稍后重试");
      return false;
    }
  }, [catalog.byId, pageKey, replace, state.order]);
  const activate = useCallback((id: string) => {
    if (!state.ids.includes(id)) return;
    try {
      update({ ...state, activeId: id });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "标签切换失败，请稍后重试");
    }
  }, [state, update]);
  const reorder = useCallback((sourceId: string, targetId: string) => {
    try {
      const order = moveTag(state.order, sourceId, targetId);
      update({ ...state, ids: order, order });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "标签排序保存失败，请稍后重试");
    }
  }, [state, update]);
  const move = useCallback((id: string, offset: -1 | 1) => {
    try {
      const order = moveTagByOffset(state.order, id, offset);
      update({ ...state, ids: order, order });
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "标签排序保存失败，请稍后重试");
    }
  }, [state, update]);

  const create = useCallback((name: string) => {
    try {
      const result = createAndSelectTag(pageKey, name, state.order);
      setState(result.state);
      setErrorMessage(null);
      return result;
    } catch (error) {
      const failure = error instanceof Error ? error : new Error("标签创建失败，请稍后重试");
      setErrorMessage(failure.message);
      throw failure;
    }
  }, [pageKey, state.order]);

  const tags = useMemo(() => state.order
    .map((id) => catalog.byId.get(id))
    .filter((tag): tag is NonNullable<typeof tag> => tag !== undefined), [catalog.byId, state.order]);

  return {
    state,
    errorMessage,
    tags,
    customTags: catalog.customTags,
    activeTag: catalog.byId.get(state.activeId),
    replace,
    remove,
    activate,
    reorder,
    move,
    create,
  };
}
