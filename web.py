# Galereya (Telegram Mini App) va sog'liq tekshiruvi serveri.
# Bu fayl bot.py bilan bir papkada turishi kerak.
import os, re, json, time, hmac, hashlib, glob, mimetypes, tempfile, threading, logging
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, parse_qsl, quote

log = logging.getLogger("web")
G = {}  # bot.py ning global o'zgaruvchilari
CACHE_DIR = os.path.join(tempfile.gettempdir(), "gcache")
CACHE_LIMIT = 200 * 1024 * 1024
DL = threading.Semaphore(4)
PAGE_SIZE = 60

PAGE = r'''<!doctype html>
<html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Galereya</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-bg-color,#fff);--tx:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#888);--bt:var(--tg-theme-button-color,#2481cc);--bt2:var(--tg-theme-button-text-color,#fff);--sec:var(--tg-theme-secondary-bg-color,#eee)}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px system-ui,sans-serif}
#bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px;display:flex;gap:6px;overflow-x:auto;border-bottom:1px solid var(--sec)}
.chip{flex:none;padding:6px 12px;border-radius:16px;background:var(--sec);border:0;color:var(--tx);font-size:14px}
.chip.on{background:var(--bt);color:var(--bt2)}
#sel{margin:8px;padding:8px;border-radius:8px;border:0;background:var(--sec);color:var(--tx);width:calc(100% - 16px);font-size:14px}
#grid{display:grid;grid-template-columns:repeat(3,1fr);gap:2px;padding:2px}
.it{position:relative;aspect-ratio:1;background:var(--sec);overflow:hidden}
.it img{width:100%;height:100%;object-fit:cover;display:block}
.ph{display:flex;align-items:center;justify-content:center;height:100%;font-size:28px}
.pl{position:absolute;right:4px;bottom:4px;background:#0008;color:#fff;border-radius:10px;padding:1px 6px;font-size:12px}
#more{display:block;margin:12px auto;padding:10px 24px;border:0;border-radius:10px;background:var(--bt);color:var(--bt2);font-size:15px}
#msg{padding:24px;text-align:center;color:var(--hint)}
#v{position:fixed;inset:0;background:#000;z-index:10;display:none;flex-direction:column}
#v .top{display:flex;justify-content:space-between;align-items:center;padding:10px;color:#fff}
#v .mid{flex:1;display:flex;align-items:center;min-height:0}
#box{flex:1;display:flex;justify-content:center;align-items:center;min-height:0;height:100%;color:#fff;text-align:center}
#box img,#box video{max-width:100%;max-height:100%}
#cap{color:#fff;padding:10px;font-size:14px;max-height:25%;overflow:auto;white-space:pre-wrap}
#v button{background:#fff2;color:#fff;border:0;border-radius:10px;padding:8px 14px;font-size:18px}
</style></head><body>
<div id="bar"></div>
<select id="sel"></select>
<div id="grid"></div>
<div id="msg"></div>
<button id="more" style="display:none">Yana</button>
<div id="v">
 <div class="top"><button id="x">✕</button><span id="n"></span><span>&nbsp;</span></div>
 <div class="mid"><button id="pv">‹</button><div id="box"></div><button id="nx">›</button></div>
 <div id="cap"></div>
</div>
<script>
var tg = window.Telegram && Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }
var Q = encodeURIComponent(tg ? tg.initData : "");
var folder = "all", month = "", page = 0, items = [], cur = 0, meta = null;
function $(id) { return document.getElementById(id); }
function say(t) { $("msg").textContent = t; }
function api(path) {
  return fetch(path + (path.indexOf("?") >= 0 ? "&" : "?") + "init=" + Q).then(function (r) {
    if (!r.ok) { var e = new Error("http"); e.code = r.status; throw e; }
    return r.json();
  });
}
function fail(e) {
  say(e.code == 403 ? "Sessiya tugadi. Botda /start yuboring, parolni kiriting va galereyani qayta oching."
    : e.code == 401 ? "Kirish tasdiqlanmadi. Galereyani bot ichidagi tugma orqali oching."
    : "Xatolik. Keyinroq urinib ko'ring.");
}
function src(it, t) { return "/api/file?id=" + it.id + "&t=" + t + "&init=" + Q; }
function renderBar() {
  var bar = $("bar"); bar.innerHTML = "";
  var chips = [["all", "Hammasi (" + meta.total + ")"], ["none", "Papkasiz (" + meta.none + ")"]];
  meta.folders.forEach(function (f) { chips.push([String(f.id), "📂 " + f.name + " (" + f.n + ")"]); });
  chips.forEach(function (c) {
    var b = document.createElement("button");
    b.className = "chip" + (folder === c[0] ? " on" : "");
    b.textContent = c[1];
    b.onclick = function () { folder = c[0]; renderBar(); load(true); };
    bar.appendChild(b);
  });
}
function renderSel() {
  var s = $("sel");
  s.innerHTML = "<option value=''>📆 Hamma oylar</option>" + meta.months.map(function (m) {
    return "<option value='" + m.m + "'>" + m.m + " (" + m.n + ")</option>";
  }).join("");
  s.onchange = function () { month = s.value; load(true); };
}
function addCell(it, i) {
  var c = document.createElement("div"); c.className = "it";
  if (it.k == "photo") {
    var im = document.createElement("img"); im.loading = "lazy"; im.src = src(it, "thumb");
    im.onerror = function () { c.innerHTML = "<div class='ph'>🖼</div>"; };
    c.appendChild(im);
  } else {
    c.innerHTML = "<div class='ph'>🎥</div>";
    var p = document.createElement("span"); p.className = "pl"; p.textContent = "▶"; c.appendChild(p);
  }
  c.onclick = function () { show(i); };
  $("grid").appendChild(c);
}
function load(reset) {
  if (reset) { page = 0; items = []; $("grid").innerHTML = ""; }
  say("Yuklanmoqda..."); $("more").style.display = "none";
  api("/api/list?folder=" + folder + "&month=" + month + "&page=" + page).then(function (d) {
    d.items.forEach(function (it) { items.push(it); addCell(it, items.length - 1); });
    say(items.length ? "" : "Bu yerda hozircha hech narsa yo'q.");
    $("more").style.display = d.more ? "block" : "none";
  }).catch(fail);
}
function show(i) {
  cur = i; var it = items[i], box = $("box"), el;
  box.innerHTML = "";
  if (it.k == "video") {
    el = document.createElement("video"); el.controls = true; el.autoplay = true; el.playsInline = true;
    el.src = src(it, "full");
    el.onerror = function () { box.textContent = "Video yuklanmadi (20 MB dan katta bo'lishi mumkin)."; };
  } else {
    el = document.createElement("img"); el.src = src(it, "full");
    el.onerror = function () { box.textContent = "Rasm yuklanmadi."; };
  }
  box.appendChild(el);
  $("n").textContent = (i + 1) + " / " + items.length;
  $("cap").textContent = (it.c ? it.c + "\n" : "") + it.d;
  $("v").style.display = "flex";
}
function closeV() { $("v").style.display = "none"; $("box").innerHTML = ""; }
$("x").onclick = closeV;
$("pv").onclick = function () { if (cur > 0) show(cur - 1); };
$("nx").onclick = function () { if (cur < items.length - 1) show(cur + 1); };
$("more").onclick = function () { page++; load(false); };
var sx = null;
$("box").addEventListener("touchstart", function (e) { sx = e.touches[0].clientX; }, { passive: true });
$("box").addEventListener("touchend", function (e) {
  if (sx === null) return;
  var dx = e.changedTouches[0].clientX - sx; sx = null;
  if (Math.abs(dx) > 60) { if (dx < 0) $("nx").click(); else $("pv").click(); }
});
api("/api/folders").then(function (d) { meta = d; renderBar(); renderSel(); load(true); }).catch(fail);
</script></body></html>'''


