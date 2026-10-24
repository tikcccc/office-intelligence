bind = "0.0.0.0:8765"
# Sessions and jobs are intentionally shared by threads in this one worker.
workers = 1
worker_class = "gthread"
threads = 8
timeout = 200
graceful_timeout = 200
keepalive = 5
limit_request_line = 4094
limit_request_fields = 50
limit_request_field_size = 4094
forwarded_allow_ips = ""
accesslog = None
errorlog = "-"
capture_output = False
preload_app = False


def worker_exit(server, worker):
    if getattr(worker, "wsgi", None):
        worker.wsgi.app.close()
