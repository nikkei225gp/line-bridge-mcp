// Offline mocks only: no accounts, sockets, subprocesses or credential files.
import { Buffer } from "node:buffer";
import { loginUnified } from "../src/line_bridge/js/login-unified.ts";
import { dispatchPersonal, mentionsMe, replyTo, sendPersonalMessage, unsendPersonalMessage } from "../src/line_bridge/js/personal.ts";
import { guardedFetch, guardRequests, withPersonalSend } from "../src/line_bridge/js/desktop-policy.ts";

function assert(value: unknown) { if (!value) throw new Error("Fixture assertion failed"); }
async function rejects(action: () => any) {
  let failed = false;
  try { await action(); } catch { failed = true; }
  assert(failed);
}
const me = "u" + "a".repeat(32), contact = "u" + "b".repeat(32), group = "c" + "c".repeat(32);

function loginFixture() {
  const calls: string[] = [], frames: any[] = [];
  const loginArgs: any[][] = [];
  const result: any = { 3: { 1: "mock-" + "x".repeat(30), 2: "mock refresh excluded" }, 4: me,
    6: { encryptedKeyChain: "mock", publicKey: "mock", keyId: "1" } };
  const base: any = {
    device: "DESKTOPWIN",
    loginProcess: {
      createSession: async () => { calls.push("session"); return { 1: "mock-login-session" }; },
      createQrCodeForSecure: async () => { calls.push("qr"); return { 1: "https://example.invalid/qr", 4: "mock nonce" }; },
      checkQrCodeVerified: async () => true,
      verifyCertificate: async () => { throw new Error("new-device"); },
      createPinCode: async () => ({ 1: "000000" }),
      checkPinCodeVerified: async () => true,
      qrCodeLoginV2ForSecure: async (...args: any[]) => { calls.push("token"); loginArgs.push(args); return result; },
    },
    talk: { getProfile: async () => ({ mid: me }) },
    square: { getJoinedSquares: async () => { calls.push("square-read"); return { squares: [] }; } },
    e2ee: {
      createSqrSecret: () => [new Uint8Array(32), "?secret=mock&e2eeVersion=1"],
      decodeE2EEKeyV1: async (metadata: any) => { assert(metadata.encryptedKeyChain === "mock"); calls.push("unwrap"); return { keyId: 1, privKey: new Uint8Array(32) }; },
      verifyStoredKeyAgainstServer: async () => true,
      registerE2EEKeyPair: async () => { calls.push("FORBIDDEN-write"); throw new Error("forbidden"); },
    },
    storage: { getAll: () => ({ "e2eeKeys:1": "mock serialized keys", refreshToken: "excluded", reqseq: "excluded" }) },
  };
  return { base, result, calls, frames, loginArgs, frame: (kind: string, value: any) => frames.push({ kind, value }) };
}

for (const [device, version, system] of [["ANDROIDSECONDARY", "26.6.2", "Android OS"], ["DESKTOPMAC", "26.2.0", "MAC"]]) {
  Deno.test(`${device} secure QR persists selected identity without auto-login`, async () => {
    const f = loginFixture(); f.base.device = device;
    const saved = await loginUnified(f.base, f.frame);
    assert(saved.device === device && saved.version === version && saved.schema === "secondary-shared-v2");
    assert(f.loginArgs.length === 1 && f.loginArgs[0][3] === system && f.loginArgs[0][4] === false);
  });
}
Deno.test("iPad uses the pinned legacy QR shape and imported keys without registering new keys", async () => {
  const f = loginFixture(); f.base.device = "IOSIPAD";
  f.base.loginProcess.createQrCode = async () => ({ 1: "https://example.invalid/qr" });
  f.base.loginProcess.qrCodeLogin = async (_session: string, automatic: boolean) => {
    assert(automatic === false);
    return { 2: "mock-" + "x".repeat(30), 4: f.result[6], 5: me };
  };
  const saved = await loginUnified(f.base, f.frame);
  assert(saved.device === "IOSIPAD" && saved.version === "26.7.2" && f.calls.includes("unwrap"));
  assert(!f.calls.includes("FORBIDDEN-write"));
});

