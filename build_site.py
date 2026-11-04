#!/usr/bin/env python3
"""產生 GitHub Pages 網站：一般版與管理者版。

    python3 build_site.py [輸出目錄，預設 _site]

  <輸出目錄>/index.html        一般版（公開網址）：沒有匯出／匯入、資料說明與檢核
  <輸出目錄>/admin/index.html  管理者版：完整功能，整頁以密碼加密

管理者版的密碼從環境變數 ADMIN_PASSWORD 讀（GitHub Actions 由 repo secret 帶入）；
沒設就產生「尚未設定密碼」的說明頁，一般版照常發布。

加密方式：PBKDF2-SHA256（60 萬次）導出金鑰 → AES-256-GCM 加密整頁 HTML。
瀏覽器端用 Web Crypto 解密後以 document.write 換成原頁，原頁的 script 照常執行。
密碼錯誤時 GCM 驗證會失敗，頁面內容不會外流；靜態網站只能靠這種方式保護，
所以密碼要夠長（建議 12 字以上），否則可被離線暴力猜。
"""
from __future__ import annotations

import base64
import hashlib
import html as htmlmod
import os
import re
import sys
from pathlib import Path

import bonddata as bd

ITER = 600_000
# 固定的網站專屬 salt：每天重產時金鑰不變，勾「記住」的裝置不必每天重輸密碼。
# IV 每次隨機，所以同一把金鑰重複使用仍然安全。
SALT = hashlib.sha256(b"govbond-monitor/admin/v1").digest()[:16]

WRAP_CSP = ('<meta http-equiv="Content-Security-Policy" content="'
            "default-src 'none'; script-src {hashes}; style-src 'unsafe-inline'; "
            "img-src data:; font-src local; connect-src 'none'; object-src 'none'; "
            "base-uri 'none'; form-action 'none'"
            '">')

# 包裝頁的 script 一定要整段包在函式裡：document.open 之後全域物件不變，
# 若這裡宣告頂層 const／let，會跟原頁同名變數衝突而讓原頁 script 整段失效。
WRAP_JS = r"""(function(){
"use strict";
var KEY_ITEM='govbond.admin.key';
var ct=document.getElementById('ct').textContent.trim();
var form=document.getElementById('f'), pw=document.getElementById('pw'),
    msg=document.getElementById('msg'), btn=document.getElementById('go'), rem=document.getElementById('rem');
function b64(s){ var b=atob(s), u=new Uint8Array(b.length); for(var i=0;i<b.length;i++) u[i]=b.charCodeAt(i); return u; }
function ub64(buf){ var u=new Uint8Array(buf), s=''; for(var i=0;i<u.length;i++) s+=String.fromCharCode(u[i]); return btoa(s); }
var P=JSON.parse(ct), SALT=b64(P.salt), IV=b64(P.iv), DATA=b64(P.data);
function store(k,v){ try{ if(v==null) k.removeItem(KEY_ITEM); else k.setItem(KEY_ITEM,v); }catch(e){} }
function load(k){ try{ return k.getItem(KEY_ITEM); }catch(e){ return null; } }
function open(raw){
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['decrypt'])
    .then(function(key){ return crypto.subtle.decrypt({name:'AES-GCM', iv:IV}, key, DATA); })
    .then(function(pt){ return new TextDecoder().decode(pt); });
}
// 一定要等包裝頁載入完才換頁：解析器還在跑時 document.open 會被忽略，原頁會被塞進現有 body（標題、CSP 都失效）
function show(html){
  var go=function(){ document.open(); document.write(html); document.close(); };
  if(document.readyState==='complete') go(); else addEventListener('load', go, {once:true});
}
function derive(pass){
  return crypto.subtle.importKey('raw', new TextEncoder().encode(pass), 'PBKDF2', false, ['deriveBits'])
    .then(function(k){ return crypto.subtle.deriveBits({name:'PBKDF2', hash:'SHA-256', salt:SALT, iterations:P.iter}, k, 256); });
}
if(!window.crypto || !crypto.subtle){
  msg.textContent='這個瀏覽器不支援網頁加密功能（需以 https 開啟），無法解鎖。'; btn.disabled=true; return;
}
// 先試記住的金鑰（本分頁或勾過「記住」的裝置）
var saved=load(sessionStorage)||load(localStorage);
if(saved){
  document.body.classList.add('busy');
  open(b64(saved)).then(show, function(){
    store(sessionStorage,null); store(localStorage,null);     // 密碼已更換，舊金鑰作廢
    document.body.classList.remove('busy'); pw.focus();
  });
} else pw.focus();
form.addEventListener('submit', function(e){
  e.preventDefault();
  if(!pw.value){ pw.focus(); return; }
  btn.disabled=true; msg.className=''; msg.textContent='驗證中…';
  var raw;
  derive(pw.value).then(function(bits){ raw=bits; return open(bits); }).then(function(html){
    var k=ub64(raw);
    store(sessionStorage,k);
    if(rem.checked) store(localStorage,k); else store(localStorage,null);
    show(html);
  }, function(){
    btn.disabled=false; msg.className='bad'; msg.textContent='密碼錯誤，請再試一次。'; pw.select();
  });
});
})();"""

