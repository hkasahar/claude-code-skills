#!/usr/bin/env python3
"""T19: localhost HTTP exercises billable retry count and credential hardening."""
import contextlib
import importlib.util
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'scripts'/'mathpix_extract.py'
spec=importlib.util.spec_from_file_location('mathpix_under_test',SCRIPT)
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

class Handler(BaseHTTPRequestHandler):
    posts=0
    mode='fail'
    redirected=0
    def log_message(self,*args):pass
    def answer(self,code,body,location=None):
        self.send_response(code)
        if location:self.send_header('Location',location)
        self.end_headers();self.wfile.write(body.encode())
    def do_POST(self):
        type(self).posts+=1
        self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path!='/v3/pdf':type(self).redirected+=1
        if self.mode=='redirect-upload':self.answer(302,'','/leak');return
        if self.mode=='fail' or (self.mode=='third' and self.posts<3):self.answer(500,'temporary failure');return
        self.answer(200,'{"pdf_id":"fixture"}')
    def do_GET(self):
        if self.path=='/leak':type(self).redirected+=1
        if self.mode=='redirect-status' and not self.path.endswith('.md'):self.answer(307,'','/leak');return
        if self.mode=='redirect-result' and self.path.endswith('.md'):self.answer(301,'','/leak');return
        self.answer(200,'# Fixture\n$$x=1$$\n' if self.path.endswith('.md') else '{"status":"completed"}')

class MathpixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)
    def setUp(self):
        Handler.posts=0;Handler.redirected=0;Handler.mode='fail'
        self.temp=tempfile.TemporaryDirectory(prefix='mathpix-test-');self.root=Path(self.temp.name)
        self.pdf=self.root/'fixture.pdf';self.pdf.write_bytes(b'%PDF-1.4\nfixture')
        self.env=dict(os.environ,MATHPIX_APP_ID='fake',MATHPIX_APP_KEY='fake',MATHPIX_API_BASE=f'http://127.0.0.1:{self.server.server_port}')
    def tearDown(self):self.temp.cleanup()
    def call(self,*args):
        return subprocess.run([sys.executable,str(SCRIPT),str(self.pdf),*args],env=self.env,capture_output=True,text=True,timeout=20)
    def test_one_upload_attempt(self):
        Handler.mode='third'
        r=self.call('--max-attempts','1')
        self.assertEqual(r.returncode,2,r.stderr);self.assertEqual(Handler.posts,1)
    def test_default_three_upload_attempts(self):
        Handler.mode='third'
        r=self.call()
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(Handler.posts,3)
        self.assertIn('$$x=1$$',r.stdout)
    def test_all_redirect_stages_refused(self):
        for mode in ('redirect-upload','redirect-status','redirect-result'):
            with self.subTest(mode=mode):
                Handler.mode=mode
                r=self.call('--max-attempts','1')
                self.assertEqual(r.returncode,2,r.stderr);self.assertIn('redirect refused',r.stderr)
                self.assertEqual(Handler.redirected,0)
    def test_output_collision_and_symlink_refused_before_upload(self):
        Handler.mode='ok'
        r=self.call(str(self.pdf),'--mode','convert')
        self.assertEqual(r.returncode,1);self.assertEqual(Handler.posts,0)
        target=self.root/'old.md';target.write_text('keep')
        link=self.root/'link.md';link.symlink_to(target)
        r=self.call(str(link),'--mode','convert')
        self.assertEqual(r.returncode,1);self.assertEqual(Handler.posts,0);self.assertEqual(target.read_text(),'keep')
    def test_atomic_convert_and_proxy_isolation(self):
        Handler.mode='ok'
        self.env['HTTP_PROXY']='http://127.0.0.1:1';self.env['HTTPS_PROXY']='http://127.0.0.1:1';self.env['NO_PROXY']=''
        out=self.root/'result.md';out.write_text('old')
        r=self.call(str(out),'--mode','convert','--max-attempts','1')
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout.strip(),str(out));self.assertIn('$$x=1$$',out.read_text())
        self.assertEqual(list(self.root.glob('.*.tmp')),[])
        self.assertFalse(helper.create_mathpix_session().trust_env)
    def test_tls_and_redirect_arguments(self):
        with patch.object(helper,'create_mathpix_session') as factory:
            response=factory.return_value.get.return_value;response.status_code=200;response.json.return_value={'status':'completed'}
            helper.check_processing_status('x','fake','fake')
            kw=factory.return_value.get.call_args.kwargs
            self.assertIs(kw['verify'],True);self.assertIs(kw['allow_redirects'],False)

if __name__=='__main__':unittest.main()
