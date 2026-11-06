// Only fixed codes cross stdio. Never inspect or export upstream messages/headers.
export type ReadFailure = "auth_required" | "network_unavailable" | "request_timeout" |
  "upstream_unavailable" | "rate_limited" | "upstream_failed" | "media_unavailable" | "media_too_large";

export class RuntimeFailure extends Error {
  constructor(readonly code: ReadFailure) { super(code); }
}

const authCodes = new Set([
  "AUTHENTICATION_FAILED", "AUTHENTICATION_FAILURE", "NOT_AUTHORIZED_DEVICE", "MUST_REFRESH_V3_TOKEN",
]);

export function readFailure(error: unknown): ReadFailure {
  if (error instanceof RuntimeFailure) return error.code;
  // Fixed linejs maps TalkException.code and SquareException.errorCode to enums.
  if (error && typeof error === "object" && "type" in error && error.type === "RequestError" && "data" in error) {
    const data = error.data;
    if (data && typeof data === "object" &&
      (("code" in data && typeof data.code === "string" && authCodes.has(data.code)) ||
       ("errorCode" in data && typeof data.errorCode === "string" && authCodes.has(data.errorCode)))) return "auth_required";
  }
  return "upstream_failed";
}

export async function runtimeFetch(request: Request, timeout = 20000): Promise<Response> {
  let response: Response;
  try {
    response = await fetch(request, { redirect: "error", signal: AbortSignal.timeout(timeout) });
  } catch (error) {
    throw new RuntimeFailure(error instanceof Error && ["AbortError", "TimeoutError"].includes(error.name)
      ? "request_timeout" : "network_unavailable");
  }
  const code = response.status === 401 ? "auth_required" : response.status === 429 ? "rate_limited" :
    response.status >= 500 && response.status <= 599 ? "upstream_unavailable" : null;
  if (code) {
    try { await response.body?.cancel(); } catch { /* No upstream details. */ }
    throw new RuntimeFailure(code);
  }
  return response;
}
