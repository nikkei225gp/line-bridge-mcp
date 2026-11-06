// Run without network, process, environment or credential-file permissions.
import { dispatch } from "../src/line_bridge/js/openchat.ts";
import { pathToFileURL } from "node:url";

function assert(value: unknown) { if (!value) throw new Error("Fixture assertion failed"); }

function fixture() {
  const calls: string[] = [];
  const chat = { squareChatMid: "fixture-room", squareMid: "fixture-square", name: "Fixture room" };
  const member = { squareChatMid: chat.squareChatMid, squareMemberMid: "fixture-member", membershipState: 1 };
  const base: any = {
    reqseqs: {},
    square: {
      getJoinedSquares: async () => { calls.push("joined"); return { squares: [{ mid: "fixture-square", name: "Fixture community" }], members: { x: { squareMid: "fixture-square", squareMemberMid: "fixture-member" } } }; },
      fetchMyEvents: async () => { calls.push("events"); return { events: [{ payload: { notifiedCreateSquareChatMember: { chat, chatMember: member } } }] }; },
      fetchSquareChatEvents: async () => ({ events: [], syncToken: "fixture-cursor" }),
      sendMessage: async (args: any) => { calls.push("send"); return { createdSquareMessage: { message: { id: "123456", createdTime: 1234, to: args.squareChatMid, text: args.text } } }; },
    },
    request: { request: async () => ({ events: [] }) },
  };
  const structures = { SquareService_fetchSquareChatEvents_args: (value: any) => value };
  const args = { action: "send", chat_id: "fixture-room", text: "Exact fixture text", sequence: 7 };
  return { base, structures, args, calls, chat, member };
}

