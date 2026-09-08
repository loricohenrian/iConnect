#!/usr/bin/env bash
# ==============================================================================
# iConnect - Connectivity Probe Diagnostic
# ==============================================================================
# Run as root: sudo bash deploy/diagnose_connectivity.sh
# Captures all iptables, NAT, conntrack, DNS, and routing state needed to
# debug the Chrome "No Internet" / Google One issue.
# ==============================================================================

echo "================================================================"
echo " iConnect Connectivity Diagnostic — $(date)"
echo "================================================================"

echo ""
echo "=== 1. Network Interfaces ==="
ip -br addr

echo ""
echo "=== 2. Default Route ==="
ip route show default

echo ""
echo "=== 3. iptables FORWARD chain (filter) ==="
iptables -L FORWARD -n -v --line-numbers 2>&1

echo ""
echo "=== 4. iptables NAT PREROUTING chain ==="
iptables -t nat -L PREROUTING -n -v --line-numbers 2>&1

echo ""
echo "=== 5. iptables NAT POSTROUTING chain ==="
iptables -t nat -L POSTROUTING -n -v --line-numbers 2>&1

echo ""
echo "=== 6. iptables NAT OUTPUT chain ==="
iptables -t nat -L OUTPUT -n -v --line-numbers 2>&1

echo ""
echo "=== 7. iptables mangle PREROUTING chain ==="
iptables -t mangle -L PREROUTING -n -v --line-numbers 2>&1

echo ""
echo "=== 8. iptables mangle FORWARD chain ==="
iptables -t mangle -L FORWARD -n -v --line-numbers 2>&1

echo ""
echo "=== 9. iptables mangle POSTROUTING chain ==="
iptables -t mangle -L POSTROUTING -n -v --line-numbers 2>&1

echo ""
echo "=== 10. Active conntrack entries (first 30) ==="
conntrack -L 2>/dev/null | head -30

echo ""
echo "=== 11. Conntrack count ==="
conntrack -C 2>/dev/null || cat /proc/sys/net/netfilter/nf_conntrack_count 2>/dev/null

echo ""
echo "=== 12. IP forwarding enabled? ==="
cat /proc/sys/net/ipv4/ip_forward

echo ""
echo "=== 13. dnsmasq main config ==="
cat /etc/dnsmasq.conf 2>/dev/null | grep -v "^#" | grep -v "^$" || echo "(no /etc/dnsmasq.conf)"

echo ""
echo "=== 14. dnsmasq.d configs ==="
for f in /etc/dnsmasq.d/*.conf; do
    echo "--- $f ---"
    cat "$f" 2>/dev/null
done

echo ""
echo "=== 15. DNS resolution test (connectivitycheck.gstatic.com) ==="
nslookup connectivitycheck.gstatic.com 2>&1 || dig connectivitycheck.gstatic.com +short 2>&1

echo ""
echo "=== 16. DNS resolution test (clients3.google.com) ==="
nslookup clients3.google.com 2>&1 || dig clients3.google.com +short 2>&1

echo ""
echo "=== 17. Curl connectivity check from Orange Pi itself ==="
curl -sS -o /dev/null -w "HTTP %{http_code} from %{remote_ip}:%{remote_port} (time: %{time_total}s)\n" http://connectivitycheck.gstatic.com/generate_204 2>&1 || echo "FAILED"

echo ""
echo "=== 18. Curl Google One from Orange Pi ==="
curl -sS -o /dev/null -w "HTTP %{http_code} from %{remote_ip}:%{remote_port} (time: %{time_total}s)\n" --max-time 5 https://one.google.com 2>&1 || echo "FAILED"

echo ""
echo "=== 19. ARP table (client devices) ==="
cat /proc/net/arp

echo ""
echo "=== 20. Active sessions in database ==="
cd /opt/iconnect/pisowifi 2>/dev/null
/opt/iconnect/pisowifi/.venv/bin/python manage.py shell -c "
from sessions_app.models import Session
active = Session.objects.filter(status='active').values_list('mac_address', 'ip_address', 'status')
for s in active:
    print(f'  MAC={s[0]}  IP={s[1]}  Status={s[2]}')
if not active:
    print('  (no active sessions)')
" 2>/dev/null || echo "Could not query database"

echo ""
echo "=== 21. Nginx probe location test (local) ==="
curl -sS -o /dev/null -D - http://127.0.0.1/generate_204 2>&1 | head -10

echo ""
echo "=== 22. IPv6 status ==="
cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null || echo "unknown"

echo ""
echo "=== 23. iptables-save (full dump) ==="
iptables-save 2>&1

echo ""
echo "================================================================"
echo " Diagnostic complete"
echo "================================================================"
