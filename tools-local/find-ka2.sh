#!/bin/bash
# find-ka2.sh — auto-discover the Kommu KA2 on the current network and probe SSH.
# Run this AFTER connecting your Mac to the KA2's WiFi hotspot (or same WiFi).
#
# Usage:  ./find-ka2.sh
#
# What it does:
#   1. Shows your Mac's current IP/interface.
#   2. Scans the local subnet for live hosts.
#   3. Probes each for SSH (port 22) and grabs the banner.
#   4. Reports any host that looks like the KA2.

set -u

echo "=== 1. Your Mac's network ==="
MY_IP=$(ifconfig | grep "inet " | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
if [ -z "$MY_IP" ]; then
  echo "❌ No IPv4 address found. Are you connected to a network?"
  exit 1
fi
SUBNET=$(echo "$MY_IP" | awk -F. '{print $1"."$2"."$3}')
echo "Your IP: $MY_IP   Subnet: $SUBNET.0/24"
echo ""

echo "=== 2. Scanning $SUBNET.0/24 for hosts (wait ~15s) ==="
ALIVE=""
for i in $(seq 1 254); do
  (ping -c 1 -W 1 $SUBNET.$i >/dev/null 2>&1 && echo "$SUBNET.$i" >> /tmp/ka2-alive.$$ &)
done
wait
ALIVE=$(sort -t. -k4 -n /tmp/ka2-alive.$$ 2>/dev/null)
rm -f /tmp/ka2-alive.$$
echo "Live hosts:"
echo "$ALIVE"
echo ""

echo "=== 3. Probing SSH (port 22) ==="
FOUND=""
for ip in $ALIVE; do
  if nc -z -w 2 $ip 22 2>/dev/null; then
    BANNER=$(echo "" | nc -w 2 $ip 22 2>/dev/null | head -c 120 | tr -d '\0')
    echo "✅ SSH OPEN on $ip   banner: $BANNER"
    FOUND="$FOUND $ip"
  fi
done
echo ""

if [ -n "$FOUND" ]; then
  echo "=== 4. Likely KA2 candidate(s):$FOUND ==="
  echo ""
  echo "Next: try logging in. Common defaults:"
  echo "  ssh root@<ip>      # password: comma"
  echo "  ssh comma@<ip>     # password: comma"
  echo ""
  echo "Or have ZCode do it — just tell it the IP."
else
  echo "❌ No SSH server found on the subnet."
  echo "Possible reasons:"
  echo "  - KA2 is not on this network (check it's in hotspot mode or joined same WiFi)"
  echo "  - SSH not enabled on the device (Settings → SSH toggle, if Kommu has one)"
  echo "  - Non-standard port (try: nc -zv <ip> 2222 8022)"
fi
