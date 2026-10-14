import getpass
import hashlib
import secrets
import sys

ITERATIONS = 200_000


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: chat_user.py <name>")
    password = getpass.getpass("password: ")
    if len(password) < 10 or password != getpass.getpass("again: "):
        raise SystemExit("passwords differ or shorter than 10 characters")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    print(f"{sys.argv[1]}:pbkdf2:{ITERATIONS}:{salt}:{digest}")
    print("add it to CHAT_USERS in .env (comma-separated), then: docker-compose up -d ui", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
