from flask import Flask, Response, request, jsonify
from playwright.sync_api import sync_playwright
import random
import re
import time
import urllib.parse
import html

app = Flask(__name__)

WIDTH = 240
HEIGHT = 320
FPS = 5
JPEG_QUALITY = 42

DEFAULT_QUERY = "edit"

search_cache = {}
CACHE_SECONDS = 300


def now():
    return time.time()


def clean_query(q: str):
    q = (q or "").strip()
    if not q:
        q = DEFAULT_QUERY
    return q[:80]


def extract_video_id(url: str):
    m = re.search(r"/video/(\d+)", url)
    if m:
        return m.group(1)
    if url.isdigit():
        return url
    return None


def make_player_url(tiktok_url: str):
    video_id = extract_video_id(tiktok_url)
    if not video_id:
        return None

    return (
        f"https://www.tiktok.com/player/v1/{video_id}"
        "?autoplay=1"
        "&controls=0"
        "&loop=1"
        "&music_info=0"
        "&description=0"
    )


def normalize_tiktok_url(url: str):
    url = html.unescape(url)
    url = url.replace("\\u002F", "/").replace("\\/", "/")
    url = url.split("?")[0]
    url = url.split("&")[0]

    if url.startswith("/@"):
        url = "https://www.tiktok.com" + url

    if "tiktok.com" not in url:
        return None

    if "/video/" not in url:
        return None

    return url


def extract_links_from_html(page_html: str):
    page_html = page_html.replace("\\u002F", "/").replace("\\/", "/")

    found = []

    patterns = [
        r"https://www\.tiktok\.com/@[^\"'\s<>]+/video/\d+",
        r"https://m\.tiktok\.com/v/\d+",
        r"/@[^\"'\s<>]+/video/\d+",
    ]

    for pat in patterns:
        for m in re.findall(pat, page_html):
            url = normalize_tiktok_url(m)
            if url and url not in found:
                found.append(url)

    return found


def collect_tiktok_search_results(query: str, max_results: int = 24):
    query = clean_query(query)

    cached = search_cache.get(query.lower())
    if cached and now() - cached["time"] < CACHE_SECONDS:
        return cached["results"]

    encoded = urllib.parse.quote(query)
    search_urls = [
        f"https://www.tiktok.com/search/video?q={encoded}",
        f"https://www.tiktok.com/search?q={encoded}",
    ]

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            is_mobile=False,
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )

        page = context.new_page()

        for search_url in search_urls:
            try:
                page.goto(search_url, wait_until="domcontentloaded", timeout=35000)
                time.sleep(5)

                # Try closing cookie popup if it appears.
                for txt in ["Accept all", "Accept", "Allow all"]:
                    try:
                        page.get_by_text(txt, exact=False).click(timeout=1200)
                        time.sleep(1)
                        break
                    except:
                        pass

                for _ in range(8):
                    try:
                        links = page.eval_on_selector_all(
                            "a[href*='/video/']",
                            """
                            els => els.map(a => a.href)
                                      .filter(h => h.includes('/video/'))
                            """,
                        )

                        for link in links:
                            url = normalize_tiktok_url(link)
                            if url and url not in results:
                                results.append(url)

                    except:
                        pass

                    page.mouse.wheel(0, 1600)
                    time.sleep(1.1)

                    if len(results) >= max_results:
                        break

                # Extra fallback: parse page HTML.
                try:
                    html_text = page.content()
                    for url in extract_links_from_html(html_text):
                        if url not in results:
                            results.append(url)
                except:
                    pass

            except Exception as e:
                print("SEARCH PAGE ERROR:", e)

            if len(results) >= max_results:
                break

        context.close()
        browser.close()

    final = []

    for i, url in enumerate(results[:max_results]):
        video_id = extract_video_id(url) or str(i + 1)
        final.append({
            "index": i,
            "title": f"TikTok result {i + 1}",
            "url": url,
            "id": video_id,
        })

    search_cache[query.lower()] = {
        "time": now(),
        "results": final,
    }

    return final


def choose_random_video(query: str):
    results = collect_tiktok_search_results(query)

    if not results:
        return None

    return random.choice(results)["url"]


def mjpeg_from_tiktok_url(tiktok_url: str):
    player_url = make_player_url(tiktok_url)

    if not player_url:
        print("BAD URL:", tiktok_url)
        return

    page_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width={WIDTH}, initial-scale=1.0">
        <style>
            html, body {{
                margin: 0;
                padding: 0;
                width: {WIDTH}px;
                height: {HEIGHT}px;
                background: black;
                overflow: hidden;
            }}

            iframe {{
                position: fixed;
                left: 0;
                top: 0;
                width: {WIDTH}px;
                height: {HEIGHT}px;
                border: none;
                background: black;
            }}
        </style>
    </head>
    <body>
        <iframe
            id="tt"
            src="{player_url}"
            allow="autoplay; encrypted-media; fullscreen"
            allowfullscreen>
        </iframe>

        <script>
            function pokePlay() {{
                try {{
                    document.getElementById("tt").contentWindow.postMessage({{
                        type: "play",
                        "x-tiktok-player": true
                    }}, "*");
                }} catch(e) {{}}
            }}

            setInterval(pokePlay, 1000);
        </script>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--autoplay-policy=no-user-gesture-required",
            ],
        )

        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            device_scale_factor=1,
            is_mobile=True,
            has_touch=True,
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Linux; Android 10; CYD) "
                "AppleWebKit/537.36 Chrome/120 Mobile Safari/537.36"
            ),
        )

        page = context.new_page()

        try:
            page.set_content(page_html, wait_until="domcontentloaded")
            time.sleep(6)

            delay = 1.0 / FPS

            while True:
                jpg = page.screenshot(
                    type="jpeg",
                    quality=JPEG_QUALITY,
                    full_page=False,
                )

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpg)).encode() + b"\r\n"
                    b"\r\n" + jpg + b"\r\n"
                )

                time.sleep(delay)

        except Exception as e:
            print("STREAM ERROR:", e)

        finally:
            context.close()
            browser.close()