def verify_init(init):
    """Telegram Mini App initData imzosini tekshiradi. To'g'ri bo'lsa foydalanuvchi ID'sini qaytaradi."""
    try:
        params = dict(parse_qsl(init, keep_blank_values=True))
        got = params.pop("hash", "")
        check = "\n".join(f"{k}={params[k]}" for k in sorted(params))
        secret = hmac.new(b"WebAppData", G["BOT_TOKEN"].encode(), hashlib.sha256).digest()
        calc = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not got or not hmac.compare_digest(calc, got):
            return None
        if time.time() - int(params.get("auth_date", "0")) > 86400:
            return None
        return int(json.loads(params["user"])["id"])
    except Exception:
        return None


def trim_cache():
    try:
        files = []
        for n in os.listdir(CACHE_DIR):
            p = os.path.join(CACHE_DIR, n)
            if os.path.isfile(p):
                files.append((os.path.getmtime(p), os.path.getsize(p), p))
        total = sum(s for _, s, _ in files)
        for _, s, p in sorted(files):
            if total <= CACHE_LIMIT:
                break
            os.remove(p)
            total -= s
    except Exception:
        pass


def tg_file(fid):
    """Telegram'dan faylni yuklab, keshga qo'yadi. Muvaffaqiyatsiz bo'lsa None."""
    h = hashlib.sha1(fid.encode()).hexdigest()
    hit = [p for p in glob.glob(os.path.join(CACHE_DIR, h + ".*")) if not p.endswith(".part")]
    if hit:
        os.utime(hit[0])
        return hit[0]
    token = G["BOT_TOKEN"]
    with DL:
        try:
            url = "https://api.telegram.org/bot%s/getFile?file_id=%s" % (token, quote(fid))
            with urllib.request.urlopen(url, timeout=20) as r:
                fp = json.load(r)["result"]["file_path"]
            dest = os.path.join(CACHE_DIR, h + (os.path.splitext(fp)[1] or ".bin"))
            tmp = dest + ".part"
            with urllib.request.urlopen("https://api.telegram.org/file/bot%s/%s" % (token, fp),
                                        timeout=60) as r, open(tmp, "wb") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(tmp, dest)
        except Exception:
            log.warning("Telegram faylini yuklab bo'lmadi")
            return None
    trim_cache()
    return dest


