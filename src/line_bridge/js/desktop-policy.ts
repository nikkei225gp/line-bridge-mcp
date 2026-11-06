// Shared credentials do not imply shared capabilities. Talk sends are scoped once.
import { runtimeFetch, RuntimeFailure } from "./runtime-errors.ts";
const sendPermits = new WeakMap<object, { available: boolean }>();
function consumeSendPermit(base: any) {
  const permit = sendPermits.get(base);
  if (!permit?.available) return false;
  permit.available = false;
  return true;
}
export const personalMethods = new Set([
  "getProfile", "getMessageBoxes", "getRecentMessagesV2", "getPreviousMessagesV2WithRequest",
  "getAllContactIds", "getContacts", "getAllChatMids", "getChats",
  "getE2EEPublicKeys", "negotiateE2EEPublicKey", "getE2EEGroupSharedKey", "getLastE2EEGroupSharedKey",
]);
export const IMAGE_MAX = 6 * 1024 * 1024;
// Plain Talk and OpenChat photos live under the message ID; Letter Sealing photos under their object ID.
export const obsImagePath = /^\/r\/(?:(?:talk|g2)\/m\/\d{1,19}(?:\/preview)?|talk\/emi\/[A-Za-z0-9_-]{1,200})$/;
export const loginMethods = new Set([
  "createSession", "createQrCodeForSecure", "createQrCode", "checkQrCodeVerified", "verifyCertificate",
  "createPinCode", "checkPinCodeVerified", "qrCodeLoginV2ForSecure", "qrCodeLogin",
]);

export function guardRequests(base: any, login = false) {
  const request = base.request.request.bind(base.request);
  base.request.request = (value: any, method: string, protocol: any, parse: any, path: string, ...rest: any[]) => {
    const permitted = login
      ? (loginMethods.has(method) && ["/acct/lgn/sq/v1", "/acct/lp/lgn/sq/v1"].includes(path)) ||
        (["getProfile", "getE2EEPublicKeys"].includes(method) && path === "/S4") ||
        (method === "getJoinedSquares" && path === "/SQ1")
      : path === "/S4" && (personalMethods.has(method) || ["sendMessage", "unsendMessage"].includes(method) && consumeSendPermit(base));
    if (!permitted) throw new Error("method_refused");
    return request(value, method, protocol, parse, path, ...rest);
  };
}

export async function withPersonalSend(base: any, action: () => Promise<any>) {
  if (sendPermits.has(base)) throw new Error("send_already_in_progress");
  sendPermits.set(base, { available: true });
  try { return await action(); }
  finally { sendPermits.delete(base); }
}

export function guardedFetch(login = false) {
  let lastRequest = 0;
  return async (input: any, init?: any) => {
    const req = new Request(input, init), url = new URL(req.url);
    const rpc = req.method === "POST" && (url.hostname === "gf.line.naver.jp" && url.pathname === "/enc" ||
      url.hostname === "legy.line-apps.com" &&
      (url.pathname === "/S4" || login && ["/SQ1", "/acct/lgn/sq/v1", "/acct/lp/lgn/sq/v1"].includes(url.pathname)));
    const image = !login && req.method === "GET" && url.hostname === "obs.line-apps.com" && obsImagePath.test(url.pathname);
    if (url.protocol !== "https:" || url.port && url.port !== "443" || url.username || url.password ||
        url.search || url.hash || !(rpc || image)) throw new Error("route_refused");
    const delay = Math.max(0, lastRequest + 100 - Date.now());
    if (delay) await new Promise(resolve => setTimeout(resolve, delay));
    lastRequest = Date.now();
    const response = await runtimeFetch(req, login ? 95000 : 20000);
    // Letter Sealing adds a 32-byte HMAC to the photo.
    const maximum = image ? IMAGE_MAX + 32 : 4 * 1024 * 1024, tooLarge = image ? "media_too_large" : "upstream_failed";
    if (Number(response.headers.get("content-length")) > maximum) {
      await response.body?.cancel(); throw new RuntimeFailure(tooLarge);
    }
    const reader = response.body?.getReader(), chunks: ArrayBuffer[] = [];
    let size = 0;
    try {
      if (reader) while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.length;
        if (size > maximum) throw new RuntimeFailure(tooLarge);
        chunks.push(new Uint8Array(value).buffer);
      }
    } catch (error) {
      if (error instanceof RuntimeFailure) throw error;
      throw new RuntimeFailure("network_unavailable");
    } finally { try { await reader?.cancel(); } catch { /* No upstream details. */ } }
    return new Response(new Blob(chunks), { status: response.status, headers: response.headers });
  };
}