@app.route("/")
def home():
    return """
    <html>
    <body style="margin:0;background:#050505;color:white;font-family:Arial">
        <div style="padding:18px;position:sticky;top:0;background:#050505;border-bottom:1px solid #222">
            <h2 style="margin:0 0 12px 0">ChipTok Relay</h2>
            <form action="/results">
                <input name="q" style="width:70%;padding:12px;background:#111;color:white;border:1px solid #444"
                       placeholder="Search TikTok, example: car edit">
                <button style="padding:12px;background:#ff0050;color:white;border:0">Search</button>
            </form>
            <p><a style="color:#00f2ea" href="/random?q=edit">Play random #edit</a></p>
        </div>
    </body>
    </html>
    """


@app.route("/results")
@app.route("/watch")
def results_page():
    q = clean_query(request.args.get("q", DEFAULT_QUERY))
    results = collect_tiktok_search_results(q)

    cards = ""

    if not results:
        cards = """
        <div style="padding:20px;color:#ff7777">
            No results. TikTok may have blocked the server, or Playwright/Chromium is not installed.
        </div>
        """

    for item in results:
        encoded_url = urllib.parse.quote(item["url"], safe="")
        cards += f"""
        <div style="
            border-bottom:1px solid #222;
            padding:14px;
            display:flex;
            justify-content:space-between;
            align-items:center;
        ">
            <div>
                <div style="font-size:18px;font-weight:bold">{item["title"]}</div>
                <div style="font-size:11px;color:#777;max-width:620px;overflow:hidden">{item["url"]}</div>
            </div>
            <a href="/watchurl?url={encoded_url}"
               style="background:#ff0050;color:white;text-decoration:none;padding:12px 18px">
               PLAY
            </a>
        </div>
        """

    return f"""
    <html>
    <body style="margin:0;background:#050505;color:white;font-family:Arial">
        <div style="padding:16px;position:sticky;top:0;background:#050505;border-bottom:1px solid #222">
            <form action="/results">
                <input name="q" value="{html.escape(q)}"
                       style="width:65%;padding:12px;background:#111;color:white;border:1px solid #444">
                <button style="padding:12px;background:#00f2ea;color:black;border:0">SEARCH</button>
                <a href="/random?q={urllib.parse.quote(q)}"
                   style="padding:12px;background:#ff0050;color:white;text-decoration:none;margin-left:6px">
                   RANDOM
                </a>
            </form>
            <div style="font-size:12px;color:#777;margin-top:8px">
                Found {len(results)} results for: {html.escape(q)}
            </div>
        </div>

        {cards}
    </body>
    </html>
    """


@app.route("/watchurl")
def watch_url():
    url = request.args.get("url", "")
    if not url:
        return "Missing url", 400

    encoded = urllib.parse.quote(url, safe="")

    return f"""
    <html>
    <body style="margin:0;background:black;display:flex;justify-content:center">
        <div>
            <img src="/stream?url={encoded}" width="240" height="320">
            <div style="font-family:Arial;background:#111;color:white;padding:8px">
                <a href="/results?q=edit" style="color:#00f2ea">Back to results</a>
            </div>
        </div>
    </body>
    </html>
    """


@app.route("/api/search")
def api_search():
    q = clean_query(request.args.get("q", DEFAULT_QUERY))
    results = collect_tiktok_search_results(q)

    return jsonify({
        "ok": True,
        "query": q,
        "count": len(results),
        "results": results,
    })


@app.route("/feed")
def text_feed():
    q = clean_query(request.args.get("q", DEFAULT_QUERY))
    results = collect_tiktok_search_results(q)

    lines = [
        "OK",
        f"QUERY={q}",
        f"COUNT={len(results)}",
    ]

    for item in results:
        safe_title = item["title"].replace("|", " ")
        lines.append(f"ITEM={item['index']}|{safe_title}|{item['url']}")

    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/random")
def random_stream():
    q = clean_query(request.args.get("q", DEFAULT_QUERY))
    print("RANDOM SEARCH:", q)

    video_url = choose_random_video(q)

    if not video_url:
        return "No TikTok results found or TikTok blocked the server.", 404

    print("SELECTED:", video_url)

    return Response(
        mjpeg_from_tiktok_url(video_url),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/search")
def search_stream():
    q = clean_query(request.args.get("q", DEFAULT_QUERY))
    video_url = choose_random_video(q)

    if not video_url:
        return "No TikTok results found or TikTok blocked the server.", 404

    return Response(
        mjpeg_from_tiktok_url(video_url),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/stream")
def direct_stream():
    url = request.args.get("url", "")

    if not url:
        return "Missing url", 400

    return Response(
        mjpeg_from_tiktok_url(url),
        mimetype="multipart/x-mixed-replace; boundary=frame",
    )


@app.route("/health")
def health():
    return "OK"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, threaded=True)