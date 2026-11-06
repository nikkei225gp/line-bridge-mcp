// Real dependency + real binary codec, fake fetch only. No network permission.
import { pathToFileURL } from "node:url";
import { dispatchPersonal, sendPersonalMessage } from "../src/line_bridge/js/personal.ts";
import { guardRequests } from "../src/line_bridge/js/desktop-policy.ts";
import { loginUnified } from "../src/line_bridge/js/login-unified.ts";
import { connectSquare } from "../src/line_bridge/js/openchat.ts";
import { dispatchDesktopSquare } from "../src/line_bridge/js/desktop.ts";
import crypto from "node:crypto";
import { Buffer } from "node:buffer";
const root = Deno.args[0].replaceAll("\\", "/").replace(/\/$/, "");
const { BaseClient } = await import(pathToFileURL(`${root}/packages/linejs/base/mod.ts`).href);
const { MemoryStorage } = await import(pathToFileURL(`${root}/packages/linejs/base/storage/memory.ts`).href);
const { Thrift, Protocols, LINEStruct } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/mod.ts`).href);
const { writeStruct } = await import(pathToFileURL(`${root}/packages/linejs/base/thrift/readwrite/write.ts`).href);
const me = "u" + "a".repeat(32), contact = "u" + "b".repeat(32), group = "c" + "c".repeat(32);
function assert(value: unknown) { if (!value) throw new Error("Codec fixture assertion failed"); }

function fixture() {
  const codec = new Thrift(), calls: any[] = [];
  const message = LINEStruct.Message({ from: contact, to: me, id: "9007199254740993", createdTime: 1700000000000,
    contentType: "NONE", toType: "USER", text: "日本語mock text", contentMetadata: {} });
  const base: any = new BaseClient({ device: "DESKTOPWIN", version: "26.5.0.3975", storage: new MemoryStorage(),
    legy: { encrypted: false }, fetch: async (input: any, init: any) => {
      const request = new Request(input, init);
      assert(new URL(request.url).pathname === "/S4");
      const decoded = codec.readThrift(new Uint8Array(await request.arrayBuffer()), Protocols[4]);
      calls.push(decoded);
      const method = decoded._info.fname;
      let fields;
      if (method === "getProfile") fields = [[12, 0, LINEStruct.Profile({ mid: me, displayName: "Mock user" })]];
      else if (method === "getContacts") fields = [[15, 0, [12, [[[11, 1, contact], [11, 22, "Mock contact"]]]]]];
      else if (method === "getRecentMessagesV2") fields = [[15, 0, [12, [message]]]];
      else if (method === "getPreviousMessagesV2WithRequest") fields = [[15, 0, [12, []]]];
      else if (method === "getMessageBoxes") fields = [[12, 0, [[15, 1, [12, [[[11, 1, contact], [10, 6, 2], [15, 7, [12, [message]]]]]]], [2, 2, false]]]];
      else throw new Error("Unexpected read method");
      return new Response(new Uint8Array(codec.writeThrift(fields, method, Protocols[4])).buffer);
    } });
  base.authToken = "mock-" + "x".repeat(30);
  base.bridgeExpectedMid = me;
  base.bridgeE2EEReady = true;
  guardRequests(base);
  return { base, calls };
}

Deno.test("real Talk codec preserves string message IDs and NONE text", async () => {
  const f = fixture(), rows = await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
  assert(rows.length === 1 && rows[0].id === "9007199254740993");
  assert(rows[0].text === "日本語mock text" && rows[0].content_type === "NONE");
  assert(rows[0].created_ms === 1700000000000 && rows[0].sender_name === "Mock contact");
});
Deno.test("real Talk codec encodes large historical cursor without precision loss", async () => {
  const f = fixture(); await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20 });
  await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "9007199254740993" });
  const request = f.calls.find(c => c._info.fname === "getPreviousMessagesV2WithRequest");
  assert(String(request.data[2][2][2]) === "9007199254740993");
  assert(Number(request.data[2][2][1]) === 1700000000000);
});
Deno.test("real Talk historical cursor resumes from archived timestamp on a fresh client", async () => {
  const f = fixture();
  await dispatchPersonal(f.base, "messages", { chat_id: contact, count: 20, before_message_id: "9007199254740993", before_created_ms: 1700000000000 });
  const request = f.calls.find(c => c._info.fname === "getPreviousMessagesV2WithRequest");
  assert(String(request.data[2][2][2]) === "9007199254740993" && Number(request.data[2][2][1]) === 1700000000000);
  assert(!f.calls.some(c => c._info.fname === "getRecentMessagesV2"));
});
Deno.test("real message-box codec supplies unread count and preview", async () => {
  const f = fixture(), chats = await dispatchPersonal(f.base, "chats", { limit: 1 });
  assert(chats.length === 1 && chats[0].unread === 2 && chats[0].name === "Mock contact");
  assert(chats[0].last_message.text === "日本語mock text");
});

Deno.test("real QR keychain unwrap survives in-memory session restore without key registration", async () => {
  const base: any = new BaseClient({ device: "DESKTOPWIN", version: "26.5.0.3975", storage: new MemoryStorage(),
    fetch: () => { throw new Error("Network forbidden"); } });
  const [loginSecret, loginPublic] = base.e2ee.createSqrSecret(true);
  const [remoteSecret, remotePublic] = base.e2ee.createSqrSecret(true);
  const [ownSecret, ownPublic] = base.e2ee.createSqrSecret(true);
  const plain = writeStruct([
    [15, 1, [12, [[[8, 2, 1], [11, 4, Buffer.from(ownPublic, "base64")], [11, 5, Buffer.from(ownSecret)]]]]],
  ], Protocols[4]);
  // Login keychains are bare compact structs, not RPC envelopes.
  const bare = Buffer.from(plain);
  const padded = Buffer.concat([bare, Buffer.alloc((16 - bare.length % 16) % 16)]);
  const shared = base.e2ee.generateSharedSecret(Buffer.from(remoteSecret), Buffer.from(loginPublic, "base64"));
  const cipher = crypto.createCipheriv("aes-256-cbc", base.e2ee.getSHA256Sum(Buffer.from(shared), "Key"),
    base.e2ee.xor(base.e2ee.getSHA256Sum(Buffer.from(shared), "IV")));
  cipher.setAutoPadding(false);
  const metadata = { keyId: "1", publicKey: remotePublic, encryptedKeyChain: Buffer.concat([
    cipher.update(padded), cipher.final(),
  ]).toString("base64"), e2eeVersion: "1" };
  base.e2ee.createSqrSecret = () => [loginSecret, "?secret=mock&e2eeVersion=1"];
  base.talk.getProfile = async () => ({ mid: me });
  base.talk.getE2EEPublicKeys = async () => [{ keyId: 1, keyData: Buffer.from(ownPublic, "base64") }];
  base.square.getJoinedSquares = async () => ({ squares: [] });
  base.loginProcess.createSession = async () => ({ 1: "mock" });
  base.loginProcess.createQrCodeForSecure = async () => ({ 1: "https://example.invalid/qr" });
  base.loginProcess.checkQrCodeVerified = async () => true;
  base.loginProcess.verifyCertificate = async () => {};
  base.loginProcess.qrCodeLoginV2ForSecure = async () => ({ 3: { 1: "mock-" + "x".repeat(30) }, 4: me, 6: metadata });
  base.e2ee.registerE2EEKeyPair = () => { throw new Error("Key registration forbidden"); };
  const saved = await loginUnified(base, () => {});
  const restored: any = new BaseClient({ device: "DESKTOPWIN", version: saved.version,
    storage: new MemoryStorage(saved.e2eeStorage), fetch: () => { throw new Error("Network forbidden"); } });
  const key = await restored.e2ee.getE2EESelfKeyDataByKeyId(1);
  assert(Buffer.from(key.privKey, "base64").equals(Buffer.from(ownSecret)));
  assert(Buffer.from(key.pubKey, "base64").equals(Buffer.from(ownPublic, "base64")));
});

Deno.test("real Square client keeps verified transport and rejects Talk access", async () => {
  const base: any = connectSquare({ device: "DESKTOPWIN", version: "26.5.0.3975",
    accessToken: "mock-" + "x".repeat(30), bridge_generation: "mock-desktop-codec-generation" }, true);
  assert(base.legy.encrypted === false && await base.storage.get("refreshToken") === undefined);
  let rejected = false;
  try { await base.talk.getProfile(); } catch { rejected = true; }
  assert(rejected);
});

async function sendFixture(chatId = contact, options: { sendErrorCode?: number; missingGroupKey?: boolean } = {}) {
  const codec = new Thrift(), calls: any[] = [], storage = new MemoryStorage();
  let wrappedGroupKey: Buffer, ownPublic: string, creatorPublic: string;
  let echoed: any;
  const base: any = new BaseClient({ device: "DESKTOPWIN", version: "26.5.0.3975", storage,
    legy: { encrypted: false }, fetch: async (input: any, init: any) => {
      const request = new Request(input, init), wire = new Uint8Array(await request.arrayBuffer());
      assert(new URL(request.url).pathname === "/S4");
      const decoded = codec.readThrift(wire, Protocols[4]), method = decoded._info.fname;
      calls.push(decoded);
      const ownKey = LINEStruct.Pb1_C13097n4({ version: 2, keyId: 1, keyData: Buffer.from(ownPublic, "base64") });
      const creatorKey = LINEStruct.Pb1_C13097n4({ version: 2, keyId: 2, keyData: Buffer.from(creatorPublic, "base64") });
      let fields;
      if (method === "getProfile") fields = [[12, 0, LINEStruct.Profile({ mid: me, displayName: "Mock user" })]];
      else if (method === "getContacts") fields = [[15, 0, [12, [[[11, 1, contact], [11, 22, "Mock contact"]]]]]];
      else if (method === "getAllChatMids") {
        assert(decoded.data[1][1] === true && decoded.data[1][2] === false);
        fields = [[12, 0, [[14, 1, [11, [group]]], [14, 2, [11, []]]]]];
      } else if (method === "getChats") fields = [[12, 0, [[15, 1, [12, [LINEStruct.Chat({ chatMid: group, chatName: "Mock group" })]]]]]];
      else if (method === "getE2EEPublicKeys") fields = [[15, 0, [12, [ownKey]]]];
      else if (method === "negotiateE2EEPublicKey") {
        assert(decoded.data[2] === contact);
        fields = [[12, 0, [[14, 1, [8, [0]]], [12, 2, creatorKey], [8, 3, 2]]]];
      } else if (method === "getLastE2EEGroupSharedKey") {
        assert(decoded.data[2] === 2 && decoded.data[3] === group);
        fields = options.missingGroupKey ? [[12, 1, [[8, 1, 5], [11, 2, "private missing key"]]]] :
          [[12, 0, [[8, 1, 2], [8, 2, 7], [11, 3, contact], [8, 4, 2], [11, 5, me], [8, 6, 1],
            [11, 7, wrappedGroupKey], [14, 8, [8, [0]]], [8, 9, 2]]]];
      } else if (method === "sendMessage") {
        const message = decoded.data[2];
        assert(decoded.data[1] === 42 && message[2] === chatId && message[15] === 0);
        assert(message[10] === undefined && message[20].length === 5);
        assert(message[18].e2eeVersion === "2" && message[18].contentType === "0" && message[18].e2eeMark === "2");
        assert(!Buffer.from(wire).includes(Buffer.from("Exact 日本語 fixture")));
        echoed = { from: me, to: chatId, toType: chatId === group ? "GROUP" : "USER", id: "9007199254740993", createdTime: 1700000000123,
          contentType: 0, chunks: message[20], contentMetadata: message[18] };
        fields = options.sendErrorCode ? [[12, 1, [[8, 1, options.sendErrorCode], [11, 2, "private send reason"]]]] :
          [[12, 0, LINEStruct.Message(echoed)]];
      } else throw new Error("Unexpected codec method");
      return new Response(new Uint8Array(codec.writeThrift(fields, method, Protocols[4])).buffer);
    } });
  const [ownSecret, ownPub] = base.e2ee.createSqrSecret(true), [creatorSecret, creatorPub] = base.e2ee.createSqrSecret(true);
  ownPublic = ownPub; creatorPublic = creatorPub;
  await storage.set("e2eeKeys:1", JSON.stringify({ keyId: 1, privKey: Buffer.from(ownSecret).toString("base64"), pubKey: ownPublic }));
  const shared = base.e2ee.generateSharedSecret(Buffer.from(ownSecret), Buffer.from(creatorPublic, "base64"));
  const cipher = crypto.createCipheriv("aes-256-cbc", base.e2ee.getSHA256Sum(Buffer.from(shared), "Key"),
    base.e2ee.xor(base.e2ee.getSHA256Sum(Buffer.from(shared), "IV")));
  // A group key already created by another member, wrapped to this account's login key.
  wrappedGroupKey = Buffer.concat([cipher.update(Buffer.from(creatorSecret)), cipher.final()]);
  base.authToken = "mock-" + "x".repeat(30);
  base.bridgeExpectedMid = me;
  base.bridgeE2EEReady = true;
  guardRequests(base);
  return { base, calls, storage, echoed: () => echoed, args: { chat_id: chatId,
    text: "Exact 日本語 fixture", sequence: 42 } };
}

for (const chatId of [contact, group]) {
  Deno.test(`real ${chatId === contact ? "direct" : "group"} E2EE text encrypts and sends once through the Talk codec`, async () => {
    const f = await sendFixture(chatId), result = await sendPersonalMessage(f.base, f.args);
    assert(JSON.stringify(result) === JSON.stringify({ state: "sent_confirmed", message_id: "9007199254740993", created_ms: 1700000000123 }));
    assert(f.calls.filter(c => c._info.fname === "sendMessage").length === 1);
    assert((await f.base.e2ee.decryptE2EETextMessage(f.echoed()))[0] === f.args.text);
    const methods = f.calls.map(c => c._info.fname);
    assert(!methods.some(m => /register|Checked|join|login|refresh|react|unsend/.test(m)));
    assert(await f.storage.get("refreshToken") === undefined && JSON.parse(await f.storage.get("reqseq")).talk === 43);
    if (chatId === group) assert(methods.includes("getLastE2EEGroupSharedKey"));
  });
}
Deno.test("real missing group key cannot register a key or send plaintext", async () => {
  const f = await sendFixture(group, { missingGroupKey: true });
  assert((await sendPersonalMessage(f.base, f.args)).state === "not_sent_e2ee_unavailable");
  const methods = f.calls.map(c => c._info.fname);
  assert(methods.includes("getLastE2EEGroupSharedKey") && !methods.includes("sendMessage"));
  assert(!methods.some(m => /register|join|login/.test(m)));
});
for (const code of [1, 82, 119]) {
  Deno.test(`real Talk send error ${code} never retries or refreshes`, async () => {
    const f = await sendFixture(contact, { sendErrorCode: code });
    assert((await sendPersonalMessage(f.base, f.args)).state === "send_outcome_unknown_no_retry");
    assert(f.calls.filter(c => c._info.fname === "sendMessage").length === 1);
    assert(await f.storage.get("refreshToken") === undefined);
    assert(!f.calls.some(c => /refresh|login/.test(c._info.fname)));
  });
}

function squareSendFixture(response: any) {
  const room = { squareChatMid: "fixture-room", squareMid: "fixture-square", name: "Fixture room" };
  const member = { squareChatMid: room.squareChatMid, squareMemberMid: "fixture-member", membershipState: 1 };
  const calls: any[] = [];
  const base: any = { square: {
    getJoinedSquares: async () => ({ squares: [{ mid: room.squareMid, name: "Fixture community" }],
      members: { mock: { squareMid: room.squareMid, squareMemberMid: member.squareMemberMid } } }),
    fetchMyEvents: async () => ({ events: [{ payload: { notifiedCreateSquareChatMember: { chat: room, chatMember: member } } }] }),
    sendMessage: async (args: any) => { calls.push(args); return { createdSquareMessage: { message: { to: room.squareChatMid, text: args.text, ...response } } }; },
  } };
  const args = { action: "send", chat_id: room.squareChatMid, text: "Exact fixture text", sequence: 42 };
  return { base, args, calls, original: base.square.sendMessage };
}
Deno.test("desktop Square send captures confirmed acknowledgment metadata without a second send", async () => {
  const f = squareSendFixture({ id: "9007199254740993", createdTime: 1700000000123, session: "Private mock session" });
  assert(JSON.stringify(await dispatchDesktopSquare(f.base, {}, f.args)) ===
    JSON.stringify({ state: "sent_confirmed", message_id: "9007199254740993", created_ms: 1700000000123 }));
  assert(f.calls.length === 1 && f.base.square.sendMessage === f.original);
});
Deno.test("desktop Square send does not fabricate IDs or confirm mismatched acknowledgment", async () => {
  for (const response of [{ id: "synthetic:fixture" }, { id: "0" }, { id: "9223372036854775808" },
    { id: "123", text: "Different text" }, { id: "123", to: "Different room" }]) {
    const f = squareSendFixture(response);
    assert(JSON.stringify(await dispatchDesktopSquare(f.base, {}, f.args)) === JSON.stringify({ state: "send_outcome_unknown_no_retry" }));
    assert(f.calls.length === 1 && f.base.square.sendMessage === f.original);
  }
});
Deno.test("desktop Square confirmed metadata omits missing creation timestamp", async () => {
  const f = squareSendFixture({ id: "123" });
  assert(JSON.stringify(await dispatchDesktopSquare(f.base, {}, f.args)) === JSON.stringify({ state: "sent_confirmed", message_id: "123" }));
  assert(f.calls.length === 1);
});
