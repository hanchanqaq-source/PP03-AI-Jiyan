import { useCallback, useMemo, useState } from "react";
import {
  createAndSelectTag,
  deleteCustomTag,
  loadCustomTagCatalog,
  loadPageTagState,
  moveTag,
  moveTagByOffset,
  resolveTag,
  savePageTagState,
} from "./preferences";
import type { PageKey, PageTagState, PageTagStateInput } from "./types";

export function usePageTags(pageKey: PageKey) {
  const [state, setState] = useState<PageTagState>(() => loadPageTagState(pageKey));
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const update = useCallback((next: PageTagStateInput) => {
    const saved = savePageTagState(pageKey, next);
    setState(saved);
    setErrorMessage(null);
    return saved;
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
    try {
      if (resolveTag(id)?.kind === "custom") {
        deleteCustomTag(id);
        setState(loadPageTagState(pageKey));
        setErrorMessage(null);
        return true;
      }
      replace(state.order.filter((tagId) => tagId !== id));
      return true;
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : "标签删除失败，请稍后重试");
      return false;
    }
  }, [pageKey, replace, state.order]);
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

  const tags = useMemo(() => state.order.map(resolveTag).filter((tag) => tag !== undefined), [state]);
  const customTags = useMemo(() => loadCustomTagCatalog().items
    .map((tag) => resolveTag(tag.id))
    .filter((tag) => tag !== undefined), [state]);

  return {
    state,
    errorMessage,
    tags,
    customTags,
    activeTag: resolveTag(state.activeId),
    replace,
    remove,
    activate,
    reorder,
    move,
    create,
  };
}
