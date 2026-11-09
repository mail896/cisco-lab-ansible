"""Read controller files once and install a group with one remote module call."""
import base64
from pathlib import Path

from ansible.errors import AnsibleActionFail
from ansible.plugins.action import ActionBase


class ActionModule(ActionBase):
    TRANSFERS_FILES = False
    _supports_check_mode = True

    def run(self, tmp=None, task_vars=None):
        result = super().run(tmp, task_vars)
        args = dict(self._task.args)
        source_root = args.pop('source_root', None)
        files = []
        for entry in args.get('files', []):
            row = dict(entry)
            if ('src' in row) == ('content' in row):
                raise AnsibleActionFail('Jede LAB-Datei benötigt genau src oder content.')
            if 'src' in row:
                name = row.pop('src')
                if source_root and not Path(name).is_absolute():
                    name = str(Path(source_root)/name)
                source = self._find_needle('files', name)
                data = Path(source).read_bytes()
            else:
                content = row.pop('content')
                if not isinstance(content, str):
                    raise AnsibleActionFail('LAB-Dateiinhalt muss Text sein; JSON mit to_json rendern.')
                data = content.encode('utf-8')
            row['data'] = base64.b64encode(data).decode('ascii')
            files.append(row)
        args['files'] = files
        result.update(self._execute_module(module_name='cisco_lab_files', module_args=args,
                                           task_vars=task_vars, tmp=tmp))
        return result
