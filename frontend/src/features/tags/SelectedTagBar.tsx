import { useLayoutEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, GripVertical, Plus, X } from "lucide-react";
import { cn } from "@/lib/utils";
import type { PageTagState, TagDefinition } from "./types";

interface SelectedTagBarProps {
  tags: TagDefinition[];
  activeId: string;
  onActivate: (id: string) => void;
  onRemove: (id: string) => boolean | PageTagState | void;
  onReorder: (sourceId: string, targetId: string) => void;
  onMove: (id: string, offset: -1 | 1) => void;
  onAdd: () => void;
}

export function SelectedTagBar({ tags, activeId, onActivate, onRemove, onReorder, onMove, onAdd }: SelectedTagBarProps) {
  const dragged = useRef<string | null>(null);
  const activationRefs = useRef(new Map<string, HTMLButtonElement>());
  const addRef = useRef<HTMLButtonElement | null>(null);
  const pendingDeleteFocus = useRef<{
    deletedId: string;
    nextId?: string;
    previousId?: string;
  } | null>(null);
  const pendingMoveFocus = useRef<{
    id: string;
    beforeOrder: string;
  } | null>(null);

  useLayoutEffect(() => {
    const pending = pendingDeleteFocus.current;
    if (!pending || tags.some((tag) => tag.id === pending.deletedId)) return;
    pendingDeleteFocus.current = null;
    const target = (pending.nextId && activationRefs.current.get(pending.nextId))
      || (pending.previousId && activationRefs.current.get(pending.previousId))
      || activationRefs.current.get(activeId)
      || addRef.current;
    target?.focus();
  }, [activeId, tags]);

  useLayoutEffect(() => {
    const pending = pendingMoveFocus.current;
    const currentOrder = tags.map((tag) => tag.id).join("\0");
    if (!pending || currentOrder === pending.beforeOrder) return;
    pendingMoveFocus.current = null;
    activationRefs.current.get(pending.id)?.focus();
  }, [tags]);

  const moveAndRestoreFocus = (id: string, offset: -1 | 1) => {
    const pending = { id, beforeOrder: tags.map((tag) => tag.id).join("\0") };
    pendingMoveFocus.current = pending;
    try {
      onMove(id, offset);
      requestAnimationFrame(() => {
        if (pendingMoveFocus.current === pending) pendingMoveFocus.current = null;
      });
    } catch (error) {
      pendingMoveFocus.current = null;
      throw error;
    }
  };

  return (
    <div aria-label="已选投研标签" className="flex min-w-0 items-center gap-2 border-y border-border/50 bg-background/80 py-2 backdrop-blur">
      <div className="flex min-w-0 flex-1 gap-2 overflow-x-auto whitespace-nowrap pb-1">
        {tags.map((tag, index) => (
          <div key={tag.id} data-testid={`selected-tag-${tag.id}`} draggable
            onDragStart={() => { dragged.current = tag.id; }}
            onDragOver={(event) => event.preventDefault()}
            onDrop={() => {
              if (dragged.current) onReorder(dragged.current, tag.id);
              dragged.current = null;
            }}
            className={cn(
              "inline-flex shrink-0 items-center rounded-full border transition-colors motion-reduce:transition-none",
              activeId === tag.id
                ? "border-primary bg-primary/15 text-primary shadow-glow"
                : "border-border bg-muted/20 text-muted-foreground hover:border-primary/50 hover:text-foreground",
            )}>
            <GripVertical className="ml-2 h-3 w-3 cursor-grab opacity-40" aria-hidden="true" />
            <button ref={(node) => {
              if (node) activationRefs.current.set(tag.id, node);
              else activationRefs.current.delete(tag.id);
            }} onClick={() => onActivate(tag.id)} aria-label={`切换到${tag.name}`} aria-pressed={activeId === tag.id}
              className="min-h-11 px-2 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              {tag.name}
            </button>
            {activeId === tag.id && <>
              <button onClick={() => moveAndRestoreFocus(tag.id, -1)} disabled={index === 0}
                aria-label={`将${tag.name}左移`}
                className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-full opacity-70 hover:bg-black/10 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-25">
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button onClick={() => moveAndRestoreFocus(tag.id, 1)} disabled={index === tags.length - 1}
                aria-label={`将${tag.name}右移`}
                className="inline-flex min-h-11 min-w-11 items-center justify-center rounded-full opacity-70 hover:bg-black/10 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-not-allowed disabled:opacity-25">
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </>}
            <button onClick={() => {
              pendingDeleteFocus.current = {
                deletedId: tag.id,
                nextId: tags[index + 1]?.id,
                previousId: tags[index - 1]?.id,
              };
              try {
                if (onRemove(tag.id) === false) pendingDeleteFocus.current = null;
              } catch (error) {
                pendingDeleteFocus.current = null;
                throw error;
              }
            }} aria-label={`删除${tag.name}`}
              className="mr-1 inline-flex min-h-11 min-w-11 items-center justify-center rounded-full p-1 opacity-60 hover:bg-black/10 hover:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
              <X className="h-3 w-3" />
            </button>
          </div>
        ))}
      </div>
      <button ref={addRef} onClick={onAdd} aria-label="添加标签"
        className="mr-1 inline-flex min-h-11 shrink-0 items-center gap-1 rounded-full border border-dashed border-primary/50 px-3 py-1.5 text-sm text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary">
        <Plus className="h-3.5 w-3.5" /> 添加标签
      </button>
    </div>
  );
}
