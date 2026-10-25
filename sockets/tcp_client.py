import socket, sys
host, port, msg = sys.argv[1], int(sys.argv[2]), sys.argv[3]
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.connect((host, port)); s.sendall(msg.encode())
print("[TCP client] reply:", s.recv(1024).decode())
s.close()
