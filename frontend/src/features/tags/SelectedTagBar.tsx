import { useRef } from "react";
import { GripVertical, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { TagDefinition } from "./types";

interface SelectedTagBarProps {
  tags: TagDefinition[];
  activeId: string;
  onActivate: (id: string) => void;
  onRemove: (id: string) => void;
  onReorder: (sourceId: string, targetId: string) => void;
  onAdd: () => void;
}

export function SelectedTagBar({ tags, activeId, onActivate, onRemove, onReorder, onAdd }: SelectedTagBarProps) {
  const dragged = useRef<string | null>(null);

  return (
    <div aria-label="已选投研标签" className="flex min-w-0 items-center gap-2 border-y border-border/50 bg-background/80 py-2 backdrop-blur">
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto whitespace-nowrap pb-1">
        {tags.map((tag) => (
          <div key={tag.id} data-testid={`selected-tag-${tag.id}`} draggable
            onDragStart={() => { dragged.current = tag.id; }}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => {
              if (dragged.current) onReorder(dragged.current, tag.id);
              dragged.current = null;
            }}
            className={cn(
              "inline-flex shrink-0 items-center rounded-full border transition-colors",
              activeId === tag.id
                ? "border-primary bg-primary/15 text-primary shadow-glow"
                : "border-border bg-muted/20 text-muted-foreground hover:border-primary/50 hover:text-foreground",
            )}>
            <GripVertical className="ml-2 h-3 w-3 cursor-grab opacity-40" aria-hidden="true" />
            <button onClick={() => onActivate(tag.id)} aria-label={`切换到${tag.name}`} aria-pressed={activeId === tag.id}
              className="px-2 py-1.5 text-sm font-medium">
              {tag.name}
            </button>
            <button onClick={() => onRemove(tag.id)} aria-label={`删除${tag.name}`}
              className="mr-1 rounded-full p-1 opacity-60 hover:bg-black/10 hover:opacity-100">
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}
      </div>
      <button onClick={onAdd} aria-label="添加标签"
        className="mr-1 inline-flex shrink-0 items-center gap-1 rounded-full border border-dashed border-primary/50 px-3 py-1.5 text-sm text-primary hover:bg-primary/10">
        <Plus className="h-3.5 w-3.5" /> 添加标签
      </button>
    </div>
  );
}
