"""Setup, login, settings, and the action buttons behind them."""

import json
import os
from urllib.parse import parse_qs

from immich_backup import auth, actions, disks, restore, status
from immich_backup.config import load, update

SHELL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; --bg:#0c100e; --line:#2a3830; --text:#eef5ec; --muted:#8ea194; --lime:#d6f25c; --red:#ff5d4a; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; font-family: ui-sans-serif, "Avenir Next", "Segoe UI", sans-serif;
    background: radial-gradient(900px 420px at 10% -10%, rgba(214,242,92,.08), transparent 60%), var(--bg); color: var(--text); }
  main { max-width: 560px; margin: 0 auto; padding: 48px 20px 64px; }
  h1 { font-weight: 620; letter-spacing: -.03em; font-size: 36px; margin: 8px 0 0; }
  p, li { color: var(--muted); line-height: 1.5; }
  form, .card { margin-top: 22px; padding: 22px; border: 1px solid var(--line); border-radius: 18px; background: #141a16; }
  label { display:block; margin-top: 14px; font-size: 13px; color: var(--muted); }
  input, select { width:100%; margin-top: 6px; padding: 12px 14px; border-radius: 12px; border: 1px solid var(--line);
    background:#0e1410; color: var(--text); font: inherit; }
  button, a.btn { margin-top: 18px; border:0; border-radius: 999px; padding: 12px 16px; background: var(--lime);
    color:#14200c; font: inherit; cursor: pointer; text-decoration:none; display:inline-block; }
  button.quiet { margin-left: 8px; background: transparent; color: var(--text); border: 1px solid var(--line); }
  .error { color: var(--red); min-height: 1.2em; }
  .steps { display:flex; gap:8px; margin-top: 18px; }
  .steps span { padding: 6px 10px; border-radius: 999px; border: 1px solid var(--line); color: var(--muted); font-size: 12px; }
  .steps .on { color:#14200c; background: var(--lime); border-color: transparent; }
  .hidden { display: none; }
  label.check { display: flex; align-items: center; gap: 10px; }
  label.check input { width: auto; }
</style>
</head>
<body>
<main>
  <div class="brand" style="letter-spacing:.16em;text-transform:uppercase;color:var(--muted);font-size:13px"><b style="color:var(--text)">Immich</b> backup</div>
  __BODY__
</main>
<script>
async function post(url, body) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Immich-Backup": "1" },
    body: JSON.stringify(body || {})
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || "Request failed");
  return data;
}
function showError(error) {
  const node = document.querySelector("form:not(.hidden) .error") || document.querySelector(".error");
  if (node) node.textContent = error.message || "Request failed";
}
__SCRIPT__
</script>
</body>
</html>
"""


def page(title, body, script=""):
    return SHELL.replace("__TITLE__", title).replace("__BODY__", body).replace("__SCRIPT__", script)


LOGIN = page(
    "Sign in",
    """
    <h1>Sign in</h1>
    <p>The status page and backup actions use this account.</p>
    <form id="login">
      <label>Email<input name="email" type="email" autocomplete="username" required></label>
      <label>Password<input name="password" type="password" autocomplete="current-password" required></label>
      <p class="error"></p>
      <button type="submit">Sign in</button>
    </form>
    """,
    """
    document.getElementById("login").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await post("/api/login", { email: form.get("email"), password: form.get("password") });
        location.href = "/";
      } catch (error) { showError(error); }
    };
    """,
)

SETUP = page(
    "Set up backup",
    """
    <h1>Set up this backup</h1>
    <div class="steps"><span class="on" id="s1">Account</span><span id="s2">Choose</span><span id="s3">Details</span></div>
    <form id="account">
      <label>Email<input name="email" type="email" autocomplete="username" required></label>
      <label>Password<input name="password" type="password" autocomplete="new-password" minlength="8" required></label>
      <label>Repeat password<input name="again" type="password" autocomplete="new-password" minlength="8" required></label>
      <p class="error"></p>
      <button type="submit">Continue</button>
    </form>
    <form id="choice" class="hidden">
      <p>Start nightly backups on this machine, or rebuild Immich from a backup disk.</p>
      <p class="error"></p>
      <button type="button" id="choose-backup">Set up a new backup</button>
      <button type="button" class="quiet" id="choose-restore">Restore this machine</button>
    </form>
    <form id="drives" class="hidden">
      <p>Choose the disk that holds the Immich photos, and the disk that should store the backup. The root disk cannot be used. Nothing is formatted.</p>
      <label>Photos drive<select name="photos" required></select></label>
      <label>Backup drive<select name="backup" required></select></label>
      <p class="error"></p>
      <button type="submit">Continue</button>
      <button type="button" class="quiet back" data-back="choice">Back</button>
    </form>
    <form id="restore" class="hidden">
      <p>Both drives must already be mounted. Nothing is formatted, and the backup drive is only read. This installs Docker and Immich, copies the photo library onto the photo drive, and replaces the Immich database with the archive you choose.</p>
      <label>Backup drive<select name="backup" required></select></label>
      <label>Photo drive<select name="photos" required></select></label>
      <label>Archive<select name="archive" required></select></label>
      <label>Alert email<input name="email" type="email" required></label>
      <label>Type restore to confirm<input name="confirm" autocomplete="off" required></label>
      <p class="error"></p>
      <button type="submit">Restore</button>
      <button type="button" class="quiet back" data-back="choice">Back</button>
    </form>
    <form id="alerts" class="hidden">
      <p>A successful backup does not send mail. Failures and warnings do.</p>
      <label>Alert email<input name="email" type="email" required></label>
      <p class="error"></p>
      <button type="submit">Finish setup</button>
      <button type="button" class="quiet back" data-back="drives">Back</button>
    </form>
    """,
    """
    const steps = ["account", "choice", "drives", "restore", "alerts"];
    function go(name) {
      steps.forEach((id) => {
        document.getElementById(id).classList.toggle("hidden", id !== name);
      });
      const mark = name === "account" ? 1 : name === "choice" ? 2 : 3;
      [1, 2, 3].forEach((number) => {
        document.getElementById("s" + number).classList.toggle("on", number === mark);
      });
    }
    function fill(select, rows) {
      select.innerHTML = rows.filter(row => row.mount !== "/").map(row => {
        const option = document.createElement("option");
        option.value = row.mount;
        const size = row.size ? Math.round(row.size / 1024 / 1024 / 1024) + " GB" : "";
        option.textContent = (row.label || row.mount) + "  " + row.mount + (size ? "  " + size : "");
        return option.outerHTML;
      }).join("");
    }
    function fillAll(rows) {
      document.querySelectorAll("[name=photos], #drives [name=backup], #restore [name=backup]").forEach((select) => fill(select, rows));
      document.querySelectorAll("#drives [name=backup], #restore [name=backup]").forEach((select) => {
        if (select.options.length > 1) select.selectedIndex = 1;
      });
    }
    async function loadArchives() {
      const mount = document.querySelector("#restore [name=backup]").value;
      const box = document.querySelector("#restore [name=archive]");
      box.innerHTML = "";
      const response = await fetch("/api/restore/archives?mount=" + encodeURIComponent(mount));
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || "Could not read the archives");
      data.forEach((row) => {
        const option = document.createElement("option");
        option.value = row.name;
        option.textContent = row.name;
        box.appendChild(option);
      });
    }
    document.querySelectorAll("[data-back]").forEach((button) => {
      button.onclick = () => {
        const form = button.closest("form");
        const node = form && form.querySelector(".error");
        if (node) node.textContent = "";
        go(button.getAttribute("data-back"));
      };
    });
    document.getElementById("account").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      if (form.get("password") !== form.get("again")) { showError(new Error("Passwords do not match")); return; }
      try {
        await post("/api/setup/account", { email: form.get("email"), password: form.get("password") });
        document.querySelector("#alerts [name=email]").value = form.get("email");
        document.querySelector("#restore [name=email]").value = form.get("email");
        go("choice");
      } catch (error) { showError(error); }
    };
    document.getElementById("choose-backup").onclick = async () => {
      try {
        fillAll(await fetch("/api/disks").then(response => response.json()));
        go("drives");
      } catch (error) { showError(error); }
    };
    document.getElementById("choose-restore").onclick = async () => {
      try {
        fillAll(await fetch("/api/disks").then(response => response.json()));
        go("restore");
        await loadArchives();
      } catch (error) { showError(error); }
    };
    document.querySelector("#restore [name=backup]").onchange = async () => {
      try { await loadArchives(); } catch (error) { showError(error); }
    };
    document.getElementById("drives").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await post("/api/setup/drives", { photos: form.get("photos"), backup: form.get("backup") });
        go("alerts");
      } catch (error) { showError(error); }
    };
    document.getElementById("restore").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        const data = await post("/api/setup/restore", {
          photos: form.get("photos"),
          backup: form.get("backup"),
          archive: form.get("archive"),
          email: form.get("email"),
          confirm: form.get("confirm")
        });
        if ((data.message || "").indexOf("already running") !== -1) {
          showError(new Error(data.message));
          return;
        }
        location.href = "/";
      } catch (error) { showError(error); }
    };
    document.getElementById("alerts").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await post("/api/setup/alerts", { email: form.get("email") });
        location.href = "/";
      } catch (error) { showError(error); }
    };
    fetch("/api/settings").then((response) => {
      if (!response.ok) return;
      const step = new URLSearchParams(location.search).get("step");
      if (step === "restore") document.getElementById("choose-restore").click();
      else if (step === "backup") document.getElementById("choose-backup").click();
      else go("choice");
    });
    """,
)

SETTINGS = page(
    "Settings",
    """
    <h1>Settings</h1>
    <p><a class="btn" href="/">Back to status</a></p>
    <form id="alerts">
      <label>Alert email<input name="email" type="email" required></label>
      <label>Nightly backup time<input name="label" required placeholder="01:00"></label>
      <label>Days to keep<input name="keep_daily" type="number" min="1" max="365" required></label>
      <label>Free-space warnings, GB<input name="warn_gb" required placeholder="50,20,10"></label>
      <p class="error" id="alert-error"></p>
      <button type="submit">Save alerts</button>
    </form>
    <form id="sleep" class="hidden">
      <p>The backup drive is a hard disk. After it sits idle, it is put to sleep. A drive that is already asleep is not woken up. Its power-management level is left as it is.</p>
      <label class="check"><input name="spindown_enabled" type="checkbox"> Sleep when idle</label>
      <label>Minutes idle before sleep<input name="idle_minutes" type="number" min="1" max="240" required></label>
      <p class="error" id="sleep-error"></p>
      <button type="submit">Save sleep</button>
    </form>
    <form id="password">
      <label>Current password<input name="current" type="password" autocomplete="current-password" required></label>
      <label>New password<input name="new_password" type="password" minlength="8" autocomplete="new-password" required></label>
      <p class="error" id="password-error"></p>
      <button type="submit">Change password</button>
    </form>
    """,
    """
    fetch("/api/settings").then(response => {
      if (response.status === 401) location.href = "/login";
      return response.json();
    }).then(data => {
      document.querySelector("[name=email]").value = data.email || "";
      document.querySelector("[name=label]").value = data.label || "";
      document.querySelector("[name=keep_daily]").value = data.keep_daily || 7;
      document.querySelector("[name=warn_gb]").value = data.warn_gb || "50,20,10";
      const sleep = document.getElementById("sleep");
      sleep.classList.toggle("hidden", !data.hdd);
      if (data.hdd) {
        document.querySelector("[name=spindown_enabled]").checked = !!data.spindown_enabled;
        document.querySelector("[name=idle_minutes]").value = data.spindown_idle_minutes || 10;
      }
    });
    document.getElementById("alerts").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await post("/api/settings", {
          email: form.get("email"),
          label: form.get("label"),
          keep_daily: Number(form.get("keep_daily")),
          warn_gb: form.get("warn_gb")
        });
        document.getElementById("alert-error").textContent = "Saved";
      } catch (error) { document.getElementById("alert-error").textContent = error.message; }
    };
    document.getElementById("sleep").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await post("/api/settings/sleep", {
          enabled: form.get("spindown_enabled") === "on",
          idle_minutes: Number(form.get("idle_minutes"))
        });
        document.getElementById("sleep-error").textContent = "Saved";
      } catch (error) { document.getElementById("sleep-error").textContent = error.message; }
    };
    document.getElementById("password").onsubmit = async (event) => {
      event.preventDefault();
      const form = new FormData(event.target);
      try {
        await post("/api/password", { current: form.get("current"), new_password: form.get("new_password") });
        location.href = "/login";
      } catch (error) { document.getElementById("password-error").textContent = error.message; }
    };
    """,
)


def reload():
    cfg = load(status.C.config_path)
    status.configure(cfg)
    return cfg


def token_of(handler):
    return auth.cookie_token(handler.headers.get("Cookie", ""))


def signed_in(handler):
    return bool(auth.session_email(status.C, token_of(handler)))


def read_json(handler):
    if handler.headers.get("X-Immich-Backup") != "1":
        raise ValueError("Request was rejected")
    length = int(handler.headers.get("Content-Length") or 0)
    if length > 20000:
        raise ValueError("Request is too large")
    raw = handler.rfile.read(length) if length else b"{}"
    data = json.loads(raw.decode("utf-8") or "{}")
    if not isinstance(data, dict):
        raise ValueError("Request is not valid")
    return data


def send_json(handler, code, payload, headers=()):
    handler._send(code, json.dumps(payload), "application/json", headers)


def session_header(token):
    cookie = auth.COOKIE + "=" + token + "; HttpOnly; SameSite=Strict; Path=/; Max-Age=1209600"
    return (("Set-Cookie", cookie),)


def clear_cookie():
    return (("Set-Cookie", auth.COOKIE + "=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"),)


def require_user(handler):
    if not signed_in(handler):
        send_json(handler, 401, {"error": "Sign in required"})
        return False
    return True


def choose_drive(mount):
    for disk in disks.list_disks():
        if disk.get("mount") == mount and mount not in {"", "/"}:
            return disk
    return None


def save_drives(photos, backup):
    photo = choose_drive(photos)
    store = choose_drive(backup)
    if not photo or not store:
        raise ValueError("Choose two mounted drives")
    if photo["mount"] == store["mount"]:
        raise ValueError("The photo drive and backup drive must be different")
    if not photo.get("uuid") or not store.get("uuid"):
        raise ValueError("The selected drive does not have a filesystem UUID")
    update(status.C.config_path, {
        "paths": {
            "media_mount": photo["mount"],
            "backup_mount": store["mount"],
            "repo": store["mount"].rstrip("/") + "/immich-borg",
        },
        "identity": {
            "media_uuid": photo["uuid"],
            "backup_uuid": store["uuid"],
            "backup_fstype": store.get("fstype") or "ext4",
            "backup_parent_serial": store.get("serial") or "",
            "backup_rotational": "1" if store.get("rotational") else "0",
        },
    })
    reload()
    marker = os.path.join(store["mount"], ".backup-disk")
    try:
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(store["uuid"] + "\n")
    except OSError:
        pass


def save_sleep(data):
    if not status.C.backup_rotational:
        raise ValueError("Sleep settings are only for a hard drive")
    minutes = int(data.get("idle_minutes") or 0)
    if minutes < 1 or minutes > 240:
        raise ValueError("Choose between 1 and 240 minutes")
    enabled = bool(data.get("enabled"))
    update(status.C.config_path, {
        "health": {
            "spindown_enabled": "true" if enabled else "false",
            "spindown_idle_seconds": minutes * 60,
        },
    })
    reload()


def save_alerts(data):
    email = str(data.get("email") or "").strip()
    label = str(data.get("label") or "").strip()
    warn = str(data.get("warn_gb") or "").strip()
    keep = int(data.get("keep_daily") or 0)
    if not auth.email_ok(email):
        raise ValueError("Enter an alert email")
    if not label or len(label) > 40:
        raise ValueError("Enter the backup time")
    try:
        parts = [int(part) for part in warn.split(",") if part.strip()]
    except ValueError:
        raise ValueError("Enter warning levels such as 50,20,10") from None
    if not parts or any(part <= 0 or part > 100000 for part in parts):
        raise ValueError("Enter warning levels such as 50,20,10")
    if keep < 1 or keep > 365:
        raise ValueError("Keep between 1 and 365 days")
    hour, _, minute = label.partition(":")
    calendar = status.C.backup_calendar
    if hour.isdigit() and minute.isdigit():
        calendar = "*-*-* " + hour.zfill(2) + ":" + minute.zfill(2) + ":00"
    update(status.C.config_path, {
        "notify": {"email": email},
        "schedule": {"label": label, "backup": calendar},
        "backup": {"keep_daily": keep, "warn_gb": ",".join(str(part) for part in parts)},
    })
    reload()


def dispatch(handler, method):
    raw = handler.path
    path = raw.split("?", 1)[0]
    query = parse_qs(raw.split("?", 1)[1]) if "?" in raw else {}
    try:
        if method == "GET" and path in {"/", "/index.html"}:
            if not auth.has_account(status.C):
                handler._send(200, SETUP, "text/html; charset=utf-8")
            elif not signed_in(handler):
                handler._send(200, LOGIN, "text/html; charset=utf-8")
            else:
                handler._send(200, status.PAGE, "text/html; charset=utf-8")
            return True
        if method == "GET" and path == "/login":
            handler._send(200, LOGIN, "text/html; charset=utf-8")
            return True
        if method == "GET" and path == "/setup":
            if auth.has_account(status.C) and not signed_in(handler):
                handler.send_response(302)
                handler.send_header("Location", "/login")
                handler.end_headers()
            else:
                handler._send(200, SETUP, "text/html; charset=utf-8")
            return True
        if method == "GET" and path == "/settings":
            if not signed_in(handler):
                handler.send_response(302)
                handler.send_header("Location", "/login")
                handler.end_headers()
            else:
                handler._send(200, SETTINGS, "text/html; charset=utf-8")
            return True
        if method == "GET" and path == "/api/status":
            if not signed_in(handler):
                send_json(handler, 401, {"error": "Sign in required"})
            else:
                send_json(handler, 200, status.cached_status())
            return True
        if method == "GET" and path == "/api/disks":
            if not require_user(handler):
                return True
            send_json(handler, 200, disks.list_disks())
            return True
        if method == "GET" and path == "/api/restore/archives":
            if not require_user(handler):
                return True
            mount = (query.get("mount") or [""])[0]
            disk = choose_drive(mount)
            if not disk:
                raise ValueError("Choose the backup drive")
            repo = disk["mount"].rstrip("/") + "/immich-borg"
            send_json(handler, 200, restore.list_archives(repo))
            return True
        if method == "GET" and path == "/api/settings":
            if not require_user(handler):
                return True
            send_json(handler, 200, {
                "email": status.C.email,
                "label": status.C.schedule_label,
                "keep_daily": status.C.keep_daily,
                "warn_gb": ",".join(str(part) for part in status.C.warn_gb),
                "photos": status.C.media_mount,
                "backup": status.C.backup_mount,
                "hdd": status.C.backup_rotational,
                "spindown_enabled": status.C.spindown_enabled,
                "spindown_idle_minutes": max(1, status.C.spindown_idle_seconds // 60),
            })
            return True
        if method == "POST" and path == "/api/setup/account":
            data = read_json(handler)
            token = auth.create_account(status.C, str(data.get("email") or ""), str(data.get("password") or ""))
            send_json(handler, 200, {"ok": True}, session_header(token))
            return True
        if method == "POST" and path == "/api/login":
            data = read_json(handler)
            email = str(data.get("email") or "")
            if not auth.login_allowed(email):
                send_json(handler, 429, {"error": "Too many attempts. Wait a minute."})
                return True
            if not auth.verify_password(status.C, email, str(data.get("password") or "")):
                auth.note_failure(email)
                send_json(handler, 401, {"error": "Email or password is wrong"})
                return True
            auth.note_success(email)
            send_json(handler, 200, {"ok": True}, session_header(auth.open_session(status.C)))
            return True
        if method == "POST" and path == "/api/logout":
            read_json(handler)
            auth.drop_session(status.C, token_of(handler))
            send_json(handler, 200, {"ok": True}, clear_cookie())
            return True
        if method == "POST" and path == "/api/setup/drives":
            if not require_user(handler):
                return True
            data = read_json(handler)
            save_drives(str(data.get("photos") or ""), str(data.get("backup") or ""))
            send_json(handler, 200, {"ok": True})
            return True
        if method == "POST" and path == "/api/setup/restore":
            if not require_user(handler):
                return True
            data = read_json(handler)
            if str(data.get("confirm") or "") != "restore":
                raise ValueError('Type "restore" to confirm')
            archive = str(data.get("archive") or "")
            if not restore.ARCHIVE_RE.match(archive):
                raise ValueError("Choose an archive")
            email = str(data.get("email") or "").strip()
            if not auth.email_ok(email):
                raise ValueError("Enter an alert email")
            save_drives(str(data.get("photos") or ""), str(data.get("backup") or ""))
            update(status.C.config_path, {
                "notify": {"email": email},
                "restore": {"archive": archive},
            })
            reload()
            send_json(handler, 200, {"ok": True, "message": actions.start(status.C, "restore")})
            return True
        if method == "POST" and path == "/api/setup/alerts":
            if not require_user(handler):
                return True
            data = read_json(handler)
            email = str(data.get("email") or "").strip()
            if not auth.email_ok(email):
                raise ValueError("Enter an alert email")
            update(status.C.config_path, {"notify": {"email": email}})
            reload()
            send_json(handler, 200, {"ok": True})
            return True
        if method == "POST" and path == "/api/settings":
            if not require_user(handler):
                return True
            save_alerts(read_json(handler))
            send_json(handler, 200, {"ok": True, "message": "Saved"})
            return True
        if method == "POST" and path == "/api/settings/sleep":
            if not require_user(handler):
                return True
            save_sleep(read_json(handler))
            send_json(handler, 200, {"ok": True, "message": "Saved"})
            return True
        if method == "POST" and path == "/api/password":
            if not require_user(handler):
                return True
            data = read_json(handler)
            auth.change_password(status.C, str(data.get("current") or ""), str(data.get("new_password") or ""))
            send_json(handler, 200, {"ok": True}, clear_cookie())
            return True
        if method == "POST" and path in {"/api/actions/backup", "/api/actions/check"}:
            if not require_user(handler):
                return True
            read_json(handler)
            command = "backup" if path.endswith("backup") else "check"
            send_json(handler, 200, {"ok": True, "message": actions.start(status.C, command)})
            return True
    except ValueError as exc:
        send_json(handler, 400, {"error": str(exc)})
        return True
    except json.JSONDecodeError:
        send_json(handler, 400, {"error": "Request is not valid"})
        return True
    if method == "GET":
        handler._send(404, "Not found", "text/plain; charset=utf-8")
        return True
    send_json(handler, 404, {"error": "Not found"})
    return True
