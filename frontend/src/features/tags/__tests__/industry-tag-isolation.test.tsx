import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createTagRequestCoordinator, tagRequestKey } from "../requestCoordinator";
import { loadCustomTagCatalog } from "../preferences";
import { usePageTags } from "../usePageTags";

interface ReportObject {
  industryId: string;
  snapshotId: string;
  metrics: string[];
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("industry tag state and request isolation", () => {
  beforeEach(() => localStorage.clear());

  it("reuses the built-in 先进封装 tag, then persists a novel custom tag and active order across remounts", async () => {
    const user = userEvent.setup();

    function Harness() {
      const tags = usePageTags("industry_research");
      return <div>
        <button onClick={() => tags.create("  先进封装  ")}>创建先进封装</button>
        <button onClick={() => tags.create("量子传感")}>创建量子传感</button>
        <output aria-label="标签状态">{JSON.stringify({
          activeId: tags.state.activeId,
          order: tags.state.order,
          names: tags.tags.map((tag) => tag.name),
        })}</output>
      </div>;
    }

    const first = render(<Harness />);
    await user.click(screen.getByRole("button", { name: "创建先进封装" }));
    expect(screen.getByLabelText("标签状态")).toHaveTextContent('"activeId":"advanced-packaging"');
    expect(loadCustomTagCatalog().items).toEqual([]);

    await user.click(screen.getByRole("button", { name: "创建量子传感" }));
    const persistedText = screen.getByLabelText("标签状态").textContent;
    expect(persistedText).toContain('"activeId":"custom-');
    expect(persistedText).toContain('"names":["量子传感"');
    first.unmount();

    render(<Harness />);
    expect(screen.getByLabelText("标签状态").textContent).toBe(persistedText);
  });

  it("binds semiconductor, storage, robotics and custom reports to independent page+industry keys", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const committed = new Map<string, ReportObject>();
    const industries = ["semiconductor", "storage", "robotics", "custom-independent"];

    for (const industryId of industries) {
      await coordinator.run({
        pageKey: "industry_research",
        industryId,
        request: async ({ queryKey }) => ({
          industryId,
          snapshotId: `snapshot-${queryKey}`,
          metrics: [`metric-${industryId}`],
        }),
        commit: (report, context) => committed.set(context.queryKey, report),
      });
    }

    expect([...committed.keys()]).toEqual(industries.map((id) => `industry_research:${id}`));
    for (const industryId of industries) {
      const report = committed.get(tagRequestKey("industry_research", industryId));
      expect(report).toEqual({
        industryId,
        snapshotId: `snapshot-industry_research:${industryId}`,
        metrics: [`metric-${industryId}`],
      });
    }
    expect(new Set([...committed.values()]).size).toBe(4);
  });

  it("aborts each prior request and ignores late responses across three rapid switches", async () => {
    const coordinator = createTagRequestCoordinator<ReportObject>();
    const ids = ["semiconductor", "storage", "robotics", "custom-independent"];
    const waits = new Map(ids.map((id) => [id, deferred<ReportObject>()]));
    const signals: AbortSignal[] = [];
    const committed: ReportObject[] = [];

    const runs = ids.map((industryId) => coordinator.run({
      pageKey: "industry_research",
      industryId,
      request: ({ signal }) => {
        signals.push(signal);
        return waits.get(industryId)!.promise;
      },
      commit: (report) => committed.push(report),
    }));

    waits.get("custom-independent")!.resolve({
      industryId: "custom-independent", snapshotId: "snapshot-custom", metrics: ["custom-only"],
    });
    waits.get("robotics")!.resolve({ industryId: "robotics", snapshotId: "snapshot-robot", metrics: ["robot-only"] });
    waits.get("storage")!.resolve({ industryId: "storage", snapshotId: "snapshot-storage", metrics: ["storage-only"] });
    waits.get("semiconductor")!.resolve({
      industryId: "semiconductor", snapshotId: "snapshot-semi", metrics: ["semi-only"],
    });
    await Promise.all(runs);

    expect(signals.map((signal) => signal.aborted)).toEqual([true, true, true, false]);
    expect(committed).toEqual([{
      industryId: "custom-independent", snapshotId: "snapshot-custom", metrics: ["custom-only"],
    }]);
    expect(coordinator.current()).toEqual({
      pageKey: "industry_research",
      industryId: "custom-independent",
      queryKey: "industry_research:custom-independent",
      sequence: 4,
    });
  });
});
