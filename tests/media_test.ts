// Offline photo downloads: fake Talk reads and a fake obs fetch. No accounts or sockets.
import { Buffer } from "node:buffer";
import { pathToFileURL } from "node:url";
import { downloadImage, talkMeta } from "../src/line_bridge/js/media.ts";
import { readFailure } from "../src/line_bridge/js/runtime-errors.ts";
const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
const { Protocols } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/mod.ts`).href);
const { writeStruct } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/readwrite/write.ts`).href);

function assert(value: unknown) { if (!value) throw new Error("Media fixture assertion failed"); }
const me = "u" + "a".repeat(32), contact = "u" + "b".repeat(32);

function fixture(row: any, status = 200) {
  const calls: any[] = [], fetches: any[] = [];
  const base: any = {
    bridgeExpectedMid: me, authToken: "mock-token", request: { userAgent: "mock-agent", systemType: "MOCK\t1" },
    talk: {
      getProfile: async () => ({ mid: me }),
      getPreviousMessagesV2WithRequest: async (args: any) => { calls.push(["previous", args]); return row ? [row] : []; },
      getRecentMessagesV2: async (args: any) => { calls.push(["recent", args]); return []; },
    },
    e2ee: {
      decryptE2EEDataMessage: async (m: any) => { calls.push(["key", m.id]); return { keyMaterial: "mock-key", fileName: "x.jpg" }; },
      decryptByKeyMaterial: async (data: Buffer, key: string) => Buffer.concat([Buffer.from(key + ":"), data]),
    },
    fetch: async (url: string, init: any) => { fetches.push({ url, headers: init.headers }); return new Response("photo", { status }); },
  };
  return { base, calls, fetches };
}
const sealed = () => ({ id: "101", from: contact, to: me, createdTime: 1000, contentType: "IMAGE",
  chunks: [Buffer.alloc(4)], contentMetadata: { SID: "emi", OID: "mock_OID-1" } });

Deno.test("talk meta matches the dependency's binary codec", () => {
  const struct = Buffer.from(JSON.parse(atob(talkMeta("9007199254740993"))).message, "base64");
  assert(struct.equals(Buffer.from(writeStruct([[11, 4, "9007199254740993"], [15, 27, [12, []]]], Protocols[3]))));
});

Deno.test("a sealed photo is located by its anchor, downloaded by object ID and decrypted", async () => {
  const f = fixture(sealed());
  const result = await downloadImage(f.base, { source: "personal", chat_id: contact, message_id: "101", created_ms: 1000 });
  assert(Buffer.from(result.image, "base64").toString() === "mock-key:photo");
  assert(f.fetches.length === 1 && f.fetches[0].url === "https://obs.line-apps.com/r/talk/emi/mock_OID-1");
  assert(f.fetches[0].headers["X-Talk-Meta"] === talkMeta("101") && f.fetches[0].headers["x-line-access"] === "mock-token");
  assert(f.calls[0][0] === "previous" && f.calls[0][1].request.endMessageId.deliveredTime === 1000);
  assert(!f.calls.some(c => c[0] === "recent"));
});

Deno.test("previews use LINE's thumbnail paths", async () => {
  let f = fixture(sealed());
  await downloadImage(f.base, { source: "personal", chat_id: contact, message_id: "101", created_ms: 1000, preview: true });
  assert(f.fetches[0].url === "https://obs.line-apps.com/r/talk/emi/mock_OID-1__ud-preview");
  f = fixture({ ...sealed(), chunks: [], contentMetadata: {} });
  await downloadImage(f.base, { source: "personal", chat_id: contact, message_id: "101", created_ms: 1000, preview: true });
  assert(f.fetches[0].url === "https://obs.line-apps.com/r/talk/m/101/preview" && !f.calls.some(c => c[0] === "key"));
  f = fixture(null);
  await downloadImage(f.base, { source: "openchat", message_id: "202", preview: true });
  assert(f.fetches[0].url === "https://obs.line-apps.com/r/g2/m/202/preview" && f.calls.length === 0);
});

Deno.test("videos, files and odd object paths are refused before any download", async () => {
  for (const row of [{ ...sealed(), contentType: "VIDEO" }, { ...sealed(), contentType: 14 },
                     { ...sealed(), contentMetadata: { SID: "emv", OID: "mock" } },
                     { ...sealed(), contentMetadata: { SID: "emi", OID: "../other" } }]) {
    const f = fixture(row);
    let failed = false;
    try { await downloadImage(f.base, { source: "personal", chat_id: contact, message_id: "101", created_ms: 1000 }); }
    catch { failed = true; }
    assert(failed && f.fetches.length === 0);
  }
});

Deno.test("a missing message falls back to the latest page, then fails without downloading", async () => {
  const f = fixture(null);
  let error: any;
  try { await downloadImage(f.base, { source: "personal", chat_id: contact, message_id: "101", created_ms: 1000 }); }
  catch (e) { error = e; }
  assert(error?.message === "message_not_in_bounded_history" && f.calls.some(c => c[0] === "recent") && f.fetches.length === 0);
});

Deno.test("an expired photo reports media_unavailable", async () => {
  const f = fixture(null, 404);
  let error: unknown;
  try { await downloadImage(f.base, { source: "openchat", message_id: "202" }); } catch (e) { error = e; }
  assert(readFailure(error) === "media_unavailable");
});
