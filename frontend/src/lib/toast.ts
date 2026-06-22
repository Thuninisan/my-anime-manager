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
