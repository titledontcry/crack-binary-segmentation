"""Regression checks for project isolation, review persistence and export consistency.

Run: .venv/bin/python -m unittest discover -s drone_inference/tests -v
"""
import base64
import csv
import io
import json
import os
import queue
import tempfile
import unittest
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

with tempfile.TemporaryDirectory() as import_root:
    with patch.dict(os.environ, {"CRACK_DATA_ROOT": import_root}):
        from drone_inference import app as m


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        data = root / "_idle"
        (data / "originals").mkdir(parents=True)
        (data / "results").mkdir()
        self.values = patch.multiple(m, DATA_ROOT=root, CATALOG=root/"catalog.sqlite3", DATA=data,
            DB=data/"inspection.sqlite3", ORIGINALS=data/"originals", RESULTS=data/"results",
            SESSION_ID="_idle", WORK_QUEUE=queue.Queue())
        self.values.start()
        self.worker = patch.object(m, "worker", return_value=None)
        self.loader = patch.object(m, "preload_model", return_value=None)
        self.worker.start(); self.loader.start()
        self.client = TestClient(m.app)
        self.client.__enter__()
        self.a, self.fa = self.project("Site A")
        self.b, self.fb = self.project("Site B")
        self.ia = self.image(self.fa)
        self.ib = self.image(self.fb)

    def tearDown(self):
        self.client.__exit__(None,None,None)
        self.worker.stop(); self.loader.stop(); self.values.stop(); self.tmp.cleanup()

    def project(self, name):
        p = self.client.post('/api/projects', json={'name':name}).json()
        f = self.client.post('/api/flights/start',json={'project_id':p['id']}).json()
        self.client.post('/api/flights/'+f['id']+'/stop')
        return p,f

    def image(self, flight):
        image_id = str(uuid.uuid4())
        base = m.DATA_ROOT / flight['session']
        dest = base/'originals'/image_id/'road.jpg'; dest.parent.mkdir(parents=True)
        Image.new('RGB',(24,24),'gray').save(dest)
        result = base/'results'/image_id;result.mkdir(parents=True)
        Image.new('L',(24,24),255).save(result/'mask.png')
        Image.new('RGB',(24,24),'red').save(result/'overlay.jpg')
        with m.session_db(flight['session']) as con:
            con.execute("INSERT INTO images(id,filename,received_at,status,metadata,measurement) VALUES(?,?,?,'done',?,?)",
                (image_id,'road.jpg',datetime.now(timezone.utc).isoformat(),json.dumps({'gsd_mm_per_px':.5}),
                 json.dumps({'crack_pixels':576,'length_px':24,'width_px_max':10})))
        return image_id

    def path(self,p=None,f=None,image_id=None):
        return f"/api/workspace/projects/{(p or self.a)['id']}/images/{(f or self.fa)['session']}/{image_id or self.ia}"

    def save(self, **kwargs):
        payload={'decision':'confirmed','reviewer':'Engineer','revision':0,'manual_measurements':[]}
        payload.update(kwargs)
        return self.client.put(self.path()+'/review',json=payload)

    def test_project_scope_and_reviewed_results(self):
        # Current legacy dashboard context points at B. A's APIs must still use A.
        before=self.client.get('/api/workspace/projects/'+self.a['id']).json()
        self.assertEqual(before['summary']['total'],1)
        self.assertEqual(self.save().status_code,200)
        after=self.client.get('/api/workspace/projects/'+self.a['id']).json()
        self.assertEqual(after['summary']['reviewed'],1)
        self.assertTrue(after['summary']['complete'])
        reviewed=self.client.get('/api/workspace/projects/'+self.a['id']+'/images?status=reviewed').json()
        self.assertEqual(reviewed['items'][0]['id'],self.ia)
        self.assertEqual(self.client.get('/api/workspace/projects/'+self.b['id']).json()['summary']['reviewed'],0)
        self.assertEqual(self.client.get(self.path(p=self.b)).status_code,404)

    def test_corrected_mask_revision_and_manual_measurements(self):
        mask=Image.new('L',(24,24),0)
        for y in range(3,20):mask.putpixel((12,y),255)
        buffer=io.BytesIO();mask.save(buffer,'PNG')
        response=self.save(decision='corrected',mask_data='data:image/png;base64,'+base64.b64encode(buffer.getvalue()).decode(),
                           manual_measurements=[{'points':[[0,0],[3,4]]}])
        self.assertEqual(response.status_code,200,response.text)
        row=response.json()
        self.assertEqual(row['measurement']['crack_pixels'],576)
        self.assertEqual(row['final_measurement']['crack_pixels'],17)
        self.assertEqual(row['manual_measurements'][0]['length_px'],5)
        self.assertEqual(row['manual_measurements'][0]['length_mm'],2.5)
        self.assertEqual(self.client.get(row['review_mask_url']).status_code,200)
        self.assertEqual(self.save().status_code,409)
        self.assertEqual(len(self.client.get(self.path()+'/history').json()['events']),1)

    def test_uncertain_is_not_complete(self):
        self.assertEqual(self.save(decision='uncertain').status_code,200)
        summary=self.client.get('/api/workspace/projects/'+self.a['id']).json()['summary']
        self.assertEqual(summary['uncertain'],1)
        self.assertEqual(summary['reviewed'],0)
        self.assertFalse(summary['complete'])

    def test_exports_match_review(self):
        self.save(reviewer='=unsafe spreadsheet name')
        base='/api/workspace/projects/'+self.a['id']+'/export/'
        response=self.client.get(base+'csv?reviewed_only=true')
        rows=list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['review_status'],'confirmed')
        self.assertTrue(rows[0]['reviewer_name'].startswith("'="))
        archive=self.client.get(base+'zip?reviewed_only=true')
        with zipfile.ZipFile(io.BytesIO(archive.content)) as z:
            report=json.loads(z.read('report.json'))
            self.assertTrue(report['summary']['complete'])
            self.assertEqual(report['included_images'],1)
            self.assertTrue(any(n.endswith('review-history.json') for n in z.namelist()))
        self.assertTrue(self.client.get(base+'pdf').content.startswith(b'%PDF'))

    def test_late_capture_inbox_assignment_and_deduplication(self):
        f=self.client.post('/api/flights/start',json={'project_id':self.a['id']}).json()
        captured=datetime.now(timezone.utc).isoformat()
        self.client.post('/api/flights/'+f['id']+'/stop')
        image=io.BytesIO();Image.new('RGB',(24,24),'white').save(image,'JPEG')
        def upload(metadata):
            return self.client.post('/api/images',files={'file':('upload.jpg',image.getvalue(),'image/jpeg')},
                data={'metadata':json.dumps(metadata)}).json()
        late=upload({'captured_at':captured})
        self.assertEqual(late['session'],f['session'])
        held=upload({'captured_at':datetime.now(timezone.utc).isoformat()})
        self.assertEqual(held['status'],'held')
        self.assertEqual(self.client.get('/api/workspace/inbox').json()['total'],1)
        assigned=self.client.post('/api/workspace/inbox/assign',json={'project_id':self.b['id'],'image_ids':[held['image_id']]})
        self.assertEqual(assigned.status_code,200,assigned.text)
        self.assertEqual(self.client.get('/api/workspace/inbox').json()['total'],0)
        self.assertEqual(self.client.get('/api/workspace/projects/'+self.b['id']).json()['summary']['total'],2)
        self.assertEqual(upload({'captured_at':captured})['status'],'duplicate')

    def test_capture_survives_catalog_reinitialization(self):
        f=self.client.post('/api/flights/start',json={'project_id':self.a['id']}).json()
        self.assertEqual(self.client.post('/api/flights/start',json={'project_id':self.b['id']}).status_code,409)
        m.init_catalog()
        self.assertEqual(self.client.get('/api/flights/active').json()['id'],f['id'])
        self.assertEqual(self.client.get('/api/workspace/projects/'+self.b['id']).json()['project']['id'],self.b['id'])

    def test_manual_measurements_without_ai(self):
        with m.session_db(self.fa['session']) as con:
            con.execute("UPDATE images SET status='failed' WHERE id=?",(self.ia,))
        result=self.save(decision='pending',manual_measurements=[{'points':[[1,1],[4,5]]}])
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['manual_measurements'][0]['length_px'],5)
        self.assertEqual(result.json()['review_status'],'pending')


if __name__=='__main__':
    unittest.main()
