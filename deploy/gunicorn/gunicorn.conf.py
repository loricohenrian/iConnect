import multiprocessing


# Unix socket consumed by Nginx upstream.
bind = "unix:/run/gunicorn/pisowifi.sock"

# Orange Pi is resource-constrained; keep worker count modest.
workers = max(2, min(4, multiprocessing.cpu_count()))
threads = 2
timeout = 60
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = "info"
capture_output = True
