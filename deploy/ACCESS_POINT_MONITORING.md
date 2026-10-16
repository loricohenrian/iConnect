# EAP225 reachability cards

The admin dashboard checks the two configured EAP225-Outdoor MACs in
`dashboard/access_points.py`. The labels #1/#2 do not identify physical locations.
The supported management LAN is the existing `10.10.10.0/24` on `usblan0`.

The staff-only GET API resolves unexpired dnsmasq leases (including permanent
leases), with a cached-neighbor fallback, then requires a fresh ARP reply with
the expected MAC. Neighbor entries alone never establish reachability. No
subnet sweep, ICMP dependency, AP credentials, database schema changes, firewall
changes, worker changes, or recovery actions are involved.

Checks run on demand while a visible dashboard is open. The existing shared
Redis cache limits scans across viewers to one per 30 seconds. The two bounded
ARP probes run concurrently; address lookup has a two-second subprocess timeout,
each probe has a two-second timeout. Missing tooling, cache failures, unresolved
addresses, and identity mismatches are Unknown. Three consecutive completed
no-reply scans on the same address produce Unreachable (about one minute after
the first miss, plus polling/detection delay). A matching reply immediately
restores Reachable. Misses reset after an unknown result, address change, or
gap over 90 seconds. Monitoring metadata/last-seen records expire after seven
days without checks; browsers show Unknown on request failures or stale data.

Reachable is not a radio/SSID/internet health test; Unreachable is not proof of
power loss. A browser whose own network fails cannot keep providing fresh AP
status. The dashboard does not provide historical uptime or continuous monitoring
while closed. MAC identity matching is not cryptographic authentication.

Deployment: back up affected files, fast-forward the verified commit, collect
static files, and gracefully reload only pisowifi. Keep Celery/coin/Telegram
services running. Verify live fresh replies and API authorization without
disconnecting APs or changing customer sessions. Negative cases use mocked
probes and isolated caches/tests only.
