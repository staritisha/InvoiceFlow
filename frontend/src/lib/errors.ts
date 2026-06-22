/**
 * errors.ts — Centralized API error parsing utility
 *
 * FastAPI can return errors in two shapes:
 *
 *   Simple string  (400, 401, 403, 404, 409, 500):
 *     { "detail": "Email already registered" }
 *
 *   Pydantic validation array  (422 Unprocessable Entity):
 *     { "detail": [{ "loc": ["body","password"], "msg": "...", "type": "..." }] }
 *
 * The raw `data.detail` in the array case is an object[], so coercing it with
 * `new Error(data.detail)` produces "[object Object]" — the bug this fixes.
 *
 * Usage:
 *   throw new ApiError(status, data);
 *   // caller: err.message        → clean human-readable string (always)
 *   // caller: err.status         → HTTP status code
 *   // caller: err.fields         → per-field messages for inline form validation
 *   // caller: err.isTimeout      → true when the request was aborted by timeout
 */

// ── Types ─────────────────────────────────────────────────────────────────────

/** A single Pydantic v2 validation error entry. */
interface PydanticDetail {
  loc: (string | number)[];
  msg: string;
  type: string;
  url?: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}

/**
 * Per-field validation messages extracted from a 422 response.
 * Key is the field name (last segment of `loc`), value is the human message.
 * e.g. { password: "Password must contain at least one uppercase letter (A-Z)" }
 */
export type FieldErrors = Record<string, string>;

// ── Helpers ───────────────────────────────────────────────────────────────────

/**
 * Strip the "Value error, " prefix that Pydantic v2 prepends to custom
 * @field_validator messages before surfacing them.
 */
function cleanMsg(raw: string): string {
  return raw.replace(/^value\s+error,\s*/i, "").trim();
}

/**
 * Derive the field name from a Pydantic `loc` array.
 * ["body", "password"] → "password"
 * ["body", "items", 0, "unit_price"] → "unit_price"
 * ["body"] → "body"
 */
function locToField(loc: (string | number)[]): string {
  const relevant = loc.filter((s) => s !== "body" && s !== "query" && s !== "path");
  if (relevant.length === 0) return String(loc[loc.length - 1] ?? "field");
  // Return last meaningful segment; numeric indices are skipped for display
  const named = relevant.filter((s) => typeof s === "string");
  return String(named[named.length - 1] ?? relevant[relevant.length - 1]);
}

/**
 * Produce a concise, human-readable label for a field name.
 * "full_name" → "Full name"
 */
function fieldLabel(field: string): string {
  return field
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

// ── HTTP status fallback messages ─────────────────────────────────────────────

const STATUS_MESSAGES: Record<number, string> = {
  400: "Bad request — please check your input.",
  401: "Your session has expired. Please sign in again.",
  403: "You don't have permission to do that.",
  404: "The requested resource was not found.",
  409: "A conflict occurred — this resource may already exist.",
  422: "Some fields contain invalid values.",
  429: "Too many requests — please slow down and try again.",
  500: "Server error. Please try again in a moment.",
  502: "Service is temporarily unavailable.",
  503: "Service is temporarily unavailable.",
};

// Special sentinel status used internally to mark AbortController timeouts.
// Not a real HTTP code — keeps timeout errors distinct from network errors.
export const TIMEOUT_STATUS = -1;

/** Message shown to the user whenever a request is aborted due to timeout. */
export const TIMEOUT_MESSAGE =
  "Server is waking up. Please try again in a few seconds.";

// ── Core parser ───────────────────────────────────────────────────────────────

/**
 * Parse any value that FastAPI might put in `data.detail` and return a
 * { message, fields } pair.
 *
 * @param detail  The raw `data.detail` value (string | object[] | unknown)
 * @param status  The HTTP status code, used for fallback messages
 */
export function parseDetail(
  detail: unknown,
  status: number
): { message: string; fields: FieldErrors } {
  // ── 422 array of Pydantic validation errors ────────────────────────────────
  if (Array.isArray(detail) && detail.length > 0) {
    const fields: FieldErrors = {};
    const messages: string[] = [];

    for (const entry of detail as PydanticDetail[]) {
      if (!entry || typeof entry !== "object") continue;

      const field = locToField(entry.loc ?? []);
      const msg = cleanMsg(entry.msg ?? "Invalid value");

      // Collect per-field errors for inline form highlighting
      if (!(field in fields)) {
        fields[field] = msg;
      }

      // Build the summary list, prefixed with the field label for clarity
      const label = fieldLabel(field);
      const fullMsg = label !== "Body" ? `${label}: ${msg}` : msg;
      if (!messages.includes(fullMsg)) {
        messages.push(fullMsg);
      }
    }

    // If only one field has an error, skip the label prefix for a cleaner UX
    const message =
      messages.length === 1
        ? cleanMsg((detail[0] as PydanticDetail).msg ?? messages[0])
        : messages.join(" · ");

    return { message: message || STATUS_MESSAGES[422], fields };
  }

  // ── Simple string detail ───────────────────────────────────────────────────
  if (typeof detail === "string" && detail.trim()) {
    return { message: detail.trim(), fields: {} };
  }

  // ── Object detail (non-array — uncommon but possible) ─────────────────────
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const asRecord = detail as Record<string, unknown>;
    const msg =
      (asRecord["message"] as string) ||
      (asRecord["msg"] as string) ||
      (asRecord["detail"] as string) ||
      JSON.stringify(detail);
    return { message: msg, fields: {} };
  }

  // ── Fallback: use HTTP status ──────────────────────────────────────────────
  return {
    message: STATUS_MESSAGES[status] ?? "An unexpected error occurred.",
    fields: {},
  };
}

// ── ApiError class ────────────────────────────────────────────────────────────

/**
 * Structured error thrown by the `request()` function in api.ts.
 *
 * Always has a clean `.message` string — safe to display directly to users.
 * Also carries `.status` and `.fields` for advanced handling.
 *
 * @example
 * try {
 *   await auth.register(...)
 * } catch (err) {
 *   if (err instanceof ApiError) {
 *     setError(err.message);             // "Password: must contain at least one digit (0-9)"
 *     setFieldErrors(err.fields);        // { password: "must contain at least one digit (0-9)" }
 *   }
 * }
 */
export class ApiError extends Error {
  /** HTTP status code (0 = network failure, -1 = timeout, no response received) */
  readonly status: number;

  /**
   * Per-field validation messages (populated for 422 errors only).
   * Keyed by field name, values are ready to display next to form inputs.
   */
  readonly fields: FieldErrors;

  /** True when this is a Pydantic 422 validation failure */
  get isValidationError(): boolean {
    return this.status === 422;
  }

  /** True when this is a network-level failure (no HTTP response received) */
  get isNetworkError(): boolean {
    return this.status === 0;
  }

  /** True when the request was aborted because it exceeded the configured timeout */
  get isTimeout(): boolean {
    return this.status === TIMEOUT_STATUS;
  }

  constructor(status: number, data: unknown) {
    const rawDetail =
      data && typeof data === "object" ? (data as Record<string, unknown>)["detail"] : data;

    const { message, fields } = parseDetail(rawDetail, status);

    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fields = fields;
  }
}

/**
 * Wrap a caught unknown value so call sites always get a clean message string.
 *
 * Works for ApiError, plain Error, and anything else:
 *   const msg = toErrorMessage(err);   // always a string
 */
export function toErrorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  if (typeof err === "string") return err;
  return "An unexpected error occurred.";
}
