import { useEffect, useRef } from "react";
import type { PageKey } from "./types";

const PAGE_KEYS = new Set<PageKey>(["market_news", "industry_research"]);
const INDUSTRY_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export interface TagRequestIdentity {
  pageKey: PageKey;
  industryId: string;
  queryKey: string;
  sequence: bigint;
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

export interface TagRequestCoordinatorOptions {
  initialSequence?: bigint;
}

export type TagRequestResult = "committed" | "ignored";

export interface TagRequestCoordinator<T> {
  run: (options: TagRequestOptions<T>) => Promise<TagRequestResult>;
  cancel: () => void;
  dispose: () => void;
  current: () => TagRequestIdentity | null;
}

function assertRequestIdentity(pageKey: unknown, industryId: unknown): asserts pageKey is PageKey {
  if (typeof pageKey !== "string" || !PAGE_KEYS.has(pageKey as PageKey)
    || typeof industryId !== "string" || !INDUSTRY_ID_PATTERN.test(industryId)) {
    throw new Error("请求标签参数无效：页面或行业 ID 不符合约定");
  }
}

export function tagRequestKey(pageKey: PageKey, industryId: string): string {
  assertRequestIdentity(pageKey, industryId);
  return `${pageKey.length}:${pageKey}${industryId.length}:${industryId}`;
}

export function createTagRequestCoordinator<T>(
  options: TagRequestCoordinatorOptions = {},
): TagRequestCoordinator<T> {
  let sequence = options.initialSequence ?? 0n;
  let disposed = false;
  let active: (TagRequestIdentity & { controller: AbortController; token: object }) | null = null;

  const current = (): TagRequestIdentity | null => active ? {
    pageKey: active.pageKey,
    industryId: active.industryId,
    queryKey: active.queryKey,
    sequence: active.sequence,
  } : null;

  const cancel = () => {
    active?.controller.abort();
    active = null;
  };

  return {
    async run({ pageKey, industryId, request, commit }) {
      if (disposed) throw new Error("请求协调器已释放，不能继续使用");
      assertRequestIdentity(pageKey, industryId);
      const queryKey = tagRequestKey(pageKey, industryId);

      active?.controller.abort();
      const controller = new AbortController();
      const token = {};
      const identity: TagRequestIdentity = {
        pageKey,
        industryId,
        queryKey,
        sequence: sequence += 1n,
      };
      active = { ...identity, controller, token };
      try {
        const value = await request({ ...identity, signal: controller.signal });
        const isCurrent = active?.token === token && !controller.signal.aborted && !disposed;
        if (!isCurrent) return "ignored";
        commit(value, identity);
        return "committed";
      } catch (error) {
        if (controller.signal.aborted || active?.token !== token || disposed) return "ignored";
        active = null;
        throw error;
      }
    },
    cancel,
    dispose() {
      if (disposed) return;
      disposed = true;
      cancel();
    },
    current,
  };
}

export function useTagRequestCoordinator<T>(): TagRequestCoordinator<T> {
  const activeRef = useRef<TagRequestCoordinator<T> | null>(null);
  const sequenceRef = useRef(0n);
  const facadeRef = useRef<TagRequestCoordinator<T> | null>(null);
  if (facadeRef.current === null) {
    facadeRef.current = {
      async run(options) {
        const coordinator = activeRef.current;
        if (coordinator === null) throw new Error("请求协调器已释放，不能继续使用");
        return coordinator.run({
          ...options,
          request: (context) => {
            sequenceRef.current = context.sequence;
            return options.request(context);
          },
        });
      },
      cancel() {
        activeRef.current?.cancel();
      },
      dispose() {
        const coordinator = activeRef.current;
        activeRef.current = null;
        coordinator?.dispose();
      },
      current() {
        return activeRef.current?.current() ?? null;
      },
    };
  }
  useEffect(() => {
    const coordinator = createTagRequestCoordinator<T>({ initialSequence: sequenceRef.current });
    activeRef.current = coordinator;
    return () => {
      coordinator.dispose();
      if (activeRef.current === coordinator) activeRef.current = null;
    };
  }, []);
  return facadeRef.current;
}
