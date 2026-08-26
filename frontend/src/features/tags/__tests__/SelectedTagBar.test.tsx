import { useState } from "react";
import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { getTag } from "../catalog";
import { loadPageTagState } from "../preferences";
import { SelectedTagBar } from "../SelectedTagBar";
import type { TagDefinition } from "../types";
import { usePageTags } from "../usePageTags";

const tags = [getTag("semiconductor")!, getTag("storage")!, getTag("robotics")!];
const customTag: TagDefinition = {
  id: "custom-focus", name: "量子传感", slug: "custom-focus", type: "自定义标签",
  parent_id: null, aliases: [], keywords: ["量子传感"], description: "测试标签",
  sort_order: 0, enabled: true, kind: "custom",
};

describe("SelectedTagBar", () => {
  beforeEach(() => localStorage.clear());

  it("activates, removes and opens the selector with explicit controls", async () => {
    const user = userEvent.setup();
    const onActivate = vi.fn();
    const onRemove = vi.fn();
    const onAdd = vi.fn();
    render(
      <SelectedTagBar tags={tags} activeId="storage" onActivate={onActivate}
        onRemove={onRemove} onReorder={vi.fn()} onMove={vi.fn()} onAdd={onAdd} />,
    );

    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    await user.click(screen.getByRole("button", { name: "删除半导体" }));
    await user.click(screen.getByRole("button", { name: "添加标签" }));

    expect(onActivate).toHaveBeenCalledWith("robotics");
    expect(onRemove).toHaveBeenCalledWith("semiconductor");
    expect(onAdd).toHaveBeenCalledOnce();
  });

  it("requests before-target reordering when a tag is dropped onto another tag", () => {
    const onReorder = vi.fn();
    render(
      <SelectedTagBar tags={tags} activeId="storage" onActivate={vi.fn()}
        onRemove={vi.fn()} onReorder={onReorder} onMove={vi.fn()} onAdd={vi.fn()} />,
    );

    fireEvent.dragStart(screen.getByTestId("selected-tag-robotics"));
    fireEvent.drop(screen.getByTestId("selected-tag-semiconductor"));

    expect(onReorder).toHaveBeenCalledWith("robotics", "semiconductor");
  });

  it("moves the active tag right and left in both DOM and persisted state", async () => {
    const user = userEvent.setup();
    function Harness() {
      const page = usePageTags("industry_research");
      return <SelectedTagBar tags={page.tags} activeId={page.state.activeId}
        onActivate={page.activate} onRemove={page.remove} onReorder={page.reorder}
        onMove={page.move} onAdd={vi.fn()} />;
    }
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "将存储左移" }));
    expect(within(screen.getByLabelText("已选投研标签")).getAllByRole("button", { name: /^切换到/ })
      .map((button) => button.getAttribute("aria-label")))
      .toEqual(["切换到存储", "切换到半导体", "切换到机器人"]);
    expect(loadPageTagState("industry_research").order)
      .toEqual(["storage", "semiconductor", "robotics"]);

    await user.click(screen.getByRole("button", { name: "将存储右移" }));
    expect(within(screen.getByLabelText("已选投研标签")).getAllByRole("button", { name: /^切换到/ })
      .map((button) => button.getAttribute("aria-label")))
      .toEqual(["切换到半导体", "切换到存储", "切换到机器人"]);
    expect(loadPageTagState("industry_research").order)
      .toEqual(["semiconductor", "storage", "robotics"]);
  });

  it("restores focus to the same active tag after its move control becomes disabled", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [current, setCurrent] = useState<TagDefinition[]>([tags[0], customTag, tags[1]]);
      return <SelectedTagBar tags={current} activeId={customTag.id}
        onActivate={vi.fn()} onRemove={vi.fn()} onReorder={vi.fn()}
        onMove={(id, offset) => setCurrent((value) => {
          const source = value.findIndex((tag) => tag.id === id);
          const target = source + offset;
          if (source < 0 || target < 0 || target >= value.length) return value;
          const next = [...value];
          [next[source], next[target]] = [next[target], next[source]];
          return next;
        })} onAdd={vi.fn()} />;
    }
    render(<Harness />);
    const moveLeft = screen.getByRole("button", { name: "将量子传感左移" });
    moveLeft.focus();

    await user.keyboard("{Enter}");

    expect(within(screen.getByLabelText("已选投研标签")).getAllByRole("button", { name: /^切换到/ })
      .map((button) => button.getAttribute("aria-label")))
      .toEqual(["切换到量子传感", "切换到半导体", "切换到存储"]);
    expect(screen.getByRole("button", { name: "切换到量子传感" })).toHaveFocus();
  });

  it.each([
    ["first built-in", [tags[0], customTag, tags[1]], tags[0].id, customTag.name],
    ["first custom", [customTag, tags[0], tags[1]], customTag.id, tags[0].name],
    ["middle built-in", [customTag, tags[0], tags[1]], tags[0].id, tags[1].name],
    ["middle custom", [tags[0], customTag, tags[1]], customTag.id, tags[1].name],
    ["last built-in", [customTag, tags[0], tags[1]], tags[1].id, tags[0].name],
    ["last custom", [tags[0], tags[1], customTag], customTag.id, tags[1].name],
  ] as const)("focuses the adjacent activation control after deleting the %s tag", async (
    _caseName, initialTags, deletedId, expectedFocusName,
  ) => {
    const user = userEvent.setup();
    function Harness() {
      const [current, setCurrent] = useState<TagDefinition[]>([...initialTags]);
      const [activeId, setActiveId] = useState(deletedId);
      return <SelectedTagBar tags={current} activeId={activeId}
        onActivate={setActiveId}
        onRemove={(id) => {
          const index = current.findIndex((tag) => tag.id === id);
          const next = current.filter((tag) => tag.id !== id);
          setCurrent(next);
          if (activeId === id) setActiveId(next[index]?.id || next[index - 1]?.id || "");
        }}
        onReorder={vi.fn()} onMove={vi.fn()} onAdd={vi.fn()} />;
    }
    render(<Harness />);

    await user.click(screen.getByRole("button", {
      name: `删除${initialTags.find((tag) => tag.id === deletedId)!.name}`,
    }));

    expect(screen.getByRole("button", { name: `切换到${expectedFocusName}` })).toHaveFocus();
  });

  it("focuses Add after deleting the only tag", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [current, setCurrent] = useState<TagDefinition[]>([customTag]);
      return <SelectedTagBar tags={current} activeId={customTag.id} onActivate={vi.fn()}
        onRemove={() => setCurrent([])} onReorder={vi.fn()} onMove={vi.fn()} onAdd={vi.fn()} />;
    }
    render(<Harness />);

    await user.click(screen.getByRole("button", { name: "删除量子传感" }));

    expect(screen.getByRole("button", { name: "添加标签" })).toHaveFocus();
  });
});
