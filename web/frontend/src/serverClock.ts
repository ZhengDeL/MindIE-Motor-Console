/** Control-plane time for observation timestamps. Never used as a retry timer. */
type RequestSample = {
  sequence: number;
  startedAt: number;
  origin: string | null;
};
const MAX_RTT_MS = 5000;
let sequence = 0;
let acceptedSequence = 0;
let anchor: { serverTime: number; monotonicTime: number } | null = null;
const listeners = new Set<() => void>();

export function serverNow(): number {
  return anchor
    ? anchor.serverTime + Math.max(0, performance.now() - anchor.monotonicTime)
    : Date.now();
}

export function captureServerClockRequest(): RequestSample {
  return {
    sequence: ++sequence,
    startedAt: performance.now(),
    origin: typeof location === "undefined" ? null : location.origin,
  };
}

/** Only a fresh same-origin HTTP Date can establish or advance the clock anchor. */
export function observeServerClockResponse(
  request: RequestSample,
  response: Pick<Response, "url" | "headers">,
): boolean {
  const receivedAt = performance.now();
  const rtt = receivedAt - request.startedAt;
  if (
    request.sequence <= acceptedSequence ||
    rtt < 0 ||
    rtt > MAX_RTT_MS ||
    !request.origin ||
    !response.url
  )
    return false;
  try {
    if (
      new URL(response.url).origin !== request.origin ||
      (typeof location !== "undefined" && location.origin !== request.origin)
    )
      return false;
  } catch {
    return false;
  }
  const date = response.headers.get("Date");
  // HTTP Date has one-second precision. Use its midpoint and half the bounded
  // round trip. A cached response cannot establish a current clock sample.
  if (
    !date ||
    !/^(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun), \d{2} (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{4} \d{2}:\d{2}:\d{2} GMT$/.test(
      date,
    )
  )
    return false;
  const parsed = Date.parse(date);
  const age = response.headers.get("Age");
  if (
    !Number.isFinite(parsed) ||
    (age !== null && (!/^\d+$/.test(age) || Number(age) !== 0))
  )
    return false;
  const estimate = parsed + 500 + rtt / 2;
  const previous = anchor
    ? anchor.serverTime + Math.max(0, receivedAt - anchor.monotonicTime)
    : null;
  anchor = {
    serverTime: previous === null ? estimate : Math.max(previous, estimate),
    monotonicTime: receivedAt,
  };
  acceptedSequence = request.sequence;
  for (const listener of listeners) {
    // A view subscriber must never turn a successful API operation into failure.
    try {
      listener();
    } catch {
      /* Subscribers are isolated from transport. */
    }
  }
  return true;
}

/** Notify mounted resource views immediately, before their periodic age tick. */
export function subscribeServerClock(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
