// The desktop WebSocket is authenticated first (ticket in a text message, then "ready"),
// and only then handed to noVNC. noVNC attaches to it a moment later, so this wrapper
// buffers any VNC bytes that arrive in between and replays them once noVNC listens.
// It has exactly the properties noVNC checks for on a "raw channel".

type Handler = ((ev: MessageEvent) => void) | null;

export class AuthedChannel {
  binaryType: BinaryType = "arraybuffer";
  protocol = "";
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  private handler: Handler = null;
  private early: MessageEvent[] = [];

  constructor(private ws: WebSocket) {
    ws.binaryType = "arraybuffer";
    ws.onmessage = (ev) => (this.handler ? this.handler(ev) : this.early.push(ev));
    ws.onclose = (ev) => this.onclose?.(ev);
    ws.onerror = (ev) => this.onerror?.(ev);
  }

  get readyState(): number {
    return this.ws.readyState;
  }

  get onmessage(): Handler {
    return this.handler;
  }

  set onmessage(fn: Handler) {
    this.handler = fn;
    if (fn) for (const ev of this.early.splice(0)) fn(ev);
  }

  send(data: ArrayBufferLike | ArrayBufferView | Blob | string): void {
    this.ws.send(data as ArrayBuffer);
  }

  close(): void {
    this.ws.close();
  }
}
