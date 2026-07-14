#!/usr/bin/env python3
# 極簡 DHCP server，只綁 usb0（USB gadget 乙太），發固定 IP 給 Mac。
# 只服務 USB 鏈路，SO_BINDTODEVICE=usb0，絕不干擾 wlan0/eth0 的 LAN DHCP。
import socket, struct, sys, time

IFACE  = "usb0"
SERVER = "192.168.9.1"
CLIENT = "192.168.9.2"     # 固定發給 Mac 這個
MASK   = "255.255.255.0"
LEASE  = 86400

def ip(a): return socket.inet_aton(a)
def opt(c, d): return bytes([c, len(d)]) + d

s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
try:
    s.setsockopt(socket.SOL_SOCKET, 25, (IFACE + "\0").encode())   # SO_BINDTODEVICE
except OSError as e:
    print("BINDTODEVICE fail:", e, flush=True)
s.bind(("", 67))
print("usb_dhcp: serving %s -> %s on %s:67" % (SERVER, CLIENT, IFACE), flush=True)

while True:
    try:
        data, addr = s.recvfrom(2048)
    except Exception as e:
        print("recv err:", e, flush=True); time.sleep(1); continue
    if len(data) < 240 or data[0] != 1:
        continue
    xid    = data[4:8]
    chaddr = data[28:44]                 # 16 bytes
    # 找 option 53 (DHCP message type)
    mt = None; i = 240
    while i + 1 < len(data):
        c = data[i]
        if c == 255: break
        if c == 0: i += 1; continue
        l = data[i+1]; v = data[i+2:i+2+l]
        if c == 53 and v: mt = v[0]
        i += 2 + l
    if mt not in (1, 3):                 # DISCOVER=1, REQUEST=3
        continue
    rt = 2 if mt == 1 else 5            # OFFER / ACK
    pkt  = struct.pack("!BBBB", 2, 1, 6, 0) + xid + struct.pack("!HH", 0, 0x8000)
    pkt += ip("0.0.0.0") + ip(CLIENT) + ip(SERVER) + ip("0.0.0.0")
    pkt += chaddr + b"\0" * 64 + b"\0" * 128       # chaddr(16)+sname(64)+file(128)
    pkt += b"\x63\x82\x53\x63"                       # magic cookie
    pkt += opt(53, bytes([rt]))
    pkt += opt(54, ip(SERVER))                       # server id
    pkt += opt(51, struct.pack("!I", LEASE))
    pkt += opt(1,  ip(MASK))
    pkt += bytes([255])
    s.sendto(pkt, ("255.255.255.255", 68))
    print("%s -> offered %s to %s" % (("DISCOVER" if mt==1 else "REQUEST"), CLIENT,
          ":".join("%02x" % b for b in chaddr[:6])), flush=True)