Deno.test("one QR and one token cover both verified services", async () => {
  const f = loginFixture(), saved = await loginUnified(f.base, f.frame);
  assert(f.calls.filter(c => c === "session").length === 1 && f.calls.filter(c => c === "qr").length === 1);
  assert(f.calls.filter(c => c === "token").length === 1 && f.calls.includes("square-read"));
  assert(f.frames.filter(f => f.kind === "qr").length === 1 && f.frames.some(f => f.kind === "pin"));
  assert(f.frames[0].value.includes("secret=") && saved.schema === "secondary-shared-v2");
  assert(!("refreshToken" in saved) && Object.keys(saved.e2eeStorage).join() === "e2eeKeys:1");
  assert(!f.calls.includes("FORBIDDEN-write") && !JSON.stringify(f.frames).includes(saved.accessToken));
});
Deno.test("returning device can skip PIN but never adds a second QR", async () => {
  const f = loginFixture(); f.base.loginProcess.verifyCertificate = async () => {};
  await loginUnified(f.base, f.frame);
  assert(f.frames.length === 1 && f.frames[0].kind === "qr");
});
Deno.test("missing E2EE fails without registering replacement keys", async () => {
  const f = loginFixture(); delete f.result[6];
  await rejects(() => loginUnified(f.base, f.frame));
  assert(!f.calls.includes("FORBIDDEN-write"));
});
Deno.test("unverified E2EE fails closed", async () => {
  const f = loginFixture(); f.base.e2ee.verifyStoredKeyAgainstServer = async () => false;
  await rejects(() => loginUnified(f.base, f.frame)); assert(!f.calls.includes("FORBIDDEN-write"));
});
Deno.test("metadata e2eeInfo JSON is supported", async () => {
  const f = loginFixture(); f.result[6] = { e2eeInfo: JSON.stringify(f.result[6]) };
  await loginUnified(f.base, f.frame); assert(f.calls.includes("unwrap"));
});
Deno.test("account identity mismatch cannot save shared session", async () => {
  const f = loginFixture(); f.result[4] = contact;
  await rejects(() => loginUnified(f.base, f.frame)); assert(!f.calls.includes("square-read"));
});
Deno.test("Square read validation failure never starts a second QR", async () => {
  const f = loginFixture(); f.base.square.getJoinedSquares = async () => { throw new Error("denied"); };
  await rejects(() => loginUnified(f.base, f.frame)); assert(f.calls.filter(c => c === "qr").length === 1);
});
Deno.test("unapproved phone QR never issues token", async () => {
  const f = loginFixture(); f.base.loginProcess.checkQrCodeVerified = async () => false;
  await rejects(() => loginUnified(f.base, f.frame)); assert(!f.calls.includes("token"));
});