def make_thumb(path):
    """Pillow o'rnatilgan bo'lsa kichik nusxa yasaydi, aks holda asl rasmni qaytaradi."""
    try:
        from PIL import Image
    except Exception:
        return path
    out = os.path.join(CACHE_DIR, "t_" + os.path.splitext(os.path.basename(path))[0] + ".jpg")
    if os.path.exists(out):
        return out
    try:
        with Image.open(path) as im:
            im = im.convert("RGB")
            im.thumbnail((360, 360))
            im.save(out, "JPEG", quality=70)
        return out
    except Exception:
        return path


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def do_HEAD(self):
        self._send(200, b"")

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/gallery":
                self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            elif u.path.startswith("/api/"):
                self._api(u.path, q)
            else:
                self._send(200, b"OK")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("Veb xatosi")
            try:
                self._json({"error": "server"}, 500)
            except Exception:
                pass

    def _api(self, path, q):
        uid = verify_init(q.get("init", [""])[0])
        if uid is None:
            return self._json({"error": "auth"}, 401)
        if uid in G["BLOCKED"] or not G["is_auth"](uid):
            return self._json({"error": "session"}, 403)
        run = G["run"]
        media = "deleted=0 AND kind IN ('photo','video')"
        if path == "/api/folders":
            folders = [{"id": r["id"], "name": r["name"], "n": r["n"]} for r in run(
                "SELECT f.id, f.name, (SELECT COUNT(*) FROM items i WHERE i.folder_id=f.id AND "
                "i.deleted=0 AND i.kind IN ('photo','video')) n FROM folders f ORDER BY f.name")]
            months = [{"m": r["m"], "n": r["n"]} for r in run(
                f"SELECT substr(created,1,7) m, COUNT(*) n FROM items WHERE {media} "
                "GROUP BY m ORDER BY m DESC LIMIT 36") if r["m"]]
            total = run(f"SELECT COUNT(*) c FROM items WHERE {media}")[0]["c"]
            none = run(f"SELECT COUNT(*) c FROM items WHERE {media} AND folder_id IS NULL")[0]["c"]
            return self._json({"folders": folders, "months": months, "total": total, "none": none})
        if path == "/api/list":
            where, args = [media], []
            folder = q.get("folder", ["all"])[0]
            if folder == "none":
                where.append("folder_id IS NULL")
            elif folder.isdigit():
                where.append("folder_id=?")
                args.append(int(folder))
            month = q.get("month", [""])[0]
            if re.fullmatch(r"\d{4}-\d{2}", month):
                where.append("created LIKE ?")
                args.append(month + "%")
            try:
                page = max(0, int(q.get("page", ["0"])[0]))
            except ValueError:
                page = 0
            rows = run("SELECT id, kind, caption, created FROM items WHERE " + " AND ".join(where) +
                       " ORDER BY id DESC LIMIT ? OFFSET ?", args + [PAGE_SIZE + 1, page * PAGE_SIZE])
            more = len(rows) > PAGE_SIZE
            items = [{"id": r["id"], "k": r["kind"], "c": r["caption"] or "", "d": r["created"]}
                     for r in rows[:PAGE_SIZE]]
            return self._json({"items": items, "more": more})
        if path == "/api/file":
            return self._file(q, run, media)
        return self._json({"error": "nf"}, 404)

    def _file(self, q, run, media):
        try:
            iid = int(q.get("id", [""])[0])
        except ValueError:
            return self._json({"error": "bad"}, 400)
        want = q.get("t", ["full"])[0]
        r = run(f"SELECT kind, file_id FROM items WHERE id=? AND {media}", (iid,))
        if not r or (want == "thumb" and r[0]["kind"] != "photo"):
            return self._json({"error": "nf"}, 404)
        path = tg_file(r[0]["file_id"])
        if not path:
            return self._json({"error": "nf"}, 404)
        if want == "thumb":
            path = make_thumb(path)
        self._serve(path)

    def _serve(self, path):
        size = os.path.getsize(path)
        ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
        start, end, code = 0, size - 1, 200
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (self.headers.get("Range") or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else size - 1
            else:
                start = max(0, size - int(m.group(2)))
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            code = 206
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.send_header("Cache-Control", "private, max-age=86400")
        if code == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def add_menu_button(g, url):
    """Asosiy menyuning eng tepasiga 🖼 Galereya tugmasini qo'shadi."""
    from telegram import WebAppInfo
    Btn, Markup, orig = g["Btn"], g["Markup"], g["kb_main"]

    def kb_main(uid):
        rows = [list(r) for r in orig(uid).inline_keyboard]
        rows.insert(0, [Btn("🖼 Galereya", web_app=WebAppInfo(url=url))])
        return Markup(rows)

    g["kb_main"] = kb_main


def start(g):
    """bot.py dan chaqiriladi: veb-serverni ishga tushiradi va galereya tugmasini qo'shadi."""
    G.clear()
    G.update(g)
    os.makedirs(CACHE_DIR, exist_ok=True)
    port = int(os.environ.get("PORT", 10000))
    server = ThreadingHTTPServer(("0.0.0.0", port), H)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/")
    if url.startswith("https://"):
        add_menu_button(g, url + "/gallery")
    else:
        log.warning("PUBLIC_URL topilmadi: Galereya tugmasi qo'shilmadi")
    return server
