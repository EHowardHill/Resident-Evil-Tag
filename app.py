"""
VR Webcam Viewer — Flask app that streams the selected webcam
and displays it in one half of a split-screen VR layout.

Optimized for high-concurrency (up to 9+ devices) using Waitress,
Condition threading, and reduced MJPEG bandwidth footprint.
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
        # Condition variable for zero-latency, real-time broadcasting to all 9 devices
        self.condition = threading.Condition()
        self.frame = None
        self.cap = None
        self._running = False
        self.camera_index = 0

    def start(self):
        if self._running:
            return
        
        self.cap = cv2.VideoCapture(self.camera_index, cv2.CAP_AVFOUNDATION)
        if not self.cap.isOpened():
            self.cap = cv2.VideoCapture(self.camera_index)
            
        # --- BANDWIDTH OPTIMIZATIONS FOR 9 DEVICES ---
        # 640x480 is the sweet spot for VR over WiFi without choking the router
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        
        # Limit hardware capture framerate to save CPU and network bandwidth
        self.cap.set(cv2.CAP_PROP_FPS, 24)
        
        # Ensure OpenCV drops old frames and only holds the newest one
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1) 
        
        time.sleep(1)
        self._running = True
        thread = threading.Thread(target=self._capture_loop, daemon=True)
        thread.start()
        print(f"[camera] capture thread started on index {self.camera_index} (Optimized for Multi-Client)")

    def _capture_loop(self):
        while self._running:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.05)
                continue
            
            # --- HORIZONTAL FLIP ---
            frame = cv2.flip(frame, 1) 
            
            # --- AGGRESSIVE COMPRESSION ---
            # 50% quality cuts file size down drastically to support 9 continuous streams
            _, buf = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
            
            # Instantly broadcast the new frame to all 9 waiting threads
            with self.condition:
                self.frame = buf.tobytes()
                self.condition.notify_all()

        self.cap.release()


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
      //  KEEP THE SCREEN ON
      // ══════════════════════════════════════════════════════════
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

      $(document).on('visibilitychange', function () {
        if (document.visibilityState === 'visible') acquireWakeLock();
      });

      $(document).on('click touchstart', function () {
        if (!wakeLock) acquireWakeLock();
      });

      setInterval(function () {
        if (!wakeLock && document.visibilityState === 'visible') {
          acquireWakeLock();
        }
      }, 10000);

      (function noSleepVideo() {
        console.log('[wake] starting video-loop fallback');
        var v = document.createElement('video');
        v.setAttribute('playsinline', '');
        v.setAttribute('muted', '');
        v.muted = true;
        v.setAttribute('loop', '');
        v.style.cssText = 'position:fixed;top:-1px;left:-1px;width:1px;height:1px;opacity:0.01;pointer-events:none';

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
    
    try:
        while True:
            # Server completely pauses here until hardware has a new frame
            with camera.condition:
                camera.condition.wait()
                frame = camera.frame
                
            if frame is None:
                continue
                
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
            )
    except GeneratorExit:
        # Prevents thread-lock if a user refreshes or closes their browser
        print("[server] Client disconnected cleanly.")
        pass


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/video_feed')
def video_feed():
    return Response(
        gen_frames(),
        mimetype='multipart/x-mixed-replace; boundary=frame',
    )


# ── Terminal UI / Startup ────────────────────────────────────────
def select_camera_cli():
    print("Scanning for available webcams (this may take a moment)...")
    available_cams = []
    
    for i in range(5):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available_cams.append(i)
        cap.release()

    if not available_cams:
        print("\n[!] No webcams detected. Defaulting to index 0.")
        return 0

    print("\n--- Available Webcams ---")
    for cam_idx in available_cams:
        print(f"  [{cam_idx}] Camera {cam_idx}")
    print("-------------------------")

    while True:
        choice = input(f"\nSelect a camera index (default {available_cams[0]}): ").strip()
        
        if not choice:
            return available_cams[0]
        
        try:
            choice_int = int(choice)
            if choice_int not in available_cams:
                print(f"Warning: Camera {choice_int} wasn't detected in the scan, but attempting to use it anyway.")
            return choice_int
        except ValueError:
            print("Invalid input. Please enter a number.")


if __name__ == '__main__':
    from waitress import serve
    
    selected_index = select_camera_cli()
    camera.camera_index = selected_index
    
    print(f"\n[server] Starting high-concurrency server on http://0.0.0.0:8000 (Targeting Camera {selected_index})...")
    
    # 16 threads provide plenty of headroom for 9 active devices plus intermittent reconnects
    serve(app, host='0.0.0.0', port=8000, threads=16)