function personalFixture() {
  const calls: any[] = [];
  const rows: any[] = [
    { id: "102", from: me, to: contact, createdTime: 2000, text: "Own mock", contentType: 0 },
    { id: "101", from: contact, to: me, createdTime: 1000, text: "Received mock", contentType: 0 },
  ];
  const base: any = {
    bridgeExpectedMid: me, bridgeE2EEReady: true,
    talk: {
      getProfile: async () => { calls.push(["profile-read"]); return { mid: me, displayName: "Mock user" }; },
      getContacts: async (args: any) => { calls.push(["contacts", args]); return args.mids.map((mid: string) => ({ mid, displayName: mid === me ? "Mock user" : "Mock contact" })); },
      getRecentMessagesV2: async (args: any) => { calls.push(["recent", args]); return rows; },
      getPreviousMessagesV2WithRequest: async (args: any) => { calls.push(["previous", args]); return []; },
      getMessageBoxes: async () => ({ messageBoxes: [{ id: contact, unreadCount: 2, lastMessages: [rows[0]] }] }),
      getAllContactIds: async () => { calls.push(["contact-ids"]); return [contact]; },
      getAllChatMids: async () => ({ memberChatMids: [group] }),
      getChats: async () => ({ chats: [{ chatMid: group, chatName: "Mock group", extra: { groupExtra: { memberMids: { [me]: 0, [contact]: 0 }, inviteeMids: {} } } }] }),
      sendMessage: async () => { throw new Error("MUTATION"); },
      sendChatChecked: async () => { throw new Error("MUTATION"); },
    },
    e2ee: { decryptE2EEMessage: async (m: any) => ({ ...m, text: "Decrypted mock" }) },
  };
  return { base, calls, rows };
}
Deno.test("consecutive chats reuse contact names and direct chat lookup", async () => {
  const f = personalFixture(); f.rows[0].from = contact;
  await dispatchPersonal(f.base, "chats", { limit: 1 });
  await dispatchPersonal(f.base, "chats", { limit: 1 });
  assert((await dispatchPersonal(f.base, "chat", { chat_id: contact })).name === "Mock contact");
  assert(f.calls.filter(c => c[0] === "contacts").length === 1);
});
Deno.test("find_contact caches full contacts and filters each query", async () => {
  const f = personalFixture();
  assert((await dispatchPersonal(f.base, "find_contact", { name: "CONTACT" })).length === 1);
  assert((await dispatchPersonal(f.base, "find_contact", { name: "absent" })).length === 0);
  assert(f.calls.filter(c => c[0] === "contact-ids").length === 1 && f.calls.filter(c => c[0] === "contacts").length === 1);
});
Deno.test("contact caches expire after ten minutes", async () => {
  const f = personalFixture(), now = Date.now; let clock = now(); Date.now = () => clock;
  try {
    await dispatchPersonal(f.base, "find_contact", { name: "contact" });
    await dispatchPersonal(f.base, "chat", { chat_id: contact });
    assert(f.calls.filter(c => c[0] === "contacts").length === 1);
    clock += 600000;
    await dispatchPersonal(f.base, "chat", { chat_id: contact });
    await dispatchPersonal(f.base, "find_contact", { name: "contact" });
    assert(f.calls.filter(c => c[0] === "contacts").length === 3 && f.calls.filter(c => c[0] === "contact-ids").length === 2);
  } finally { Date.now = now; }
});
Deno.test("failed contact reads are retried without caching errors", async () => {
  const f = personalFixture(), original = f.base.talk.getContacts; let calls = 0;
  f.base.talk.getContacts = async (args: any) => { if (++calls === 1) throw new Error("failed"); return await original(args); };
  await rejects(() => dispatchPersonal(f.base, "find_contact", { name: "contact" }));
  assert((await dispatchPersonal(f.base, "find_contact", { name: "contact" })).length === 1);
  assert(calls === 2 && f.calls.filter(c => c[0] === "contact-ids").length === 2);
});
Deno.test("personal reads internally verify saved account identity", async () => {
  const f = personalFixture(); await dispatchPersonal(f.base, "chats", { limit: 1 });
  assert(f.base.profile.mid === me);
  const mismatch = personalFixture(); mismatch.base.bridgeExpectedMid = contact;
  await rejects(() => dispatchPersonal(mismatch.base, "chats", { limit: 1 }));
});
Deno.test("personal reads include correct received and own flags", async () => {
  const f = personalFixture(), messages = await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
  assert(messages.length === 2 && messages[0].from_me === false && messages[1].from_me === true);
  assert(messages[0].sender_name === "Mock contact" && messages[0].created_ms === 1000);
});
Deno.test("foreign-chat rows are excluded", async () => {
  const f = personalFixture(); f.rows.push({ id: "103", from: contact, to: group, createdTime: 3000 });
  assert((await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 })).length === 2);
});
Deno.test("history uses known message time and excludes duplicate anchor", async () => {
  const f = personalFixture(); await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
  await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "101" });
  const request = f.calls.find(c => c[0] === "previous")[1].request;
  assert(request.endMessageId.messageId === 101n && request.endMessageId.deliveredTime === 1000);
});
Deno.test("unknown history anchor is reported instead of guessed", async () => {
  const f = personalFixture(); await rejects(() => dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "999" }));
  assert(!f.calls.some(c => c[0] === "previous"));
});
Deno.test("archived history timestamp resumes after a worker restart without recent-message fetch", async () => {
  const f = personalFixture();
  await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "101", before_created_ms: 1700000000123 });
  const request = f.calls.find(c => c[0] === "previous")[1].request;
  assert(request.endMessageId.messageId === 101n && request.endMessageId.deliveredTime === 1700000000123);
  assert(!request.withReadCount && !f.calls.some(c => c[0] === "recent"));
});
Deno.test("broker archived timestamp takes precedence over fresh in-memory anchor", async () => {
  const f = personalFixture(); await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
  await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "101", before_created_ms: 1700000000123 });
  assert(f.calls.find(c => c[0] === "previous")[1].request.endMessageId.deliveredTime === 1700000000123);
});
Deno.test("supplied invalid archived timestamps are rejected without coercion or cached fallback", async () => {
  for (const timestamp of [0, -1, 1.5, "1000", true, null, NaN, Infinity, 9007199254740992]) {
    const f = personalFixture(); await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
    await rejects(() => dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "101", before_created_ms: timestamp }));
    assert(!f.calls.some(c => c[0] === "previous"));
  }
});
Deno.test("decrypt failure remains visible", async () => {
  const f = personalFixture(); f.rows[1].chunks = [new Uint8Array(1)];
  f.base.e2ee.decryptE2EEMessage = async () => { throw new Error("sealed"); };
  const rows = await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
  assert(rows[0].text.startsWith("[encrypted message"));
});
Deno.test("E2EE text is decrypted when supported", async () => {
  const f = personalFixture(); f.rows[1].chunks = [new Uint8Array(1)];
  assert((await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 }))[0].text === "Decrypted mock");
});
Deno.test("chat listing and contacts use read-only adapters", async () => {
  const f = personalFixture();
  const chats = await dispatchPersonal(f.base, "chats", { limit: 1 });
  assert(chats[0].chat_id === contact && chats[0].unread === 2);
  assert((await dispatchPersonal(f.base, "find_contact", { name: "CONTACT" })).length === 1);
  assert((await dispatchPersonal(f.base, "contact_chats", { contact_id: contact })).kind === "direct");
});
Deno.test("chat listing reads every message-box page and returns the most recently active first", async () => {
  const f = personalFixture(), requests: any[] = [];
  const box = (id: string, delivered: number) => ({ id, unreadCount: 0, lastDeliveredMessageId: { deliveredTime: String(delivered), messageId: "1" } });
  const older = "u" + "1".repeat(32), newest = "u" + "f".repeat(32);
  const pages: Record<string, any> = {
    "": { messageBoxes: [box(older, 100), { id: "s-not-a-talk-chat" }, box(contact, 300)], hasNext: true },
    [contact]: { messageBoxes: [box(contact, 300), box(newest, 900)], hasNext: false },
  };
  f.base.talk.getMessageBoxes = async (args: any) => { requests.push(args.messageBoxListRequest); return pages[args.messageBoxListRequest.minChatId ?? ""]; };
  const chats = await dispatchPersonal(f.base, "chats", { limit: 2 });
  assert(chats.map((c: any) => c.chat_id).join() === [newest, contact].join());
  assert(requests.length === 2 && requests[1].minChatId === contact && requests.every(r => r.messageBoxCountLimit === 100));
});
Deno.test("chat listing stops when a page makes no progress", async () => {
  const f = personalFixture(); let calls = 0;
  f.base.talk.getMessageBoxes = async () => { calls++; return { messageBoxes: [{ id: contact }], hasNext: true }; };
  assert((await dispatchPersonal(f.base, "chats", { limit: 5 })).length === 1 && calls === 2);
});
Deno.test("joined groups and chat information do not include group-member details", async () => {
  const f = personalFixture(); assert((await dispatchPersonal(f.base, "groups", {}))[0].mid === group);
  const chat = await dispatchPersonal(f.base, "chat", { chat_id: group });
  assert(chat.kind === "group" && !Object.hasOwn(chat, "raw") && !Object.hasOwn(chat, "members"));
});
for (const operation of ["profile", "group_members", "last_interaction", "context", "receipts", "image"]) {
  Deno.test(`removed personal ${operation} operation is refused before identity or network reads`, async () => {
    const f = personalFixture();
    try { await dispatchPersonal(f.base, operation, { chat_id: group, message_id: "101" }); throw new Error("Unexpected operation allowed"); }
    catch (error) { assert(error instanceof Error && error.message === "operation_refused"); }
    assert(f.calls.length === 0);
  });
}
Deno.test("members of a group are named without exposing member IDs; the account is first", async () => {
  const f = personalFixture();
  const result = await dispatchPersonal(f.base, "members", { chat_id: group, limit: 10 });
  assert(JSON.stringify(result.members) === JSON.stringify([{ name: "Mock user", is_me: true, is_creator: false }, { name: "Mock contact", is_me: false, is_creator: false }]));
  assert(result.total === 2 && result.truncated === false && !JSON.stringify(result).includes(contact));
  assert(f.calls.filter(c => c[0] === "contacts").length === 1 && JSON.stringify(f.calls.find(c => c[0] === "contacts")[1].mids) === JSON.stringify([contact]));
});
Deno.test("members are bounded, mark the creator and accept a Map of member IDs", async () => {
  const f = personalFixture(), other = "u" + "d".repeat(32);
  f.base.talk.getChats = async (args: any) => {
    assert(args.withMembers === true && args.withInvitees === false);
    return { chats: [{ chatMid: group, chatName: "Mock group", extra: { groupExtra: { creator: contact, memberMids: new Map([[other, 0], [contact, 0], [me, 0]]) } } }] };
  };
  const result = await dispatchPersonal(f.base, "members", { chat_id: group, limit: 2 });
  assert(result.total === 3 && result.truncated === true && result.members.length === 2);
  assert(result.members[0].is_me === true && result.members[1].is_creator === false);
  const all = await dispatchPersonal(f.base, "members", { chat_id: group, limit: 5 });
  assert(all.members.find((m: any) => m.is_creator).name === "Mock contact");
});
Deno.test("members of a direct chat are the account and the contact; unknown chats and bad limits fail", async () => {
  const f = personalFixture();
  const direct = await dispatchPersonal(f.base, "members", { chat_id: contact, limit: 5 });
  assert(direct.members.length === 2 && direct.members[0].is_me && direct.members[1].name === "Mock contact" && direct.total === 2);
  f.base.talk.getChats = async () => ({ chats: [] });
  await rejects(() => dispatchPersonal(f.base, "members", { chat_id: group, limit: 5 }));
  await rejects(() => dispatchPersonal(f.base, "members", { chat_id: group, limit: 201 }));
  await rejects(() => dispatchPersonal(f.base, "members", { chat_id: "not-a-chat", limit: 5 }));
});
Deno.test("personal mutations and oversized limits are rejected", async () => {
  const f = personalFixture();
  for (const operation of ["send", "send_message", "personal_send", "login", "join", "mark_read", "registerE2EEKeyPair"]) {
    await rejects(() => dispatchPersonal(f.base, operation, {}));
  }
  await rejects(() => dispatchPersonal(f.base, "messages", { chat_id: contact, count: 201 }));
});

