// Real fixed dependency and codec; mocked fetch only, without network permission.
import { pathToFileURL } from "node:url";
import { readFailure, runtimeFetch } from "../src/line_bridge/js/runtime-errors.ts";
import { guardedFetch, guardRequests } from "../src/line_bridge/js/desktop-policy.ts";
const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
const { BaseClient } = await import(pathToFileURL(`${root}/packages/linejs/base/mod.ts`).href);
const { MemoryStorage } = await import(pathToFileURL(`${root}/packages/linejs/base/storage/memory.ts`).href);
const { Thrift, Protocols } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/mod.ts`).href);
function assert(value: unknown) { if (!value) throw new Error("Recovery fixture assertion failed"); }
async function failure(action: () => Promise<unknown>) {
  try { await action(); } catch (error) { return readFailure(error); }
  throw new Error("Expected fixture failure");
}
async function withFetch(mock: typeof fetch, action: () => Promise<void>) {
  const old = globalThis.fetch;
  try { globalThis.fetch = mock; await action(); } finally { globalThis.fetch = old; }
}
function request() { return new Request("https://legy.line-apps.com/S4", { method: "POST" }); }

Deno.test("only structured upstream auth enums are classified, never message text", () => {
  for (const code of ["AUTHENTICATION_FAILED", "AUTHENTICATION_FAILURE", "NOT_AUTHORIZED_DEVICE", "MUST_REFRESH_V3_TOKEN"]) {
    assert(readFailure({ type: "RequestError", data: { code }, message: "private detail" }) === "auth_required");
    assert(readFailure({ type: "RequestError", data: { errorCode: code } }) === "auth_required");
    assert(readFailure(new Error(code)) === "upstream_failed");
  }
  for (const value of [null, "private error", { data: { code: "AUTHENTICATION_FAILED" } },
    { type: "RequestError", data: { code: 1 } }, { type: "RequestError", data: { code: "private unknown" } }]) {
    assert(readFailure(value) === "upstream_failed");
  }
});
Deno.test("real Talk exception codec distinguishes revoked and refresh-required credentials", async () => {
  for (const code of [1, 8, 119]) {
    let calls = 0;
    const codec = new Thrift(), storage = new MemoryStorage();
    const base: any = new BaseClient({ device: "DESKTOPWIN", version: "26.5.0.3975", storage,
      legy: { encrypted: false }, fetch: async () => {
        calls++;
        return new Response(new Uint8Array(codec.writeThrift([[12, 1, [[8, 1, code], [11, 2, "private upstream reason"]]]],
          "getProfile", Protocols[4])).buffer);
      } });
    base.authToken = "mock-" + "x".repeat(30);
    guardRequests(base);
    assert(await failure(() => base.talk.getProfile()) === "auth_required");
    assert(calls === 1 && await storage.get("refreshToken") === undefined);
  }
});
Deno.test("real Square exception codec distinguishes authentication failure", async () => {
  const codec = new Thrift(); let calls = 0;
  const base: any = new BaseClient({ device: "DESKTOPWIN", version: "26.5.0.3975", storage: new MemoryStorage(),
    legy: { encrypted: false }, fetch: async () => {
      calls++;
      return new Response(new Uint8Array(codec.writeThrift([[12, 1, [[8, 1, 401], [11, 3, "private upstream reason"]]]],
        "getJoinedSquares", Protocols[4])).buffer);
    } });
  base.authToken = "mock-" + "x".repeat(30);
  assert(await failure(() => base.square.getJoinedSquares({ limit: 1 })) === "auth_required");
  assert(calls === 1 && await base.storage.get("refreshToken") === undefined);
});
Deno.test("HTTP errors have fixed codes and discard private response bodies", async () => {
  for (const [status, code] of [[401, "auth_required"], [429, "rate_limited"], [503, "upstream_unavailable"]] as const) {
    let cancelled = false, calls = 0;
    await withFetch(async () => {
      calls++;
      const stream = new ReadableStream({ cancel() { cancelled = true; } });
      return new Response(stream, { status, headers: { "private-header": "withheld" } });
    }, async () => { assert(await failure(() => runtimeFetch(request())) === code); });
    assert(cancelled && calls === 1);
  }
});
Deno.test("HTTP forbidden is not incorrectly labelled as expired authentication", async () => {
  await withFetch(async () => new Response("private", { status: 403 }), async () => {
    const response = await runtimeFetch(request());
    assert(response.status === 403); await response.body?.cancel();
  });
});
Deno.test("fetch network failure is sanitized and not automatically retried", async () => {
  let calls = 0;
  await withFetch(async () => { calls++; throw new TypeError("private DNS or proxy detail"); }, async () => {
    assert(await failure(() => runtimeFetch(request())) === "network_unavailable");
  });
  assert(calls === 1);
});
Deno.test("fetch timeouts remain distinct from network failures and auth expiry", async () => {
  for (const name of ["TimeoutError", "AbortError"]) {
    await withFetch(async () => { throw new DOMException("private timeout detail", name); }, async () => {
      assert(await failure(() => runtimeFetch(request())) === "request_timeout");
    });
  }
});
Deno.test("next explicit fetch succeeds after a transient outage", async () => {
  let calls = 0;
  await withFetch(async (_, init) => {
    assert(init?.redirect === "error" && init.signal instanceof AbortSignal);
    if (++calls === 1) throw new TypeError("mock offline");
    return new Response("mock recovered");
  }, async () => {
    assert(await failure(() => runtimeFetch(request())) === "network_unavailable");
    assert(calls === 1);
    assert(await (await runtimeFetch(request())).text() === "mock recovered");
  });
  assert(calls === 2);
});
Deno.test("Talk streaming-body failures are sanitized", async () => {
  await withFetch(async () => new Response(new ReadableStream({ start(controller) {
    controller.error(new Error("private streaming response"));
  } })), async () => {
    assert(await failure(() => guardedFetch()(request())) === "network_unavailable");
  });
});
