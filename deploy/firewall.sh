#!/bin/sh
# Runs on the docker network evalnet (172.30.0.0/24, bridge evalbr0) may reach only the
# metering proxy on the host (port 8001): no internet, no other host ports. Idempotent;
# re-applied by eval-portal-firewall.timer in case Docker or the platform rewrites the chains.
set -e
iptables -C DOCKER-USER -s 172.30.0.0/24 -j DROP 2>/dev/null || iptables -I DOCKER-USER -s 172.30.0.0/24 -j DROP
iptables -C INPUT -i evalbr0 -p tcp --dport 8001 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -i evalbr0 -p tcp --dport 8001 -j ACCEPT
iptables -C INPUT -i evalbr0 -j DROP 2>/dev/null || iptables -I INPUT 2 -i evalbr0 -j DROP