Deno.test("Talk method guard rejects all writes and cross-service routes", async () => {
  const calls: string[] = [], base = { request: { request: async (_: any, method: string) => calls.push(method) } };
  guardRequests(base);
  await (base.request.request as any)([], "getProfile", 4, true, "/S4");
  for (const method of ["sendMessage", "sendChatChecked", "getMessageReadRange", "joinSquare", "registerE2EEKey", "registerE2EEGroupKey", "qrCodeLoginV2ForSecure"]) {
    await rejects(() => (base.request.request as any)([], method, 4, true, "/S4"));
  }
  await rejects(() => (base.request.request as any)([], "getProfile", 4, true, "/SQ1"));
  assert(calls.join() === "getProfile");
});
Deno.test("Talk send capability permits exactly one request and is then revoked", async () => {
  const calls: string[] = [], base = { request: { request: async (_: any, method: string) => calls.push(method) } };
  guardRequests(base);
  const send = () => (base.request.request as any)([], "sendMessage", 4, true, "/S4");
  await rejects(send);
  await withPersonalSend(base, async () => {
    await rejects(() => (base.request.request as any)([], "sendMessage", 4, true, "/SQ1"));
    await send();
    await rejects(send);
    await rejects(() => withPersonalSend(base, send));
  });
  await rejects(send);
  assert(calls.join() === "sendMessage");
});
Deno.test("failed Talk send consumes its capability without permitting retry", async () => {
  let calls = 0;
  const base = { request: { request: async () => { calls++; throw new Error("private upstream detail"); } } };
  guardRequests(base);
  const send = () => (base.request.request as any)([], "sendMessage", 4, true, "/S4");
  await withPersonalSend(base, async () => { await rejects(send); await rejects(send); });
  await rejects(send);
  assert(calls === 1);
});
Deno.test("login capabilities cannot permit Talk sending", async () => {
  const base = { request: { request: async () => { throw new Error("MUTATION"); } } };
  guardRequests(base, true);
  await withPersonalSend(base, async () => {
    await rejects(() => (base.request.request as any)([], "sendMessage", 4, true, "/S4"));
  });
});
Deno.test("login guard rejects E2EE registration and Square posting", async () => {
  const base = { request: { request: async () => {} } }; guardRequests(base, true);
  for (const [method, path] of [["registerE2EEKey", "/S4"], ["sendMessage", "/SQ1"], ["getRecentMessagesV2", "/S4"]]) {
    await rejects(() => (base.request.request as any)([], method, 4, true, path));
  }
});
Deno.test("runtime route guard rejects login, uploads and arbitrary URLs before networking", async () => {
  const fetch = guardedFetch();
  for (const [url, method] of [
    ["https://legy.line-apps.com/acct/lgn/sq/v1", "POST"], ["https://legy.line-apps.com/SQ1", "POST"],
    ["https://obs.line-apps.com/r/talk/m/101/object_info.obs", "GET"], ["https://obs.line-apps.com/r/talk/m/101", "POST"],
    ["https://obs.line-apps.com/r/talk/emx/mock", "GET"], ["https://obs.line-apps.com/r/myhome/h/mock", "GET"],
    // Videos, voice messages and files are never downloaded.
    ["https://obs.line-apps.com/r/talk/emv/mock", "GET"], ["https://obs.line-apps.com/r/talk/ema/mock", "GET"],
    ["https://obs.line-apps.com/r/talk/emf/mock", "GET"],
    ["https://obs.line-apps.com/r/g2/m/202?x=1", "GET"],
    ["https://example.invalid/S4", "POST"], ["http://legy.line-apps.com/S4", "POST"],
    ["https://legy.line-apps.com:8443/S4", "POST"], ["https://legy.line-apps.com/S4?redirect=other", "POST"],
  ]) {
    let error: unknown;
    try { await fetch(url, { method }); } catch (e) { error = e; }
    assert(error instanceof Error && error.message === "route_refused");
  }
});
Deno.test("runtime route guard lets only photo downloads reach obs", async () => {
  const fetch = guardedFetch();
  for (const url of ["https://obs.line-apps.com/r/talk/m/101", "https://obs.line-apps.com/r/g2/m/202/preview",
                     "https://obs.line-apps.com/r/talk/emi/mock_OID-1__ud-preview"]) {
    let error: unknown;
    try { await fetch(url, { method: "GET" }); } catch (e) { error = e; }
    // The test has no network permission, so a permitted route fails at the socket instead.
    assert(error instanceof Error && error.message !== "route_refused");
  }
  await rejects(() => guardedFetch(true)("https://obs.line-apps.com/r/talk/m/101", { method: "GET" }));
});

