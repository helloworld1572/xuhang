import re
import socket
import subprocess


def lan_addresses():
    addresses = set()
    try:
        result = subprocess.run(["ipconfig"], capture_output=True, timeout=5)
        addresses.update(re.findall(r"IPv4[^:\r\n]*:\s*(\d+\.\d+\.\d+\.\d+)", result.stdout.decode(errors="replace")))
    except (OSError, subprocess.TimeoutExpired):
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    return sorted(ip for ip in addresses if ip.startswith(("10.", "192.168.")) or
                  (ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31))
