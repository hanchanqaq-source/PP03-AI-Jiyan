import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { getTag } from "../catalog";
import { SelectedTagBar } from "../SelectedTagBar";

const tags = [getTag("semiconductor")!, getTag("storage")!, getTag("robotics")!];

describe("SelectedTagBar", () => {
  it("activates, removes and opens the selector with explicit controls", async () => {
    const user = userEvent.setup();
    const onActivate = vi.fn();
    const onRemove = vi.fn();
    const onAdd = vi.fn();
    render(
      <SelectedTagBar tags={tags} activeId="storage" onActivate={onActivate}
        onRemove={onRemove} onReorder={vi.fn()} onAdd={onAdd} />,
    );

    expect(screen.getByRole("button", { name: "切换到存储" })).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: "切换到机器人" }));
    await user.click(screen.getByRole("button", { name: "删除半导体" }));
    await user.click(screen.getByRole("button", { name: "添加标签" }));

    expect(onActivate).toHaveBeenCalledWith("robotics");
    expect(onRemove).toHaveBeenCalledWith("semiconductor");
    expect(onAdd).toHaveBeenCalledOnce();
  });

  it("requests reordering when a tag is dropped onto another tag", () => {
    const onReorder = vi.fn();
    render(
      <SelectedTagBar tags={tags} activeId="storage" onActivate={vi.fn()}
        onRemove={vi.fn()} onReorder={onReorder} onAdd={vi.fn()} />,
    );

    fireEvent.dragStart(screen.getByTestId("selected-tag-robotics"));
    fireEvent.drop(screen.getByTestId("selected-tag-semiconductor"));

    expect(onReorder).toHaveBeenCalledWith("robotics", "semiconductor");
  });

  it("exposes keyboard controls to move the selected tag left and right", async () => {
    const user = userEvent.setup();
    const onReorder = vi.fn();
    render(
      <SelectedTagBar tags={tags} activeId="storage" onActivate={vi.fn()}
        onRemove={vi.fn()} onReorder={onReorder} onAdd={vi.fn()} />,
    );

    await user.click(screen.getByRole("button", { name: "将存储左移" }));
    await user.click(screen.getByRole("button", { name: "将存储右移" }));

    expect(onReorder).toHaveBeenNthCalledWith(1, "storage", "semiconductor");
    expect(onReorder).toHaveBeenNthCalledWith(2, "storage", "robotics");
  });
});
