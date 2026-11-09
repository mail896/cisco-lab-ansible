"""Full history remains readable while each response contains at most ten rows."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from dashboard.login_audit import LoginAudit
from dashboard.pagination import paginate
from tests import test_dashboard_prefix as prefix


class PaginationTests(unittest.TestCase):
    def test_boundaries_clamping_and_legacy_shape(self):
        rows=[dict(id=i) for i in range(35)]
        data=dict(runs=rows,other='metadata')
        self.assertIs(paginate(data,None),data)
        for page,expected in ((1,list(range(10))),(2,list(range(10,20))),(4,list(range(30,35))),(999999,list(range(30,35)))):
            result=paginate(data,page)
            self.assertEqual([row['id'] for row in result['runs']],expected)
            self.assertEqual(result['pagination']['total'],35)
            self.assertEqual(result['pagination']['pages'],4)
        self.assertEqual(len(data['runs']),35)
        self.assertEqual(paginate(dict(runs=[]),8)['pagination']['page'],1)
        for page in (0,-1,True,1000000,'1'):
            with self.assertRaises(ValueError):paginate(data,page)

    def test_activity_and_b_acceptance_are_independent_of_visible_page(self):
        rows=[dict(id=i,status='succeeded',host='LAB-08B',phase='maintenance') for i in range(25)]
        rows[0]['status']='running'; rows[1]['status']='queued'
        result=paginate(dict(runs=rows),3,fresh=True)
        self.assertTrue(result['busy']);self.assertEqual(result['active_counts'],dict(running=1,queued=1))
        self.assertEqual(result['pilot_run']['id'],2)
        self.assertNotIn(result['pilot_run'],result['runs'])
        central=paginate(dict(runs=[dict(status='running')],busy=False),1)
        self.assertFalse(central['busy'])

    def test_fresh_active_jobs_are_outside_completed_history_in_one_response(self):
        active=[dict(id=str(index),status='queued' if index<7 else 'running') for index in range(11)]
        completed=[dict(id=str(index+100),status='succeeded') for index in range(35)]
        for page in (1,2,4):
            result=paginate(dict(runs=active+completed),page,fresh=True)
            self.assertEqual(result['pagination']['total'],35)
            self.assertEqual(result['pagination']['pages'],4)
            self.assertEqual(len(result['active_runs']),11)
            self.assertEqual([row['status'] for row in result['active_runs']],['running']*4+['queued']*7)
            self.assertEqual(result['runs'],completed[(page-1)*10:page*10])

    def test_login_history_can_page_past_old_200_limit_and_across_utf8_blocks(self):
        with TemporaryDirectory() as temp:
            audit=LoginAudit(Path(temp)/'audit')
            audit.append('success','signed_in')
            row=audit.records()[0]
            file=next(audit.directory.iterdir())
            file.write_text(''.join(json.dumps(dict(row,account='test.'+str(index),name='Ü'*120),ensure_ascii=False)+'\n' for index in range(420)))
            self.assertEqual(len(audit.records()),200)
            result=paginate(dict(records=audit.iter_records()),22,'records')
            self.assertEqual(result['pagination']['total'],420)
            self.assertEqual(result['records'][0]['account'],'test.209')
            self.assertEqual(result['records'][0]['name'],'Ü'*120)
            self.assertEqual(result['records'][-1]['account'],'test.200')

    def test_paged_backup_api_uses_full_history_and_preserves_metadata(self):
        handler=prefix.PrefixTests().handler('/cisco-lab/api/backup/page/3')
        handler.server.backups=Mock(profile='apps')
        handler.server.backups.state.return_value=dict(runs=[dict(id=i,status='finished') for i in range(35)],
            latest=dict(id=0),locations=dict(apps=dict(path='fixture')),transfer_available=True,mode='apps')
        handler.do_GET();status,data=handler._json.call_args.args
        self.assertEqual(status,200);self.assertEqual([row['id'] for row in data['runs']],list(range(20,30)))
        self.assertEqual(data['pagination']['total'],35)
        self.assertTrue(data['transfer_available'])
        self.assertEqual(data['latest']['id'],0)
        self.assertEqual(data['locations']['apps']['path'],'fixture')
        handler.server.backups.state.assert_called_once_with(limit=None)


if __name__=='__main__':unittest.main()
