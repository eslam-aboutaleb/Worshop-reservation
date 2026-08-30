type LogLevel = "debug" | "info" | "warn" | "error";

interface LogEvent {
  level: LogLevel;
  message: string;
  context?: Record<string, unknown>;
  timestamp: string;
}

function serializeError(err: unknown): unknown {
  if (err instanceof Error) {
    return { name: err.name, message: err.message, stack: err.stack };
  }
  return err;
}

function log(level: LogLevel, message: string, context?: Record<string, unknown>) {
  const event: LogEvent = {
    level,
    message,
    context: context ? { ...context } : undefined,
    timestamp: new Date().toISOString(),
  };

  if (event.context?.error) {
    event.context.error = serializeError(event.context.error);
  }

  if (import.meta.env.DEV) {
    console[level](message, event.context || "");
  } else {
    console[level](JSON.stringify(event));
  }
}

export const logger = {
  debug: (message: string, context?: Record<string, unknown>) => log("debug", message, context),
  info: (message: string, context?: Record<string, unknown>) => log("info", message, context),
  warn: (message: string, context?: Record<string, unknown>) => log("warn", message, context),
  error: (message: string, context?: Record<string, unknown>) => log("error", message, context),
};
