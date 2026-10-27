"""Thread-safe, display-only connection details for the menu-bar UI."""
from threading import Lock

_lock = Lock()
_clients = {}


def clean(value):
    return ''.join(c for c in value if c.isprintable())[:160] if isinstance(value, str) else ''


def add(client, disconnect):
    with _lock:
        _clients[client] = {'state': 'Connected · checking shared window', 'disconnect': disconnect}


def update(client, **details):
    with _lock:
        if client in _clients:
            _clients[client].update({k: clean(v) for k, v in details.items()})


def remove(client):
    with _lock:
        _clients.pop(client, None)


def snapshot():
    with _lock:
        return [{k: v for k, v in row.items() if k != 'disconnect'} for row in _clients.values()]


def disconnect_all():
    with _lock:
        callbacks = [row['disconnect'] for row in _clients.values()]
    for callback in callbacks:
        callback()


def surface_label(response):
    if not response.get('ok'):
        return 'Not matched'
    if response.get('kind') == 'monitor':
        return f"Entire display · {response.get('width', '?'):g} × {response.get('height', '?'):g}" if all(isinstance(response.get(k), (int, float)) for k in ('width', 'height')) else 'Entire display'
    return ' · '.join(str(response.get(k) or '') for k in ('owner', 'name')).strip(' · ') or 'Shared window (title unavailable)'
