# Traffic & Usage Measurement

How the OpenVPN Manager measures and displays throughput and session totals.

## How it works

The app reads the kernel's byte counters for the active VPN interface directly:

- `detect_iface()` finds the active `tun*`/`tap*` interface via `/sys/class/net`.
- `Sampler` reads `rx_bytes` / `tx_bytes` from that interface's
  `/sys/class/net/<iface>/statistics/` every second.
- **Live speed** is the difference between consecutive 1-second samples.
- **Session totals** are `current counter − baseline`, where the baseline is
  captured the first time the app samples a freshly-detected interface.

Convention: on the tunnel interface, *received* bytes are your download, so
`rx` feeds the Down value and `rx` feeds the cumulative session download.

## What "Session" means

Session totals count **traffic since the app started watching the interface**,
not "since the VPN connected":

- If the app is launched, then the VPN is connected, the baseline is captured
  at connect time and Session covers the whole session.
- If the VPN is already connected (and passing traffic) *before* the app opens,
  or the app is restarted mid-session, Session only counts from that point;
  earlier traffic is not included.
- The sampler is dropped on disconnect, so Session resets on each connect.

## Accuracy caveats

The numbers are real bytes counted by the kernel for the VPN interface, not
estimates, but they will not match an application's own figures exactly:

- **It counts all tunnel traffic, not one app's traffic.** The counters include
  every packet crossing the tunnel: IP/TCP headers, retransmissions, DNS, and
  any other application's traffic. A torrent client reports its payload only,
  so the manager typically reads a few percent *higher* than the file size
  (e.g. ~1.03 GB for a 1 GB download), never lower.
- **It only counts traffic that traverses the tunnel interface.** If an
  application bypasses the VPN (split tunnel, per-app routing, direct bind),
  its bytes never touch `tun*` and are not counted.
- **Speed sampling is 1-second granularity.** Fine for sustained transfers
  (torrents, streams); very short bursts may be under- or over-reported.

## Verifying the source

The raw counters an active session is built from:

```sh
cat /sys/class/net/tun0/statistics/rx_bytes   # download (Down / Session ↓)
cat /sys/class/net/tun0/statistics/tx_bytes   # upload   (Up / Session ↑)
```

If the numbers the app shows disagree with these, the app's view of the
interface (or its baseline timing) is the thing to investigate, not the counter.
