"""Status page. It reads logs and saved files. It does not call borg or smartctl."""

import json
import os
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

C = None
_status_cache = None
_status_cache_at = 0.0
_status_lock = threading.Lock()
_rate_samples = []
_check_cache = None
_check_cache_at = 0.0


def configure(cfg):
    global C
    C = cfg


PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Immich backup</title>
<style>
  :root {
    color-scheme: dark;
    --bg: #0c100e;
    --bg2: #141a16;
    --card: #171e19;
    --line: #2a3830;
    --text: #eef5ec;
    --muted: #8ea194;
    --lime: #d6f25c;
    --lime-dim: rgba(214, 242, 92, 0.16);
    --amber: #ffb020;
    --red: #ff5d4a;
    --ok: #7ddea0;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; min-height: 100%; }
  body {
    font-family: ui-sans-serif, "Avenir Next", "Segoe UI", sans-serif;
    background:
      radial-gradient(900px 420px at 10% -10%, rgba(214, 242, 92, 0.08), transparent 60%),
      var(--bg);
    color: var(--text);
  }
  .wrap { max-width: 1080px; margin: 0 auto; padding: 28px 20px 64px; }
  header { display: flex; justify-content: space-between; align-items: center; gap: 16px; }
  .brand { font-size: 14px; letter-spacing: 0.16em; text-transform: uppercase; color: var(--muted); }
  .brand b { color: var(--text); font-weight: 650; }
  .live { display: flex; align-items: center; gap: 18px; color: var(--muted); font-size: 13px; }
  .pair { display: flex; align-items: center; gap: 8px; }
  .dot { width: 8px; height: 8px; border-radius: 99px; background: var(--muted); }
  .dot.run { background: var(--lime); box-shadow: 0 0 0 5px var(--lime-dim); }
  .dot.ok { background: var(--ok); }
  .dot.bad { background: var(--red); }
  .hero {
    display: flex;
    flex-direction: column;
    gap: 28px;
    margin-top: 28px;
    padding: 28px;
    border: 1px solid var(--line);
    border-radius: 22px;
    background: linear-gradient(180deg, #1c261f, var(--card));
  }
  .title { display: flex; flex-direction: column; gap: 12px; }
  .meter { display: flex; flex-direction: column; gap: 14px; }
  .kicker { margin: 0; color: var(--muted); letter-spacing: 0.14em; text-transform: uppercase; font-size: 12px; }
  h1 {
    margin: 0;
    font-family: inherit;
    font-size: clamp(40px, 6vw, 64px);
    font-weight: 620;
    letter-spacing: -0.045em;
    line-height: 1;
  }
  .detail { margin: 0; color: var(--muted); font-size: 16px; }
  .note { margin: 0; color: var(--muted); font-size: 13px; }
  .note:empty { display: none; }
  .track { height: 8px; border-radius: 99px; background: #0d120f; overflow: hidden; }
  .fill { height: 100%; width: 0; border-radius: inherit; background: var(--lime); transition: width 0.4s ease; }
  .fill.warn { background: var(--amber); }
  .fill.bad { background: var(--red); }
  .grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 14px; margin-top: 14px; }
  .card, .panel {
    border: 1px solid var(--line);
    border-radius: 18px;
    background: var(--bg2);
  }
  .card { padding: 18px 18px 16px; }
  .card span, .panel h2 { color: var(--muted); font-size: 12px; letter-spacing: 0.12em; text-transform: uppercase; }
  .card strong { display: block; margin-top: 10px; font-size: 28px; font-variant-numeric: tabular-nums; letter-spacing: -0.03em; }
  .card em { display: block; margin-top: 6px; color: var(--muted); font-style: normal; font-size: 13px; }
  .card em:empty { display: none; }
  .card em.warn { color: var(--amber); }
  .card em.bad { color: var(--red); }
  .split { display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 14px; margin-top: 14px; }
  .panel { padding: 18px; min-height: 280px; }
  .panel h2 { margin: 0 0 14px; }
  table { width: 100%; border-collapse: collapse; }
  td { padding: 10px 0; border-top: 1px solid var(--line); font-size: 14px; vertical-align: top; }
  td.when { color: var(--muted); text-align: right; white-space: nowrap; }
  .empty { color: var(--muted); font-size: 14px; line-height: 1.5; }
  pre {
    margin: 0;
    white-space: pre-wrap;
    word-break: break-word;
    font: 12.5px/1.55 ui-monospace, "SF Mono", Menlo, Consolas, monospace;
    color: #d5e0d4;
  }
  .recover { margin-top: 14px; padding: 18px; border: 1px solid var(--line); border-radius: 18px; background: var(--bg2); }
  .recover[hidden] { display: none; }
  .recover p { margin: 0 0 12px; font-size: 16px; }
  .tools { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
  .recover .tools { margin-top: 0; }
  button, .btn {
    border: 0; border-radius: 999px; padding: 10px 14px; font: inherit; font-size: 14px;
    background: var(--lime); color: #14200c; text-decoration: none; cursor: pointer;
  }
  button.quiet, a.quiet { background: transparent; color: var(--text); border: 1px solid var(--line); }
  @media (max-width: 980px) {
    .grid { grid-template-columns: 1fr 1fr; }
  }
  @media (max-width: 800px) {
    .grid, .split { grid-template-columns: 1fr; }
    .hero { padding: 22px; }
  }
</style>
</head>
<body>
  <div class="wrap">
    <header>
      <div class="brand"><b>Immich</b> backup</div>
      <div class="live">
        <span class="pair"><i class="dot" id="idot"></i><span id="immich">Checking Immich</span></span>
        <span class="pair"><i class="dot" id="dot"></i><span id="live">Connecting</span></span>
      </div>
    </header>
    <nav class="tools">
      <button id="run" type="button">Run backup now</button>
      <button id="checkrepo" type="button">Check repository</button>
      <a class="btn quiet" href="/settings">Settings</a>
      <button id="logout" class="quiet" type="button">Log out</button>
    </nav>
    <section class="hero">
      <div class="title">
        <p class="kicker">On this VM</p>
        <h1 id="state">Checking</h1>
      </div>
      <div class="meter">
        <p class="detail" id="detail">Reading the backup service.</p>
        <div class="track"><div class="fill" id="fill"></div></div>
        <p class="note" id="barnote"></p>
      </div>
    </section>
    <section id="recover" class="recover" hidden>
      <p id="reason"></p>
      <div class="tools">
        <a class="btn" id="again-restore" href="/setup?step=restore">Try restore again</a>
        <a class="btn quiet" id="again-backup" href="/setup?step=backup">Set up a new backup instead</a>
        <a class="btn quiet" href="/settings">Settings</a>
      </div>
    </section>
    <section class="grid">
      <article class="card"><span>Backup disk</span><strong id="disk">—</strong><em id="disksub">Backup disk</em><em id="diskhealth">SMART</em></article>
      <article class="card"><span>Library</span><strong id="lib">—</strong><em id="libsub">Photos and videos</em><em id="libhealth">SMART</em></article>
      <article class="card"><span>Next run</span><strong id="next">—</strong><em id="elapsed">Nightly at 01:00</em><em id="check">Repository check</em></article>
      <article class="card"><span>Last success</span><strong id="last">—</strong><em id="lastsub">No finished copy yet</em></article>
    </section>
    <section class="split">
      <article class="panel">
        <h2>Archives</h2>
        <div id="archives" class="empty">Waiting for the first finished copy.</div>
      </article>
      <article class="panel">
        <h2>Recent log</h2>
        <pre id="log">No log yet.</pre>
      </article>
    </section>
  </div>
<script>
const $ = (id) => document.getElementById(id);
function gb(n) {
  if (n == null) return "—";
  const v = n / 1024 / 1024 / 1024;
  return (v >= 10 ? v.toFixed(0) : v.toFixed(1)) + " GB";
}
function finishClock(sec) {
  return new Date(Date.now() + sec * 1000).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
function duration(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 90) return sec + " sec";
  const min = Math.round(sec / 60);
  if (min < 90) return min + " min";
  const h = Math.floor(min / 60);
  const rem = min % 60;
  return rem ? h + " h " + rem + " min" : h + " h";
}
function clock(value, withDate) {
  if (value == null || value === "") return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString([], withDate
    ? { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { weekday: "short", hour: "numeric", minute: "2-digit" });
}
function localTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value || "");
  return date.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}
function localLog(line) {
  return String(line).replace(
    /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?/,
    (stamp) => clock(stamp, true)
  );
}
function smartLine(s) {
  if (!s || (s.at == null && !s.checked_at)) return "SMART not checked yet";
  const word = s.ok ? (s.summary || "Healthy") : (s.summary || "SMART problem");
  const when = typeof s.at === "number" ? clock(s.at * 1000, false) : s.checked_at;
  return word + ", checked " + when;
}
function render(d) {
  const dot = $("dot");
  dot.className = "dot" + (d.state === "running" ? " run" : d.state === "failed" ? " bad" : d.state === "ok" ? " ok" : "");
  $("live").textContent = d.state === "running" ? "Live" : "Updated " + localTime(d.updated);
  const immichUp = d.immich === "up";
  $("idot").className = "dot" + (immichUp ? " ok" : " bad");
  $("immich").textContent = immichUp ? "Immich up" : "Immich down";
  $("state").textContent = d.headline || "Unknown";
  $("detail").textContent = d.detail || "";
  const fill = $("fill");
  const pct = d.progress == null ? 0 : Math.max(0, Math.min(100, d.progress));
  fill.style.width = (d.state === "ok" ? 100 : pct) + "%";
  fill.className = "fill" + (d.state === "failed" ? " bad" : d.state === "running" ? "" : "");
  const b = d.backup || {};
  $("disk").textContent = gb(b.used);
  const spaceNote = { low: ". Under 50 GB", tight: ". Under 20 GB", critical: ". Under 10 GB" }[d.space] || "";
  $("disksub").textContent = b.mount_ok ? gb(b.avail) + " free of " + gb(b.size) + spaceNote : "Backup disk is not mounted";
  $("disksub").className = d.space === "low" ? "warn" : (d.space === "tight" || d.space === "critical" || d.space === "down" ? "bad" : "");
  const smart = d.smart || {};
  $("diskhealth").textContent = smartLine(smart.backup);
  $("diskhealth").className = smart.backup && smart.backup.checked_at && !smart.backup.ok ? "bad" : "";
  $("libhealth").textContent = smartLine(smart.primary);
  $("libhealth").className = smart.primary && smart.primary.checked_at && !smart.primary.ok ? "bad" : "";
  const m = d.media || {};
  $("lib").textContent = gb(m.used);
  $("libsub").textContent = m.mount_ok ? gb(m.avail) + " free of " + gb(m.size) : "Photo disk is not mounted";
  const when = d.next_run ? clock(d.next_run, false) : "Not scheduled";
  $("next").textContent = when;
  $("elapsed").textContent = d.elapsed ? "This copy has run " + d.elapsed : (d.next_run ? clock(d.next_run, true) : "Not scheduled");
  const check = d.check || {};
  const checkWord = { passed: "Check passed", failed: "Check failed", skipped: "Check skipped", running: "Check running" }[check.state] || "";
  const checkWhen = check.at ? clock(check.at, false) : "";
  $("check").textContent = checkWord ? (checkWhen ? checkWord + " " + checkWhen : checkWord) : "";
  $("check").className = check.state === "failed" ? "bad" : "";
  const last = d.last_success || {};
  $("last").textContent = last.at ? clock(last.at, false) : "None";
  $("lastsub").textContent = last.took ? "Finished in " + last.took : "No finished copy yet";
  if (d.state === "running" && d.bytes_per_sec) {
    const mb = d.bytes_per_sec / 1024 / 1024;
    let note = (mb >= 10 ? mb.toFixed(0) : mb.toFixed(1)) + " MB/s";
    if (d.eta_seconds) note += ", about " + duration(d.eta_seconds) + " left, around " + finishClock(d.eta_seconds);
    $("barnote").textContent = note;
  } else {
    $("barnote").textContent = d.state === "running" ? "Measuring write speed" : "";
  }
  const rows = d.archives || [];
  $("archives").className = rows.length ? "" : "empty";
  $("archives").innerHTML = rows.length
    ? "<table>" + rows.map(a => "<tr><td>" + esc(a.name) + "</td><td class='when'>" + esc(clock(a.time, true) || a.time) + "</td></tr>").join("") + "</table>"
    : esc(d.archives_note || "Waiting for the first finished copy.");
  $("log").textContent = (d.log && d.log.length) ? d.log.map(localLog).join("\n") : "No log yet.";
  const recover = $("recover");
  if (d.state === "failed") {
    recover.hidden = false;
    $("reason").textContent = d.failure || d.detail || "The last attempt did not finish.";
    const restore = d.failure_kind === "restore";
    $("again-restore").textContent = restore ? "Try restore again" : "Restore this machine instead";
    $("again-backup").textContent = restore ? "Set up a new backup instead" : "Choose the drives again";
  } else {
    recover.hidden = true;
  }
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;" }[c]));
}
async function act(url, confirmText) {
  if (confirmText && !confirm(confirmText)) return;
  const response = await fetch(url, { method: "POST", headers: { "X-Immich-Backup": "1" } });
  if (response.status === 401) { location.href = "/login"; return; }
  const data = await response.json().catch(() => ({}));
  if (url === "/api/logout") { location.href = "/login"; return; }
  if (url.endsWith("/backup")) {
    $("state").textContent = "Starting";
    $("detail").textContent = data.message || data.error || "Starting the backup.";
    $("dot").className = "dot run";
    $("live").textContent = "Live";
  } else if (url.endsWith("/check")) {
    $("state").textContent = "Checking";
    $("detail").textContent = data.message || data.error || "Reading the backup archives.";
    $("dot").className = "dot run";
    $("live").textContent = "Live";
  }
  $("barnote").textContent = data.message || data.error || "";
  arm(300);
}
$("run").onclick = () => act("/api/actions/backup", "Start a backup now? The backup disk will spin up.");
$("checkrepo").onclick = () => act("/api/actions/check", "Check the repository now? The backup disk will spin up.");
$("logout").onclick = () => act("/api/logout");
async function tick() {
  try {
    const r = await fetch("/api/status", { cache: "no-store" });
    if (r.status === 401) { location.href = "/login"; return null; }
    const data = await r.json();
    render(data);
    return data;
  } catch (e) {
    $("live").textContent = "Cannot reach the status service";
    $("dot").className = "dot bad";
    return null;
  }
}
let soon = 0;
function arm(ms) {
  clearTimeout(soon);
  soon = setTimeout(loop, ms);
}
async function loop() {
  const data = await tick();
  arm(data && data.state === "running" ? 2000 : 15000);
}
loop();
</script>
</body>
</html>
"""


def run(args, timeout=8, env=None):
    try:
        proc = subprocess.run(
            args,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def df_bytes(path):
    code, out, _ = run(["df", "-B1", "--output=size,used,avail,target", path])
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if code != 0 or len(lines) < 2:
        return {"size": None, "used": None, "avail": None, "mount_ok": False}
    parts = lines[-1].split()
    try:
        size, used, avail = (int(parts[0]), int(parts[1]), int(parts[2]))
    except (ValueError, IndexError):
        return {"size": None, "used": None, "avail": None, "mount_ok": False}
    return {"size": size, "used": used, "avail": avail, "mount_ok": True}


def mount_uuid(path):
    code, out, _ = run(["findmnt", "-n", "-o", "UUID,TARGET", "-T", path])
    if code != 0 or not out:
        return "", ""
    bits = out.split()
    if len(bits) < 2:
        return "", ""
    return bits[0], bits[1]


def borg_running():
    code, out, _ = run(["ps", "-C", "borg", "-o", "etime=,pcpu=,args="])
    create = [ln.strip() for ln in out.splitlines() if "borg create" in ln]
    if not create:
        code, out, _ = run(["ps", "-eo", "etime=,pcpu=,args="])
        create = [ln.strip() for ln in out.splitlines() if "borg create" in ln]
    if not create:
        return None
    parts = create[0].split(None, 2)
    return {"elapsed": parts[0] if parts else "", "cpu": parts[1] if len(parts) > 1 else ""}


def read_log_all():
    try:
        with open(C.log, "r", encoding="utf-8", errors="replace") as fh:
            return [ln for ln in fh.read().splitlines() if ln.strip()]
    except OSError:
        return []


def parse_stamp(line):
    match = re.match(
        r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?)",
        line,
    )
    if not match:
        return None
    try:
        return datetime.fromisoformat(match.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None


def last_success(lines):
    started = None
    found = None
    for line in lines:
        stamp = parse_stamp(line)
        if stamp is None:
            continue
        if "starting local borg create" in line or line.endswith("starting borg create"):
            started = stamp
        elif "borg create exit" in line and started is not None:
            code_match = re.search(r"borg create exit (\d+)", line)
            code = int(code_match.group(1)) if code_match else 2
            if code < 2:
                found = {"at": stamp, "seconds": max(0, int((stamp - started).total_seconds()))}
            started = None
    if not found:
        return None
    return {
        "at": found["at"].isoformat(timespec="seconds"),
        "took": format_duration(found["seconds"]),
    }


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 90:
        return str(seconds) + " sec"
    minutes = round(seconds / 60)
    if minutes < 90:
        return str(minutes) + " min"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return str(hours) + " h " + str(minutes) + " min"
    return str(hours) + " h"


def write_rate(used):
    global _rate_samples
    if not used:
        return None
    now = time.monotonic()
    _rate_samples.append((now, used))
    _rate_samples = [sample for sample in _rate_samples if now - sample[0] <= 90]
    if len(_rate_samples) < 2:
        return None
    start, end = _rate_samples[0], _rate_samples[-1]
    elapsed = end[0] - start[0]
    if elapsed < 20:
        return None
    rate = (end[1] - start[1]) / elapsed
    if rate < 1024 * 1024:
        return None
    return rate


def latest_outcome(lines):
    """The newest finished backup, restore, or repository check in the log."""
    outcome = ("", "")
    for line in lines:
        if "repository check passed" in line:
            outcome = ("check", "passed")
        elif "repository check failed" in line:
            outcome = ("check", "failed")
        elif "repository check skipped" in line:
            outcome = ("check", "skipped")
        elif "restore finished" in line:
            outcome = ("restore", "ok")
        elif " FAILED " in (" " + line):
            outcome = ("restore", "failed") if "restore" in line.lower() else ("backup", "failed")
        elif "borg create exit" in line:
            match = re.search(r"borg create exit (\d+)", line)
            code = int(match.group(1)) if match else 2
            if code == 0:
                outcome = ("backup", "ok")
            elif code < 2:
                outcome = ("backup", "warning")
            else:
                outcome = ("backup", "failed")
    return outcome


def borg_check_running():
    _, out, _ = run(["ps", "-C", "borg", "-o", "args="])
    if "borg check" in out:
        return True
    _, out, _ = run(["ps", "-eo", "args="])
    return "borg check" in out


def listed_command(text, command):
    pattern = re.compile(r"(?:^|\s)immich_backup\s+" + re.escape(command) + r"(?:\s|$)")
    return any(pattern.search(line) for line in text.splitlines())


def launch_state(command, started, now, lines, process_text):
    """The job a click just started, until its process is gone and the log has the result."""
    if command not in {"backup", "check", "restore"}:
        return ""
    if listed_command(process_text, command):
        return command
    for line in lines:
        stamp = parse_stamp(line)
        if stamp is None or stamp.timestamp() + 1 < started:
            continue
        finished = (
            "borg create exit" in line
            or "repository check passed" in line
            or "repository check failed" in line
            or "repository check skipped" in line
            or "restore finished" in line
            or " FAILED " in (" " + line)
        )
        if finished:
            return ""
    if now - started < 20:
        return command
    return ""


def pending_command(lines, process_text):
    path = os.path.join(C.state_dir, "pending-job")
    try:
        command, started = open(path, encoding="utf-8").read().split()
        started = float(started)
    except (OSError, ValueError):
        return ""
    return launch_state(command, started, time.time(), lines, process_text)


def clock_iso(text):
    """Turn a UTC timer stamp into an ISO time the browser can show locally."""
    match = re.fullmatch(
        r"[A-Za-z]{3}\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s+([A-Za-z0-9_+\-/]+)",
        (text or "").strip(),
    )
    if not match:
        return text or ""
    naive = datetime.fromisoformat(match.group(1) + "T" + match.group(2))
    zone = match.group(3)
    if zone in {"UTC", "GMT", "Z", "Etc/UTC"}:
        return naive.replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    try:
        from zoneinfo import ZoneInfo
        return naive.replace(tzinfo=ZoneInfo(zone)).isoformat(timespec="seconds")
    except Exception:
        return text


def local_iso(text):
    """Attach this machine's offset to a clock time that was stored without one."""
    text = (text or "").strip()
    if not text or re.search(r"(?:Z|[+-]\d{2}:\d{2})$", text):
        return text
    naive = None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%a, %Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            naive = datetime.strptime(text, fmt)
            break
        except ValueError:
            continue
    if naive is None:
        return text
    return naive.replace(tzinfo=datetime.now().astimezone().tzinfo).isoformat(timespec="seconds")


def last_check(lines):
    state = ""
    at = ""
    for line in lines:
        stamp = parse_stamp(line)
        iso = stamp.isoformat(timespec="seconds") if stamp else at
        if "repository check passed" in line:
            state, at = "passed", iso
        elif "repository check failed" in line:
            state, at = "failed", iso
        elif "repository check skipped" in line:
            state, at = "skipped", iso
    _, active, _ = run(["systemctl", "is-active", C.check_service], timeout=4)
    if active.strip() in {"activating", "active"} or borg_check_running():
        state = "running"
    return {"state": state or "unknown", "at": at}


def space_level(backup):
    if not backup.get("mount_ok"):
        return "down"
    avail = backup.get("avail") or 0
    gib = 1024 ** 3
    if avail < 10 * gib:
        return "critical"
    if avail < 20 * gib:
        return "tight"
    if avail < 50 * gib:
        return "low"
    return "ok"


def next_run():
    code, out, _ = run(
        ["systemctl", "show", C.backup_timer, "-p", "NextElapseUSecRealtime", "--value"]
    )
    text = out.strip()
    if code != 0 or not text or text.lower() in {"n/a", ""}:
        return ""
    return clock_iso(text)


def repo_id():
    try:
        with open(os.path.join(C.repo, "config"), encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("id = "):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def load_archives():
    try:
        with open(C.archive_cache, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if (
            isinstance(data, dict)
            and data.get("repo") == repo_id()
            and isinstance(data.get("archives"), list)
        ):
            rows = []
            for item in data["archives"]:
                if not isinstance(item, dict):
                    continue
                rows.append({
                    "name": str(item.get("name") or ""),
                    "time": local_iso(str(item.get("time") or "")),
                })
            return rows
    except (OSError, json.JSONDecodeError):
        pass
    return []


def load_smart():
    try:
        with open(C.smart_file, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    clean = {}
    for key in ("backup", "primary"):
        item = data.get(key)
        if not isinstance(item, dict):
            continue
        checked = item.get("at")
        clean[key] = {
            "ok": bool(item.get("ok")),
            "at": checked if isinstance(checked, (int, float)) else None,
            "checked_at": str(item.get("checked_at") or "")[:40],
            "summary": str(item.get("summary") or "")[:160],
        }
    return clean


def immich_up():
    try:
        with urllib.request.urlopen(C.immich_ping, timeout=1) as resp:
            return resp.status == 200 and b"pong" in resp.read(32).lower()
    except Exception:
        return False


def failure_text(lines):
    kind = ""
    reason = ""
    for line in lines:
        padded = " " + line
        if " starting restore " in padded:
            kind = "restore"
        if " starting local borg create" in line:
            kind = "backup"
        if " FAILED " in padded:
            reason = line.split(" FAILED ", 1)[1].strip()
            if kind == "" and "restore" in reason.lower():
                kind = "restore"
    return kind, reason


def restore_in_progress(lines):
    active = False
    for line in lines:
        padded = " " + line
        if " starting restore " in padded:
            active = True
        if " restore finished" in padded or " FAILED " in padded:
            active = False
    return active


def status():
    backup_uuid, backup_target = mount_uuid(C.backup_mount)
    media_uuid, media_target = mount_uuid(C.media_mount)
    backup = df_bytes(C.backup_mount)
    media = df_bytes(C.media_mount)
    backup["mount_ok"] = backup["mount_ok"] and backup_uuid == C.backup_uuid and backup_target == C.backup_mount
    media["mount_ok"] = media["mount_ok"] and media_uuid == C.media_uuid and media_target == C.media_mount
    proc = borg_running()
    full_log = read_log_all()
    log = full_log[-14:]
    last = log[-1] if log else ""
    _, process_text, _ = run(["ps", "-eo", "args="])
    pending = pending_command(full_log, process_text)
    _, active, _ = run(["systemctl", "is-active", C.backup_service])
    active = active.strip()
    kind, reason = failure_text(full_log)
    backup_alive = bool(proc) or active == "activating" or listed_command(process_text, "backup") or pending == "backup"

    if restore_in_progress(full_log) or listed_command(process_text, "restore") or pending == "restore":
        state = "running"
        headline = "Restoring"
        detail = "Installing Immich and copying the library back."
        if proc and proc.get("elapsed"):
            detail = "Running for " + proc["elapsed"]
    elif backup_alive:
        state = "running"
        headline = "Copying" if proc else "Starting"
        detail = "Starting the backup." if not proc else "Borg is writing the library to the backup disk on this VM."
        if proc and proc.get("elapsed"):
            detail = "Running for " + proc["elapsed"]
    elif borg_check_running() or listed_command(process_text, "check") or pending == "check":
        state = "running"
        headline = "Checking"
        detail = "Reading the backup archives."
    elif active == "failed" or " FAILED " in (" " + last):
        state = "failed"
        headline = "Restore failed" if kind == "restore" else "Backup failed"
        detail = reason or "The last attempt did not finish."
    else:
        job, result = latest_outcome(full_log)
        if job == "check" and result == "passed":
            state, headline, detail = "ok", "Repository is healthy", "The integrity check passed."
        elif job == "check" and result == "failed":
            state, headline, detail = "failed", "Check failed", "The repository check did not pass."
        elif job == "check" and result == "skipped":
            state, headline, detail = "ok", "Check skipped", "A backup was already running."
        elif job == "backup" and result == "ok":
            state, headline, detail = "ok", "Up to date", "Backup finished."
        elif job == "backup" and result == "warning":
            state, headline, detail = "ok", "Finished with a warning", "The archive was saved, with a warning."
        elif job == "restore" and result == "ok":
            state, headline, detail = "ok", "Restored", "Immich was restored from the backup."
        elif result == "failed":
            state = "failed"
            headline = "Restore failed" if job == "restore" else "Backup failed"
            detail = reason or "The last attempt did not finish."
        else:
            state, headline = "ok", "Idle"
            detail = "Waiting for the nightly backup at " + C.schedule_label + "."

    progress = None
    rate = None
    eta = None
    if state == "running" and backup.get("used") and media.get("used"):
        progress = min(99.0, backup["used"] / media["used"] * 100.0)
        rate = write_rate(backup["used"])
        remaining = media["used"] - backup["used"]
        if rate and remaining > 1024 ** 3:
            eta = remaining / rate
    elif state == "ok" and "exit 0" in last:
        progress = 100.0

    archives_note = "The first archive appears when this copy finishes."
    if state == "running":
        archives = load_archives()
        if archives:
            archives_note = "Showing the previous list. A new archive is still being written."
        else:
            archives = []
    else:
        archives = load_archives()
        if not archives:
            archives_note = "No finished archives yet."

    check = last_check(full_log)
    if headline == "Checking":
        check = {"state": "running", "at": check.get("at") or ""}

    return {
        "state": state,
        "headline": headline,
        "detail": detail,
        "progress": progress,
        "elapsed": proc.get("elapsed") if proc else "",
        "cpu": proc.get("cpu") if proc else "",
        "backup": backup,
        "media": media,
        "next_run": next_run(),
        "log": log,
        "archives": archives,
        "archives_note": archives_note,
        "bytes_per_sec": rate,
        "eta_seconds": eta,
        "space": space_level(backup),
        "last_success": last_success(full_log),
        "check": check,
        "immich": "up" if immich_up() else "down",
        "smart": load_smart(),
        "failure": reason if state == "failed" else "",
        "failure_kind": kind if state == "failed" else "",
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def invalidate_cache():
    global _status_cache, _status_cache_at
    with _status_lock:
        _status_cache = None
        _status_cache_at = 0.0


def cached_status():
    global _status_cache, _status_cache_at
    now = time.monotonic()
    ttl = 2 if _status_cache and _status_cache.get("state") == "running" else C.status_ttl
    if _status_cache is not None and now - _status_cache_at < ttl:
        return _status_cache
    with _status_lock:
        now = time.monotonic()
        ttl = 2 if _status_cache and _status_cache.get("state") == "running" else C.status_ttl
        if _status_cache is None or now - _status_cache_at >= ttl:
            _status_cache = status()
            _status_cache_at = now
        return _status_cache


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _send(self, code, body, content_type, headers=None):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for key, value in headers or ():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        from immich_backup.web import dispatch
        dispatch(self, "GET")

    def do_POST(self):
        from immich_backup.web import dispatch
        dispatch(self, "POST")



def serve(cfg):
    configure(cfg)
    os.makedirs(cfg.state_dir, exist_ok=True)
    server = ThreadingHTTPServer((cfg.status_host, cfg.status_port), Handler)
    server.serve_forever()
