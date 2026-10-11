"""Behavioral checks against a temporary checkout; no AI or Cadence services."""
import argparse
import ast
import http.client
import json
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/explorer'))
from explorer import Explorer
from server import Handler
from source import InvalidSelection, Unsupported


class ProbeStub(BaseHTTPRequestHandler):
    def send_json(self, status, body):
        payload=json.dumps(body).encode()
        self.send_response(status);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)

    def do_GET(self):
        if self.path=='/auth':
            self.send_json(401,dict(error='missing key'))
        elif self.path=='/redirect':
            self.send_response(307);self.end_headers()
        elif self.path=='/malformed':
            payload=b'not json';self.send_response(200);self.send_header('Content-Length',str(len(payload)))
            self.end_headers();self.wfile.write(payload)
        elif self.path=='/slow':
            time.sleep(.05)
            try:self.send_json(200,dict(ok=True))
            except BrokenPipeError:pass
        elif self.path=='/api/tags':
            self.send_json(200,dict(models=[dict(name='another-model')]))
        else:
            self.send_json(200,dict(status='ok'))

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        ProbeStub.posts.append((self.path,body))
        self.send_json(200,dict(answers=dict(relevant='yes'),response='ok'))

    def log_message(self,*args):
        pass


ProbeStub.posts=[]


class WorkflowStub(BaseHTTPRequestHandler):
    mode='found'
    signal_mode='accepted'
    run_id='run-current'
    paths=[]
    posts=[]

    def do_GET(self):
        WorkflowStub.paths.append(self.path)
        if WorkflowStub.mode=='not-found':
            self.send_response(404);self.end_headers();return
        if WorkflowStub.mode=='auth':
            self.send_response(401);self.end_headers();return
        if WorkflowStub.mode=='slow':
            time.sleep(.05)
        if WorkflowStub.mode=='malformed':
            payload=b'not json'
        elif WorkflowStub.mode=='missing':
            payload=json.dumps(dict(workflowExecutionInfo=None)).encode()
        else:
            payload=json.dumps(dict(workflowExecutionInfo=dict(
                workflowExecution=dict(runId=WorkflowStub.run_id)))).encode()
        try:
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def do_POST(self):
        WorkflowStub.paths.append(self.path)
        body=self.rfile.read(int(self.headers.get('Content-Length','0')))
        WorkflowStub.posts.append((self.path,body))
        if WorkflowStub.signal_mode=='not-found':
            self.send_response(404);self.end_headers();return
        if WorkflowStub.signal_mode=='auth':
            self.send_response(401);self.end_headers();return
        if WorkflowStub.signal_mode=='rejected':
            self.send_response(400);self.end_headers();return
        if WorkflowStub.signal_mode=='slow':
            time.sleep(.05)
        payload=b'{}'
        try:
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
        except BrokenPipeError:
            pass

    def log_message(self,*args):
        pass