Deno.test("list does not send", async () => {
  const f = fixture(), result = await dispatch(f.base, f.structures, { action: "list" });
  assert(result.chats?.length === 1); assert(!f.calls.includes("send"));
});
Deno.test("read does not send or mark read", async () => {
  const f = fixture(), result = await dispatch(f.base, f.structures, { action: "messages", chat_id: "fixture-room", limit: 1 });
  assert(result.latestGuaranteed === false); assert(!f.calls.includes("send"));
});
const read = { action: "messages", chat_id: "fixture-room", limit: 1 };
Deno.test("consecutive messages reuse memberships until TTL expires", async () => {
  const f = fixture(), now = Date.now; let clock = now();
  Date.now = () => clock;
  try {
    await dispatch(f.base, f.structures, read);
    await dispatch(f.base, f.structures, read);
    assert(f.calls.filter(c => c === "joined").length === 1 && f.calls.filter(c => c === "events").length === 1);
    clock += 60000; await dispatch(f.base, f.structures, read);
    assert(f.calls.filter(c => c === "joined").length === 2);
  } finally { Date.now = now; }
});
Deno.test("unknown cached room refetches exactly once before failing", async () => {
  const f = fixture(); await dispatch(f.base, f.structures, read);
  try { await dispatch(f.base, f.structures, { ...read, chat_id: "foreign-room" }); throw new Error("allowed"); }
  catch (error) { assert(error instanceof Error && error.message === "membership_unconfirmed"); }
  assert(f.calls.filter(c => c === "joined").length === 2 && f.calls.filter(c => c === "events").length === 2);
});
Deno.test("new room is found by refreshing cached memberships", async () => {
  const f = fixture(); await dispatch(f.base, f.structures, { action: "list" });
  f.chat.squareChatMid = f.member.squareChatMid = "new-room";
  assert((await dispatch(f.base, f.structures, { ...read, chat_id: "new-room" })).chat.id === "new-room");
  assert(f.calls.filter(c => c === "joined").length === 2);
});
Deno.test("membership errors are not cached", async () => {
  const f = fixture(), original = f.base.square.fetchMyEvents; let calls = 0;
  f.base.square.fetchMyEvents = async () => { if (++calls === 1) throw new Error("failed"); return await original(); };
  try { await dispatch(f.base, f.structures, read); } catch { /* Retry below. */ }
  await dispatch(f.base, f.structures, read);
  assert(calls === 2);
});
Deno.test("unknown room read fails", async () => {
  const f = fixture(); let rejected = false;
  try { await dispatch(f.base, f.structures, { ...read, chat_id: "foreign-room" }); } catch { rejected = true; }
  assert(rejected);
});
Deno.test("unconfirmed own membership read fails closed", async () => {
  const f = fixture(); f.member.membershipState = 2; let rejected = false;
  try { await dispatch(f.base, f.structures, read); } catch { rejected = true; }
  assert(rejected);
});
Deno.test("truncated membership read fails closed", async () => {
  const f = fixture(); f.base.square.fetchMyEvents = async () => ({ events: [], continuationToken: "more" }); let rejected = false;
  try { await dispatch(f.base, f.structures, read); } catch { rejected = true; }
  assert(rejected);
});
Deno.test("send goes straight to the room without membership or name reads", async () => {
  const f = fixture(); assert((await dispatch(f.base, f.structures, f.args)).state === "sent_confirmed");
  assert(!f.calls.includes("joined")); assert(f.calls.filter(x => x === "send").length === 1);
});
Deno.test("exact text and sequence sent once", async () => {
  const f = fixture(), result = await dispatch(f.base, f.structures, f.args);
  assert(result.state === "sent_confirmed" && result.message_id === "123456" && result.created_ms === 1234);
  assert(f.calls.filter(x => x === "send").length === 1); assert(f.base.reqseqs.sq === 7);
});
Deno.test("invalid OpenChat acknowledgement ID is unknown without retry", async () => {
  const f = fixture(); f.base.square.sendMessage = async (args: any) => {
    f.calls.push("send"); return { createdSquareMessage: { message: { id: "synthetic", to: args.squareChatMid, text: args.text } } };
  };
  assert((await dispatch(f.base, f.structures, f.args)).state === "send_outcome_unknown_no_retry");
  assert(f.calls.filter(x => x === "send").length === 1);
});
Deno.test("timeout never automatically retried", async () => {
  const f = fixture(); f.base.square.sendMessage = async () => { f.calls.push("send"); throw new Error("unknown outcome"); };
  assert((await dispatch(f.base, f.structures, f.args)).state === "send_outcome_unknown_no_retry");
  assert(f.calls.filter(x => x === "send").length === 1);
});
Deno.test("unexpected confirmation is uncertain not success", async () => {
  const f = fixture(); f.base.square.sendMessage = async () => ({ createdSquareMessage: { message: { id: "fixture", to: "other", text: f.args.text } } });
  assert((await dispatch(f.base, f.structures, f.args)).state === "send_outcome_unknown_no_retry");
});
Deno.test("members lists names and roles of a joined room; member IDs stay inside", async () => {
  const f = fixture(); let request: any;
  f.base.square.getSquareChatMembers = async (args: any) => { request = args; f.calls.push("members"); return { continuationToken: "", squareChatMembers: [
    { squareMemberMid: "fixture-member", displayName: "Me", role: "ADMIN" }, { squareMemberMid: "other-member", displayName: "Other", role: 10 },
    { squareMemberMid: "third-member", displayName: "Third", role: "CO_ADMIN" }] }; };
  const result = await dispatch(f.base, f.structures, { action: "members", chat_id: "fixture-room", limit: 50 });
  assert(request.squareChatMid === "fixture-room" && result.truncated === false);
  assert(JSON.stringify(result.members) === JSON.stringify([{ name: "Me", role: "admin", is_me: true },
    { name: "Other", role: "member", is_me: false }, { name: "Third", role: "co_admin", is_me: false }]));
  assert(!JSON.stringify(result.members).includes("other-member") && !f.calls.includes("send"));
});
Deno.test("members are cut at the limit and say so", async () => {
  const f = fixture(); let pages = 0;
  f.base.square.getSquareChatMembers = async () => { pages++; return { continuationToken: "more", squareChatMembers: [
    { squareMemberMid: "a", displayName: "A", role: 10 }, { squareMemberMid: "b", displayName: "B", role: 10 }] }; };
  const result = await dispatch(f.base, f.structures, { action: "members", chat_id: "fixture-room", limit: 3 });
  assert(result.members.length === 3 && result.truncated === true && pages === 2);
  f.base.square.getSquareChatMembers = async () => ({ squareChatMembers: [{ squareMemberMid: "a", displayName: "A", role: 10 }] });
  assert((await dispatch(f.base, f.structures, { action: "members", chat_id: "fixture-room", limit: 1 })).truncated === false);
});
Deno.test("members of a room that is not confirmed joined, or with a bad limit, are never requested", async () => {
  const f = fixture(); let asked = 0;
  f.base.square.getSquareChatMembers = async () => { asked++; return { squareChatMembers: [] }; };
  for (const args of [{ chat_id: "foreign-room", limit: 5 }, { chat_id: "fixture-room", limit: 0 }, { chat_id: "fixture-room", limit: 201 }]) {
    let rejected = false;
    try { await dispatch(f.base, f.structures, { action: "members", ...args }); } catch { rejected = true; }
    assert(rejected);
  }
  assert(asked === 0);
});
Deno.test("a quote reply is sent with the related message and reports the echoed quote", async () => {
  const f = fixture(); let sent: any;
  f.base.square.sendMessage = async (args: any) => { sent = args; return { createdSquareMessage: { message: { id: "777", to: args.squareChatMid, text: args.text, relatedMessageId: args.relatedMessageId } } }; };
  const result = await dispatch(f.base, f.structures, { ...f.args, reply_to: "4242" });
  assert(result.state === "sent_confirmed" && result.reply_confirmed === true && sent.relatedMessageId === "4242" && sent.contentType === 0);
  f.base.square.sendMessage = async (args: any) => ({ createdSquareMessage: { message: { id: "778", to: args.squareChatMid, text: args.text } } });
  assert((await dispatch(f.base, f.structures, { ...f.args, reply_to: "4242" })).reply_confirmed === false);
});
Deno.test("a plain OpenChat send has no reply fields; reply with thread or a bad ID is refused unsent", async () => {
  const f = fixture(); let sent: any;
  f.base.square.sendMessage = async (args: any) => { sent = args; f.calls.push("send"); return { createdSquareMessage: { message: { id: "779", to: args.squareChatMid, text: args.text } } }; };
  const plain = await dispatch(f.base, f.structures, f.args);
  assert(!Object.hasOwn(plain, "reply_confirmed") && !Object.hasOwn(sent, "relatedMessageId"));
  const before = f.calls.length;
  for (const extra of [{ reply_to: "1", thread_message_id: "2" }, { reply_to: "abc" }, { reply_to: "0" }, { reply_to: 5 }]) {
    assert((await dispatch(f.base, f.structures, { ...f.args, ...extra })).state === "not_sent_destination_changed");
  }
  assert(f.calls.length === before);
});

