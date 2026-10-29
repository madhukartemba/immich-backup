"""Local account for the status page. The password is stored as a hash."""

import hashlib
import hmac
import json
import os
import secrets
import time

ITERATIONS = 200_000
COOKIE = "ib_session"
_lock_guard = __import__("threading").Lock()
_failures = {}


def _path(cfg):
    return os.path.join(cfg.state_dir, "auth.json")


def _read(cfg):
    try:
        with open(_path(cfg), encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write(cfg, data):
    os.makedirs(cfg.state_dir, mode=0o700, exist_ok=True)
    tmp = _path(cfg) + "." + str(os.getpid()) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, _path(cfg))


def has_account(cfg):
    data = _read(cfg)
    return bool(data.get("email") and data.get("password_hash"))


def email_ok(email):
    email = (email or "").strip()
    return 3 <= len(email) <= 200 and "@" in email and " " not in email and "\n" not in email


def password_ok(password):
    return isinstance(password, str) and 8 <= len(password) <= 200


def _hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), ITERATIONS).hex()


def create_account(cfg, email, password):
    email = email.strip()
    if has_account(cfg):
        raise ValueError("An account already exists")
    if not email_ok(email) or not password_ok(password):
        raise ValueError("Use an email address and a password of at least 8 characters")
    salt = secrets.token_hex(16)
    _write(cfg, {
        "email": email,
        "salt": salt,
        "password_hash": _hash(password, salt),
        "sessions": [],
    })
    return open_session(cfg)


def verify_password(cfg, email, password):
    data = _read(cfg)
    known = data.get("email", "")
    given = email.strip()
    if len(known) != len(given) or not hmac.compare_digest(known, given):
        return False
    digest = _hash(password, data.get("salt", ""))
    return hmac.compare_digest(data.get("password_hash", ""), digest)


def login_allowed(email):
    until = _failures.get(email.strip(), (0, 0))
    count, locked_until = until
    return time.time() >= locked_until


def note_failure(email):
    email = email.strip()
    count, _locked = _failures.get(email, (0, 0))
    count += 1
    locked = time.time() + 60 if count >= 8 else 0
    _failures[email] = (0 if locked else count, locked)


def note_success(email):
    _failures.pop(email.strip(), None)


def open_session(cfg):
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _lock_guard:
        data = _read(cfg)
        sessions = [item for item in data.get("sessions", []) if item.get("expires", 0) > time.time()]
        sessions.append({"hash": digest, "expires": time.time() + 14 * 24 * 3600})
        data["sessions"] = sessions[-20:]
        _write(cfg, data)
    return token


def session_email(cfg, token):
    if not token:
        return ""
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.time()
    for item in _read(cfg).get("sessions", []):
        if hmac.compare_digest(item.get("hash", ""), digest) and item.get("expires", 0) > now:
            return _read(cfg).get("email", "")
    return ""


def drop_session(cfg, token):
    if not token:
        return
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    with _lock_guard:
        data = _read(cfg)
        data["sessions"] = [item for item in data.get("sessions", []) if item.get("hash") != digest]
        _write(cfg, data)


def change_password(cfg, current, new_password):
    data = _read(cfg)
    if not password_ok(new_password):
        raise ValueError("Use a password of at least 8 characters")
    digest = _hash(current, data.get("salt", ""))
    if not hmac.compare_digest(data.get("password_hash", ""), digest):
        raise ValueError("Current password is wrong")
    salt = secrets.token_hex(16)
    data["salt"] = salt
    data["password_hash"] = _hash(new_password, salt)
    data["sessions"] = []
    _write(cfg, data)


def cookie_token(header):
    for part in (header or "").split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE:
            return value
    return ""