class TerminalStub:
    def __init__(self,available=True,state='prefilled'):
        self.available=available
        self.state=state
        self.commands=[]

    def launch(self,command):
        self.commands.append(command)
        return dict(state=self.state,detail='stub result')


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cadence explorer ')
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT/'recipes',self.root/'recipes',
            ignore=shutil.ignore_patterns('.venv','venv','__pycache__'))
        for name in ('models.yaml','classifiers.yaml','README.md'):
            shutil.copy(ROOT/name,self.root/name)
        self.explorer = Explorer(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def watch(self,framework='google-adk',**kwargs):
        request=dict(sample='recurring-ai-watch',implementation=f'recipes/recurring-ai-watch/python/{framework}',
            mode='live',model='gemini-flash-lite',classifier='jev-default')
        request.update(kwargs)
        return self.explorer.resolve(request)

    def ticket(self,**kwargs):
        request=dict(sample='ticket-routing',implementation='recipes/ticket-routing/go',mode='demo',classifier='mock')
        request.update(kwargs)
        return self.explorer.resolve(request)

    def real_parser(self,framework):
        # Test the actual argparse code without importing its heavy runtime modules.
        path=self.root/f'recipes/recurring-ai-watch/python/{framework}/main.py'
        tree=ast.parse(path.read_text())
        body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('parser','_positive')]
        scope=dict(argparse=argparse,Path=Path,__file__=str(path),__doc__='CLI validation',
            DEFAULT_TASK_LIST='recurring-ai-watch-'+framework,DEFAULT_WORKFLOW_ID='recurring-ai-watch-'+framework+'-demo')
        exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),scope)
        return scope['parser']()

    def test_current_discovery_and_modes(self):
        state=self.explorer.snapshot()
        self.assertEqual([r['id'] for r in state['recipes']],['recurring-ai-watch','ticket-routing'])
        watch,ticket=state['recipes']
        self.assertEqual([v['framework'] for v in watch['variants']],['google-adk','openai-agents'])
        self.assertTrue(all(v['supported'] for v in watch['variants']+ticket['variants']))
        self.assertEqual(watch['variants'][0]['modes'],['mock','live'])
        self.assertEqual(ticket['variants'][0]['live_modes'],['live-demo','live-batch'])
        self.assertEqual(ticket['variants'][0]['mock_modes'],['demo','start-ticket','batch'])
        self.assertEqual([entry['id'] for entry in state['classifiers']],['jev-default','laya-local'])

    def test_every_watch_combination_and_actual_cli_parser(self):
        accepted=blocked=evidenced=commands=0
        for framework in ('google-adk','openai-agents'):
            parser=self.real_parser(framework)
            for model in self.explorer.snapshot()['models']:
                for classifier in self.explorer.snapshot()['classifiers']:
                    for mode in ('mock','live'):
                        with self.subTest(framework=framework,model=model['id'],classifier=classifier['id'],mode=mode):
                            if classifier['id'] not in {'jev-default','laya-local'}:
                                with self.assertRaises(InvalidSelection):self.watch(framework,model=model['id'],classifier=classifier['id'],mode=mode)
                                blocked+=1
                                continue
                            result=self.watch(framework,model=model['id'],classifier=classifier['id'],mode=mode)
                            accepted+=1
                            if result['evidence']:evidenced+=1
                            self.assertEqual(result['evidence'] is not None,mode=='live' and classifier['id']=='jev-default')
                            if model['provider']=='ollama' and classifier['provider']=='laya':self.assertEqual(result['credentials'],[])
                            parsed=[]
                            for line in [result['worker']]+result['client'].splitlines():
                                tokens=shlex.split(line)
                                self.assertEqual(tokens[:3],['cd',str((self.root/f'recipes/recurring-ai-watch/python/{framework}').resolve()),'&&'])
                                self.assertEqual(tokens[3:5],['.venv/bin/python','main.py'])
                                args=parser.parse_args(tokens[5:]);parsed.append(args);commands+=1
                                self.assertEqual(args.domain,'cadence-ai-samples')
                                self.assertEqual(args.workflow_id,result['workflow_id'])
                                if args.command in {'worker','start'}:
                                    self.assertEqual(args.mode,mode)
                                    self.assertEqual(args.model_id,model['id'])
                                    self.assertEqual(args.classifier_id,classifier['id'])
                                    self.assertEqual(args.task_list,result['task_list'])
                            self.assertEqual(parsed[0].confirm_live,mode=='live')
        self.assertEqual((accepted,blocked,evidenced,commands),(24,0,6,120))

    def test_refresh_reads_catalog_edits_and_new_models(self):
        self.assertEqual(self.watch(model='llama3.2-local')['model']['model'],'llama3.2:latest')
        path=self.root/'models.yaml'
        path.write_text(path.read_text().replace('llama3.2:latest','llama3.2:changed')+
            '\n  - id: another-local\n    provider: ollama\n    model: llama3.2:changed\n    endpoint: http://localhost:11435\n')
        result=self.watch(model='another-local',classifier='laya-local')
        self.assertEqual(result['model']['model'],'llama3.2:changed')
        self.assertIn('11435',result['local'][0]['warmup'])
        self.assertIn('another-local',result['worker'])
        self.assertIsNone(result['evidence'])
        self.assertEqual(len(self.explorer.snapshot()['models']),4)

    def test_modes_and_flags_follow_source_edits(self):
        path=self.root/'recipes/recurring-ai-watch/python/google-adk/main.py'
        path.write_text(path.read_text().replace('choices=("mock", "live")','choices=("mock",)'))
        variant=self.explorer.snapshot()['recipes'][0]['variants'][0]
        self.assertEqual(variant['modes'],['mock'])
        with self.assertRaises(InvalidSelection):self.watch()
        path.write_text(path.read_text().replace('cli.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)',''))
        variant=self.explorer.snapshot()['recipes'][0]['variants'][0]
        self.assertFalse(variant['supported']);self.assertIn('--workflow-id',variant['reason'])

    def test_live_validation_follows_source_instead_of_id_list(self):
        main=self.root/'recipes/recurring-ai-watch/python/google-adk/main.py'
        live=main.with_name('live.py')
        for path in (main,live):
            path.write_text(path.read_text().replace('("laya-local", "laya")','("renamed-local", "laya")'))
        classifiers=self.root/'classifiers.yaml'
        classifiers.write_text(classifiers.read_text().replace('id: laya-local','id: renamed-local'))
        self.assertEqual(self.watch(classifier='renamed-local')['classifier']['id'],'renamed-local')
        with self.assertRaises(InvalidSelection):self.watch(classifier='laya-local')

    def test_incomplete_and_new_recipes_remain_visible(self):
        path=self.root/'recipes/unrecognized/rust'
        path.mkdir(parents=True);path.parent.joinpath('README.md').write_text('# Unrecognized recipe\n')
        new=self.explorer.snapshot()['recipes'][-1]
        self.assertEqual(new['id'],'unrecognized');self.assertFalse(new['variants'][0]['supported'])
        self.assertTrue(new['readme'])
        new_py=self.root/'recipes/new-simple/python'
        new_py.mkdir(parents=True)
        main=self.root/'recipes/recurring-ai-watch/python/google-adk/main.py'
        shutil.copy(main,new_py/'main.py');shutil.copy(main.with_name('live.py'),new_py/'live.py')
        recipe=next(r for r in self.explorer.snapshot()['recipes'] if r['id']=='new-simple')
        self.assertEqual(recipe['variants'][0]['framework'],None)
        self.assertTrue(recipe['variants'][0]['supported'])

    def test_generated_python_bare_recipe_is_discovered_and_resolved(self):
        shutil.copytree(ROOT/'scripts',self.root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'templates',self.root/'templates',ignore=shutil.ignore_patterns('.venv','__pycache__'))
        generated=subprocess.run([str(self.root/'scripts/new-recipe.sh'),'invoice-review','--language','python',
            '--classifier','none','--agent','none'],capture_output=True,text=True,timeout=30)
        self.assertEqual(generated.returncode,0,generated.stderr)
        recipe=next(r for r in self.explorer.snapshot()['recipes'] if r['id']=='invoice-review')
        self.assertEqual(recipe['title'],'Invoice Review');self.assertTrue(recipe['readme'])
        implementation='recipes/invoice-review/python/bare'
        self.assertEqual([v['id'] for v in recipe['variants']],[implementation])
        variant=recipe['variants'][0]
        self.assertTrue(variant['supported'],variant['reason'])
        self.assertEqual((variant['framework'],variant['modes'],variant['model'],variant['classifier']),(None,[],False,False))
        self.assertEqual(variant['defaults']['--domain'],'cadence-ai-samples')
        result=self.explorer.resolve(dict(sample='invoice-review',implementation=implementation))
        self.assertEqual((result['live'],result['credentials'],result['controls']),(False,[],[]))
        path=self.root/implementation/'main.py'
        tree=ast.parse(path.read_text())
        body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='parser' or
            isinstance(n,ast.Assign) and n.targets[0].id.startswith('DEFAULT_')]
        scope=dict(argparse=argparse,__doc__='CLI validation')
        exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),scope)
        commands=[]
        for line in (result['worker'],result['start']):
            tokens=shlex.split(line)
            self.assertEqual(tokens[:5],['cd',str((self.root/implementation).resolve()),'&&','.venv/bin/python','main.py'])
            args=scope['parser']().parse_args(tokens[5:])
            self.assertEqual((args.domain,args.task_list,args.workflow_id),
                (result['domain'],result['task_list'],result['workflow_id']))
            commands.append(args.command)
        self.assertEqual(commands,['worker','start'])

    def test_unfamiliar_validation_and_invalid_endpoints_fail_closed(self):
        model_file=self.root/'models.yaml'
        model_file.write_text(model_file.read_text().replace('http://localhost:11434','http://example.com:11434'))
        with self.assertRaises(InvalidSelection):self.watch(model='llama3.2-local')
        live=self.root/'recipes/recurring-ai-watch/python/google-adk/live.py'
        live.write_text(live.read_text().replace('classifier = (selection.classifier.id, selection.classifier.provider)',
            'classifier = determine_classifier(selection)'))
        with self.assertRaises(Unsupported):self.watch()

    def test_recipe_modules_are_never_executed(self):
        main=self.root/'recipes/recurring-ai-watch/python/google-adk/main.py'
        sentinel=self.root/'executed'
        main.write_text(main.read_text()+f'\nopen({str(sentinel)!r},"w").write("executed")\n')
        self.watch()
        self.assertFalse(sentinel.exists())

    def test_go_actions_candidates_and_live_batch_bounds(self):
        for mode in ('demo','batch','start-ticket'):
            result=self.ticket(mode=mode)
            self.assertIn('-classifier-id mock',result['worker'])
            self.assertIn('-mode '+mode,result['client'])
        for classifier in ('jev-default','laya-local'):
            result=self.ticket(mode='live-demo',classifier=classifier)
            self.assertTrue(result['live']);self.assertIn(classifier,result['worker']);self.assertIn(classifier,result['client'])
            result=self.ticket(mode='live-batch',classifier=classifier,count='3',concurrency='1')
            self.assertIn('-count 3 -concurrency 1 -confirm-live',result['client'])
        for classifier in ('kev-local','von-local','reflex-local'):
            with self.assertRaises(InvalidSelection):self.ticket(mode='live-demo',classifier=classifier)
        for count,concurrency in [('', ''),('11','1'),('3','6'),('1','2'),('0','1')]:
            with self.assertRaises(InvalidSelection):self.ticket(mode='live-batch',classifier='laya-local',count=count,concurrency=concurrency)
        with self.assertRaises(InvalidSelection):self.ticket(mode='acknowledge')
        batch=self.root/'recipes/ticket-routing/go/batch.go'
        batch.write_text(batch.read_text().replace('maximumLiveBatchCount   = 10','maximumLiveBatchCount   = 2'))
        with self.assertRaises(InvalidSelection):self.ticket(mode='live-batch',classifier='laya-local',count='3',concurrency='1')

    def test_bad_catalogs_and_source_paths_are_reported(self):
        for name in ('../outside','.git/config','tools/explorer/server.py','recipes/recurring-ai-watch/.env'):
            with self.assertRaises(InvalidSelection):self.explorer.source_path(name)
        self.assertTrue(self.explorer.source_path('models.yaml').is_file())
        path=self.root/'models.yaml';path.write_text('models: [broken')
        self.assertIn('models',self.explorer.snapshot()['catalog_errors'])
        with self.assertRaises(InvalidSelection):self.watch()

    def test_symlinks_cannot_read_outside_checkout(self):
        with tempfile.TemporaryDirectory() as outside:
            target=Path(outside)/'models.yaml';shutil.copy(ROOT/'models.yaml',target)
            path=self.root/'models.yaml';path.unlink();path.symlink_to(target)
            self.assertIn('models',self.explorer.snapshot()['catalog_errors'])
            with self.assertRaises(InvalidSelection):self.explorer.source_path('models.yaml')

    def test_probe_states_and_bounded_local_warmup(self):
        stub=ThreadingHTTPServer(('127.0.0.1',0),ProbeStub)
        thread=threading.Thread(target=stub.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{stub.server_port}'
        try:
            self.assertEqual(self.explorer.http_probe('auth',base+'/auth')['state'],'authentication required')
            self.assertEqual(self.explorer.http_probe('redirect',base+'/redirect',expect_json=False)['state'],'reachable')
            self.assertEqual(self.explorer.http_probe('bad',base+'/malformed')['state'],'unreachable')
            self.assertEqual(self.explorer.http_probe('slow',base+'/slow',timeout=.001)['detail'],'Timed out.')
            refused=socket.socket();refused.bind(('127.0.0.1',0));port=refused.getsockname()[1];refused.close()
            self.assertEqual(self.explorer.http_probe('closed',f'http://127.0.0.1:{port}')['state'],'unreachable')
            remote=dict(id='remote',provider='google',model='model',endpoint='https://example.com')
            self.assertEqual(self.explorer.model_probe(remote)['state'],'unavailable check')
            local=dict(id='local',provider='ollama',model='llama3.2:latest',endpoint=base)
            self.assertEqual(self.explorer.model_probe(local)['state'],'missing model')

            models=self.root/'models.yaml';models.write_text(models.read_text().replace(
                'endpoint: http://localhost:11434',f'endpoint: {base}'))
            classifiers=self.root/'classifiers.yaml';classifiers.write_text(classifiers.read_text().replace(
                'endpoint: http://localhost:8008/v1/systemone',f'endpoint: {base}/v1/systemone'))
            ProbeStub.posts.clear()
            result=self.explorer.warmup(dict(sample='recurring-ai-watch',
                implementation='recipes/recurring-ai-watch/python/google-adk',mode='live',
                model='llama3.2-local',classifier='laya-local'))
            self.assertEqual([item['state'] for item in result['components']],['warmed','warmed'])
            self.assertEqual([path for path,_ in ProbeStub.posts],['/api/generate','/v1/systemone'])
            blocked=self.explorer.post_json('remote','https://example.com/v1',{},1)
            self.assertEqual(blocked['state'],'unavailable check')
        finally:
            stub.shutdown();stub.server_close();thread.join()

    def test_workflow_run_resolution_states_and_http_route(self):
        stub=ThreadingHTTPServer(('127.0.0.1',0),WorkflowStub)
        thread=threading.Thread(target=stub.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{stub.server_port}'
        request=dict(sample='recurring-ai-watch',
            implementation='recipes/recurring-ai-watch/python/google-adk',
            mode='mock',model='gemini-flash-lite',classifier='jev-default',
            cadence_web_url='http://attacker.example',cluster='attacker')
        try:
            self.explorer=Explorer(self.root,cadence_web_url=base)
            WorkflowStub.mode='found';WorkflowStub.run_id='run-current';WorkflowStub.paths.clear()
            result=self.explorer.workflow_run(request)
            self.assertEqual((result['state'],result['run_id'],result['cluster']),
                ('found','run-current','cluster0'))
            self.assertEqual(WorkflowStub.paths[-1],
                '/api/domains/cadence-ai-samples/cluster0/workflows/'
                'recurring-ai-watch-python-google-adk-mock-gemini-flash-lite-jev-default-demo')
            WorkflowStub.run_id='run-continued'
            self.assertEqual(self.explorer.workflow_run(request)['run_id'],'run-continued')
            for mode,state in [('not-found','no execution'),('auth','authentication required'),
                    ('malformed','malformed response'),('missing','no execution')]:
                WorkflowStub.mode=mode
                self.assertEqual(self.explorer.workflow_run(request)['state'],state)
            WorkflowStub.mode='slow'
            timed=Explorer(self.root,cadence_web_url=base,workflow_timeout=.001)
            self.assertEqual(timed.workflow_run(request)['state'],'unreachable')
            refused=socket.socket();refused.bind(('127.0.0.1',0))
            port=refused.getsockname()[1];refused.close()
            closed=Explorer(self.root,cadence_web_url=f'http://127.0.0.1:{port}')
            self.assertEqual(closed.workflow_run(request)['state'],'unreachable')
            with self.assertRaises(Unsupported):
                self.explorer.workflow_run(dict(sample='ticket-routing',
                    implementation='recipes/ticket-routing/go',mode='demo',classifier='mock'))

            WorkflowStub.mode='found';WorkflowStub.run_id='run-http'
            terminal=TerminalStub()
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(
                Handler,explorer=self.explorer,terminal_launcher=terminal))
            server_thread=threading.Thread(target=server.serve_forever,daemon=True)
            server_thread.start()
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            try:
                connection.request('GET','/api/workflow-run?'+urlencode(request))
                response=connection.getresponse();self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read())['run_id'],'run-http')
            finally:
                connection.close();server.shutdown();server.server_close();server_thread.join()
        finally:
            stub.shutdown();stub.server_close();thread.join()

    def test_workflow_signal_allowlist_revalidation_and_http_route(self):
        stub=ThreadingHTTPServer(('127.0.0.1',0),WorkflowStub)
        thread=threading.Thread(target=stub.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{stub.server_port}'
        request=dict(sample='recurring-ai-watch',
            implementation='recipes/recurring-ai-watch/python/google-adk',
            mode='mock',model='gemini-flash-lite',classifier='jev-default',
            run_id='run-current',signalName='attacker',signalInput='secret',
            cadence_web_url='http://attacker.example',cluster='attacker')
        try:
            self.explorer=Explorer(self.root,cadence_web_url=base)
            WorkflowStub.mode='found';WorkflowStub.run_id='run-current'
            WorkflowStub.signal_mode='accepted';WorkflowStub.posts.clear()
            for action,signal_name in [('check-now','check-now'),('stop-watch','stop-watch')]:
                result=self.explorer.workflow_signal({**request,'action':action})
                self.assertEqual((result['state'],result['signal_name']),('accepted',signal_name))
                path,body=WorkflowStub.posts[-1]
                self.assertTrue(path.endswith(f'/run-current/signal'))
                self.assertEqual(json.loads(body),dict(signalName=signal_name))
                self.assertNotIn(b'secret',body)
            post_count=len(WorkflowStub.posts)
            stale=self.explorer.workflow_signal({**request,'action':'check-now','run_id':'run-old'})
            self.assertEqual(stale['state'],'stale run')
            self.assertEqual(len(WorkflowStub.posts),post_count)
            for action in ('status','arbitrary',''):
                with self.assertRaises(InvalidSelection):
                    self.explorer.workflow_signal({**request,'action':action})
            with self.assertRaises(Unsupported):
                self.explorer.workflow_signal(dict(sample='ticket-routing',
                    implementation='recipes/ticket-routing/go',mode='demo',
                    classifier='mock',run_id='run-current',action='check-now'))
            for mode,state in [('not-found','no execution'),('auth','authentication required'),
                    ('rejected','rejected')]:
                WorkflowStub.signal_mode=mode
                self.assertEqual(self.explorer.workflow_signal(
                    {**request,'action':'check-now'})['state'],state)
            WorkflowStub.signal_mode='slow'
            timed=Explorer(self.root,cadence_web_url=base,workflow_timeout=.001)
            self.assertEqual(timed.workflow_signal(
                {**request,'action':'check-now'})['state'],'unreachable')

            WorkflowStub.signal_mode='accepted'
            terminal=TerminalStub()
            server=ThreadingHTTPServer(('127.0.0.1',0),partial(
                Handler,explorer=self.explorer,terminal_launcher=terminal))
            server_thread=threading.Thread(target=server.serve_forever,daemon=True)
            server_thread.start()
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
            headers={'Content-Type':'application/json',
                'Origin':f'http://127.0.0.1:{server.server_port}'}
            try:
                body=json.dumps({**request,'action':'check-now'})
                connection.request('POST','/api/workflow-signal',body=body,headers=headers)
                response=connection.getresponse();self.assertEqual(response.status,200)
                self.assertEqual(json.loads(response.read())['state'],'accepted')
                connection.request('POST','/api/workflow-signal',body=body,
                    headers={'Content-Type':'application/json'})
                response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            finally:
                connection.close();server.shutdown();server.server_close();server_thread.join()
        finally:
            stub.shutdown();stub.server_close();thread.join()

    def test_http_refresh_errors_and_loopback_boundary(self):
        terminal=TerminalStub()
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(
            Handler,explorer=self.explorer,terminal_launcher=terminal))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        try:
            connection.request('GET','/api/state');response=connection.getresponse()
            self.assertEqual(response.status,200);self.assertEqual(response.getheader('Cache-Control'),'no-store')
            state=json.loads(response.read());self.assertEqual(len(state['models']),3)
            self.assertTrue(state['terminal_handoff'])
            path=self.root/'models.yaml';path.write_text(path.read_text().replace('llama3.2:latest','new-model'))
            connection.request('GET','/api/state');response=connection.getresponse()
            self.assertEqual(json.loads(response.read())['models'][1]['model'],'new-model')
            connection.request('GET','/api/resolve?sample=bad');response=connection.getresponse()
            self.assertEqual(response.status,422);self.assertIn('error',json.loads(response.read()))
            connection.request('GET','/source?path=.git/config');response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            connection.request('GET','/api/state',headers={'Host':'attacker.example'});response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request('POST','/api/resolve',body='{}');response=connection.getresponse();self.assertEqual(response.status,405);response.read()
            request=json.dumps(dict(sample='recurring-ai-watch',
                implementation='recipes/recurring-ai-watch/python/google-adk',mode='mock',
                model='gemini-flash-lite',classifier='jev-default'))
            connection.request('POST','/api/warmup',body=request,headers={'Content-Type':'application/json'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request('POST','/api/warmup',body=request,headers={
                'Content-Type':'application/json','Origin':f'http://127.0.0.1:{server.server_port}'})
            response=connection.getresponse();self.assertEqual(response.status,200)
            self.assertEqual(json.loads(response.read())['components'],[])
            terminal_request=dict(sample='recurring-ai-watch',
                implementation='recipes/recurring-ai-watch/python/google-adk',mode='mock',
                model='gemini-flash-lite',classifier='jev-default',action='worker',
                command='malicious command')
            body=json.dumps(terminal_request)
            connection.request('POST','/api/terminal',body=body,headers={'Content-Type':'application/json'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            headers={'Content-Type':'application/json',
                'Origin':f'http://127.0.0.1:{server.server_port}'}
            connection.request('POST','/api/terminal',body=body,headers={
                **headers,'Host':'attacker.example'})
            response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request('POST','/api/terminal',body=body,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,200)
            self.assertEqual(json.loads(response.read())['state'],'prefilled')
            self.assertEqual(len(terminal.commands),1)
            self.assertIn('.venv/bin/python',terminal.commands[0])
            self.assertNotIn('malicious command',terminal.commands[0])
            terminal_request['action']='start';body=json.dumps(terminal_request)
            connection.request('POST','/api/terminal',body=body,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,200);response.read()
            self.assertIn(' start --mode mock',terminal.commands[-1])
            terminal_request['action']='status';body=json.dumps(terminal_request)
            connection.request('POST','/api/terminal',body=body,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            self.assertEqual(len(terminal.commands),2)
            unavailable=dict(sample='ticket-routing',implementation='recipes/ticket-routing/go',
                mode='worker',classifier='mock',action='start')
            connection.request('POST','/api/terminal',body=json.dumps(unavailable),headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            self.assertEqual(len(terminal.commands),2)
            connection.request('POST','/api/terminal',body='{broken',headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            connection.request('POST','/api/terminal',body='{}',headers={
                'Content-Type':'text/plain','Origin':headers['Origin']})
            response=connection.getresponse();self.assertEqual(response.status,415);response.read()
            connection.request('POST','/api/terminal',body=' '*16385,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,413);response.read()
            terminal.available=False
            terminal_request['action']='worker';body=json.dumps(terminal_request)
            connection.request('POST','/api/terminal',body=body,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            self.assertEqual(len(terminal.commands),2)
        finally:
            connection.close();server.shutdown();server.server_close();thread.join()

    def test_launcher_from_another_directory_and_shutdown(self):
        process=subprocess.Popen([str(ROOT/'scripts/explore.sh'),'--no-browser','--port','0'],
            cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            first_line=process.stdout.readline()
            self.assertIn('http://127.0.0.1:',first_line)
            process.send_signal(signal.SIGINT)
            stdout,stderr=process.communicate(timeout=5)
            self.assertEqual(process.returncode,0,stderr)
            self.assertIn('Explorer stopped.',stdout)
        finally:
            if process.poll() is None:
                process.kill();process.communicate()


if __name__=='__main__':
    unittest.main()