function sendFixture(chatId = contact) {
  const f = personalFixture(), sends: any[] = [], encryptions: any[] = [];
  const args: any = { chat_id: chatId, text: "Exact 日本語 fixture", sequence: 42 };
  const chunks = [Buffer.alloc(16, 1), Buffer.alloc(32, 2), Buffer.alloc(12, 3), Buffer.alloc(4, 4), Buffer.alloc(4, 5)];
  f.base.e2ee.encryptE2EEMessage = async (...args: any[]) => { encryptions.push(args); return chunks; };
  f.base.talk.sendMessage = async (value: any) => {
    sends.push(value);
    return { id: "123", createdTime: 1700000000123, to: value.to, contentType: "NONE", chunks: value.chunks };
  };
  return { ...f, args, chunks, sends, encryptions };
}
Deno.test("send destination reads are no longer an operation; legacy rooms are not sendable", async () => {
  const f = sendFixture();
  await rejects(() => dispatchPersonal(f.base, "send_destination", { chat_id: contact }));
  Object.assign(f.args, { chat_id: "r" + "c".repeat(32) });
  assert((await sendPersonalMessage(f.base, f.args)).state === "not_sent_destination_changed");
  assert(f.sends.length === 0 && f.encryptions.length === 0);
});
for (const chatId of [contact, group]) {
  Deno.test(`personal ${chatId === contact ? "direct" : "group"} text uses SDK E2EE without plaintext or retries`, async () => {
    const f = sendFixture(chatId), result = await sendPersonalMessage(f.base, f.args);
    assert(JSON.stringify(result) === JSON.stringify({ state: "sent_confirmed", message_id: "123", created_ms: 1700000000123 }));
    assert(f.sends.length === 1 && f.encryptions.length === 1);
    assert(f.encryptions[0][0] === chatId && f.encryptions[0][1] === f.args.text && f.encryptions[0][2] === "NONE");
    const sent = f.sends[0];
    assert(sent.to === chatId && sent.e2ee === true && !Object.hasOwn(sent, "text"));
    assert(sent.chunks === f.chunks && sent.contentMetadata.e2eeVersion === "2" && f.base.reqseqs.talk === f.args.sequence);
    assert(!f.calls.some(c => c[0] === "receipt-read" || c[0] === "recent"));
  });
}
Deno.test("a quote reply names the answered message and reports whether LINE kept the quote", async () => {
  const f = sendFixture(group); f.args.reply_to = "9007199254740993";
  f.base.talk.sendMessage = async (value: any) => {
    f.sends.push(value);
    return { id: "123", to: value.to, contentType: "NONE", chunks: value.chunks, relatedMessageId: value.relatedMessageId };
  };
  const result: any = await sendPersonalMessage(f.base, f.args);
  assert(result.state === "sent_confirmed" && result.reply_confirmed === true);
  assert(f.sends.length === 1 && f.sends[0].relatedMessageId === "9007199254740993" && f.sends[0].e2ee === true && !Object.hasOwn(f.sends[0], "text"));
});
Deno.test("a quote that LINE did not echo is a confirmed send with reply_confirmed false, never a retry", async () => {
  const f = sendFixture(); f.args.reply_to = "55";
  const result: any = await sendPersonalMessage(f.base, f.args);
  assert(result.state === "sent_confirmed" && result.reply_confirmed === false && f.sends.length === 1);
});
Deno.test("a plain send carries no reply fields", async () => {
  const f = sendFixture(), result: any = await sendPersonalMessage(f.base, f.args);
  assert(!Object.hasOwn(result, "reply_confirmed") && !Object.hasOwn(f.sends[0], "relatedMessageId"));
});
Deno.test("an invalid reply target is refused before encryption", async () => {
  for (const reply_to of ["", "0", "abc", "9223372036854775808", 5 as any]) {
    const f = sendFixture(); f.args.reply_to = reply_to;
    assert((await sendPersonalMessage(f.base, f.args)).state === "not_sent_destination_changed");
    assert(f.sends.length === 0 && f.encryptions.length === 0);
  }
});
Deno.test("personal send skips destination re-reads", async () => {
  const f = sendFixture(); f.base.talk.getContacts = async () => { throw new Error("must not read contacts"); };
  f.base.talk.getAllChatMids = async () => { throw new Error("must not read memberships"); };
  assert((await sendPersonalMessage(f.base, f.args)).state === "sent_confirmed");
});
Deno.test("personal saved identity is verified before the first send", async () => {
  const f = sendFixture(); f.base.talk.getProfile = async () => ({ mid: contact });
  await rejects(() => sendPersonalMessage(f.base, f.args));
  assert(f.sends.length === 0 && f.encryptions.length === 0);
});
Deno.test("personal encryption failure never falls back to plaintext", async () => {
  for (const ready of [true, false]) {
    const f = sendFixture(group); f.base.bridgeE2EEReady = ready;
    f.base.e2ee.encryptE2EEMessage = async () => { throw new Error("No usable shared key"); };
    assert((await sendPersonalMessage(f.base, f.args)).state === "not_sent_e2ee_unavailable");
    assert(f.sends.length === 0);
  }
});
Deno.test("personal invalid ciphertext is not sent", async () => {
  const f = sendFixture(); f.base.e2ee.encryptE2EEMessage = async () => [];
  assert((await sendPersonalMessage(f.base, f.args)).state === "not_sent_e2ee_unavailable");
  assert(f.sends.length === 0);
});
Deno.test("personal send exception has an unknown outcome without SDK resend", async () => {
  const f = sendFixture();
  f.base.talk.sendMessage = async (args: any) => { f.sends.push(args); throw new Error("private message or token"); };
  assert(JSON.stringify(await sendPersonalMessage(f.base, f.args)) === JSON.stringify({ state: "send_outcome_unknown_no_retry" }));
  assert(f.sends.length === 1 && f.sends[0].e2ee === true);
});
Deno.test("personal confirmation requires exact destination and encrypted envelope", async () => {
  for (const response of [{ id: "123", to: contact }, { id: "123", to: group },
    { id: "123", to: contact, chunks: [Buffer.alloc(16)] }]) {
    const f = sendFixture(); f.base.talk.sendMessage = async () => response;
    assert((await sendPersonalMessage(f.base, f.args)).state === "send_outcome_unknown_no_retry");
  }
});
Deno.test("confirmed personal metadata never includes text or sessions and omits unavailable timestamp", async () => {
  const f = sendFixture();
  f.base.talk.sendMessage = async (args: any) => ({ id: "123", to: args.to, chunks: args.chunks,
    text: "Private acknowledgment text", session: "Private acknowledgment session" });
  assert(JSON.stringify(await sendPersonalMessage(f.base, f.args)) === JSON.stringify({ state: "sent_confirmed", message_id: "123" }));
});
Deno.test("personal synthetic or unusable acknowledgment IDs never become confirmed metadata", async () => {
  for (const id of [undefined, "synthetic:fixture", "0", "0123", "-1", 123, "9223372036854775808"]) {
    const f = sendFixture(); f.base.talk.sendMessage = async (args: any) => ({ id, to: args.to, chunks: args.chunks });
    assert(JSON.stringify(await sendPersonalMessage(f.base, f.args)) === JSON.stringify({ state: "send_outcome_unknown_no_retry" }));
  }
});
Deno.test("personal text and sequence limits reject before any send or key lookup", async () => {
  for (const change of [{ text: "" }, { text: "😀".repeat(2501) }, { sequence: 0 }, { sequence: 2147483648 }, { sequence: 1.5 }]) {
    const f = sendFixture(); Object.assign(f.args, change);
    assert((await sendPersonalMessage(f.base, f.args)).state === "not_sent_destination_changed");
    assert(f.sends.length === 0 && f.encryptions.length === 0);
  }
});
Deno.test("notification hints detect mentions of the account, @all and replies", () => {
  const me = (mid: string) => mid === contact;
  assert(mentionsMe({ MENTION: JSON.stringify({ MENTIONEES: [{ S: "0", E: "3", M: contact }] }) }, me));
  assert(mentionsMe({ MENTION: { MENTIONEES: [{ S: "0", E: "4", A: "1" }] } }, me));
  assert(!mentionsMe({ MENTION: JSON.stringify({ MENTIONEES: [{ M: "u" + "f".repeat(32) }] }) }, me));
  assert(!mentionsMe({ MENTION: "not json" }, me) && !mentionsMe(undefined, me));
  assert(replyTo({ relatedMessageId: "123", messageRelationType: "REPLY" }) === "123");
  assert(replyTo({ relatedMessageId: "123", messageRelationType: 3 }) === "123");
  assert(replyTo({ relatedMessageId: "123", messageRelationType: "FORWARD" }) === null && replyTo({}) === null);
});
Deno.test("a send to our own chat is confirmed by its sender although LINE readdresses it", async () => {
  const f = sendFixture(me);
  f.base.talk.sendMessage = async (value: any) => ({ id: "124", from: me, to: "u" + "e".repeat(32), contentType: "NONE", chunks: value.chunks });
  assert((await sendPersonalMessage(f.base, f.args)).state === "sent_confirmed");
  const other = sendFixture();
  other.base.talk.sendMessage = async (value: any) => ({ id: "125", from: me, to: "u" + "e".repeat(32), contentType: "NONE", chunks: value.chunks });
  assert((await sendPersonalMessage(other.base, other.args)).state === "send_outcome_unknown_no_retry");
});
Deno.test("a direct send acknowledged without a recipient is confirmed by its sender and exact ciphertext", async () => {
  // Real DESKTOPWIN acknowledgments of direct messages carry from, id, createdTime and chunks but no to.
  const f = sendFixture();
  f.base.talk.sendMessage = async (value: any) => ({ id: "126", from: me, createdTime: 1700000000126, contentType: "NONE", chunks: value.chunks });
  assert(JSON.stringify(await sendPersonalMessage(f.base, f.args)) === JSON.stringify({ state: "sent_confirmed", message_id: "126", created_ms: 1700000000126 }));
  for (const response of [{ from: contact }, {}, { from: me, chunks: [Buffer.alloc(16, 1)] }]) {
    const other = sendFixture();
    other.base.talk.sendMessage = async (value: any) => ({ id: "127", contentType: "NONE", chunks: value.chunks, ...response });
    assert((await sendPersonalMessage(other.base, other.args)).state === "send_outcome_unknown_no_retry");
  }
});
Deno.test("personal unsend sends the message ID once with the bridge sequence", async () => {
  const f = personalFixture(), calls: any[] = [];
  f.base.talk.unsendMessage = async (value: any) => { calls.push(value); };
  const args = { chat_id: contact, message_id: "638846355892863099", sequence: 43 };
  assert((await unsendPersonalMessage(f.base, args)).state === "unsent");
  assert(calls.length === 1 && calls[0].seq === 43 && calls[0].messageId === args.message_id && f.base.reqseqs.talk === 43);
  f.base.talk.unsendMessage = async () => { throw new Error("refused"); };
  const refused = await unsendPersonalMessage(f.base, args);
  assert(refused.state === "not_unsent" && refused.reason === "upstream_failed");
  assert((await unsendPersonalMessage(f.base, { ...args, message_id: "abc" })).state === "not_unsent" && calls.length === 1);
});
Deno.test("Talk unsend needs the same once-only permit as a send", async () => {
  const f = personalFixture(), requests: string[] = [];
  f.base.request = { request: async (_value: any, method: string) => { requests.push(method); return {}; } };
  guardRequests(f.base);
  await rejects(() => f.base.request.request({}, "unsendMessage", 4, true, "/S4"));
  await withPersonalSend(f.base, () => f.base.request.request({}, "unsendMessage", 4, true, "/S4"));
  assert(requests.join() === "unsendMessage");
});
