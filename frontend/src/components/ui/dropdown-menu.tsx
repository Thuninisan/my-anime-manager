import { Menu } from "@base-ui/react/menu"
import { cn } from "@/lib/utils"
import type { ComponentProps } from "react"

function DropdownMenuRoot({ children, ...props }: Menu.Root.Props) {
  return <Menu.Root {...props}>{children}</Menu.Root>
}

function DropdownMenuTrigger({
  children,
  className,
  ...props
}: Menu.Trigger.Props) {
  return (
    <Menu.Trigger
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-lg border border-border bg-background px-2.5 py-1 text-[0.8rem] font-medium whitespace-nowrap transition-all outline-none select-none hover:bg-muted hover:text-foreground focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-muted aria-expanded:text-foreground disabled:pointer-events-none disabled:opacity-50 cursor-pointer",
        className,
      )}
      {...props}
    >
      {children}
    </Menu.Trigger>
  )
}

function DropdownMenuContent({
  children,
  className,
  align = "start",
  sideOffset = 4,
  ...props
}: Menu.Popup.Props & { align?: "start" | "center" | "end"; sideOffset?: number }) {
  return (
    <Menu.Portal>
      <Menu.Positioner
        sideOffset={sideOffset}
        align={align}
        className="z-50 outline-none"
      >
        <Menu.Popup
          className={cn(
            "min-w-[8rem] overflow-hidden rounded-lg border bg-popover p-1 text-popover-foreground shadow-md outline-none",
            "origin-(--transform-origin) transition-[transform,opacity] duration-150 data-closed:scale-95 data-closed:opacity-0 data-open:scale-100 data-open:opacity-100",
            className,
          )}
          {...props}
        >
          {children}
        </Menu.Popup>
      </Menu.Positioner>
    </Menu.Portal>
  )
}

function DropdownMenuItem({
  children,
  className,
  ...props
}: Menu.Item.Props) {
  return (
    <Menu.Item
      className={cn(
        "relative flex cursor-pointer select-none items-center gap-2 rounded-md px-2 py-1.5 text-sm outline-none transition-colors hover:bg-muted hover:text-foreground focus-visible:bg-muted data-disabled:pointer-events-none data-disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4",
        className,
      )}
      {...props}
    >
      {children}
    </Menu.Item>
  )
}

function DropdownMenuSeparator({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      className={cn("-mx-1 my-1 h-px bg-border", className)}
      {...props}
    />
  )
}

export {
  DropdownMenuRoot,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
}
