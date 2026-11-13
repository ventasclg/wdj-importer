#!/usr/bin/env python3
"""
Webdesign Journal News Importer — GitHub Actions Version
Holt neue Beiträge von webdesign-journal.de und lädt sie per FTP auf den Grav-Server.
Credentials kommen aus Umgebungsvariablen (GitHub Secrets).
wdj_seen.json wird im Repo gespeichert und nach jedem Lauf committed.
"""

import ftplib
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

# ── Konfiguration ──────────────────────────────────────────────────────────────
RSS_URL     = "https://www.webdesign-journal.de/feed/"
HOST        = os.environ.get("FTP_HOST", "w0193fc7.kasserver.com")
FTP_USER    = os.environ.get("FTP_USER", "")
FTP_PASS    = os.environ.get("FTP_PASS", "")
REMOTE_BASE = "/user/pages/05.blog"
MAX_NEW     = 5   # max. neue Beiträge pro Lauf

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE  = os.path.join(SCRIPT_DIR, "wdj_seen.json")

# ── Inline-CSS ─────────────────────────────────────────────────────────────────
ARTICLE_CSS = """\
<style>
.container-narrow{max-width:760px;margin:0 auto;padding:0 24px}
.article-hero{background:#f4f5f7;padding:56px 0 0}
.breadcrumb{font-size:13px;color:var(--muted,#888);margin-bottom:16px}
.breadcrumb a{color:var(--ink-2,#444);text-decoration:none}
.article-hero h1{font-size:clamp(22px,4vw,36px);line-height:1.2;margin:12px 0 24px}
.meta{display:flex;gap:12px;align-items:center;font-size:13px;color:var(--muted,#888);margin-bottom:40px;flex-wrap:wrap}
.source-badge{font-weight:700;color:var(--ink-2,#444)}
.sep{width:4px;height:4px;border-radius:50%;background:var(--muted,#bbb);display:inline-block}
.article-cover{border-radius:12px;overflow:hidden;margin-bottom:0}
.article-cover img{width:100%;height:auto;display:block}
.article-body{padding:40px 0}
.article-body p{line-height:1.8;color:var(--ink-2,#444);margin:0 0 18px}
.article-body a{color:var(--blue,#1c54b2)}
.tag{display:inline-block;padding:4px 10px;border-radius:999px;background:var(--blue,#1c54b2);color:#fff;font-size:11px;font-weight:700;letter-spacing:.06em;text-transform:uppercase}
.source-box{border-top:1px solid #e8eaee;padding:20px 0;font-size:13px;color:var(--muted,#888);margin-bottom:48px}
.source-box a{color:var(--blue,#1c54b2);font-weight:700;text-decoration:none}
.read-more-btn{display:inline-flex;align-items:center;gap:8px;margin-top:8px;padding:14px 28px;background:var(--blue,#1c54b2);color:#fff;border-radius:999px;font-weight:700;font-size:15px;text-decoration:none;transition:opacity .18s}
.read-more-btn:hover{opacity:.85}
</style>"""

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def load_seen():
    if os.path.isfile(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=2)

def slugify(text):
    text = text.lower()
    for src, dst in [("ä","ae"),("ö","oe"),("ü","ue"),("ß","ss"),
                     ("Ä","ae"),("Ö","oe"),("Ü","ue")]:
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:60]

def url_to_slug(url):
    path = url.rstrip("/").split("/")[-1]
    return slugify(path) if path else slugify(url)

def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()

def format_date_grav(pub_date_str):
    try:
        dt = parsedate_to_datetime(pub_date_str).astimezone(timezone.utc)
        return dt.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return datetime.now().strftime("%d-%m-%Y %H:%M")

def format_date_human(pub_date_str):
    months = ["Januar","Februar","März","April","Mai","Juni",
              "Juli","August","September","Oktober","November","Dezember"]
    try:
        dt = parsedate_to_datetime(pub_date_str).astimezone(timezone.utc)
        return f"{dt.day}. {months[dt.month-1]} {dt.year}"
    except Exception:
        return ""

