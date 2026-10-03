# @ws/ui

Shared React UI primitives for the workshop-reservation platform.

## Exports

| Export                          | Description                                                |
| ------------------------------- | ---------------------------------------------------------- |
| `ToastProvider`                 | Toast context provider; renders the toast region           |
| `useToast`                      | `showToast(message, kind?)` accessor                       |
| `useToastError(error, onClear)` | Show an API error exactly once (StrictMode-safe)           |
| `getToastErrorMessage(error)`   | Map `ApiError` codes to user-facing copy                   |
| `ConfirmDialog`                 | Destructive-action confirmation modal (focus trap, Escape) |
| `ErrorBoundary`                 | Render error boundary with a reload fallback               |

## Styling

Components are styled with Tailwind utility classes that
reference the app's brand theme tokens (`bg-paper`,
`text-ink`, `bg-coral`, `bg-soft`, `border-line`, …).
Consumers must:

1. Define those tokens in a Tailwind v4 `@theme` block
   (see `frontend/src/index.css` for the reference
   palette).
2. Register this package's source so its utility
   classes are generated:

   ```css
   @import "tailwindcss";
   @source "../packages/ui/src";
   ```

`ErrorBoundary` also relies on the `.paper-grid` and
`.display-font` helper classes from the app stylesheet.

## Error reporting

`ErrorBoundary` accepts an optional `onError(error,
errorInfo)` prop. The app passes its structured logger;
the default is `console.error`.

## Dependencies

- `@ws/api-client` — `Toast` branches on `ApiError`
  codes.
- `react` (peer).