Deno.test("baseline polling does not post", async () => {
  const f = fixture(); const r = await dispatch(f.base, f.structures, { action: "poll", chat_id: "fixture-room" });
  assert(r.checkpoint?.baseline === false); assert(!f.calls.includes("send"));
  f.base.square.fetchSquareChatEvents = async () => ({ syncToken: "new", events: [{ payload: { receiveMessage: {
    squareChatMid: "fixture-room", squareMessage: { message: { id: "own-fixture", from: "fixture-member", createdTime: 1000, text: "own post" } },
  } } }] });
  const own = await dispatch(f.base, f.structures, { action: "poll", chat_id: "fixture-room", checkpoint: { baseline: false, syncToken: "previous" } });
  assert(own.messages?.[0].from_me === true && own.messages?.[0].sender_identity_known === true);
});
Deno.test("oversized UTF16 text blocked", async () => {
  const f = fixture(); assert((await dispatch(f.base, f.structures, { ...f.args, text: "😀".repeat(2501) })).state === "not_sent_destination_changed");
  assert(!f.calls.includes("send"));
});

Deno.test("patched Thrift real codec preserves fixture values", async () => {
  const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
  const { Thrift, Protocols } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/mod.ts`).href);
  const codec = new Thrift();
  for (const key of [3, 4]) {
    const binary = codec.writeThrift([[11, 1, "日本語fixture"], [8, 2, 42], [10, 3, -17]], "fixture", Protocols[key]);
    const result = codec.readThrift(binary, Protocols[key]);
    assert(result.data[1] === "日本語fixture" && result.data[2] === 42 && result.data[3] === -17);
  }
});

const memberMid = (n: number) => "p" + String(n).repeat(32), botMid = "v" + "9".repeat(32);
function senderFixture(events: any[]) {
  const f = fixture(), lookups: string[][] = [], state = { revision: 1 };
  f.base.request.request = async () => ({ events: events.map(([from, revision, name], i) => ({ payload: { receiveMessage: {
    squareChatMid: "fixture-room", senderRevision: revision, senderDisplayName: name,
    squareMessage: { message: { id: String(100 + i), from, createdTime: 1000 + i, text: "fixture", contentType: 0 } } } } })) });
  f.base.square.getSquareMembers = async (args: any) => {
    lookups.push([...args.request.mids]);
    if (args.request.mids.includes(botMid)) throw new Error("ILLEGAL_ARGUMENT");
    return { members: Object.fromEntries(args.request.mids.filter((m: string) => m !== memberMid(3))
      .map((m: string) => [m, { squareMemberMid: m, displayName: "Name " + m.slice(1, 2) + " r" + state.revision, revision: state.revision }])) };
  };

  return { ...f, lookups, state };
}
const page = { action: "messages", chat_id: "fixture-room", limit: 10 };
Deno.test("missing sender names come from one batched member lookup, bots excluded", async () => {
  const f = senderFixture([[memberMid(1), 1, ""], [memberMid(2), 1, ""], [memberMid(1), 1, ""], [botMid, 0, ""], [memberMid(4), 1, "Shown name"]]);
  const names = (await dispatch(f.base, f.structures, page)).messages.map((m: any) => m.sender);
  assert(names.join("|") === "Name 1 r1|Name 2 r1|Name 1 r1||Shown name");
  assert(f.lookups.length === 1 && f.lookups[0].join() === [memberMid(1), memberMid(2)].join());
});
Deno.test("cached names are reused until the sender's revision moves", async () => {
  const f = senderFixture([[memberMid(1), 1, ""], [memberMid(3), 1, ""]]);
  await dispatch(f.base, f.structures, page);
  await dispatch(f.base, f.structures, page);
  // A member that left (3) is remembered as unnamed instead of being asked for again.
  assert(f.lookups.length === 1);
  f.state.revision = 2;
  f.base.request.request = senderFixture([[memberMid(1), 2, ""]]).base.request.request;
  assert((await dispatch(f.base, f.structures, page)).messages[0].sender === "Name 1 r2" && f.lookups.length === 2);
});
Deno.test("a failed member lookup still returns the messages", async () => {
  const f = senderFixture([[memberMid(1), 1, ""]]);
  f.base.square.getSquareMembers = async () => { throw new Error("upstream"); };
  const result = await dispatch(f.base, f.structures, page);
  assert(result.messages.length === 1 && result.messages[0].sender === "");
});

const threadMid = "t" + "a".repeat(32);
function threadFixture(exists = true) {
  const f = fixture(), requests: any[] = [];
  f.base.square.getSquareThreadMid = async (args: any) => {
    f.calls.push("mid:" + args.request.chatMid + ":" + args.request.messageId); return { threadMid };
  };
  f.base.square.getSquareThread = async () => {
    if (!exists) throw Object.assign(new Error("fixture"), { data: { errorCode: "NOT_FOUND" } });
    return { squareThread: { threadMid, state: "ALIVE" } };
  };
  f.base.square.sendSquareThreadMessage = async (args: any) => {
    f.calls.push("thread-send"); requests.push(args.request);
    const m = args.request.threadMessage.message;
    return { createdThreadMessage: { message: { id: "654321", createdTime: 2000, to: m.to, text: m.text } } };
  };
  f.base.request.request = async (value: any) => {
    requests.push(value.request);
    return { events: [{ payload: { receiveMessage: { squareChatMid: "fixture-room", threadTotalMessageCount: 3, threadLastMessageAt: 1500,
      squareMessage: { threadInfo: { chatThreadMid: threadMid, threadRoot: !value.request.threadMid },
        message: { id: "500", from: "fixture-member", createdTime: 1000, text: "fixture", contentType: 0 } } } } }] };
  };
  return { ...f, requests };
}
Deno.test("a thread root carries its reply count and replies are read from the thread", async () => {
  const f = threadFixture();
  const root = (await dispatch(f.base, f.structures, page)).messages[0];
  assert(root.thread?.reply_count === 3 && root.thread?.last_reply_ms === 1500);
  const result = await dispatch(f.base, f.structures, { action: "thread_messages", chat_id: "fixture-room", message_id: "500", limit: 10 });
  assert(result.thread_exists === true && result.messages.length === 1 && result.messages[0].thread === undefined);
  assert(f.calls.includes("mid:fixture-room:500") && f.requests.at(-1).threadMid === threadMid);
  assert(!f.calls.includes("send") && !f.calls.includes("thread-send"));
});
Deno.test("a message without a thread reads as no thread", async () => {
  const f = threadFixture(false);
  const result = await dispatch(f.base, f.structures, { action: "thread_messages", chat_id: "fixture-room", message_id: "500", limit: 10 });
  assert(result.thread_exists === false && result.messages.length === 0 && f.requests.length === 0);
});
Deno.test("a thread reply goes to the thread once and is confirmed against it", async () => {
  const f = threadFixture(), result = await dispatch(f.base, f.structures, { ...f.args, thread_message_id: "500" });
  assert(result.state === "sent_confirmed" && result.message_id === "654321");
  const request = f.requests[0];
  assert(request.chatMid === "fixture-room" && request.threadMid === threadMid && request.reqSeq === 7);
  assert(request.threadMessage.message.to === threadMid && request.threadMessage.message.text === f.args.text);
  assert(f.calls.filter(x => x === "thread-send").length === 1 && !f.calls.includes("send"));
});
Deno.test("a failed thread lookup sends nothing", async () => {
  const f = threadFixture(); f.base.square.getSquareThreadMid = async () => { throw new Error("upstream"); };
  assert((await dispatch(f.base, f.structures, { ...f.args, thread_message_id: "500" })).state === "not_sent_thread_unavailable");
  assert(!f.calls.includes("thread-send") && !f.calls.includes("send"));
});
Deno.test("a small read returns the newest end of LINE's oldest-first window in one request", async () => {
  const f = fixture(), requests: any[] = [];
  f.base.request.request = async (value: any) => { requests.push(value.request); return { events: Array.from({ length: 30 }, (_, i) => ({ payload: {
    receiveMessage: { squareChatMid: "fixture-room", senderDisplayName: "Fixture",
      squareMessage: { message: { id: String(700 + i), from: "fixture-member", createdTime: 1000 + i, text: "m" + i, contentType: 0 } } } } })),
    continuationToken: "ignored-by-line" }; };
  const result = await dispatch(f.base, f.structures, { action: "messages", chat_id: "fixture-room", limit: 5 });
  assert(result.messages.map((m: any) => m.text).join() === "m25,m26,m27,m28,m29" && result.truncated === true);
  assert(requests.length === 1 && requests[0].limit === 200 && requests[0].continuationToken === undefined);
});
function removeFixture() {
  const f = threadFixture(), removals: any[] = [];
  f.base.square.unsendMessage = async (request: any) => { removals.push(["unsend", request]); };
  f.base.square.destroyMessage = async (request: any) => { removals.push(["destroy", request]); };
  return { ...f, removals };
}
Deno.test("OpenChat unsend and delete go straight to LINE, threads included", async () => {
  const f = removeFixture();
  assert((await dispatch(f.base, f.structures, { action: "unsend", chat_id: "fixture-room", message_id: "501" })).state === "unsent");
  assert((await dispatch(f.base, f.structures, { action: "destroy", chat_id: "fixture-room", message_id: "502", thread_message_id: "500" })).state === "deleted");
  const [unsend, destroy] = f.removals;
  assert(unsend[0] === "unsend" && unsend[1].messageId === "501" && unsend[1].squareChatMid === "fixture-room" && unsend[1].threadMid === undefined);
  assert(destroy[0] === "destroy" && destroy[1].messageId === "502" && destroy[1].threadMid === threadMid);
  assert(!f.calls.includes("joined") && !f.calls.includes("send"));
});
Deno.test("a refused OpenChat delete reports a fixed reason", async () => {
  const f = removeFixture(); f.base.square.destroyMessage = async () => { throw new Error("FORBIDDEN fixture"); };
  const result = await dispatch(f.base, f.structures, { action: "destroy", chat_id: "fixture-room", message_id: "502" });
  assert(result.state === "not_deleted" && result.reason === "upstream_failed");
  const invalid = await dispatch(f.base, f.structures, { action: "unsend", chat_id: "fixture-room", message_id: "x" });
  assert(invalid.state === "not_unsent" && invalid.reason === "invalid_request");
});
