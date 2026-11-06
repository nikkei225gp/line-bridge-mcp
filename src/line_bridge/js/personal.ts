// Bounded Talk reads and once-only text sends. No login, sync, or key registration.
import { Buffer } from "node:buffer";
import { withPersonalSend } from "./desktop-policy.ts";
import { readFailure } from "./runtime-errors.ts";

const operations = new Set(["chats", "chat", "messages", "find_contact", "groups", "contact_chats", "members"]);
function integer(value: any, minimum: number, maximum: number) {
  if (!Number.isInteger(value) || value < minimum || value > maximum) throw new Error("invalid_limit");
  return value;
}
function id(value: any) {
  if (typeof value !== "string" || !/^[ucr][0-9a-f]{32}$/.test(value)) throw new Error("invalid_chat");
  return value;
}
function list(value: any) {
  if (!Array.isArray(value)) throw new Error("invalid_result");
  return value;
}
function time(value: any) {
  const result = Number(value ?? 0);
  return Number.isSafeInteger(result) && result >= 0 ? result : 0;
}
function contentType(value: any) { return [undefined, 0, "NONE", "TEXT"].includes(value) ? "0" : value === "IMAGE" ? "1" : String(value); }
function nativeContentType(value: any): string {
  if (value === undefined || value === null || typeof value === "boolean") return "";
  const names: Record<number, string> = { 0: "NONE", 1: "IMAGE", 2: "VIDEO", 3: "AUDIO", 4: "HTML", 5: "PDF", 6: "CALL", 7: "STICKER", 8: "PRESENCE", 9: "GIFT", 10: "GROUPBOARD", 11: "APPLINK", 12: "LINK", 13: "CONTACT", 14: "FILE", 15: "LOCATION", 16: "POSTNOTIFICATION", 17: "RICH", 18: "CHATEVENT", 19: "MUSIC", 20: "PAYMENT", 21: "EXTIMAGE", 22: "FLEX" };
  if (typeof value === "number") return names[value] ?? String(value);
  const s = String(value).trim();
  if (!s) return "";
  if (/^\d+$/.test(s)) return names[Number(s)] ?? s;
  const upper = s.toUpperCase();
  return upper === "TEXT" ? "NONE" : upper;
}
export function sanitizeMetadata(meta: any): Record<string, unknown> | undefined {
  if (meta === null || typeof meta !== "object" || Array.isArray(meta)) return undefined;
  const out: Record<string, unknown> = {};
  let count = 0;
  for (const [key, value] of Object.entries(meta)) {
    if (typeof key !== "string") continue;
    if (value === null || ["string", "number", "boolean"].includes(typeof value)) {
      out[key] = value;
    } else if (typeof value === "object" && !Array.isArray(value) && !(value instanceof Uint8Array)) {
      // Some content metadata values (notably FLEX_JSON) arrive pre-parsed.
      out[key] = value;
    }
    if (++count >= 64) break;
  }
  return Object.keys(out).length ? out : undefined;
}
export { nativeContentType };
// Notification hints only: whether a message mentions the account (or @all) and which message it replies to.
export function mentionsMe(meta: any, mine: (mid: string) => boolean) {
  try {
    const raw = meta?.MENTION, value = typeof raw === "string" ? JSON.parse(raw) : raw;
    return Array.isArray(value?.MENTIONEES) && value.MENTIONEES.some((m: any) => m?.A === "1" || typeof m?.M === "string" && mine(m.M));
  } catch { return false; }
}
export function replyTo(m: any) {
  const id = m?.relatedMessageId, type = m?.messageRelationType;
  return id && (type === "REPLY" || type === 3) ? String(id) : null;
}
function messageNumber(value: string) {
  if (typeof value !== "string" || !/^\d{1,19}$/.test(value) || BigInt(value) > 9223372036854775807n) throw new Error("invalid_message_id");
  return BigInt(value);
}
export function confirmedSendResult(message: any, replyTo?: string) {
  const messageId = message?.id;
  if (typeof messageId !== "string" || !/^[1-9]\d{0,18}$/.test(messageId) || BigInt(messageId) > 9223372036854775807n) {
    return { state: "send_outcome_unknown_no_retry" };
  }
  const created = typeof message.createdTime === "number" || typeof message.createdTime === "bigint" ? time(message.createdTime) : 0;
  // The send itself is confirmed; whether LINE kept the quote is reported separately, never assumed.
  return { state: "sent_confirmed", message_id: messageId, ...(created > 0 ? { created_ms: created } : {}),
    ...(replyTo ? { reply_confirmed: String(message.relatedMessageId ?? "") === replyTo } : {}) };
}

