import { api, ApiError, isAbortError, validateNewsPipelineStatus } from "@/lib/api";
import { cn } from "@/lib/utils";
import type {
  MarketNewsQuery,
  NewsPipelinePhase,
  NewsPipelineErrorCode,
  NewsPipelineStarted,
  NewsPipelineStatusData,
} from "./types";

const ACTIVE_PHASES = new Set<NewsPipelinePhase>([
  "queued",
  "fetching",
  "raw_saved",
  "verifying",
  "evidence_saved",
]);

const PHASE_ORDER: Partial<Record<NewsPipelinePhase, number>> = {
  queued: 0,
  fetching: 1,
  raw_saved: 2,
  verifying: 3,
  evidence_saved: 4,
  trusted_published: 5,
};

const STAGES: Array<{ phase: NewsPipelinePhase; label: string }> = [
  { phase: "queued", label: "排队" },
  { phase: "fetching", label: "抓取来源" },
  { phase: "raw_saved", label: "原始快照" },
  { phase: "verifying", label: "确定性核验" },
  { phase: "evidence_saved", label: "证据快照" },
  { phase: "trusted_published", label: "可信发布" },
];

const TERMINAL_LABEL: Partial<Record<NewsPipelinePhase, string>> = {
  failed: "运行失败",
  interrupted: "运行中断",
};

const FAILURE_COPY: Record<NewsPipelineErrorCode, string> = {
  collection_failed: "公开资讯抓取失败；继续显示上一份可信快照。",
  verification_failed: "确定性核验失败；继续显示上一份可信快照。",
  evidence_persistence_failed: "证据快照保存失败；继续显示上一份可信快照。",
  evidence_compatibility_failed: "证据兼容发布未完成；继续显示上一份可信快照。",
  publication_failed: "可信资讯发布失败；继续显示上一份可信快照。",
  radar_compatibility_failed: "资讯雷达兼容更新未完成；可信资讯快照仍可使用。",
  pipeline_interrupted: "本轮资讯刷新在完成前中断；继续显示上一份可信快照。",
  storage_error: "资讯快照存储暂时不可用；继续显示上一份可信快照。",
  pipeline_error: "资讯流水线未完成；继续显示上一份可信快照。",
};

export function newsPipelineFailureMessage(status: NewsPipelineStatusData): string {
  if (status.redacted_error) return FAILURE_COPY[status.redacted_error];
  return status.phase === "interrupted"
    ? FAILURE_COPY.pipeline_interrupted
    : FAILURE_COPY.pipeline_error;
}

export function isNewsPipelineActive(phase: NewsPipelineStatusData["phase"]): phase is NewsPipelinePhase {
  return phase !== null && ACTIVE_PHASES.has(phase);
}

function nextPoll(signal: AbortSignal | undefined, delayMs: number): Promise<boolean> {
  if (signal?.aborted) return Promise.resolve(false);
  if (delayMs <= 0) return Promise.resolve().then(() => !signal?.aborted);
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      resolve(value);
    };
    const abort = () => finish(false);
    const timer = window.setTimeout(() => finish(true), delayMs);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

export interface RunNewsPipelineOptions {
  query?: MarketNewsQuery;
  signal?: AbortSignal;
  pollIntervalMs?: number;
  onStarted?: (runId: string) => void;
  onStatus?: (status: NewsPipelineStatusData) => void | Promise<void>;
  kickoff?: (signal?: AbortSignal) => Promise<NewsPipelineStarted>;
}

