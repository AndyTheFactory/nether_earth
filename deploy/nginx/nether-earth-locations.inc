# server-context routes shared by gateway.conf and gateway-tls.conf.
# No location below sets add_header, so these server-level headers apply to
# every response.

add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "DENY" always;
add_header Referrer-Policy "no-referrer" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'" always;

# Gateway liveness (does not touch the backend).
location = /healthz {
    access_log off;
    default_type text/plain;
    return 200 "ok\n";
}

# Backend HTTP API: /api/health (liveness), /api/ready (readiness).
location /api/ {
    limit_except GET { deny all; }
    limit_req zone=ne_api burst=20 nodelay;
    proxy_pass http://ne_backend/;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_connect_timeout 5s;
    proxy_read_timeout 15s;
}

# Match WebSocket. Uvicorn pings every 20s and active matches stream
# snapshots at 20 Hz, so a 120s idle timeout only closes dead sockets.
location = /ws {
    limit_req zone=ne_ws burst=30 nodelay;
    limit_conn ne_conn 32;
    proxy_pass http://ne_backend;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection $connection_upgrade;
    proxy_set_header Host $host;
    proxy_set_header Origin $http_origin;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_connect_timeout 5s;
    proxy_read_timeout 120s;
    proxy_send_timeout 120s;
}

# Static frontend.
location / {
    limit_except GET { deny all; }
    proxy_pass http://ne_frontend;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_connect_timeout 5s;
    proxy_read_timeout 15s;
}
