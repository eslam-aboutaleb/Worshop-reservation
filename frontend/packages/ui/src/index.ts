/**
 * Shared React UI primitives for the
 * workshop-reservation platform.
 *
 * - `ToastProvider` / `useToast` / `useToastError` /
 *   `getToastErrorMessage` — the global toast layer
 * - `ConfirmDialog` — destructive-action confirmation
 *   modal
 * - `ErrorBoundary` — top-level render error boundary
 *
 * The components are styled with the app's Tailwind
 * theme tokens (`bg-paper`, `text-ink`, `bg-coral`,
 * …). Consumers must define those tokens (a `@theme`
 * block) and register this package's source with
 * Tailwind's `@source` directive so the utility
 * classes are generated:
 *
 * ```css
 * @import "tailwindcss";
 * @source "../packages/ui/src";
 * ```
 */
export {
  ConfirmDialog,
  type Props as ConfirmDialogProps,
} from "./ConfirmDialog";
export { ErrorBoundary } from "./ErrorBoundary";
export {
  ToastProvider,
  useToast,
  useToastError,
  getToastErrorMessage,
} from "./Toast";