function validatePollSequence(
  previous: NewsPipelineStatusData | null,
  next: NewsPipelineStatusData,
  started: NewsPipelineStarted,
): void {
  if (!next.loaded || next.run_id !== started.run_id || next.raw_snapshot_id !== started.raw_snapshot_id) {
    throw new ApiError("资讯流水线响应无效", 502);
  }
  if (next.phase === "trusted_published" && (
    next.trusted_snapshot_id !== started.raw_snapshot_id
    || next.displayed_trusted_snapshot_id !== started.raw_snapshot_id
    || next.displayed_trusted?.snapshot_id !== started.raw_snapshot_id
    || next.displayed_trusted.event_count !== next.admitted_count
  )) {
    throw new ApiError("资讯流水线响应无效", 502);
  }
  if (!previous) return;
  const previousOrder = previous.phase ? PHASE_ORDER[previous.phase] : undefined;
  const nextOrder = next.phase ? PHASE_ORDER[next.phase] : undefined;
  if (previousOrder !== undefined && nextOrder !== undefined && nextOrder < previousOrder) {
    throw new ApiError("资讯流水线响应无效", 502);
  }
  if (previous.evidence_snapshot_id !== null && next.evidence_snapshot_id !== previous.evidence_snapshot_id) {
    throw new ApiError("资讯流水线响应无效", 502);
  }
  if (previous.trusted_snapshot_id !== null && next.trusted_snapshot_id !== previous.trusted_snapshot_id) {
    throw new ApiError("资讯流水线响应无效", 502);
  }
  if (previous.counts && next.counts) {
    for (const key of Object.keys(previous.counts) as Array<keyof typeof previous.counts>) {
      if (next.counts[key] < previous.counts[key]) throw new ApiError("资讯流水线响应无效", 502);
    }
  }
  if (next.phase !== "trusted_published"
    && next.displayed_trusted_snapshot_id !== previous.displayed_trusted_snapshot_id) {
    throw new ApiError("资讯流水线响应无效", 502);
  }
}

export async function runNewsPipelineRefresh({
  query,
  signal,
  pollIntervalMs = 800,
  onStarted,
  onStatus,
  kickoff,
}: RunNewsPipelineOptions = {}): Promise<NewsPipelineStatusData | null> {
  const requestSignal = signal ?? new AbortController().signal;
  if (requestSignal.aborted) return null;
  let started: NewsPipelineStarted;
  try {
    started = await (kickoff ? kickoff(requestSignal) : api.marketNewsRefresh(query, requestSignal));
  } catch (error) {
    if (requestSignal.aborted || isAbortError(error)) return null;
    throw error;
  }
  if (requestSignal.aborted) return null;
  onStarted?.(started.run_id);
  let previousStatus: NewsPipelineStatusData | null = null;

  while (!requestSignal.aborted) {
    let status: NewsPipelineStatusData;
    try {
      status = validateNewsPipelineStatus(await api.newsPipelineStatus(started.run_id, requestSignal));
    } catch (error) {
      if (requestSignal.aborted || isAbortError(error)) return null;
      throw error;
    }
    if (requestSignal.aborted) return null;
    validatePollSequence(previousStatus, status, started);
    await onStatus?.(status);
    previousStatus = status;
    if (requestSignal.aborted) return null;
    if (!isNewsPipelineActive(status.phase)) return status;
    if (!await nextPoll(requestSignal, pollIntervalMs)) return null;
  }
  return null;
}

function Count({ label, value }: { label: string; value: number | undefined }) {
  return <span className="inline-flex items-baseline gap-1 whitespace-nowrap"><span className="text-muted-foreground">{label}</span><strong className="font-semibold tabular-nums text-foreground">{value ?? "—"}</strong></span>;
}

function SnapshotId({ label, value }: { label: string; value: string | null }) {
  return <span className="min-w-0"><span className="text-muted-foreground">{label}：</span><span className="break-all font-mono text-foreground">{value || "—"}</span></span>;
}