WRAP_HTML = """<!doctype html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<meta name="referrer" content="no-referrer">
{csp}
<title>台灣公債買賣斷市場觀測｜管理者版</title>
<style>
:root{{color-scheme:light dark;--bg:#f4f6f9;--card:#fff;--ink:#14202e;--muted:#5d6b7a;--line:#d7dde5;--acc:#2f6fd1;--bad:#c0392b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0d131b;--card:#151d28;--ink:#e6edf5;--muted:#93a1b1;--line:#2a3646;--acc:#6aa3ff;--bad:#ff7b6b}}}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:16px;
  background:var(--bg);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang TC","Noto Sans TC","Microsoft JhengHei",sans-serif}}
.box{{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:28px 26px}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{color:var(--muted);font-size:13px;margin:0 0 18px}}
label{{display:block;font-size:13px;color:var(--muted);margin-bottom:6px}}
input[type=password]{{width:100%;font:inherit;padding:10px 12px;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit}}
input[type=password]:focus{{outline:2px solid var(--acc);outline-offset:1px}}
.rem{{display:flex;gap:6px;align-items:center;margin:12px 0 16px;font-size:13px;color:var(--muted)}}
button{{width:100%;font:inherit;font-weight:600;padding:10px;border:0;border-radius:8px;background:var(--acc);color:#fff;cursor:pointer}}
button:disabled{{opacity:.6;cursor:default}}
#msg{{min-height:1.6em;margin-top:10px;font-size:13px;color:var(--muted)}} #msg.bad{{color:var(--bad)}}
.foot{{margin-top:16px;font-size:12px;color:var(--muted)}} .foot a{{color:var(--acc)}}
body.busy .box{{visibility:hidden}}
</style>
</head>
<body>
<form class="box" id="f" autocomplete="on">
  <h1>台灣公債買賣斷市場觀測</h1>
  <p class="sub">管理者版（含匯出／匯入、資料說明與檢核）</p>
  <input type="text" name="username" value="admin" autocomplete="username" hidden>
  <label for="pw">密碼</label>
  <input type="password" id="pw" autocomplete="current-password" required>
  <label class="rem"><input type="checkbox" id="rem"> 在這台裝置記住（共用電腦請勿勾選）</label>
  <button id="go" type="submit">開啟</button>
  <div id="msg" role="status" aria-live="polite"></div>
  <div class="foot">一般使用者請改用<a href="../">公開版</a>。</div>
</form>
<script type="application/json" id="ct">{payload}</script>
<script>{js}</script>
</body>
</html>
"""

PLACEHOLDER = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>台灣公債買賣斷市場觀測｜管理者版</title></head>
<body style="font:15px/1.7 -apple-system,'PingFang TC',sans-serif;padding:24px">
<p>管理者版尚未設定密碼（GitHub repo secret <code>ADMIN_PASSWORD</code>），暫不開放。</p>
<p><a href="../">前往公開版</a></p></body></html>
"""


def encrypt_page(html: str, password: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), SALT, ITER, dklen=32)
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, html.encode("utf-8"), None)
    b = lambda x: base64.b64encode(x).decode()           # noqa: E731
    payload = '{"iter":%d,"salt":"%s","iv":"%s","data":"%s"}' % (ITER, b(SALT), b(iv), b(ct))

    inner = re.findall(r"<script>(.*?)</script>", html, re.S)
    if len(inner) != 1:
        raise SystemExit("原頁應該只有一段 inline script")
    # 包裝頁的 CSP 在 document.open 後仍然有效，所以要一併放行原頁 script 的 hash
    hashes = f"'{bd.script_hash(WRAP_JS)}' '{bd.script_hash(inner[0])}'"
    return WRAP_HTML.format(csp=WRAP_CSP.format(hashes=hashes), payload=htmlmod.escape(payload, quote=False), js=WRAP_JS)


def main(argv):
    outdir = Path(argv[0]) if argv else Path("_site")
    store = bd.read_store()
    if not store:
        sys.exit("沒有資料檔（data/records.csv）")
    payload = bd.build_payload(store, f"櫃買日檔更新至 {max(r['d'] for r in store)}")

    (outdir / "admin").mkdir(parents=True, exist_ok=True)
    (outdir / "index.html").write_text(bd.render_html(payload, "public"), encoding="utf-8")

    pw = os.environ.get("ADMIN_PASSWORD", "")
    admin = outdir / "admin" / "index.html"
    if pw:
        admin.write_text(encrypt_page(bd.render_html(payload, "full"), pw), encoding="utf-8")
        print(f"管理者版（已加密）：{admin}")
    else:
        admin.write_text(PLACEHOLDER, encoding="utf-8")
        print("未設定 ADMIN_PASSWORD，管理者版僅放說明頁")
    print(f"一般版：{outdir / 'index.html'}\n{bd.summary(payload)}")


if __name__ == "__main__":
    main(sys.argv[1:])
