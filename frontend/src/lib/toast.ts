import { toast } from "sonner";

/** Show an error toast from a caught error. */
export function showError(err: unknown) {
  const msg =
    err instanceof Error
      ? err.message
      : typeof err === "string"
        ? err
        : "未知错误";
  toast.error(msg.slice(0, 200), {
    duration: 6000,
    position: "bottom-left",
  });
}

/** Show a success toast. */
export function showSuccess(msg: string) {
  toast.success(msg, {
    duration: 3000,
    position: "bottom-left",
  });
}

/** Show a loading toast and return its id for later updates. */
export function showLoadingToast(msg: string): string | number {
  return toast.loading(msg, { position: "bottom-left" });
}

/** Update an existing toast — change message and/or transition to success/error. */
export function updateToast(
  id: string | number,
  msg: string,
  type: "loading" | "success" | "error" = "loading",
) {
  if (type === "success") {
    toast.success(msg, { id, position: "bottom-left", duration: 3000 });
  } else if (type === "error") {
    toast.error(msg, { id, position: "bottom-left", duration: 6000 });
  } else {
    toast.loading(msg, { id, position: "bottom-left" });
  }
}
