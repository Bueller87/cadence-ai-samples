"""Behavioral checks against a temporary checkout; no AI or Cadence services."""
import argparse
import ast
import http.client
import json
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/explorer'))
from explorer import Explorer
from server import Handler
from source import InvalidSelection, Unsupported


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='cadence explorer ')
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT/'recipes',self.root/'recipes')
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
                                self.assertEqual(tokens[:3],['cd',str(self.root/f'recipes/recurring-ai-watch/python/{framework}'),'&&'])
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
        self.assertEqual((accepted,blocked,evidenced,commands),(24,36,6,120))

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

    def test_http_refresh_errors_and_loopback_boundary(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,explorer=self.explorer))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        connection=http.client.HTTPConnection('127.0.0.1',server.server_port)
        try:
            connection.request('GET','/api/state');response=connection.getresponse()
            self.assertEqual(response.status,200);self.assertEqual(response.getheader('Cache-Control'),'no-store')
            self.assertEqual(len(json.loads(response.read())['models']),3)
            path=self.root/'models.yaml';path.write_text(path.read_text().replace('llama3.2:latest','new-model'))
            connection.request('GET','/api/state');response=connection.getresponse()
            self.assertEqual(json.loads(response.read())['models'][1]['model'],'new-model')
            connection.request('GET','/api/resolve?sample=bad');response=connection.getresponse()
            self.assertEqual(response.status,422);self.assertIn('error',json.loads(response.read()))
            connection.request('GET','/source?path=.git/config');response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            connection.request('GET','/api/state',headers={'Host':'attacker.example'});response=connection.getresponse();self.assertEqual(response.status,403);response.read()
            connection.request('POST','/api/resolve',body='{}');response=connection.getresponse();self.assertEqual(response.status,405);response.read()
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
