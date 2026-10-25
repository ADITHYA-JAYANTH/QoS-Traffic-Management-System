import socket, sys
port = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("0.0.0.0", port)); s.listen(1)
c, addr = s.accept()
msg = c.recv(1024).decode()
print(f"[TCP server] got '{msg}' from {addr}")
c.sendall(f"ACK: {msg}".encode())
c.close(); s.close()
