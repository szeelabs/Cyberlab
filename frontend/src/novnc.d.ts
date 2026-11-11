// Minimal types for the parts of noVNC CyberLab uses (the package ships no types).
declare module "@novnc/novnc" {
  export default class RFB extends EventTarget {
    constructor(target: HTMLElement, urlOrChannel: string | object, options?: { shared?: boolean });
    scaleViewport: boolean;
    resizeSession: boolean;
    viewOnly: boolean;
    focusOnClick: boolean;
    background: string;
    disconnect(): void;
    focus(): void;
    sendCtrlAltDel(): void;
  }
}
