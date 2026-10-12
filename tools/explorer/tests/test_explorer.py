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

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/explorer'))
from explorer import Explorer
from server import Handler
from signal_metadata import (MAX_METADATA_BYTES, MAX_SIGNALS,
                             SignalMetadataError, metadata_revision,
                             serialize_arguments, validate_arguments,
                             validate_metadata)
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
        payload=b'not json' if WorkflowStub.signal_mode=='malformed' else b'{}'
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
        self.assertEqual([r['id'] for r in state['recipes']],
                         ['human-in-the-loop','recurring-ai-watch','ticket-routing'])
        hitl,watch,ticket=state['recipes']
        self.assertEqual([v['framework'] for v in hitl['variants']],['openai-agents'])
        self.assertEqual([v['framework'] for v in watch['variants']],['google-adk','openai-agents'])
        self.assertTrue(all(v['supported'] for v in
                            hitl['variants']+watch['variants']+ticket['variants']))
        self.assertEqual(hitl['variants'][0]['modes'],['mock','live'])
        self.assertEqual(watch['variants'][0]['modes'],['mock','live'])
        self.assertEqual(ticket['variants'][0]['live_modes'],['live-demo','live-batch'])
        self.assertEqual(ticket['variants'][0]['mock_modes'],['demo','start-ticket','batch'])
        self.assertEqual([entry['id'] for entry in state['classifiers']],['jev-default','laya-local'])

    def test_human_in_the_loop_resolves_worker_and_start_without_imports(self):
        implementation='recipes/human-in-the-loop/python/openai-agents'
        variant=next(r for r in self.explorer.snapshot()['recipes']
                     if r['id']=='human-in-the-loop')['variants'][0]
        self.assertFalse(variant['generic'])
        self.assertEqual(set(variant['actions']),
                         {'worker','start','status','approve','reject'})
        result=self.explorer.resolve(dict(sample='human-in-the-loop',
            implementation=implementation,mode='live',model='llama3.2-local'))
        self.assertEqual([control['name'] for control in result['controls']],['status'])
        path=self.root/implementation/'main.py'
        tree=ast.parse(path.read_text())
        body=[n for n in tree.body if
              isinstance(n,ast.FunctionDef) and n.name=='parser' or
              isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
              and n.targets[0].id.startswith('DEFAULT_')]
        scope=dict(argparse=argparse,Path=Path,__file__=str(path),__doc__='CLI validation')
        exec(compile(ast.Module(body=body,type_ignores=[]),str(path),'exec'),scope)
        parsed={}
        for name,line in (('worker',result['worker']),('start',result['start'])):
            tokens=shlex.split(line)
            args=scope['parser']().parse_args(tokens[tokens.index('main.py')+1:])
            parsed[name]=args
        self.assertEqual(parsed['worker'].model_id,'llama3.2-local')
        self.assertEqual(parsed['worker'].mode,'live')
        self.assertTrue(parsed['worker'].confirm_live)
        self.assertEqual(parsed['start'].mode,'live')
        self.assertFalse(hasattr(parsed['start'],'model_id'))

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
        variant=next(r for r in self.explorer.snapshot()['recipes']
                     if r['id']=='recurring-ai-watch')['variants'][0]
        self.assertEqual(variant['modes'],['mock'])
        with self.assertRaises(InvalidSelection):self.watch()
        path.write_text(path.read_text().replace('cli.add_argument("--workflow-id", default=DEFAULT_WORKFLOW_ID)',''))
        variant=next(r for r in self.explorer.snapshot()['recipes']
                     if r['id']=='recurring-ai-watch')['variants'][0]
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
        self.assertEqual((variant['framework'],set(variant['actions']),
                          set(variant['modes']),variant['model'],variant['classifier']),
                         (None,{'worker','start'},{'mock','live'},False,False))
        self.assertEqual(variant['defaults']['--domain'],'cadence-ai-samples')
        result=self.explorer.resolve(dict(
            sample='invoice-review',implementation=implementation,mode='mock'))
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
            self.assertEqual((args.domain,args.task_list),
                             (result['domain'],result['task_list']))
            if args.command == 'start':
                self.assertEqual(args.workflow_id,result['workflow_id'])
            commands.append(args.command)
        self.assertEqual(commands,['worker','start'])

    def test_every_generated_profile_is_discovered_and_resolved(self):
        shutil.copytree(ROOT/'scripts',self.root/'scripts',ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'templates',self.root/'templates',ignore=shutil.ignore_patterns('.venv','__pycache__'))
        cases=[
            ('generated-go-bare',('go','none','none'),'go/bare'),
            ('generated-go-systemone',('go','systemone','none'),'go/systemone'),
            ('generated-java-bare',('java','none','none'),'java/bare'),
            ('generated-java-systemone',('java','systemone','none'),'java/systemone'),
            ('generated-python-bare',('python','none','none'),'python/bare'),
            ('generated-python-systemone',('python','systemone','none'),'python/systemone'),
            ('generated-google-adk',('python','none','google-adk'),'python/google-adk'),
            ('generated-google-adk-systemone',('python','systemone','google-adk'),
                'python/google-adk-systemone'),
            ('generated-openai-agents',('python','none','openai-agents'),
                'python/openai-agents'),
            ('generated-openai-agents-systemone',('python','systemone','openai-agents'),
                'python/openai-agents-systemone'),
        ]
        forbidden_actions={'demo','live-demo','batch','live-batch','acknowledge','benchmark'}
        for slug,(language,classifier,agent),profile in cases:
            with self.subTest(profile=profile):
                generated=subprocess.run([str(self.root/'scripts/new-recipe.sh'),slug,
                    '--language',language,'--classifier',classifier,'--agent',agent],
                    capture_output=True,text=True,timeout=30)
                self.assertEqual(generated.returncode,0,generated.stderr)
                implementation=f'recipes/{slug}/{profile}'
                recipe=next(r for r in self.explorer.snapshot()['recipes'] if r['id']==slug)
                variant=next(v for v in recipe['variants'] if v['id']==implementation)
                self.assertTrue(variant['supported'],variant['reason'])
                self.assertTrue(variant['generic'])
                self.assertEqual(set(variant['actions']),{'worker','start'})
                self.assertEqual(set(variant['modes']),{'mock','live'})
                self.assertFalse(set(variant['actions']) & forbidden_actions)
                self.assertFalse(set(variant['modes']) & forbidden_actions)
                for mode in ('mock','live'):
                    request=dict(sample=slug,implementation=implementation,mode=mode)
                    if mode == 'live' and agent != 'none':
                        request['model']='llama3.2-local'
                    if mode == 'live' and classifier == 'systemone':
                        request['classifier']='laya-local'
                    result=self.explorer.resolve(request)
                    self.assertEqual((result['live'],result['mock']),
                                     (mode=='live',mode=='mock'))
                    self.assertEqual(result['controls'],[])
                    commands={'worker':result['worker'],'start':result['start']}
                    for action,command in commands.items():
                        tokens=shlex.split(command)
                        if language == 'python':
                            cli=tokens[tokens.index('main.py')+1:]
                            actual_action=next(item for item in cli
                                               if item in {'worker','start'})
                            actual_mode=cli[cli.index('--mode')+1]
                        elif language == 'go':
                            cli=tokens[tokens.index('.')+1:]
                            actual_action=cli[0]
                            actual_mode=cli[cli.index('-mode')+1]
                        else:
                            argument=next(item for item in tokens
                                          if item.startswith('--args='))
                            cli=shlex.split(argument.removeprefix('--args='))
                            actual_action=cli[0]
                            actual_mode=cli[cli.index('--mode')+1]
                        self.assertEqual((actual_action,actual_mode),(action,mode))
                        self.assertNotIn(actual_action,forbidden_actions)
                        self.assertNotIn(actual_mode,forbidden_actions)
                    start_tokens=shlex.split(result['start'])
                    for flag in ('confirm-live','catalog-dir','model-id','classifier-id'):
                        self.assertFalse(any(flag in token for token in start_tokens),
                                         (profile,mode,flag,result['start']))
                    inference=classifier=='systemone' or agent!='none'
                    confirm='--confirm-live' if language!='go' else '-confirm-live'
                    self.assertEqual(confirm in result['worker'],
                                     mode=='live' and inference)
                    setup=result['setup'].splitlines()
                    expected_setup={
                        'go':['go mod download'],
                        'java':['./gradlew test'],
                        'python':['python3 -m venv .venv',
                                  'source .venv/bin/activate',
                                  'python -m pip install -e .'],
                    }[language]
                    self.assertEqual(setup[1:],expected_setup)

                if classifier=='systemone' or agent!='none':
                    request=dict(sample=slug,implementation=implementation,mode='live')
                    expected_credentials=[]
                    if classifier=='systemone':
                        request['classifier']='jev-default'
                        expected_credentials.append('CLASSIFIER_AI_KEY')
                    if agent!='none':
                        request['model']='gemini-flash-lite'
                        expected_credentials.append('MODEL_AI_KEY')
                    live_result=self.explorer.resolve(request)
                    self.assertEqual(
                        live_result['credentials'],
                        sorted(expected_credentials),
                        f'{profile}: credential-name drift',
                    )

        java_systemone=self.explorer.resolve(dict(
            sample='generated-java-systemone',
            implementation='recipes/generated-java-systemone/java/systemone',
            mode='live',classifier='laya-local'))
        self.assertTrue(java_systemone['live'])
        self.assertIn('--classifier-id laya-local',java_systemone['worker'])
        self.assertNotIn('--classifier-id',java_systemone['start'])
        self.assertEqual(java_systemone['credentials'],[])
        java_typesafe=self.explorer.resolve(dict(
            sample='generated-java-systemone',
            implementation='recipes/generated-java-systemone/java/systemone',
            mode='live',classifier='jev-default'))
        self.assertEqual(java_typesafe['credentials'],['CLASSIFIER_AI_KEY'])

        go_main=self.root/'recipes/generated-go-bare/go/bare/main.go'
        go_main.write_text(go_main.read_text().replace(
            'case "worker", "start":','case "worker", "start", "benchmark":'))
        recipe=next(r for r in self.explorer.snapshot()['recipes']
            if r['id']=='generated-go-bare')
        self.assertFalse(recipe['variants'][0]['supported'])
        self.assertIn('exactly worker and start',recipe['variants'][0]['reason'])

        java_main=self.root/(
            'recipes/generated-java-bare/java/bare/src/main/java/com/example/'
            'cadence/recipe/RecipeApplication.java')
        java_main.write_text(java_main.read_text().replace(
            '&& !"start".equals(action)',
            '&& !"start".equals(action) && !"benchmark".equals(action)'))
        recipe=next(r for r in self.explorer.snapshot()['recipes']
            if r['id']=='generated-java-bare')
        self.assertFalse(recipe['variants'][0]['supported'])
        self.assertIn('exactly worker and start',recipe['variants'][0]['reason'])

        classifiers=self.root/'classifiers.yaml'
        classifiers.write_text(classifiers.read_text().replace(
            'http://localhost:8008/v1/systemone',
            'http://example.com:8008/v1/systemone'))
        for profile,slug in (
                ('python/systemone','generated-python-systemone'),
                ('python/google-adk-systemone','generated-google-adk-systemone'),
                ('python/openai-agents-systemone','generated-openai-agents-systemone')):
            request=dict(sample=slug,implementation=f'recipes/{slug}/{profile}',
                         mode='live',classifier='laya-local')
            if profile != 'python/systemone':
                request['model']='llama3.2-local'
            with self.subTest(profile=profile,validation='catalog endpoint'):
                with self.assertRaises(InvalidSelection):
                    self.explorer.resolve(request)

    def test_generated_java_rejects_nested_source_symlinks(self):
        shutil.copytree(ROOT/'scripts',self.root/'scripts',
                        ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'templates',self.root/'templates',
                        ignore=shutil.ignore_patterns('.venv','__pycache__'))
        generated=subprocess.run([str(self.root/'scripts/new-recipe.sh'),'linked-java',
            '--language','java','--classifier','systemone','--agent','none'],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(generated.returncode,0,generated.stderr)
        implementation='recipes/linked-java/java/systemone'
        source=self.root/implementation/(
            'src/main/java/com/example/cadence/recipe/ClassifierFactory.java')
        with tempfile.TemporaryDirectory(prefix='outside java ') as outside:
            target=Path(outside)/'ClassifierFactory.java'
            target.write_text(source.read_text())
            source.unlink()
            source.symlink_to(target)
            recipe=next(r for r in self.explorer.snapshot()['recipes']
                        if r['id']=='linked-java')
            self.assertFalse(recipe['variants'][0]['supported'])
            self.assertIn('must not be a symlink',recipe['variants'][0]['reason'])
            request=dict(sample='linked-java',implementation=implementation,
                         mode='live',classifier='jev-default')
            with self.assertRaisesRegex(InvalidSelection,'must not be a symlink'):
                self.explorer.resolve(request)
            with self.assertRaisesRegex(InvalidSelection,'must not be a symlink'):
                self.explorer.check(request)

    def test_generated_java_rejects_symlinked_source_directory(self):
        shutil.copytree(ROOT/'scripts',self.root/'scripts',
                        ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copytree(ROOT/'templates',self.root/'templates',
                        ignore=shutil.ignore_patterns('.venv','__pycache__'))
        generated=subprocess.run([str(self.root/'scripts/new-recipe.sh'),'linked-java-dir',
            '--language','java','--classifier','systemone','--agent','none'],
            capture_output=True,text=True,timeout=30)
        self.assertEqual(generated.returncode,0,generated.stderr)
        implementation='recipes/linked-java-dir/java/systemone'
        source_dir=self.root/implementation/'src/main/java'
        with tempfile.TemporaryDirectory(prefix='outside java dir ') as outside:
            target=Path(outside)/'java'
            shutil.copytree(source_dir,target)
            shutil.rmtree(source_dir)
            source_dir.symlink_to(target,target_is_directory=True)
            recipe=next(r for r in self.explorer.snapshot()['recipes']
                        if r['id']=='linked-java-dir')
            self.assertFalse(recipe['variants'][0]['supported'])
            self.assertIn('must not be a symlink',recipe['variants'][0]['reason'])
            request=dict(sample='linked-java-dir',implementation=implementation,
                         mode='live',classifier='jev-default')
            with self.assertRaisesRegex(InvalidSelection,'must not be a symlink'):
                self.explorer.resolve(request)
            with self.assertRaisesRegex(InvalidSelection,'must not be a symlink'):
                self.explorer.check(request)

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
        generic=self.root/'recipes/generic-single/python'
        generic.mkdir(parents=True)
        watch_source=self.root/'recipes/recurring-ai-watch/python/google-adk'
        shutil.copy(watch_source/'main.py',generic/'main.py')
        shutil.copy(watch_source/'live.py',generic/'live.py')
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
            continued=self.explorer.workflow_run({**request,'run_id':'run-current'})
            self.assertEqual((continued['state'],continued['run_id']),
                ('stale run','run-continued'))
            self.assertIn('Continue-As-New',continued['detail'])

            hitl=dict(sample='human-in-the-loop',
                implementation='recipes/human-in-the-loop/python/openai-agents',
                mode='mock',model='gemini-flash-lite')
            WorkflowStub.run_id='run-hitl'
            hitl_result=self.explorer.workflow_run(hitl)
            self.assertEqual((hitl_result['state'],hitl_result['workflow_id'],
                hitl_result['run_id']),('found',
                    'human-in-the-loop-python-openai-agents-mock-gemini-flash-lite-demo',
                    'run-hitl'))

            generic_request=dict(sample='generic-single',
                implementation='recipes/generic-single/python',mode='mock',
                model='gemini-flash-lite',classifier='jev-default')
            WorkflowStub.run_id='run-generic'
            generic_result=self.explorer.workflow_run(generic_request)
            self.assertEqual((generic_result['state'],
                generic_result['workflow_id'],generic_result['run_id']),
                ('found',
                    'generic-single-python-bare-mock-gemini-flash-lite-jev-default-demo',
                    'run-generic'))

            ticket=dict(sample='ticket-routing',
                implementation='recipes/ticket-routing/go',mode='demo',
                classifier='mock')
            for workflow_id in ('ticket-routing-parent-001',
                    'ticket-routing-parent-001-child-billing',
                    'ticket-routing-batch-token-ticket-001'):
                WorkflowStub.run_id='run-ticket'
                selected=self.explorer.workflow_run(
                    {**ticket,'workflow_id':workflow_id})
                self.assertEqual((selected['state'],selected['workflow_id']),
                    ('found',workflow_id))
                self.assertTrue(WorkflowStub.paths[-1].endswith(
                    '/workflows/'+workflow_id))
            encoded=self.explorer.workflow_run(
                {**ticket,'workflow_id':'ticket parent/001'})
            self.assertEqual(encoded['workflow_id'],'ticket parent/001')
            self.assertTrue(WorkflowStub.paths[-1].endswith(
                '/workflows/ticket%20parent%2F001'))
            with self.assertRaises(Unsupported):
                self.explorer.workflow_run(ticket)
            with self.assertRaises(InvalidSelection):
                self.explorer.workflow_run(
                    {**ticket,'workflow_id':'bad\nworkflow'})

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

    def test_signal_metadata_schema_and_fail_closed_loading(self):
        valid=dict(version=1,signals=[dict(
            id='typed-signal',name='typed_signal',label='Typed signal',
            description='Exercise every supported scalar.',intent='confirm',
            arguments=[
                dict(name='text',label='Text',type='string',required=True,
                     constraints=dict(min_length=1,max_length=12),
                     help='Enter text.'),
                dict(name='count',label='Count',type='integer',required=False,
                     constraints=dict(minimum=1,maximum=5),
                     help='Enter a count.'),
                dict(name='ratio',label='Ratio',type='number',required=False,
                     constraints=dict(minimum=0,maximum=1),
                     help='Enter a ratio.'),
                dict(name='enabled',label='Enabled',type='boolean',
                     required=False,constraints={},help='Choose a state.'),
            ])])
        controls=validate_metadata(valid)
        self.assertEqual([argument['type'] for argument in controls[0]['arguments']],
                         ['string','integer','number','boolean'])
        revision=metadata_revision(controls)
        self.assertEqual(len(revision),64)
        self.assertEqual(revision,metadata_revision(validate_metadata(
            json.loads(json.dumps(valid)))))
        changed_controls=json.loads(json.dumps(controls))
        changed_controls[0]['intent']='destructive'
        self.assertNotEqual(revision,metadata_revision(changed_controls))
        values=validate_arguments(
            controls[0],dict(text='safe',count=2,ratio=.5,enabled=True))
        self.assertEqual(values,['safe',2,.5,True])
        self.assertEqual(
            serialize_arguments(values),'"safe" 2 0.5 true')
        self.assertIsNone(serialize_arguments([]))
        self.assertEqual(validate_metadata(dict(version=1)),[])

        invalid=[]
        invalid.append(('unknown field',{**valid,'command':'rm -rf /'}))
        invalid.append(('unknown version',{**valid,'version':2}))
        invalid.append(('too many signals',dict(
            version=1,signals=valid['signals']*(MAX_SIGNALS+1))))
        duplicate=json.loads(json.dumps(valid))
        duplicate['signals'].append(json.loads(json.dumps(valid['signals'][0])))
        invalid.append(('duplicate IDs',duplicate))
        duplicate_name=json.loads(json.dumps(valid))
        duplicate_name['signals'].append(
            json.loads(json.dumps(valid['signals'][0])))
        duplicate_name['signals'][1]['id']='other-signal'
        invalid.append(('duplicate names',duplicate_name))
        duplicate_argument=json.loads(json.dumps(valid))
        duplicate_argument['signals'][0]['arguments'][1]['name']='text'
        invalid.append(('duplicate arguments',duplicate_argument))
        too_many_arguments=json.loads(json.dumps(valid))
        too_many_arguments['signals'][0]['arguments']*=3
        invalid.append(('too many arguments',too_many_arguments))
        bad_type=json.loads(json.dumps(valid))
        bad_type['signals'][0]['arguments'][0]['type']='object'
        invalid.append(('unsupported type',bad_type))
        unhashable_type=json.loads(json.dumps(valid))
        unhashable_type['signals'][0]['arguments'][0]['type']=['string']
        invalid.append(('non-string type',unhashable_type))
        bad_order=json.loads(json.dumps(valid))
        bad_order['signals'][0]['arguments'][1]['required']=False
        bad_order['signals'][0]['arguments'][2]['required']=True
        invalid.append(('required after optional',bad_order))
        bad_constraint=json.loads(json.dumps(valid))
        bad_constraint['signals'][0]['arguments'][0]['constraints']['minimum']=1
        invalid.append(('type constraint',bad_constraint))
        bad_limits=json.loads(json.dumps(valid))
        bad_limits['signals'][0]['arguments'][0]['constraints']={
            'min_length':10,'max_length':1}
        invalid.append(('reversed limits',bad_limits))
        unsafe=json.loads(json.dumps(valid))
        unsafe['signals'][0]['description']='<script>unsafe</script>'
        invalid.append(('HTML',unsafe))
        too_long=json.loads(json.dumps(valid))
        too_long['signals'][0]['label']='x'*81
        invalid.append(('text limit',too_long))
        padded=json.loads(json.dumps(valid))
        padded['signals'][0]['label']=' padded'
        invalid.append(('padded text',padded))
        unknown_argument=json.loads(json.dumps(valid))
        unknown_argument['signals'][0]['arguments'][0]['extra']=True
        invalid.append(('unknown argument field',unknown_argument))
        for label,document in invalid:
            with self.subTest(label=label):
                with self.assertRaises(SignalMetadataError):
                    validate_metadata(document)

        for supplied in (
                {},dict(text=''),dict(text='too-long-for-limit'),
                dict(text='safe',count=0),
                dict(text='safe',count=10**1000),
                dict(text='safe',ratio=.5),
                dict(text='safe',unexpected='private-value')):
            with self.subTest(supplied=list(supplied)):
                with self.assertRaises(SignalMetadataError) as caught:
                    validate_arguments(controls[0],supplied)
                self.assertNotIn('private-value',str(caught.exception))

        watch=self.watch(mode='mock')
        self.assertEqual(
            [signal['id'] for signal in watch['signals']],
            ['check-now','stop-watch'])
        self.assertIsNone(watch['signal_metadata_error'])
        self.assertEqual(len(watch['signal_metadata_revision']),64)
        hitl=self.explorer.resolve(dict(
            sample='human-in-the-loop',
            implementation='recipes/human-in-the-loop/python/openai-agents',
            mode='mock',model='gemini-flash-lite'))
        self.assertEqual(
            [signal['id'] for signal in hitl['signals']],
            ['approve-tool-call','reject-tool-call'])
        self.assertEqual(
            hitl['signals'][0]['arguments'][0]['name'],'call_id')
        self.assertNotEqual(
            watch['signal_metadata_revision'],
            hitl['signal_metadata_revision'])
        ticket=self.ticket()
        self.assertEqual(ticket['signals'],[])
        self.assertIsNone(ticket['signal_metadata_error'])

        metadata=self.root/'recipes/recurring-ai-watch/explorer.yaml'
        metadata.write_text('version: 1\nsignals: [broken')
        broken=self.watch(mode='mock')
        self.assertEqual(broken['signals'],[])
        self.assertIsNone(broken['signal_metadata_revision'])
        self.assertIn('valid safe YAML',broken['signal_metadata_error'])
        metadata.write_bytes(b'x'*(MAX_METADATA_BYTES+1))
        self.assertIn('must not exceed',self.watch(
            mode='mock')['signal_metadata_error'])
        metadata.unlink()
        metadata.symlink_to(self.root/'models.yaml')
        self.assertIn('must not be a symlink',self.watch(
            mode='mock')['signal_metadata_error'])

    def test_workflow_signal_metadata_revalidation_and_http_route(self):
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
            WorkflowStub.paths.clear()
            request['signal_metadata_revision']=self.explorer.resolve(
                request)['signal_metadata_revision']
            missing_revision=dict(request)
            missing_revision.pop('signal_metadata_revision')
            with self.assertRaisesRegex(
                    InvalidSelection,'Signal metadata changed; refresh'):
                self.explorer.workflow_signal({
                    **missing_revision,'signal_id':'check-now','arguments':{}})
            self.assertEqual((WorkflowStub.paths,WorkflowStub.posts),([],[]))
            for signal_id,signal_name in [('check-now','check-now'),('stop-watch','stop-watch')]:
                result=self.explorer.workflow_signal({
                    **request,'signal_id':signal_id,'arguments':{}})
                self.assertEqual((result['state'],result['signal_name']),('accepted',signal_name))
                path,body=WorkflowStub.posts[-1]
                self.assertTrue(path.endswith(f'/run-current/signal'))
                self.assertEqual(json.loads(body),dict(signalName=signal_name))
                self.assertNotIn(b'secret',body)
            post_count=len(WorkflowStub.posts)
            stale=self.explorer.workflow_signal({
                **request,'signal_id':'check-now','arguments':{},
                'run_id':'run-old'})
            self.assertEqual(stale['state'],'stale run')
            self.assertEqual(len(WorkflowStub.posts),post_count)
            for signal_id in ('status','arbitrary',''):
                with self.assertRaises(InvalidSelection):
                    self.explorer.workflow_signal({
                        **request,'signal_id':signal_id,'arguments':{}})
            ticket=dict(sample='ticket-routing',
                implementation='recipes/ticket-routing/go',mode='demo',
                classifier='mock')
            ticket['signal_metadata_revision']=self.explorer.resolve(
                ticket)['signal_metadata_revision']
            path_count=len(WorkflowStub.paths)
            with self.assertRaises(InvalidSelection):
                self.explorer.workflow_signal({
                    **ticket,
                    'workflow_id':'ticket-routing-parent-001',
                    'run_id':'run-current','signal_id':'check-now',
                    'arguments':{}})
            self.assertEqual(len(WorkflowStub.posts),post_count)
            self.assertEqual(len(WorkflowStub.paths),path_count)

            hitl={**request,'sample':'human-in-the-loop',
                'implementation':'recipes/human-in-the-loop/python/openai-agents',
                'model':'gemini-flash-lite'}
            hitl['signal_metadata_revision']=self.explorer.resolve(
                hitl)['signal_metadata_revision']
            secret='call-id-private-value'
            result=self.explorer.workflow_signal({
                **hitl,'signal_id':'approve-tool-call',
                'arguments':{'call_id':secret}})
            self.assertEqual(result['state'],'accepted')
            _,body=WorkflowStub.posts[-1]
            self.assertEqual(json.loads(body),dict(
                signalName='approve_tool_call',
                signalInput=json.dumps(secret)))
            with self.assertRaises(InvalidSelection) as caught:
                self.explorer.workflow_signal({
                    **hitl,'signal_id':'approve-tool-call',
                    'arguments':{'call_id':'','schema':'private-value'}})
            self.assertNotIn('private-value',str(caught.exception))

            metadata=self.root/'recipes/human-in-the-loop/explorer.yaml'
            original=metadata.read_text()
            original_document=yaml.safe_load(original)
            changes=[
                ('Signal name',lambda document:
                    document['signals'][0].__setitem__(
                        'name','changed_signal')),
                ('intent',lambda document:
                    document['signals'][0].__setitem__('intent','normal')),
                ('argument name',lambda document:
                    document['signals'][0]['arguments'][0].__setitem__(
                        'name','changed_call_id')),
                ('argument type',lambda document:(
                    document['signals'][0]['arguments'][0].__setitem__(
                        'type','boolean'),
                    document['signals'][0]['arguments'][0].__setitem__(
                        'constraints',{}))),
                ('argument constraints',lambda document:
                    document['signals'][0]['arguments'][0][
                        'constraints'].__setitem__('max_length',254)),
                ('argument required status',lambda document:
                    document['signals'][0]['arguments'][0].__setitem__(
                        'required',False)),
                ('control removal',lambda document:
                    document['signals'].pop(0)),
            ]
            for label,change in changes:
                document=json.loads(json.dumps(original_document))
                change(document)
                metadata.write_text(yaml.safe_dump(
                    document,sort_keys=False))
                path_count=len(WorkflowStub.paths)
                post_count=len(WorkflowStub.posts)
                with self.subTest(metadata_change=label):
                    with self.assertRaisesRegex(
                            InvalidSelection,
                            'Signal metadata changed; refresh'):
                        self.explorer.workflow_signal({
                            **hitl,'signal_id':'approve-tool-call',
                            'arguments':{'call_id':secret}})
                    self.assertEqual(len(WorkflowStub.paths),path_count)
                    self.assertEqual(len(WorkflowStub.posts),post_count)
            metadata.write_text(original)

            for mode,state in [('not-found','no execution'),('auth','authentication required'),
                    ('rejected','rejected'),('malformed','malformed response')]:
                WorkflowStub.signal_mode=mode
                self.assertEqual(self.explorer.workflow_signal(
                    {**request,'signal_id':'check-now',
                     'arguments':{}})['state'],state)
            WorkflowStub.signal_mode='slow'
            timed=Explorer(self.root,cadence_web_url=base,workflow_timeout=.001)
            self.assertEqual(timed.workflow_signal(
                {**request,'signal_id':'check-now',
                 'arguments':{}})['state'],'unreachable')

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
                body=json.dumps({
                    **request,'signal_id':'check-now','arguments':{}})
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
