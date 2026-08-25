import { useEffect, useRef } from "react";
import type { PageKey } from "./types";

export interface TagRequestIdentity {
  pageKey: PageKey;
  industryId: string;
  queryKey: string;
  sequence: number;
}

export interface TagRequestContext extends TagRequestIdentity {
  signal: AbortSignal;
}

export interface TagRequestOptions<T> {
  pageKey: PageKey;
  industryId: string;
  request: (context: TagRequestContext) => Promise<T>;
  commit: (value: T, context: TagRequestIdentity) => void;
}

export type TagRequestResult = "committed" | "ignored";

export interface TagRequestCoordinator<T> {
  run: (options: TagRequestOptions<T>) => Promise<TagRequestResult>;
  cancel: () => void;
  current: () => TagRequestIdentity | null;
}

export function tagRequestKey(pageKey: PageKey, industryId: string): string {
  return `${pageKey}:${industryId}`;
}

export function createTagRequestCoordinator<T>(): TagRequestCoordinator<T> {
  let sequence = 0;
  let active: (TagRequestIdentity & { controller: AbortController }) | null = null;

  const current = (): TagRequestIdentity | null => active ? {
    pageKey: active.pageKey,
    industryId: active.industryId,
    queryKey: active.queryKey,
    sequence: active.sequence,
  } : null;

  return {
    async run({ pageKey, industryId, request, commit }) {
      active?.controller.abort();
      const controller = new AbortController();
      const identity: TagRequestIdentity = {
        pageKey,
        industryId,
        queryKey: tagRequestKey(pageKey, industryId),
        sequence: ++sequence,
      };
      active = { ...identity, controller };
      try {
        const value = await request({ ...identity, signal: controller.signal });
        const isCurrent = active?.sequence === identity.sequence
          && active.pageKey === pageKey
          && active.industryId === industryId
          && active.queryKey === identity.queryKey
          && !controller.signal.aborted;
        if (!isCurrent) return "ignored";
        commit(value, identity);
        return "committed";
      } catch (error) {
        if (controller.signal.aborted || active?.sequence !== identity.sequence) return "ignored";
        throw error;
      }
    },
    cancel() {
      active?.controller.abort();
      active = null;
    },
    current,
  };
}

export function useTagRequestCoordinator<T>(): TagRequestCoordinator<T> {
  const coordinatorRef = useRef<TagRequestCoordinator<T> | null>(null);
  if (coordinatorRef.current === null) coordinatorRef.current = createTagRequestCoordinator<T>();
  useEffect(() => {
    const coordinator = coordinatorRef.current;
    return () => coordinator?.cancel();
  }, []);
  return coordinatorRef.current;
}
