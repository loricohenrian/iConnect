import multiprocessing


# Bind to both Unix socket (for Nginx) and TCP (as fallback).
bind = [
    "unix:/run/gunicorn/pisowifi.sock",
    "0.0.0.0:8000",
]

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
