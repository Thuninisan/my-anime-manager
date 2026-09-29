import { Dialog } from "@base-ui/react/dialog"
import { cn } from "@/lib/utils"
function DialogRoot({ children, ...props }: Dialog.Root.Props) {
  return <Dialog.Root {...props}>{children}</Dialog.Root>
}

function DialogTrigger({ children, ...props }: Dialog.Trigger.Props) {
  return <Dialog.Trigger {...props}>{children}</Dialog.Trigger>
}

function DialogContent({
  children,
  className,
  ...props
}: Dialog.Popup.Props) {
  return (
    <Dialog.Portal>
      <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/80 transition-opacity duration-150 data-closed:opacity-0 data-open:opacity-100" />
      <Dialog.Popup
        className={cn(
          "fixed left-[50%] top-[50%] z-50 w-full max-w-2xl translate-x-[-50%] translate-y-[-50%] rounded-lg border bg-card shadow-lg transition-[transform,opacity] duration-150 data-closed:scale-95 data-closed:opacity-0 data-open:scale-100 data-open:opacity-100",
          className,
        )}
        {...props}
      >
        {children}
      </Dialog.Popup>
    </Dialog.Portal>
  )
}

function DialogHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-center justify-between px-6 py-4 border-b border-border", className)}
      {...props}
    />
  )
}

function DialogTitle({ className, ...props }: Dialog.Title.Props) {
  return (
    <Dialog.Title
      className={cn("text-lg font-semibold", className)}
      {...props}
    />
  )
}

function DialogDescription({ className, ...props }: Dialog.Description.Props) {
  return (
    <Dialog.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

function DialogBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-6 py-4", className)} {...props} />
}

function DialogFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("flex items-center justify-end gap-2 px-6 py-4 border-t border-border", className)}
      {...props}
    />
  )
}

function DialogClose({ children, ...props }: Dialog.Close.Props) {
  return <Dialog.Close {...props}>{children}</Dialog.Close>
}

export {
  DialogRoot,
  DialogTrigger,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogBody,
  DialogFooter,
  DialogClose,
}
