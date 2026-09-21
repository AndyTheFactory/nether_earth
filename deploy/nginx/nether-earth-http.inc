# http-context definitions shared by gateway.conf and gateway-tls.conf.

# Docker's embedded DNS. Re-resolving service names means a recreated
# backend/frontend container (new IP after an update or rollback) is picked
# up without restarting the gateway.
resolver 127.0.0.11 valid=10s ipv6=off;

upstream ne_backend {
    zone ne_backend 64k;
    server backend:8000 resolve;
}

upstream ne_frontend {
    zone ne_frontend 64k;
    server frontend:8080 resolve;
}

map $http_upgrade $connection_upgrade {
    default upgrade;
    ''      close;
}

# Abuse bounds per client IP. Each WebSocket handshake is one request; a
# socket can create/join at most one match, so the handshake rate also
# bounds match creation.
limit_req_zone  $binary_remote_addr zone=ne_ws:10m  rate=30r/m;
limit_req_zone  $binary_remote_addr zone=ne_api:10m rate=10r/s;
limit_conn_zone $binary_remote_addr zone=ne_conn:10m;
limit_req_status  429;
limit_conn_status 429;

server_tokens off;
client_max_body_size 16k;
client_body_timeout 10s;
client_header_timeout 10s;
large_client_header_buffers 4 8k;
