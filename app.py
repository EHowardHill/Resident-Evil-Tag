"""
VR Webcam Viewer — Flask app that streams the default webcam
and displays it in one half of a split-screen VR layout.

Single feed only (no duplication artefacts). Three-finger tap
swaps which eye (left / right panel) receives the image.
"""

import time
import threading
import cv2
from flask import Flask, Response, render_template_string

app = Flask(__name__)


# ── Shared camera ────────────────────────────────────────────────
class Camera:
    """Thread-safe wrapper around a single VideoCapture instance."""

    def __init__(self):
        self.lock = threading.Lock()
        self.frame = None
        self.cap = None
        self._running = False

    def start(self):
        if self._running:
            return
        self.cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(0)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        time.sleep(1)
        self._running = True
        thread = threading.Thread(target=self._capture_loop, daemon=True)
        thread.start()
        print("[camera] capture thread started")

    def _capture_loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            frame = cv2.flip(frame, 1)  # mirror for natural VR feel
            _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            with self.lock:
                self.frame = buf.tobytes()
        self.cap.release()

    def get_frame(self):
        with self.lock:
            return self.frame


camera = Camera()


# ── HTML ─────────────────────────────────────────────────────────
HTML = r"""
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, user-scalable=no, maximum-scale=1.0">
<title>VR Webcam</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 100%; height: 100%; overflow: hidden; background: #000; touch-action: none; }
  #container {
    display: flex;
    width: 100vw;
    height: 100vh;
    touch-action: none;
  }
  .eye {
    width: 50vw;
    height: 100vh;
    overflow: hidden;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: none;
  }
  .eye img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    transform-origin: center center;
    will-change: transform;
  }
  #divider {
    position: fixed;
    top: 0;
    left: 50%;
    width: 2px;
    height: 100vh;
    background: #222;
    z-index: 10;
  }
  #side-toast {
    position: fixed;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    color: #fff;
    font: bold 28px sans-serif;
    background: rgba(0,0,0,0.7);
    padding: 12px 32px;
    border-radius: 10px;
    z-index: 100;
    pointer-events: none;
    opacity: 0;
    transition: opacity 0.35s;
  }
  #side-toast.show { opacity: 1; }
</style>
<script src="https://code.jquery.com/jquery-3.7.1.min.js"></script>
</head>
<body>
  <div id="container">
    <div class="eye" id="left-eye"></div>
    <div class="eye" id="right-eye"></div>
  </div>
  <div id="divider"></div>
  <div id="side-toast"></div>

  <script>
    $(function () {

      // ── Single feed element ────────────────────────────────
      var $feed = $('<img>').attr({ id: 'feed', src: '/video_feed', alt: 'Webcam' });
      var activeEye = 'left';

      function placeFeed(side) {
        activeEye = side;
        $('#left-eye, #right-eye').empty();
        $('#' + side + '-eye').append($feed);
        applyTransform();
        var $t = $('#side-toast').text(side === 'left' ? '\u25C0  LEFT' : 'RIGHT  \u25B6');
        $t.addClass('show');
        clearTimeout(placeFeed._tid);
        placeFeed._tid = setTimeout(function () { $t.removeClass('show'); }, 800);
      }
      placeFeed('left');

      // ── Transform state ────────────────────────────────────
      var scale = 1, tx = 0, ty = 0;
      var MIN_SCALE = 0.5, MAX_SCALE = 4;

      function applyTransform() {
        $feed.css('transform', 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')');
      }

      // ── Pinch-to-zoom + single-finger pan ──────────────────
      var startDist = null, startScale = 1;
      var startTx, startTy, startMid = null;
      var dragging = false, dragStart = null;

      function dist(t) {
        var dx = t[1].clientX - t[0].clientX;
        var dy = t[1].clientY - t[0].clientY;
        return Math.sqrt(dx * dx + dy * dy);
      }
      function mid(t) {
        return {
          x: (t[0].clientX + t[1].clientX) / 2,
          y: (t[0].clientY + t[1].clientY) / 2
        };
      }

      document.addEventListener('touchstart', function (e) {
        if (e.touches.length === 2) {
          dragging   = false;
          startDist  = dist(e.touches);
          startScale = scale;
          startMid   = mid(e.touches);
          startTx    = tx;
          startTy    = ty;
        } else if (e.touches.length === 1) {
          dragging  = true;
          dragStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
          startTx   = tx;
          startTy   = ty;
        }
      }, { passive: true });

      document.addEventListener('touchmove', function (e) {
        e.preventDefault();
        if (e.touches.length === 2 && startDist) {
          var newScale = startScale * (dist(e.touches) / startDist);
          scale = Math.max(MIN_SCALE, Math.min(MAX_SCALE, newScale));
          var m = mid(e.touches);
          tx = startTx + (m.x - startMid.x);
          ty = startTy + (m.y - startMid.y);
          applyTransform();
        } else if (e.touches.length === 1 && dragging) {
          tx = startTx + (e.touches[0].clientX - dragStart.x);
          ty = startTy + (e.touches[0].clientY - dragStart.y);
          applyTransform();
        }
      }, { passive: false });

      document.addEventListener('touchend', function () {
        startDist = null;
        dragging  = false;
      }, { passive: true });

      // ── Three-finger tap  →  swap active eye ───────────────
      document.addEventListener('touchstart', function (e) {
        if (e.touches.length === 3) {
          placeFeed(activeEye === 'left' ? 'right' : 'left');
        }
      }, { passive: true });

      // ── Two-finger double-tap  →  reset zoom / pan ─────────
      var lastTwoTap = 0;
      document.addEventListener('touchstart', function (e) {
        if (e.touches.length === 2) {
          var now = Date.now();
          if (now - lastTwoTap < 400) {
            scale = 1; tx = 0; ty = 0;
            applyTransform();
          }
          lastTwoTap = now;
        }
      }, { passive: true });

      // ── Double-tap  →  toggle fullscreen ───────────────────
      var lastTap = 0;
      $(document).on('touchend click', function (e) {
        var now = Date.now();
        if (now - lastTap < 350) {
          e.preventDefault();
          if (!document.fullscreenElement && !document.webkitFullscreenElement) {
            var el = document.documentElement;
            var fn = el.requestFullscreen || el.webkitRequestFullscreen || el.msRequestFullscreen;
            if (fn) fn.call(el);
          } else {
            var ex = document.exitFullscreen || document.webkitExitFullscreen || document.msExitFullscreen;
            if (ex) ex.call(document);
          }
          lastTap = 0;
          return;
        }
        lastTap = now;
      });

      // ══════════════════════════════════════════════════════════
      //  KEEP THE SCREEN ON — two independent strategies so at
      //  least one works on every browser / OS combination.
      // ══════════════════════════════════════════════════════════

      // Strategy 1 — Wake Lock API (Chrome 84+, Edge 84+, Safari 16.4+)
      var wakeLock = null;

      async function acquireWakeLock() {
        if (!('wakeLock' in navigator)) return;
        try {
          wakeLock = await navigator.wakeLock.request('screen');
          console.log('[wake] lock acquired');
          wakeLock.addEventListener('release', function () {
            console.log('[wake] lock released');
            wakeLock = null;
          });
        } catch (e) {
          console.warn('[wake] request failed:', e);
          wakeLock = null;
        }
      }

      acquireWakeLock();

      // Re-acquire when the page becomes visible (browsers release
      // the lock automatically when the tab is backgrounded).
      $(document).on('visibilitychange', function () {
        if (document.visibilityState === 'visible') acquireWakeLock();
      });

      // Re-acquire on any user interaction (some browsers need a
      // fresh gesture to grant the lock after it was released).
      $(document).on('click touchstart', function () {
        if (!wakeLock) acquireWakeLock();
      });

      // Poll every 10 s as a final safety net.
      setInterval(function () {
        if (!wakeLock && document.visibilityState === 'visible') {
          acquireWakeLock();
        }
      }, 10000);

      // Strategy 2 — hidden <video> loop ("NoSleep" technique).
      // Works on older iOS Safari and Android WebViews that lack
      // the Wake Lock API.  A tiny silent MP4 plays in a 1 px
      // invisible element; the browser treats it as active media
      // and refuses to sleep.
      (function noSleepVideo() {
        // Use this as a FALLBACK even when Wake Lock exists,
        // because some devices still sleep through it.
        console.log('[wake] starting video-loop fallback');

        var v = document.createElement('video');
        v.setAttribute('playsinline', '');
        v.setAttribute('muted', '');
        v.muted = true;
        v.setAttribute('loop', '');
        v.style.cssText =
          'position:fixed;top:-1px;left:-1px;width:1px;height:1px;opacity:0.01;pointer-events:none';

        // Minimal valid silent MP4 (~ 1 kB, loops forever)
        v.src = 'data:video/mp4;base64,' +
          'AAAAIGZ0eXBpc29tAAACAGlzb21pc28yYXZjMW1wNDEAAAAIZnJlZQAAADht' +
          'ZGF0AAAC0AYF//+s3EXpvebZSLeWLNgg2SPu73gyNjQgLSBjb3JlIDE2NC' +
          'AAAAhNb292AAAAYG12aGQAAAAA4woFR+MKBUcAAV+QAAFfkAABAAABAAAAAA' +
          'AAAAAAAAABAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAQAAAAAAAAA' +
          'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAHRyYWsAAABcdGtoZAAAAAMAAAAAAAAAAAAAAAEAAAAA' +
          'AAAD6AAAAAAAAAAAAAAAAAAAAAAAAQAAAAAAAAAAAAAAAAAAAAEAAAAAAoAAAAFAAAAAAAAk' +
          'ZWR0cwAAABxlbHN0AAAAAAAAAAEAAAPAAAAAAAEAAAAAAA==';

        document.body.appendChild(v);

        function tryPlay() { v.play().catch(function () {}); }
        tryPlay();

        // Re-trigger on every user gesture (iOS often pauses
        // background media and needs a fresh interaction).
        $(document).on('click touchstart', tryPlay);
      })();

      // ── Auto-reconnect if the stream drops ─────────────────
      $feed.on('error', function () {
        setTimeout(function () {
          $feed.attr('src', '/video_feed?' + Date.now());
        }, 1000);
      });

    });
  </script>
</body>
</html>
"""


# ── Routes ───────────────────────────────────────────────────────
def gen_frames():
    """Yield JPEG frames from the shared camera as an MJPEG stream."""
    camera.start()
    while True:
        frame = camera.get_frame()
        if frame is None:
            time.sleep(0.05)
            continue
        yield (
            b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
        )
        time.sleep(0.033)  # ~30 fps cap


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/video_feed')
def video_feed():
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, threaded=True)