import { Popover } from "@base-ui/react/popover"
import { cn } from "@/lib/utils"
import type { ReactNode } from "react"

interface Props {
  trigger: ReactNode;
  children: ReactNode;
  className?: string;
}

export default function HoverCard({ trigger, children, className }: Props) {
  return (
    <Popover.Root>
      <Popover.Trigger className="inline cursor-default" openOnHover>
        {trigger}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} align="center">
          <Popover.Popup
            className={cn(
              "z-50 rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-lg text-xs",
              "origin-(--transform-origin) transition-[transform,opacity] duration-100 data-closed:scale-95 data-closed:opacity-0",
              className,
            )}
          >
            {children}
          </Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  );
}
