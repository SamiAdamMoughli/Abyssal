#!/bin/sh
# Entrypoint: dispatch to the right process based on SERVICE_TYPE env var.
# SERVICE_TYPE=api    → uvicorn (default)
# SERVICE_TYPE=worker → celery worker
# SERVICE_TYPE=beat   → celery beat
set -e

case "${SERVICE_TYPE:-api}" in
  worker|beat)
    # Minimal HTTP health server so Railway's health check passes
    python3 -c "
import http.server, threading, os
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b'ok')
    def log_message(self, *a): pass
port = int(os.environ.get('PORT', 8000))
t = threading.Thread(target=lambda: http.server.HTTPServer(('0.0.0.0', port), H).serve_forever(), daemon=True)
t.start()
import time; time.sleep(86400)
" &

    if [ "${SERVICE_TYPE}" = "beat" ]; then
      exec celery -A spyhop.worker.celery_app beat \
        --loglevel=info \
        --scheduler celery.beat.PersistentScheduler \
        --schedule /tmp/celerybeat-schedule
    else
      exec celery -A spyhop.worker.celery_app worker \
        --loglevel=info \
        --concurrency=4 \
        --queues=default,scoring,sync
    fi
    ;;
  *)
    exec sh -c "uvicorn vesselx.spatial_engine.app:app \
      --host 0.0.0.0 \
      --port ${PORT:-8000} \
      --workers 4 \
      --loop uvloop \
      --http httptools \
      --log-level info"
    ;;
esac
