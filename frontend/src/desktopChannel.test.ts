import { describe, expect, it } from "vitest";

import { AuthedChannel } from "./desktopChannel";

class FakeSocket {
  readyState = 1;
  binaryType = "blob";
  sent: unknown[] = [];
  closed = false;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  send(d: unknown) { this.sent.push(d); }
  close() { this.closed = true; }
  emit(data: unknown) { this.onmessage?.({ data } as MessageEvent); }
}

describe("AuthedChannel", () => {
  it("buffers VNC bytes that arrive before noVNC attaches, then replays them in order", () => {
    const ws = new FakeSocket();
    const ch = new AuthedChannel(ws as unknown as WebSocket);
    expect(ws.binaryType).toBe("arraybuffer");
    ws.emit("first");
    ws.emit("second");
    const got: unknown[] = [];
    ch.onmessage = (ev) => got.push(ev.data);
    expect(got).toEqual(["first", "second"]);
    ws.emit("third"); // later messages go straight through
    expect(got).toEqual(["first", "second", "third"]);
  });

  it("has every property noVNC requires of a raw channel", () => {
    const ch = new AuthedChannel(new FakeSocket() as unknown as WebSocket);
    const props = [...Object.keys(ch), ...Object.getOwnPropertyNames(Object.getPrototypeOf(ch))];
    for (const p of ["send", "close", "binaryType", "onerror", "onmessage", "onopen", "protocol", "readyState"]) {
      expect(props).toContain(p);
    }
  });

  it("passes sends, closes and close events through", () => {
    const ws = new FakeSocket();
    const ch = new AuthedChannel(ws as unknown as WebSocket);
    let closed = false;
    ch.onclose = () => { closed = true; };
    ch.send("x");
    ch.close();
    ws.onclose?.({} as CloseEvent);
    expect(ws.sent).toEqual(["x"]);
    expect(ws.closed && closed).toBe(true);
    expect(ch.readyState).toBe(1);
  });
});
