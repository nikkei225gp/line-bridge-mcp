// One subprocess owns one desktop session; Talk and Square retain separate guards.
import { stdin } from "node:process";
import { pathToFileURL } from "node:url";
import { createInterface } from "node:readline";
import { connectSquare, dispatch } from "./openchat.ts";
import { dispatchPersonal, sendPersonalMessage, unsendPersonalMessage } from "./personal.ts";
import { guardedFetch, guardRequests } from "./desktop-policy.ts";
import { downloadImage } from "./media.ts";
import { readFailure } from "./runtime-errors.ts";
import { sessionProfile } from "./devices.ts";

const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
const { BaseClient } = await import(pathToFileURL(`${root}/packages/linejs/base/mod.ts`).href);
const { MemoryStorage } = await import(pathToFileURL(`${root}/packages/linejs/base/storage/memory.ts`).href);
const { LINEStruct } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/mod.ts`).href);
let personal: any, generation = "";

async function connect(saved: any) {
  const identity = sessionProfile(saved);
  if (!/^u[0-9a-f]{32}$/.test(saved.mid) || !saved.e2eeStorage || typeof saved.bridge_generation !== "string") throw new Error("session_refused");
  if (personal && generation === saved.bridge_generation) return;
  const storage = new MemoryStorage();
  // Load only E2EE key entries. Never load refreshToken or upstream reqseq/storage.
  for (const [key, value] of Object.entries(saved.e2eeStorage)) {
    if (!/^e2eeKeys:\d+$/.test(key) || typeof value !== "string") throw new Error("storage_refused");
    await storage.set(key, value);
  }
  personal = new BaseClient({ device: identity.device, version: identity.version, storage, legy: { encrypted: identity.legacy ? false : "auto" },
    fetch: guardedFetch() });
  personal.authToken = saved.accessToken;
  personal.bridgeExpectedMid = saved.mid;
  personal.bridgeE2EEReady = true;
  // The SDK can attempt registration when a group has no shared key. Never allow it.
  personal.e2ee.tryRegisterE2EEGroupKey = async () => { throw new Error("method_refused"); };
  guardRequests(personal);
  generation = saved.bridge_generation;
}

export async function dispatchDesktopSquare(base: any, structures: any, args: any) {
  return await dispatch(base, structures, args);
}

if (import.meta.main) for await (const line of createInterface({ input: stdin, crlfDelay: Infinity })) {
  let identity: unknown = null;
  try {
    if (line.length > 262144) throw new Error("request_too_large");
    const args = JSON.parse(line);
    identity = args.id;
    if (!["personal", "personal_send", "personal_unsend", "image", "list", "messages", "thread_messages", "members", "poll", "send", "unsend", "destroy"].includes(args.action)) throw new Error("invalid_action");
    await connect(args.session);
    const result = args.action === "personal"
      ? { result: await dispatchPersonal(personal, args.operation, args.arguments) }
      : args.action === "personal_send" ? await sendPersonalMessage(personal, args)
      : args.action === "personal_unsend" ? await unsendPersonalMessage(personal, args)
      // OpenChat photos use the same account token, so both sources go through the personal client's guarded fetch.
      : args.action === "image" ? await downloadImage(personal, args)
      : await dispatchDesktopSquare(connectSquare({ schema: args.session.schema, device: args.session.device, version: args.session.version,
        accessToken: args.session.accessToken, bridge_generation: args.session.bridge_generation }, true), LINEStruct, args);
    console.log(JSON.stringify({ id: identity, ...result }, (_, value) => typeof value === "bigint" ? value.toString() : value));
  } catch (error) { console.log(JSON.stringify({ id: identity, state: "failed", error_code: readFailure(error) })); }
}
