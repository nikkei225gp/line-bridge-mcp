// One QR issues one secondary desktop credential for both Talk and Square.
// No upstream login helper: those helpers may register replacement E2EE keys.
import { Buffer } from "node:buffer";
import { pathToFileURL } from "node:url";
import { guardedFetch, guardRequests } from "./desktop-policy.ts";
import { deviceProfile } from "./devices.ts";

export async function loginUnified(base: any, frame: (kind: string, value: any) => void) {
  const identity = deviceProfile(base.device);
  const ipad = identity.device === "IOSIPAD";
  const { 1: session } = await base.loginProcess.createSession();
  const code = ipad ? await base.loginProcess.createQrCode(session) : await base.loginProcess.createQrCodeForSecure(session);
  if (typeof code[1] !== "string") throw new Error("missing_qr");
  const [secret, secretUrl] = base.e2ee.createSqrSecret();
  frame("qr", code[1] + (code[1].includes("?") ? "&" + secretUrl.slice(1) : secretUrl));
  const count = Math.max(1, Math.min(Number(code[2] ?? 12), 12));
  const interval = Math.max(1, Math.min(Number(code[3] ?? 30), 30));
  if (!await base.loginProcess.checkQrCodeVerified(session, count, interval)) throw new Error("not_verified");
  try { await base.loginProcess.verifyCertificate(session); }
  catch {
    const pin = await base.loginProcess.createPinCode(session);
    frame("pin", pin[1]);
    if (!await base.loginProcess.checkPinCodeVerified(session, count, interval)) throw new Error("not_verified");
  }
  const system = identity.device === "ANDROIDSECONDARY" ? "Android OS" : identity.device === "DESKTOPMAC" ? "MAC" : "Windows";
  const result = ipad ? await base.loginProcess.qrCodeLogin(session, false) :
    await base.loginProcess.qrCodeLoginV2ForSecure(session, String(code[4] ?? ""), "LineBridge", system, false);
  const accessToken = ipad ? result[2] : result[3]?.[1] ?? result[2];
  if (typeof accessToken !== "string" || accessToken.length < 20) throw new Error("missing_token");
  base.authToken = accessToken;
  const profile = await base.talk.getProfile();
  const issuedMid = ipad ? result[5] : result[4];
  if (!/^u[0-9a-f]{32}$/.test(profile?.mid) || issuedMid && profile.mid !== issuedMid) throw new Error("identity_mismatch");
  base.profile = profile;
  let metadata = ipad ? result[4] : result[10] ?? result[6];
  if (typeof metadata === "string") metadata = JSON.parse(metadata);
  if (metadata?.e2eeInfo) metadata = metadata.e2eeInfo;
  if (typeof metadata === "string") metadata = JSON.parse(metadata);
  if (!metadata?.encryptedKeyChain || !metadata?.publicKey) throw new Error("missing_e2ee_login_keys");
  const key = await base.e2ee.decodeE2EEKeyV1(metadata, Buffer.from(secret));
  if (!key || !await base.e2ee.verifyStoredKeyAgainstServer(key.keyId, key.privKey)) throw new Error("unverified_e2ee_keys");
  // Probe only existing Square membership, never join/create/mark read.
  const joined = await base.square.getJoinedSquares({ limit: 1 });
  if (!Array.isArray(joined?.squares)) throw new Error("square_access_unverified");
  const e2eeStorage = Object.fromEntries(Object.entries(base.storage.getAll()).filter(([name]) => /^e2eeKeys:\d+$/.test(name)));
  if (!Object.keys(e2eeStorage).length) throw new Error("missing_saved_keys");
  // Intentionally omit refresh tokens, certificates and arbitrary storage entries.
  return { schema: "secondary-shared-v2", ...identity,
    accessToken, mid: profile.mid, e2eeStorage };
}

if (import.meta.main) {
  const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
  const { BaseClient } = await import(pathToFileURL(`${root}/packages/linejs/base/mod.ts`).href);
  const { MemoryStorage } = await import(pathToFileURL(`${root}/packages/linejs/base/storage/memory.ts`).href);
  const identity = deviceProfile(Deno.args[1] ?? "ANDROIDSECONDARY");
  const base = new BaseClient({ ...identity, storage: new MemoryStorage(),
    legy: { encrypted: "auto" }, fetch: guardedFetch(true) });
  guardRequests(base, true);
  const frame = (kind: string, value: any) => console.log(JSON.stringify({ kind, value }));
  try { frame("session", await loginUnified(base, frame)); }
  catch { frame("failed", null); Deno.exit(1); }
}