const nameCache = new WeakMap<object, Map<string, { at: number, value: string }>>();
const contactCache = new WeakMap<object, { at: number, value: any[] }>();
const contactTTL = 600000;
async function contacts(base: any, mids: string[]) {
  const values: any[] = [];
  const unique = [...new Set(mids)].slice(0, 1000);
  for (let i = 0; i < unique.length; i += 100) values.push(...list(await base.talk.getContacts({ mids: unique.slice(i, i + 100) })));
  const cache = nameCache.get(base) ?? new Map();
  for (const c of values) cache.set(c.mid, { at: Date.now(), value: c.displayName ?? "" });
  nameCache.set(base, cache);
  return values;
}
async function names(base: any, mids: string[]) {
  const unique = [...new Set(mids.filter(mid => typeof mid === "string" && mid.startsWith("u")))].slice(0, 1000);
  const missing = unique.filter(mid => { const c = nameCache.get(base)?.get(mid); return !c || Date.now() - c.at >= contactTTL; });
  if (missing.length) await contacts(base, missing);
  const result = new Map<string, string>();
  for (const mid of unique) { const c = nameCache.get(base)?.get(mid); if (c && Date.now() - c.at < contactTTL) result.set(mid, c.value); }
  return result;
}
async function chatInfo(base: any, chatId: string) {
  id(chatId);
  if (chatId.startsWith("u")) {
    const labels = await names(base, [chatId]);
    if (!labels.has(chatId)) throw new Error("chat_not_found");
    return { id: chatId, name: labels.get(chatId), kind: "direct", unread_count: null };
  }
  const result = await base.talk.getChats({ chatMids: [chatId], withMembers: false, withInvitees: false });
  const chat = list(result?.chats).find(c => c.chatMid === chatId);
  if (!chat) throw new Error("chat_not_found");
  return { id: chatId, name: chat.chatName ?? "", kind: chatId.startsWith("c") ? "group" : "room", unread_count: null };
}
async function profile(base: any) {
  if (!base.profile) {
    const value = await base.talk.getProfile();
    if (value?.mid !== base.bridgeExpectedMid) throw new Error("identity_mismatch");
    base.profile = value;
  }
}
export async function sendPersonalMessage(base: any, args: any) {
  if (typeof args.chat_id !== "string" || !/^[uc][0-9a-f]{32}$/.test(args.chat_id) ||
      typeof args.text !== "string" || args.text.length < 1 || args.text.length > 5000 ||
      !Number.isInteger(args.sequence) || args.sequence < 1 || args.sequence > 2147483647) return { state: "not_sent_destination_changed" };
  if (args.reply_to !== undefined && (typeof args.reply_to !== "string" || !/^[1-9]\d{0,18}$/.test(args.reply_to) ||
      BigInt(args.reply_to) > 9223372036854775807n)) return { state: "not_sent_destination_changed" };
  if (base.bridgeE2EEReady !== true) return { state: "not_sent_e2ee_unavailable" };
  await profile(base);
  let chunks: Buffer[];
  try {
    // Encrypt before opening the send capability. Missing group keys must never be registered.
    chunks = await base.e2ee.encryptE2EEMessage(args.chat_id, args.text, "NONE");
    if (!Array.isArray(chunks) || chunks.length !== 5 || chunks.some(c => !Buffer.isBuffer(c) || !c.length)) {
      return { state: "not_sent_e2ee_unavailable" };
    }
  } catch { return { state: "not_sent_e2ee_unavailable" }; }
  base.reqseqs = { talk: args.sequence };
  try {
    const result = await withPersonalSend(base, () => base.talk.sendMessage({ to: args.chat_id,
      chunks, contentType: "NONE", contentMetadata: { e2eeVersion: "2", contentType: "0", e2eeMark: "2" },
      // A quote reply names the message it answers; the text itself stays encrypted.
      ...(args.reply_to ? { relatedMessageId: args.reply_to } : {}),
      // Explicit E2EE disables the SDK's plaintext-to-encrypted retry path.
      e2ee: true }));
    const echoed = result?.chunks;
    // A message to our own chat (Keep memo) comes back addressed to another internal mid, so it is matched by its sender.
    // A direct message to someone else comes back with no recipient at all; the exact ciphertext echo below then binds it.
    const fromMe = result?.from === base.bridgeExpectedMid;
    const addressed = result?.to === args.chat_id || args.chat_id === base.bridgeExpectedMid && fromMe ||
      (result?.to === undefined || result?.to === null) && fromMe;
    const confirmed = addressed &&
      contentType(result.contentType) === "0" && Array.isArray(echoed) && echoed.length === chunks.length &&
      // The SDK codec returns valid UTF-8 binary fields (notably key IDs) as strings.
      echoed.every((c: any, i: number) => (typeof c === "string" || Buffer.isBuffer(c) || c instanceof Uint8Array) && chunks[i].equals(Buffer.from(c)));
    return confirmed ? confirmedSendResult(result, args.reply_to) : { state: "send_outcome_unknown_no_retry" };
  } catch { return { state: "send_outcome_unknown_no_retry" }; }
}
// Unsending is one write like a send: it uses the same once-only permit and the bridge's request sequence.
export async function unsendPersonalMessage(base: any, args: any) {
  if (typeof args.chat_id !== "string" || !/^[uc][0-9a-f]{32}$/.test(args.chat_id) ||
      typeof args.message_id !== "string" || !/^\d{1,19}$/.test(args.message_id) ||
      !Number.isInteger(args.sequence) || args.sequence < 1 || args.sequence > 2147483647) return { state: "not_unsent", reason: "invalid_request" };
  base.reqseqs = { talk: args.sequence };
  try {
    await withPersonalSend(base, () => base.talk.unsendMessage({ seq: args.sequence, messageId: args.message_id }));
    return { state: "unsent" };
  } catch (error) { return { state: "not_unsent", reason: readFailure(error) }; }
}
async function rawMessages(base: any, chatId: string, count: number, before = "", beforeCreatedMs?: any) {
  id(chatId); integer(count, 1, 200);
  let result;
  if (before) {
    const beforeNumber = messageNumber(before);
    let anchorTime;
    if (beforeCreatedMs !== undefined) {
      if (!Number.isSafeInteger(beforeCreatedMs) || beforeCreatedMs <= 0) throw new Error("history_anchor_unavailable");
      anchorTime = beforeCreatedMs;
    } else anchorTime = time(base.bridgeMessages?.get(`${chatId}\0${before}`)?.createdTime);
    if (!anchorTime) throw new Error("history_anchor_unavailable");
    result = await base.talk.getPreviousMessagesV2WithRequest({ request: { messageBoxId: chatId,
      endMessageId: { deliveredTime: anchorTime, messageId: beforeNumber }, messagesCount: count,
      withReadCount: false, receivedOnly: false }, syncReason: "INTERNAL" });
  } else result = await base.talk.getRecentMessagesV2({ messageBoxId: chatId, messagesCount: count });
  base.bridgeMessages ??= new Map();
  const rows = list(result).filter(m => m?.id && (chatId.startsWith("u")
    ? m.to === chatId && m.from === base.profile.mid || m.to === base.profile.mid && m.from === chatId
    : m.to === chatId));
  for (const row of rows) base.bridgeMessages.set(`${chatId}\0${row.id}`, row);
  while (base.bridgeMessages.size > 2000) base.bridgeMessages.delete(base.bridgeMessages.keys().next().value);
  return rows.filter(m => !before || String(m.id) !== before).slice(0, count);
}
// The raw message for a media download: from this worker's cache, a page anchored on it, or the latest 200.
export async function locateMessage(base: any, chatId: string, messageId: string, createdMs?: any) {
  await profile(base);
  const key = `${id(chatId)}\0${messageId}`;
  // rawMessages caches every row of the page, the anchor included, before dropping the anchor from its result.
  if (!base.bridgeMessages?.has(key) && Number.isSafeInteger(createdMs) && createdMs > 0) await rawMessages(base, chatId, 5, messageId, createdMs);
  if (!base.bridgeMessages?.has(key)) await rawMessages(base, chatId, 200);
  const message = base.bridgeMessages.get(key);
  if (!message) throw new Error("message_not_in_bounded_history");
  return message;
}
async function normalize(base: any, rows: any[]) {
  const labels = await names(base, rows.map(m => m.from));
  const messages = [];
  for (const m of rows) {
    let text = m.text ?? null;
    if (m.chunks?.length && contentType(m.contentType) === "0") {
      try { const plain = await base.e2ee.decryptE2EEMessage(m); text = plain.text ?? "[encrypted message unavailable]"; }
      catch { text = "[encrypted message unavailable]"; }
    }
    messages.push({ id: String(m.id), created_ms: time(m.createdTime), content_type: nativeContentType(m.contentType),
      text, content_metadata: sanitizeMetadata(m.contentMetadata), sender_name: labels.get(m.from) ?? "",
      from_me: m.from === base.profile.mid,
      sender_identity_known: typeof m.from === "string" && m.from.length > 0, unsent: false,
      mentions_me: mentionsMe(m.contentMetadata, mid => mid === base.profile.mid), reply_to: replyTo(m) });
  }
  return messages.sort((a, b) => a.created_ms - b.created_ms);
}
// LINE pages message boxes in chat-ID order, not by activity. Read every page (bounded) and sort by the last delivery.
async function messageBoxes(base: any) {
  const boxes = new Map<string, any>();
  let cursor: string | undefined;
  for (let page = 0; page < 20; page++) {
    const result = await base.talk.getMessageBoxes({ messageBoxListRequest: { messageBoxCountLimit: 100,
      activeOnly: false, withUnreadCount: true, lastMessagesPerMessageBoxCount: 1, unreadOnly: false,
      ...(cursor ? { minChatId: cursor } : {}) }, syncReason: "INTERNAL" });
    const rows = list(result?.messageBoxes);
    const before = boxes.size;
    for (const box of rows) if (typeof box?.id === "string" && /^[ucr][0-9a-f]{32}$/.test(box.id)) boxes.set(box.id, box);
    const last = rows.at(-1)?.id;
    if (!result.hasNext || typeof last !== "string" || last === cursor || boxes.size === before) break;
    cursor = last;
  }
  const delivered = (b: any) => time(b.lastDeliveredMessageId?.deliveredTime) || time(b.lastMessages?.[0]?.createdTime);
  return [...boxes.values()].sort((a, b) => delivered(b) - delivered(a));
}
// Display names of the people in one chat. Raw member IDs never leave this function.
async function members(base: any, chatId: string, limit: number) {
  id(chatId); integer(limit, 1, 200);
  const own = { name: String(base.profile.displayName ?? ""), is_me: true, is_creator: false };
  if (chatId.startsWith("u")) {
    const labels = await names(base, [chatId]);
    if (!labels.has(chatId)) throw new Error("chat_not_found");
    return { members: [own, { name: labels.get(chatId), is_me: false, is_creator: false }], total: 2, truncated: false };
  }
  const result = await base.talk.getChats({ chatMids: [chatId], withMembers: true, withInvitees: false });
  const chat = list(result?.chats).find(c => c.chatMid === chatId);
  if (!chat) throw new Error("chat_not_found");
  const raw = chat.extra?.groupExtra?.memberMids;
  const mids: string[] = (raw instanceof Map ? [...raw.keys()] : Object.keys(raw ?? {})).filter(mid => /^u[0-9a-f]{32}$/.test(mid));
  const creator = chat.extra?.groupExtra?.creator;
  // The account itself first, then the others in LINE's order.
  const ordered = [...mids.filter(mid => mid === base.profile.mid), ...mids.filter(mid => mid !== base.profile.mid)];
  const selected = ordered.slice(0, limit), labels = await names(base, selected.filter(mid => mid !== base.profile.mid));
  return { members: selected.map(mid => ({ name: mid === base.profile.mid ? own.name : labels.get(mid) ?? "",
      is_me: mid === base.profile.mid, is_creator: mid === creator })), total: mids.length, truncated: mids.length > limit };
}
export async function dispatchPersonal(base: any, operation: string, args: any): Promise<any> {
  if (!operations.has(operation)) throw new Error("operation_refused");
  await profile(base);
  if (operation === "chats") {
    const limit = integer(args.limit, 1, 100);
    const selected = (await messageBoxes(base)).slice(0, limit);
    const labels = await names(base, selected.map(b => b.id));
    const groupIds = selected.filter(b => !b.id.startsWith("u")).map(b => b.id);
    if (groupIds.length) {
      const chats = await base.talk.getChats({ chatMids: groupIds, withMembers: false, withInvitees: false });
      for (const chat of list(chats?.chats)) labels.set(chat.chatMid, chat.chatName ?? "");
    }
    const previews = new Map((await normalize(base, selected.flatMap(b => (b.lastMessages ?? []).slice(0, 1))))
      .map(m => [m.id, m]));
    const result = [];
    for (const b of selected) {
      const last = previews.get(String(b.lastMessages?.[0]?.id));
      result.push({ chat_id: b.id, name: labels.get(b.id) ?? "", unread: Number(b.unreadCount ?? 0),
        last_message: last ? { text: last.text, from_me: last.from_me, time: last.created_ms } : {},
        last_message_id: b.lastMessages?.[0]?.id ? String(b.lastMessages[0].id) : "" });
    }
    return result;
  }
  if (operation === "members") return await members(base, args.chat_id, args.limit);
  if (operation === "chat" || operation === "contact_chats") return await chatInfo(base, args.chat_id ?? args.contact_id);
  if (operation === "messages") return await normalize(base, await rawMessages(base, args.chat_id, args.count, args.before_message_id, args.before_created_ms));
  if (operation === "find_contact") {
    if (typeof args.name !== "string" || !args.name.trim()) throw new Error("invalid_name");
    let cached = contactCache.get(base);
    if (!cached || Date.now() - cached.at >= contactTTL) {
      const mids = list(await base.talk.getAllContactIds()).slice(0, 1000);
      const value = await contacts(base, mids);
      cached = { at: Date.now(), value }; contactCache.set(base, cached);
    }
    return cached.value.filter(c => String(c.displayName ?? "").toLocaleLowerCase().includes(args.name.toLocaleLowerCase()));
  }
  if (operation === "groups") {
    const joined = await base.talk.getAllChatMids({ request: { withMemberChats: true, withInvitedChats: false }, syncReason: "INTERNAL" });
    const mids = list(joined?.memberChatMids).slice(0, 500), result = [];
    for (let i = 0; i < mids.length; i += 100) {
      const chats = await base.talk.getChats({ chatMids: mids.slice(i, i + 100), withMembers: false, withInvitees: false });
      result.push(...list(chats?.chats).map(c => ({ mid: c.chatMid, name: c.chatName ?? "" })));
    }
    return result;
  }
  throw new Error("operation_refused");
}