def fetch_feed():
    req = urllib.request.Request(RSS_URL, headers={"User-Agent": "VentasNewsFetcher/1.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read()

def parse_items(xml_bytes):
    root = ET.fromstring(xml_bytes)
    items = []
    for item in root.iter("item"):
        title   = item.findtext("title", "").strip()
        link    = item.findtext("link", "").strip()
        pubdate = item.findtext("pubDate", "")
        desc    = strip_html(item.findtext("description", ""))
        image   = None
        enc = item.find("enclosure")
        if enc is not None and "image" in (enc.get("type") or ""):
            image = enc.get("url")
        items.append({"title": title, "link": link, "pubdate": pubdate,
                      "desc": desc, "image": image})
    return items

def build_page(item):
    slug       = url_to_slug(item["link"])
    grav_date  = format_date_grav(item["pubdate"])
    human_date = format_date_human(item["pubdate"])
    title_esc  = item["title"].replace("'", "&#39;")
    desc_esc   = item["desc"].replace("'", "&#39;")[:160]
    desc_short = (item["desc"][:300].rsplit(" ", 1)[0] + " …"
                  if len(item["desc"]) > 300 else item["desc"])

    cover_html = ""
    if item.get("image"):
        cover_html = (f'  <div class="container-narrow">\n'
                      f'    <div class="article-cover">'
                      f'<img src="{item["image"]}" alt="{title_esc}" loading="lazy">'
                      f'</div>\n  </div>\n')

    content = f"""---
title: '{title_esc}'
date: '{grav_date}'
description: '{desc_esc}'
---

{ARTICLE_CSS}

<section class="article-hero">
  <div class="container-narrow">
    <div class="breadcrumb"><a href="../../blog">Blog</a> &nbsp;·&nbsp; KI &amp; Webdesign</div>
    <span class="tag">KI &amp; Webdesign</span>
    <h1>{item['title']}</h1>
    <div class="meta">
      <span class="source-badge">Webdesign Journal</span>
      <span class="sep"></span>
      <span>{human_date}</span>
    </div>
  </div>
{cover_html}</section>

<section style="padding-top:0">
  <div class="container-narrow">
    <article class="article-body">
      <p>{desc_short}</p>
      <a href="{item['link']}" target="_blank" rel="noopener" class="read-more-btn">
        Vollständigen Artikel lesen &rarr;
      </a>
    </article>
    <div class="source-box">
      Quelle: <a href="https://www.webdesign-journal.de" target="_blank" rel="noopener">Webdesign Journal</a>
    </div>
  </div>
</section>
"""
    return slug, content

def ftp_upload(slug, md_content):
    remote_dir = f"{REMOTE_BASE}/wdj-{slug}"
    tmp_file   = os.path.join(SCRIPT_DIR, f"_tmp_{slug}.md")

    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write(md_content)

    ftp = ftplib.FTP(HOST, timeout=60)
    ftp.login(FTP_USER, FTP_PASS)
    ftp.set_pasv(True)
    ftp.voidcmd("TYPE I")
    try:
        ftp.mkd(remote_dir)
    except ftplib.error_perm:
        pass
    ftp.cwd(remote_dir)
    with open(tmp_file, "rb") as f:
        ftp.storbinary("STOR blog-item.md", f)
    ftp.quit()
    os.remove(tmp_file)

# ── Hauptprogramm ─────────────────────────────────────────────────────────────

def main():
    if not FTP_USER or not FTP_PASS:
        raise SystemExit("FTP_USER / FTP_PASS nicht gesetzt (GitHub Secrets fehlen).")

    print(f"Feed: {RSS_URL}")
    seen  = load_seen()
    xml   = fetch_feed()
    items = parse_items(xml)
    print(f"{len(items)} Einträge im Feed, {len(seen)} bereits importiert.")

    new_items = [i for i in items if i["link"] not in seen][:MAX_NEW]
    if not new_items:
        print("Keine neuen Einträge — fertig.")
        return

    print(f"{len(new_items)} neue Einträge:")
    for item in new_items:
        print(f"  → {item['title']}")
        slug, content = build_page(item)
        ftp_upload(slug, content)
        seen.add(item["link"])
        print(f"    ✓ /blog/wdj-{slug}")

    save_seen(seen)
    print(f"\n{len(new_items)} Beiträge importiert.")

if __name__ == "__main__":
    main()