export function NewsPipelineStatus({ status }: { status: NewsPipelineStatusData | null }) {
  if (!status) return null;
  const counts = status.counts;
  const stageIndex = STAGES.findIndex((stage) => stage.phase === status.phase);
  const inVerification = status.phase === "raw_saved" || status.phase === "verifying" || status.phase === "evidence_saved";
  const pendingEvidence = Boolean(
    status.has_pending_evidence_message
    || (status.phase === "trusted_published" && (counts?.raw_event_count ?? 0) > 0 && status.admitted_count === 0),
  );
  const currentTrustedId = status.displayed_trusted_snapshot_id;

  return <section role="status" aria-label="资讯流水线状态" className="mb-4 overflow-hidden rounded-xl border border-border/65 bg-gradient-to-r from-slate-950/55 via-background/55 to-primary/5">
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border/45 px-3 py-2.5">
      <p className="mr-1 text-xs font-semibold tracking-wide text-foreground">资讯流水线</p>
      <ol className="flex min-w-0 flex-1 flex-wrap items-center gap-1" aria-label="流水线阶段">
        {STAGES.map((stage, index) => {
          const reached = stageIndex >= index && status.phase !== "failed" && status.phase !== "interrupted";
          const current = status.phase === stage.phase;
          return <li key={stage.phase} className="flex items-center gap-1">
            {index > 0 && <span aria-hidden="true" className="h-px w-2 bg-border sm:w-4" />}
            <span
              aria-current={current ? "step" : undefined}
              className={cn(
                "rounded-full border px-2 py-1 text-[11px] leading-none",
                current && "border-primary/55 bg-primary/15 font-semibold text-primary",
                !current && reached && "border-emerald-500/35 bg-emerald-500/10 text-emerald-300",
                !current && !reached && "border-border/55 text-muted-foreground",
              )}
            >{stage.label}</span>
          </li>;
        })}
      </ol>
      {status.phase && TERMINAL_LABEL[status.phase] && <span className="rounded-full border border-destructive/35 bg-destructive/10 px-2 py-1 text-[11px] font-semibold text-destructive">{TERMINAL_LABEL[status.phase]}</span>}
    </div>

    <div className="flex flex-wrap gap-x-4 gap-y-1.5 px-3 py-2 text-xs">
      <Count label="已抓取" value={counts?.raw_event_count} />
      <Count label="已核验" value={counts?.verified_count} />
      <Count label="多源印证" value={counts?.corroborated_count} />
      <Count label="待核验" value={counts?.pending_count} />
      <Count label="存在冲突" value={counts?.conflicting_count} />
      <Count label="已更正" value={counts?.corrected_count} />
      <Count label="已证伪" value={counts?.disproved_count} />
      <Count label="来源失败" value={counts?.failed_source_count} />
    </div>

    <div className="grid gap-1 border-t border-border/35 px-3 py-2 text-[11px] sm:grid-cols-2 xl:grid-cols-4">
      <SnapshotId label="原始快照" value={status.raw_snapshot_id} />
      <SnapshotId label="证据快照" value={status.evidence_snapshot_id} />
      <SnapshotId label="本轮可信快照" value={status.trusted_snapshot_id} />
      <SnapshotId label="当前可信快照" value={currentTrustedId} />
    </div>

    {inVerification && <p className="border-t border-primary/20 bg-primary/5 px-3 py-2 text-xs text-primary">新资讯已抓取，核验处理中；当前显示上一份可信快照。</p>}
    {pendingEvidence && <p className="border-t border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">本次已抓取 {counts?.raw_event_count ?? 0} 条资讯，目前尚无完成核验的内容。待核验资讯可在证据中心查看。</p>}
    {status.phase === "trusted_published" && status.redacted_error === "radar_compatibility_failed" && <p className="border-t border-warning/25 bg-warning/5 px-3 py-2 text-xs text-warning">{FAILURE_COPY.radar_compatibility_failed}</p>}
    {(status.phase === "failed" || status.phase === "interrupted") && <p className="border-t border-destructive/25 bg-destructive/5 px-3 py-2 text-xs text-destructive">{newsPipelineFailureMessage(status)}</p>}
  </section>;
}
