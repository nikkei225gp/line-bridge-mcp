// linejs is an independently licensed MIT dependency. Never log requests or errors.
import { stdin } from "node:process";
import { pathToFileURL } from "node:url";
import { readFailure, runtimeFetch } from "./runtime-errors.ts";
import { confirmedSendResult, mentionsMe, nativeContentType, replyTo, sanitizeMetadata } from "./personal.ts";
import { sessionProfile } from "./devices.ts";

const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
const { BaseClient } = await import(pathToFileURL(`${root}/packages/linejs/base/mod.ts`).href);
const { MemoryStorage } = await import(pathToFileURL(`${root}/packages/linejs/base/storage/memory.ts`).href);
const { LINEStruct } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/mod.ts`).href);
const { createInterface } = await import("node:readline");

let base: any;
let generation = "";
let sending = false;
const readMethods = new Set(["getJoinedSquares", "fetchMyEvents", "fetchSquareChatEvents", "getSquareMembers",
  "getSquareThreadMid", "getSquareThread", "getSquareChatMembers"]);
const sendMethods = new Set(["sendMessage", "sendSquareThreadMessage", "unsendMessage", "destroyMessage"]);

export function connectSquare(saved: any, verifiedPlainAccess = false) {
  const identity = sessionProfile({ schema: "desktop-shared-v1", ...saved });
  if (typeof saved.bridge_generation !== "string") {
    throw new Error("session_refused");
  }
  if (generation === saved.bridge_generation && base) return base;
  generation = saved.bridge_generation;
  base = new BaseClient({ device: identity.device, version: identity.version,
    // Unified login probes both services on this same transport before saving.
    legy: { encrypted: verifiedPlainAccess && identity.legacy ? false : "auto" },
    // Do NOT load refreshToken: the upstream request layer can retry a send after refresh.
    storage: new MemoryStorage(), fetch: async (input: any, init: any) => {
      const req = new Request(input, init), url = new URL(req.url);
      if (url.hostname !== "legy.line-apps.com" || url.pathname !== "/SQ1" || url.protocol !== "https:") throw new Error("route_refused");
      return await runtimeFetch(req);
    } });
  base.authToken = saved.accessToken;
  const request = base.request.request.bind(base.request);
  base.request.request = (value: any, method: string, ...rest: any[]) => {
    if (!readMethods.has(method) && !(sendMethods.has(method) && sending)) throw new Error("method_refused");
    return request(value, method, ...rest);
  };
  return base;
}

const membershipCache = new WeakMap<object, { at: number, value: Awaited<ReturnType<typeof memberships>> }>();
async function cachedMemberships(base: any) {
  const value = await memberships(base);
  membershipCache.set(base, { at: Date.now(), value });
  return value;
}
async function memberships(base: any) {
  const own = new Set<string>(), squares = new Map<string, any>(), rooms = new Map<string, any>();
  let continuationToken: string | undefined;
  for (let page = 0; page < 5; page++) {
    const joined: any = await base.square.getJoinedSquares({ limit: 100, continuationToken });
    if (!Array.isArray(joined?.squares)) throw new Error("invalid_memberships");
    for (const s of joined.squares) squares.set(s.mid, { id: s.mid, name: s.name });
    for (const m of Object.values(joined.members ?? {}) as any[]) own.add(`${m.squareMid}:${m.squareMemberMid}`);
    continuationToken = joined.continuationToken;
    if (!continuationToken) break;
  }
  const squaresTruncated = Boolean(continuationToken);
  continuationToken = undefined;
  for (let page = 0; page < 10; page++) {
    const result: any = await base.square.fetchMyEvents({ limit: 100, continuationToken });
    if (!Array.isArray(result?.events)) throw new Error("invalid_memberships");
    for (const event of result.events) {
      const item = event.payload?.notifiedCreateSquareChatMember, c = item?.chat, m = item?.chatMember;
      if (c && m && c.squareChatMid === m.squareChatMid && own.has(`${c.squareMid}:${m.squareMemberMid}`) &&
          (m.membershipState === "JOINED" || m.membershipState === 1)) {
        rooms.set(c.squareChatMid, { id: c.squareChatMid, squareId: c.squareMid, name: c.name });
      }
      const changed = event.payload?.notifiedUpdateSquareChatMember;
      if (changed?.squareChatMember && !["JOINED", 1].includes(changed.squareChatMember.membershipState)) rooms.delete(changed.squareChatMid);
    }
    continuationToken = result.continuationToken;
    if (!continuationToken) break;
  }
  return { squares: [...squares.values()], rooms, own, truncated: squaresTruncated || Boolean(continuationToken) };
}

function message(r: any, chatId: string, squareId: string, own: Set<string>) {
  const m = r?.squareMessage?.message;
  if (!m?.id || r.squareChatMid !== chatId) return null;
  return { id: String(m.id), sender: r.senderDisplayName ?? "", created_ms: Number(m.createdTime),
    content_type: nativeContentType(m.contentType), text: m.text ?? null,
    content_metadata: sanitizeMetadata(m.contentMetadata),
    from_me: own.has(`${squareId}:${m.from}`), sender_identity_known: typeof m.from === "string" && m.from.length > 0,
    mentions_me: mentionsMe(m.contentMetadata, mid => own.has(`${squareId}:${mid}`)), reply_to: replyTo(m),
    // A message with replies in a thread carries the thread's size; the replies themselves are read separately.
    ...(r.squareMessage.threadInfo?.threadRoot ? { thread: { reply_count: Number(r.threadTotalMessageCount ?? 0) || 0,
      last_reply_ms: Number(r.threadLastMessageAt ?? 0) || 0 } } : {}) };
}

// Events usually carry only the sender's profile revision, not the name. Names come from one batched
// getSquareMembers call for senders that are unknown or whose revision moved, and are cached per member.
const memberNames = new WeakMap<object, Map<string, { name: string, revision: number }>>();
async function fillSenders(base: any, pairs: [any, any][]) {
  const cache = memberNames.get(base) ?? new Map<string, { name: string, revision: number }>();
  memberNames.set(base, cache);
  const wanted = new Map<string, number>();
  for (const [r] of pairs) {
    const mid = r.squareMessage.message.from, revision = Number(r.senderRevision ?? 0) || 0;
    if (typeof mid !== "string" || !mid) continue;
    if (r.senderDisplayName) cache.set(mid, { name: r.senderDisplayName, revision });
    // Only members (p...) have profiles; a bot sender (v...) makes the whole batch fail with ILLEGAL_ARGUMENT.
    else if (/^p[0-9a-f]{32}$/.test(mid) && (cache.get(mid)?.revision ?? -1) < revision) wanted.set(mid, revision);
  }
  const mids = [...wanted.keys()];
  for (let i = 0; i < mids.length; i += 100) {
    const chunk = mids.slice(i, i + 100);
    let members: any[] = [];
    try { members = Object.values((await base.square.getSquareMembers({ request: { mids: chunk } }))?.members ?? {}); }
    catch { continue; } // Names are best effort; the messages are still returned.
    // Members that left are not returned; remember them as unnamed at this revision instead of asking again.
    for (const mid of chunk) cache.set(mid, { name: "", revision: wanted.get(mid)! });
    for (const m of members) {
      if (typeof m?.squareMemberMid === "string") cache.set(m.squareMemberMid, { name: String(m.displayName ?? ""), revision: Number(m.revision ?? 0) || 0 });
    }
  }
  while (cache.size > 5000) cache.delete(cache.keys().next().value!);
  for (const [r, m] of pairs) if (!m.sender) m.sender = cache.get(r.squareMessage.message.from)?.name ?? "";
}

async function history(base: any, structures: any, chat: any, own: Set<string>, limit: number, thread?: string) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 50) throw new Error("invalid_limit");
  const messages = new Map<string, any>(), events = new Map<string, any>();
  // Without a sync token LINE returns its recent window (about the latest 50 messages, mixed with read and join
  // events) oldest first, and ignores continuationToken. Take the whole window in one page and keep its newest end.
  const result: any = await base.request.request(structures.SquareService_fetchSquareChatEvents_args({ request: {
    squareChatMid: chat.id, subscriptionId: 0, limit: 200, threadMid: thread,
    direction: "FORWARD", inclusive: "OFF", fetchType: "DEFAULT",
  } }), "fetchSquareChatEvents", 4, true, "/SQ1");
  if (!Array.isArray(result?.events)) throw new Error("invalid_history");
  for (const event of result.events) {
    const r = event.payload?.receiveMessage ?? event.payload?.sendMessage, m = message(r, chat.id, chat.squareId, own);
    if (m) { messages.set(m.id, m); events.set(m.id, r); }
  }
  const sorted = [...messages.values()].sort((a, b) => a.created_ms - b.created_ms), selected = sorted.slice(-limit);
  await fillSenders(base, selected.map(m => [events.get(m.id), m]));
  return { messages: selected, latestGuaranteed: false, truncated: sorted.length > limit };
}

// A thread hangs off one root message. LINE derives its mid from the chat and message, whether or not it exists yet.
async function threadMid(base: any, chatId: string, messageId: string) {
  const result: any = await base.square.getSquareThreadMid({ request: { chatMid: chatId, messageId } });
  if (typeof result?.threadMid !== "string" || !/^t[0-9a-f]{32}$/.test(result.threadMid)) throw new Error("invalid_thread");
  return result.threadMid;
}

// Who is in one joined chat, by display name and role. Member IDs stay inside this worker.
async function members(base: any, chat: any, own: Set<string>, limit: number) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 200) throw new Error("invalid_limit");
  const found: any[] = [];
  let token: string | undefined;
  for (let page = 0; page < 3 && found.length <= limit; page++) {
    const result: any = await base.square.getSquareChatMembers({ squareChatMid: chat.id, continuationToken: token, limit: 100 });
    if (!Array.isArray(result?.squareChatMembers)) throw new Error("invalid_members");
    found.push(...result.squareChatMembers);
    token = result.continuationToken || undefined;
    if (!token) break;
  }
  const roles: Record<string, string> = { "1": "admin", ADMIN: "admin", "2": "co_admin", CO_ADMIN: "co_admin", "10": "member", MEMBER: "member" };
  return { members: found.slice(0, limit).map(m => ({ name: String(m.displayName ?? ""), role: roles[String(m.role)] ?? "member",
      is_me: own.has(`${chat.squareId}:${m.squareMemberMid}`) })),
    // A further page, or more rows than asked for, means the list is cut.
    truncated: found.length > limit || Boolean(token) };
}

async function handle(args: any) {
  connectSquare(args.session);
  return await dispatch(base, LINEStruct, args);
}

export async function dispatch(base: any, structures: any, args: any): Promise<any> {
  if (args.action === "send") return await send(base, args);
  if (args.action === "unsend" || args.action === "destroy") return await remove(base, args);
  const cached = membershipCache.get(base), hit = cached && Date.now() - cached.at < 60000;
  let joined = hit ? cached.value : await cachedMemberships(base);
  if (args.action === "list") return { squares: joined.squares, chats: [...joined.rooms.values()], truncated: joined.truncated };
  if (hit && (joined.truncated || !joined.rooms.has(args.chat_id))) joined = await cachedMemberships(base);
  if (joined.truncated) throw new Error("membership_incomplete");
  const chat = joined.rooms.get(args.chat_id);
  if (!chat) throw new Error("membership_unconfirmed");
  if (args.action === "members") return { chat, ...await members(base, chat, joined.own, args.limit) };
  if (args.action === "messages") return { chat, ...await history(base, structures, chat, joined.own, args.limit) };
  if (args.action === "thread_messages") {
    if (typeof args.message_id !== "string" || !/^\d{1,19}$/.test(args.message_id)) throw new Error("invalid_message");
    const mid = await threadMid(base, chat.id, args.message_id);
    let thread: any;
    try { thread = (await base.square.getSquareThread({ request: { threadMid: mid, includeRootMessage: false } }))?.squareThread; }
    catch (error: any) { if (error?.data?.errorCode !== "NOT_FOUND") throw error; }
    // A message nobody replied to has no thread yet; LINE answers NOT_FOUND for it.
    if (!thread || thread.state === "DELETED" || thread.state === 2) return { chat, thread_exists: false, messages: [], latestGuaranteed: false, truncated: false };
    return { chat, thread_exists: true, ...await history(base, structures, chat, joined.own, args.limit, mid) };
  }
  if (args.action === "poll") {
    let state = args.checkpoint ?? { baseline: true };
    const messages = [], pairs: [any, any][] = [];
    for (let page = 0; page < (state.baseline ? 20 : 5); page++) {
      const result = await base.square.fetchSquareChatEvents({ squareChatMid: chat.id, limit: 100, syncToken: state.syncToken });
      if (!Array.isArray(result?.events) || typeof result.syncToken !== "string") throw new Error("invalid_poll");
      for (const event of result.events) {
        const m = message(event.payload?.receiveMessage, chat.id, chat.squareId, joined.own);
        if (m) { messages.push(m); pairs.push([event.payload.receiveMessage, m]); }
      }
      const stalled = state.syncToken === result.syncToken && result.events.length > 0;
      state = { syncToken: result.syncToken, baseline: Boolean(state.baseline && result.events.length), stalled };
      if (stalled || result.events.length === 0 || (!state.baseline && result.events.length < 100)) break;
    }
    await fillSenders(base, pairs);
    return { chat, messages, checkpoint: state };
  }
  throw new Error("invalid_action");
}

async function send(base: any, args: any) {
  if (typeof args.chat_id !== "string" || !args.chat_id ||
      typeof args.text !== "string" || args.text.length < 1 || args.text.length > 5000 || !Number.isSafeInteger(args.sequence)) {
    return { state: "not_sent_destination_changed" };
  }
  let thread: string | undefined;
  // A quote reply and a thread reply are different things; asking for both is refused before any network use.
  const reply = args.reply_to;
  if (reply !== undefined && (typeof reply !== "string" || !/^[1-9]\d{0,18}$/.test(reply) || args.thread_message_id !== undefined)) {
    return { state: "not_sent_destination_changed" };
  }
  if (args.thread_message_id !== undefined) {
    if (typeof args.thread_message_id !== "string" || !/^\d{1,19}$/.test(args.thread_message_id)) return { state: "not_sent_destination_changed" };
    // Nothing has been sent yet, so a failed lookup is a definite non-send.
    try { thread = await threadMid(base, args.chat_id, args.thread_message_id); } catch { return { state: "not_sent_thread_unavailable" }; }
  }
  base.reqseqs = { sq: args.sequence };
  sending = true;
  try {
    // Replying to a message that has no thread yet starts one, as in the LINE app.
    const result = thread
      ? await base.square.sendSquareThreadMessage({ request: { reqSeq: args.sequence, chatMid: args.chat_id, threadMid: thread,
        threadMessage: { message: { to: thread, text: args.text, contentType: "NONE", toType: "SQUARE_THREAD" } } } })
      : await base.square.sendMessage({ squareChatMid: args.chat_id, text: args.text, contentType: 0,
        ...(reply ? { relatedMessageId: reply } : {}) });
    const m = (result?.createdThreadMessage ?? result?.createdSquareMessage)?.message;
    return m?.id && m.to === (thread ?? args.chat_id) && m.text === args.text
      ? confirmedSendResult(m, reply) : { state: "send_outcome_unknown_no_retry" };
  } catch {
    return { state: "send_outcome_unknown_no_retry" };
  } finally { sending = false; }
}

// unsend takes back our own message; destroy is the co-admin deletion of anyone's message. LINE enforces both.
async function remove(base: any, args: any) {
  const [done, failed] = args.action === "unsend" ? ["unsent", "not_unsent"] : ["deleted", "not_deleted"];
  if (typeof args.chat_id !== "string" || !args.chat_id || typeof args.message_id !== "string" || !/^\d{1,19}$/.test(args.message_id) ||
      (args.thread_message_id !== undefined && (typeof args.thread_message_id !== "string" || !/^\d{1,19}$/.test(args.thread_message_id)))) {
    return { state: failed, reason: "invalid_request" };
  }
  let thread: string | undefined;
  if (args.thread_message_id) {
    try { thread = await threadMid(base, args.chat_id, args.thread_message_id); } catch { return { state: failed, reason: "thread_unavailable" }; }
  }
  sending = true;
  try {
    const request = { messageId: args.message_id, squareChatMid: args.chat_id, threadMid: thread };
    await (args.action === "unsend" ? base.square.unsendMessage(request) : base.square.destroyMessage(request));
    return { state: done };
  } catch (error) { return { state: failed, reason: readFailure(error) }; }
  finally { sending = false; }
}

if (import.meta.main) for await (const line of createInterface({ input: stdin, crlfDelay: Infinity })) {
  let id: unknown = null;
  try {
    if (line.length > 262144) throw new Error("request_too_large");
    const args = JSON.parse(line);
    id = args.id;
    if (!["list", "messages", "thread_messages", "members", "poll", "send", "unsend", "destroy"].includes(args.action)) throw new Error("invalid_action");
    console.log(JSON.stringify({ id, ...await handle(args) }));
  } catch (error) {
    console.log(JSON.stringify({ id, state: "failed", error_code: readFailure(error) }));
  }
}
