import { Dialog } from "@base-ui/react/dialog"
import { cn } from "@/lib/utils"

function SheetRoot({ children, ...props }: Dialog.Root.Props) {
  return <Dialog.Root {...props}>{children}</Dialog.Root>
}

function SheetTrigger({ children, ...props }: Dialog.Trigger.Props) {
  return <Dialog.Trigger {...props}>{children}</Dialog.Trigger>
}

function SheetContent({
  children,
  className,
  ...props
}: Dialog.Popup.Props) {
  return (
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/80 transition-opacity duration-150 data-closed:opacity-0 data-open:opacity-100" />
      <Dialog.Popup
        className={cn(
          "fixed top-0 right-0 z-50 flex h-full w-full max-w-md flex-col border-l bg-card shadow-lg transition-transform duration-200 data-closed:translate-x-full data-open:translate-x-0",
          className,
        )}
        {...props}
      >
        {children}
      </Dialog.Popup>
    </Dialog.Portal>
  )
}

function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-center justify-between px-6 py-4 border-b border-border", className)}
      {...props}
    />
  )
}

function SheetTitle({ className, ...props }: Dialog.Title.Props) {
  return (
    <Dialog.Title
      className={cn("text-lg font-semibold", className)}
      {...props}
    />
  )
}

function SheetDescription({ className, ...props }: Dialog.Description.Props) {
  return (
    <Dialog.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

function SheetBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-6 py-4 overflow-y-auto flex-1", className)} {...props} />
}

function SheetFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-center justify-end gap-2 px-6 py-4 border-t border-border", className)}
      {...props}
    />
  )
}

function SheetClose({ children, ...props }: Dialog.Close.Props) {
  return <Dialog.Close {...props}>{children}</Dialog.Close>
}

export {
  SheetRoot,
  SheetTrigger,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetBody,
  SheetFooter,
  SheetClose,
}
