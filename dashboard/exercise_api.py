"""Authenticated teaching endpoints, separate from device administration."""
import json
import re
import threading

from dashboard.exercise_outputs import configs, pdf, solution_zip, svg, variant_at, export_name

EXPORT_SLOTS = threading.BoundedSemaphore(2)


def view(task):
    for variant in task['variants']:
        variant['configs'] = configs(task, variant)
        variant['svg'] = svg(task, variant)
    return task


def get(handler):
    path = handler.path
    if not path.startswith('/api/exercises'):
        return False
    try:
        store = handler.server.exercises
        user = handler.identity
        if path == '/api/exercises':
            handler._json(200, store.state(user, handler.history_page or 1))
        elif match := re.fullmatch(r'/api/exercises/task/([a-f0-9]{32})', path):
            handler._json(200, view(store.task(match[1], user)))
        elif match := re.fullmatch(r'/api/exercises/export/(pdf|zip)/([a-f0-9]{32})/(all|[1-8])', path):
            if not EXPORT_SLOTS.acquire(blocking=False):
                handler._json(409, dict(error='Ein Export läuft bereits. Bitte gleich erneut versuchen.'))
                return True
            try:
                kind, identity, selection = match.groups()
                task = store.task(identity, user)
                team = None if selection == 'all' else int(selection)
                if team is not None:
                    variant_at(task, team)
                body = pdf(task, team) if kind == 'pdf' else solution_zip(task, team)
                handler._send(200, body, 'application/pdf' if kind == 'pdf' else 'application/zip',
                              headers=[('Content-Disposition', 'attachment; filename="'+export_name(task,kind,team)+'"')])
            finally:
                EXPORT_SLOTS.release()
        else:
            handler._json(404, dict(error='Aufgabenseite nicht gefunden.'))
    except FileNotFoundError:
        handler._json(404, dict(error='Aufgabe nicht gefunden.'))
    except ValueError as error:
        handler._json(400, dict(error=str(error)))
    except OSError:
        handler._json(503, dict(error='Aufgaben konnten nicht gelesen werden.'))
    return True


def post(handler):
    path = handler.path
    if not path.startswith('/api/exercises/'):
        return False
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if (not 0 < length <= 131072 or handler.headers.get('Transfer-Encoding')
                or handler.headers.get_content_type() != 'application/json'):
            raise ValueError('Ungültige Aufgabenanfrage.')
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError('Ungültige Aufgabenanfrage.')
        store, user = handler.server.exercises, handler.identity
        fields = set(payload) - {'team_names'}
        if path == '/api/exercises/create' and fields == {'settings', 'text'}:
            handler._json(201, view(store.create(payload['settings'], payload['text'], user, payload.get('team_names'))))
        elif path == '/api/exercises/preview' and fields == {'settings', 'text'}:
            handler._json(200, view(store.preview(payload['settings'], payload['text'], user, payload.get('team_names'))))
        elif path == '/api/exercises/families' and fields == {'identity', 'ipv4', 'ipv6', 'text'}:
            handler._json(200, view(store.families(payload['identity'], payload['ipv4'], payload['ipv6'], payload['text'], user, payload.get('team_names'))))
        elif path in ('/api/exercises/text', '/api/exercises/save') and fields == {'identity', 'text'}:
            action = store.save if path.endswith('/save') else store.revise
            handler._json(200, view(action(payload['identity'], payload['text'], user, payload.get('team_names'))))
        elif path == '/api/exercises/standard' and set(payload) == {'settings', 'text'}:
            handler._json(200, store.standard(payload['text'], payload['settings'], user))
        elif path == '/api/exercises/template' and set(payload) == {'identity', 'settings', 'text'}:
            handler._json(200, store.template(payload['identity'], payload['text'], payload['settings'], user))
        elif path == '/api/exercises/delete' and set(payload) == {'kind', 'identity', 'confirmed'} and payload['confirmed'] is True:
            store.delete(payload['kind'], payload['identity'], user)
            handler._json(200, dict(deleted=True))
        elif path == '/api/exercises/delete-all' and set(payload) == {'token','confirmed'}:
            handler._json(200,store.delete_all(payload['token'],payload['confirmed'],user))
        else:
            raise ValueError('Unbekannte Aufgabenaktion.')
    except PermissionError as error:
        handler._json(403, dict(error=str(error)))
    except (ValueError, TypeError) as error:
        handler._json(400, dict(error=str(error)))
    except FileNotFoundError:
        handler._json(404, dict(error='Eintrag nicht gefunden.'))
    except OSError:
        handler._json(503, dict(error='Aufgabe konnte nicht gespeichert werden.'))
    return True
