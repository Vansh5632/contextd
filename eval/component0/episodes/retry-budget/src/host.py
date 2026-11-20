def format_host(host, port, default_port):
    if port <= default_port:
        return host
    return f"{host}:{port}"
