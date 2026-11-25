"""Authenticated teaching endpoints, separate from device administration."""
import json
import re
import threading

from dashboard.exercise_outputs import configs, pdf, solution_zip, svg, variant_at, export_name

EXPORT_SLOTS = threading.BoundedSemaphore(4)
# A class exporting at once waits briefly in line instead of failing at once.
EXPORT_WAIT_SECONDS = 20
GENERATORS = {'/api/exercises': 'network', '/api/cli-exercises': 'linux'}
# Read-only and draft-only actions for students; ownership checks stay in the store.
STUDENT_POST = {'/preview', '/families', '/text'}


def audit(handler, prefix, action, task):
    """Record who generated, saved or exported a task; never blocks the task itself."""
    log = getattr(handler.server, 'task_audit', None)
    if not log:
        return
    detail = task.get('label') or task.get('title', '')
    try:
        log.append(GENERATORS[prefix], action, task['id'], detail, handler.identity)
    except (OSError, ValueError, KeyError) as error:
        print('Aufgabenprotokoll nicht geschrieben:', type(error).__name__, flush=True)


def view(task):
    for variant in task['variants']:
        variant['configs'] = configs(task, variant)
        variant['svg'] = svg(task, variant)
    return task


def get(handler, prefix='/api/exercises', store_name='exercises', render=view, exports=None):
    """Read endpoints of one teaching generator below its own API prefix."""
    path = handler.path
    if path != prefix and not path.startswith(prefix + '/'):
        return False
    pdf_export, zip_export, filename = exports or (pdf, solution_zip, export_name)
    try:
        store = getattr(handler.server, store_name)
        user = handler.identity
        if path == prefix:
            handler._json(200, store.state(user, handler.history_page or 1))
        elif match := re.fullmatch(re.escape(prefix) + r'/task/([a-f0-9]{32})', path):
            handler._json(200, render(store.task(match[1], user)))
        elif match := re.fullmatch(re.escape(prefix) + r'/export/(pdf|zip)/([a-f0-9]{32})/(all|[1-8])', path):
            if not EXPORT_SLOTS.acquire(timeout=EXPORT_WAIT_SECONDS):
                handler._json(409, dict(error='Gerade exportieren viele gleichzeitig. Bitte gleich erneut versuchen.'))
                return True
            try:
                kind, identity, selection = match.groups()
                task = store.task(identity, user)
                team = None if selection == 'all' else int(selection)
                if team is not None:
                    variant_at(task, team)
                body = pdf_export(task, team) if kind == 'pdf' else zip_export(task, team)
                audit(handler, prefix, 'export_' + kind,
                      dict(task, label=(task.get('label') or task['title']) + ' · ' + ('alle Teams' if team is None else 'Team ' + str(team))))
                handler._send(200, body, 'application/pdf' if kind == 'pdf' else 'application/zip',
                              headers=[('Content-Disposition', 'attachment; filename="'+filename(task,kind,team)+'"')])
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


def post(handler, prefix='/api/exercises', store_name='exercises', render=view, families=True):
    """Write endpoints; ownership comes from the authenticated identity only."""
    path = handler.path
    if not path.startswith(prefix + '/'):
        return False
    action_path = path[len(prefix):]
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if (not 0 < length <= 131072 or handler.headers.get('Transfer-Encoding')
                or handler.headers.get_content_type() != 'application/json'):
            raise ValueError('Ungültige Aufgabenanfrage.')
        payload = json.loads(handler.rfile.read(length))
        if not isinstance(payload, dict):
            raise ValueError('Ungültige Aufgabenanfrage.')
        store, user = getattr(handler.server, store_name), handler.identity
        if user.get('role') == 'student' and action_path not in STUDENT_POST:
            raise PermissionError('Schüler können Aufgaben erzeugen und exportieren, aber nicht speichern oder Vorlagen ändern.')
        fields = set(payload) - {'team_names'}
        if action_path == '/create' and fields == {'settings', 'text'}:
            task = store.create(payload['settings'], payload['text'], user, payload.get('team_names'))
            audit(handler, prefix, 'save', task)
            handler._json(201, render(task))
        elif action_path == '/preview' and fields == {'settings', 'text'}:
            task = store.preview(payload['settings'], payload['text'], user, payload.get('team_names'))
            audit(handler, prefix, 'preview', task)
            handler._json(200, render(task))
        elif families and action_path == '/families' and fields == {'identity', 'ipv4', 'ipv6', 'text'}:
            handler._json(200, render(store.families(payload['identity'], payload['ipv4'], payload['ipv6'], payload['text'], user, payload.get('team_names'))))
        elif action_path in ('/text', '/save') and fields == {'identity', 'text'}:
            action = store.save if action_path == '/save' else store.revise
            task = action(payload['identity'], payload['text'], user, payload.get('team_names'))
            if action_path == '/save':
                audit(handler, prefix, 'save', task)
            handler._json(200, render(task))
        elif action_path == '/standard' and set(payload) == {'settings', 'text'}:
            handler._json(200, store.standard(payload['text'], payload['settings'], user))
        elif action_path == '/template' and set(payload) == {'identity', 'settings', 'text'}:
            handler._json(200, store.template(payload['identity'], payload['text'], payload['settings'], user))
        elif action_path == '/delete' and set(payload) == {'kind', 'identity', 'confirmed'} and payload['confirmed'] is True:
            store.delete(payload['kind'], payload['identity'], user)
            handler._json(200, dict(deleted=True))
        elif action_path == '/delete-all' and set(payload) == {'token','confirmed'}:
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
