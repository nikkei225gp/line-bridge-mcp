import { deviceProfile, sessionProfile } from "../src/line_bridge/js/devices.ts";
import { guardedFetch } from "../src/line_bridge/js/desktop-policy.ts";
import { pathToFileURL } from "node:url";
import { Buffer } from "node:buffer";
const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
const { BaseClient } = await import(pathToFileURL(`${root}/packages/linejs/base/mod.ts`).href);
const { MemoryStorage } = await import(pathToFileURL(`${root}/packages/linejs/base/storage/memory.ts`).href);
function check(ok: unknown) { if (!ok) throw new Error("device_fixture_assertion"); }

Deno.test("old desktop session retains its verified transport; mobile sessions use v2 identity", () => {
  check(sessionProfile({ schema: "desktop-shared-v1", device: "DESKTOPWIN", version: "26.5.0.3975" }).legacy);
  for (const device of ["ANDROIDSECONDARY", "IOSIPAD", "DESKTOPWIN", "DESKTOPMAC"]) {
    check(!sessionProfile({ schema: "secondary-shared-v2", ...deviceProfile(device) }).legacy);
  }
  for (const device of ["ANDROID", "IOS", "CHROMEOS"]) {
    let refused = false;
    try { deviceProfile(device); } catch { refused = true; }
    check(refused);
  }
});

Deno.test("real fixed Android V3 transport selects encrypted Talk and direct Square routes", async () => {
  const routes: string[] = [];
  const base: any = new BaseClient({ ...deviceProfile("ANDROIDSECONDARY"), storage: new MemoryStorage(),
    fetch: (request: Request) => { routes.push(new URL(request.url).origin + new URL(request.url).pathname); throw new Error("fixture_stop"); } });
  base.authToken = [Buffer.from(JSON.stringify({ alg: "HS256" })).toString("base64url"),
    Buffer.from(JSON.stringify({ ver: 3, exp: 1 })).toString("base64url"), "synthetic"].join(".");
  try { await base.talk.getProfile(); } catch { /* capture only */ }
  try { await base.square.getJoinedSquares({ limit: 1 }); } catch { /* capture only */ }
  check(routes.join() === "https://gf.line.naver.jp/enc,https://legy.line-apps.com/SQ1");
  check(await base.storage.get("refreshToken") === undefined);
});

Deno.test("outer encrypted gateway is allowed while arbitrary paths remain rejected", async () => {
  const original = globalThis.fetch; let calls = 0;
  try {
    globalThis.fetch = async () => { calls++; return new Response(new Uint8Array()); };
    await guardedFetch()(new Request("https://gf.line.naver.jp/enc", { method: "POST" }));
    for (const url of ["https://gf.line.naver.jp/S4", "https://gf.line.naver.jp/enc?x=1", "http://gf.line.naver.jp/enc"]) {
      let refused = false;
      try { await guardedFetch()(new Request(url, { method: "POST" })); } catch { refused = true; }
      check(refused);
    }
    check(calls === 1);
  } finally { globalThis.fetch = original; }
});
