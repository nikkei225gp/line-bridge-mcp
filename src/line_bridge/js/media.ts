// One posted photo per call, returned as Base64. Videos, audio and files are never requested.
import { Buffer } from "node:buffer";
import { RuntimeFailure } from "./runtime-errors.ts";
import { locateMessage, nativeContentType } from "./personal.ts";

function messageId(value: any) {
  if (typeof value !== "string" || !/^\d{1,19}$/.test(value)) throw new Error("invalid_message_id");
  return value;
}

// Thrift binary struct { 4: string id, 27: list<struct> [] } naming the message for E2EE downloads.
export function talkMeta(id: string) {
  const value = Buffer.from(id), length = Buffer.alloc(4);
  length.writeUInt32BE(value.length);
  const struct = Buffer.concat([Buffer.from([11, 0, 4]), length, value, Buffer.from([15, 0, 27, 12, 0, 0, 0, 0, 0])]);
  return Buffer.from(JSON.stringify({ message: struct.toString("base64") })).toString("base64");
}

async function obs(base: any, path: string, headers: Record<string, string> = {}) {
  const response = await base.fetch(`https://obs.line-apps.com${path}`, { headers: { accept: "*/*",
    "user-agent": base.request.userAgent, "x-line-application": base.request.systemType,
    "x-line-access": base.authToken, ...headers } });
  // Expired, unsent or foreign objects answer 403/404/410; the body may hold signed URLs.
  if ([403, 404, 410].includes(response.status)) throw new RuntimeFailure("media_unavailable");
  if (!response.ok) throw new RuntimeFailure("upstream_failed");
  return Buffer.from(await response.arrayBuffer());
}

export async function downloadImage(base: any, args: any) {
  const preview = args.preview === true;
  if (args.source === "openchat") {
    return { image: (await obs(base, `/r/g2/m/${messageId(args.message_id)}${preview ? "/preview" : ""}`)).toString("base64") };
  }
  const m = await locateMessage(base, args.chat_id, messageId(args.message_id), args.created_ms);
  if (nativeContentType(m.contentType) !== "IMAGE") throw new Error("not_an_image");
  if (!m.chunks?.length) return { image: (await obs(base, `/r/talk/m/${m.id}${preview ? "/preview" : ""}`)).toString("base64") };
  const { SID, OID } = m.contentMetadata ?? {};
  if (SID !== "emi" || typeof OID !== "string" || !/^[A-Za-z0-9_-]{1,180}$/.test(OID)) throw new Error("not_an_image");
  const { keyMaterial } = await base.e2ee.decryptE2EEDataMessage(m);
  if (!keyMaterial) throw new Error("decrypt_failed");
  const data = await obs(base, `/r/talk/emi/${OID}${preview ? "__ud-preview" : ""}`, { "X-Talk-Meta": talkMeta(String(m.id)) });
  return { image: (await base.e2ee.decryptByKeyMaterial(data, keyMaterial)).toString("base64") };
}
