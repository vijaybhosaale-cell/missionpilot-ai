"""Tiny SMTP server for tests: accepts AUTH LOGIN/PLAIN and stores every message as a .eml file.
Run: python tests/fake_smtp.py 2525 /tmp/mails"""
import base64
import os
import socketserver
import sys
import time

OUT = sys.argv[2] if len(sys.argv) > 2 else "/tmp/mails"


class H(socketserver.StreamRequestHandler):
    def w(self, line):
        self.wfile.write((line + "\r\n").encode())

    def handle(self):
        self.w("220 fake ESMTP")
        data_mode, buf, rcpt = False, [], []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode(errors="replace").rstrip("\r\n")
            if data_mode:
                if text == ".":
                    os.makedirs(OUT, exist_ok=True)
                    with open(os.path.join(OUT, f"{time.time():.6f}.eml"), "w") as f:
                        f.write("X-Rcpt: " + ",".join(rcpt) + "\n" + "\n".join(buf))
                    data_mode, buf, rcpt = False, [], []
                    self.w("250 OK queued")
                else:
                    buf.append(text[1:] if text.startswith("..") else text)
                continue
            cmd = text.upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                self.wfile.write(b"250-fake\r\n250-AUTH LOGIN PLAIN\r\n250 OK\r\n")
            elif cmd.startswith("AUTH PLAIN"):
                self.w("235 ok")
            elif cmd.startswith("AUTH LOGIN"):
                self.w("334 VXNlcm5hbWU6")
                self.rfile.readline()
                self.w("334 UGFzc3dvcmQ6")
                pw = base64.b64decode(self.rfile.readline().strip() or b"").decode(errors="replace")
                self.w("535 5.7.8 Username and Password not accepted" if pw == "wrong" else "235 ok")
            elif cmd.startswith("MAIL FROM"):
                self.w("250 OK")
            elif cmd.startswith("RCPT TO"):
                rcpt.append(text.split(":", 1)[1].strip(" <>"))
                self.w("250 OK")
            elif cmd == "DATA":
                data_mode = True
                self.w("354 go")
            elif cmd == "QUIT":
                self.w("221 bye")
                return
            else:
                self.w("250 OK")


if __name__ == "__main__":
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    socketserver.ThreadingTCPServer(("127.0.0.1", int(sys.argv[1] if len(sys.argv) > 1 else 2525)), H).serve_forever()
