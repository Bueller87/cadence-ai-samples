"""Behavioral checks against a temporary checkout; no AI or Cadence services."""
import argparse
import ast
import http.client
import json
import os
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
from unittest.mock import patch
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import yaml

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT/'tools/explorer'))
from explorer import Explorer
from server import ExplorerHTTPServer, Handler
from signal_metadata import (MAX_METADATA_BYTES, MAX_OBJECT_FIELDS, MAX_SIGNALS,
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
        request=dict(sample='ticket-routing',
            implementation='recipes/ticket-routing/go/systemone',mode='mock')
        request.update(kwargs)
        return self.explorer.resolve(request)

    def fake_preparation_launcher(self, implementation):
        path=self.root/implementation
        launcher=path/'run.sh'
        launcher.write_text("""#!/bin/sh
set -eu
[ "${1-}" = setup ] && [ "$#" -eq 1 ] || exit 64
state="$PWD/.run-state"; marker="$state/ready.v1"; temporary="$state/ready.tmp.$$"
mkdir -p "$state"
trap 'rm -f "$temporary"' EXIT HUP INT TERM
launcher=$(cksum "$PWD/run.sh" | awk '{print $1 ":" $2}')
dependency=$(cksum "$PWD/pyproject.toml" | awk '{print $1 ":" $2}')
printf 'version=1\\nlauncher=%s\\ndependency=pyproject.toml:%s\\n' \
  "$launcher" "$dependency" >"$temporary"
mv "$temporary" "$marker"
echo "Dependencies prepared."
""")
        launcher.chmod(0o755)
        return launcher

    def write_current_readiness(self, implementation):
        dependencies=self.explorer.dependency_paths(implementation)
        lines=['version=1',
            f'launcher={self.explorer.cksum(implementation/"run.sh")}']
        lines.extend(
            f'dependency={name}:{self.explorer.cksum(implementation/name)}'
            for name in dependencies)
        state=implementation/'.run-state'
        state.mkdir(exist_ok=True)
        (state/'ready.v1').write_text('\n'.join(lines)+'\n')

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
                         ['agent-handoffs','auto-research','human-in-the-loop',
                          'incident-triage','recurring-ai-watch','ticket-routing'])
        handoffs,auto,hitl,triage,watch,ticket=state['recipes']
        self.assertEqual([v['framework'] for v in handoffs['variants']],
                         ['google-adk','openai-agents'])
        self.assertTrue(all(v['modes']==['mock','live']
                            for v in handoffs['variants']))
        self.assertEqual([v['framework'] for v in auto['variants']],
                         ['google-adk','openai-agents'])
        self.assertEqual([v['framework'] for v in hitl['variants']],['openai-agents'])
        self.assertEqual([v['framework'] for v in watch['variants']],['google-adk','openai-agents'])
        self.assertTrue(all(v['supported'] for v in
                            hitl['variants']+watch['variants']+ticket['variants']))
        self.assertEqual([v['id'] for v in triage['variants']],
                         ['recipes/incident-triage/python/bare'])
        self.assertTrue(triage['variants'][0]['supported'])
        self.assertEqual(triage['variants'][0]['modes'],['mock','live'])
        triage_result=self.explorer.resolve(dict(
            sample='incident-triage',
            implementation='recipes/incident-triage/python/bare',
            mode='mock'))
        self.assertEqual(
            [signal['id'] for signal in triage_result['signals']],
            ['add-guidance','revise-brief','approval-decision'])
        self.assertEqual(hitl['variants'][0]['modes'],['mock','live'])
        self.assertEqual(watch['variants'][0]['modes'],['mock','live'])
        ticket_variant=next(v for v in ticket['variants']
                            if v['id']=='recipes/ticket-routing/go/systemone')
        self.assertTrue(ticket_variant['generic'])
        self.assertEqual(set(ticket_variant['actions']),{'start','worker'})
        self.assertEqual(set(ticket_variant['modes']),{'live','mock'})
        self.assertEqual(
            sorted(entry['id'] for entry in state['classifiers']),
            ['jev-default', 'laya-local'],
        )

    def test_agent_handoffs_resolves_both_framework_commands_without_imports(self):
        for framework in ('google-adk','openai-agents'):
            implementation=f'recipes/agent-handoffs/python/{framework}'
            with self.subTest(framework=framework):
                result=self.explorer.resolve(dict(sample='agent-handoffs',
                    implementation=implementation,mode='live',
                    model='llama3.2-local'))
                self.assertIn('--model-id llama3.2-local',result['worker'])
                self.assertIn('worker --mode live',result['worker'])
                self.assertIn('--confirm-live',result['worker'])
                self.assertIn('start --mode live',result['start'])
                self.assertNotIn('--model-id',result['start'])
                path=self.root/implementation/'main.py'
                tree=ast.parse(path.read_text())
                body=[n for n in tree.body if
                      isinstance(n,ast.FunctionDef) and n.name=='parser' or
                      isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
                      and n.targets[0].id.startswith('DEFAULT_')]
                scope=dict(argparse=argparse,Path=Path,__file__=str(path),
                           __doc__='CLI validation')
                exec(compile(ast.Module(body=body,type_ignores=[]),
                             str(path),'exec'),scope)
                for command in (result['worker'],result['start']):
                    tokens=shlex.split(command)
                    scope['parser']().parse_args(
                        tokens[tokens.index('./run.sh')+1:])

    def test_auto_research_resolves_commands_and_typed_signals(self):
        recipe=next(r for r in self.explorer.snapshot()['recipes']
                    if r['id']=='auto-research')
        for framework in ('google-adk','openai-agents'):
            implementation=f'recipes/auto-research/python/{framework}'
            variant=next(v for v in recipe['variants']
                         if v['framework']==framework)
            with self.subTest(framework=framework):
                self.assertFalse(variant['generic'])
                self.assertEqual(set(variant['actions']),
                                 {'worker','start','status','approve','revise','expand'})
                result=self.explorer.resolve(dict(sample='auto-research',
                    implementation=implementation,mode='live',
                    model='llama3.2-local'))
                self.assertEqual([control['name'] for control in result['controls']],
                                 ['status'])
                self.assertEqual([signal['id'] for signal in result['signals']],
                                 ['revise-report','expand-report','approve-report'])
                self.assertTrue(all(signal['intent']=='confirm'
                                    for signal in result['signals']))
                self.assertEqual(
                    [argument['type'] for argument in
                     result['signals'][0]['arguments']],['object'])
                self.assertEqual(
                    [field['name'] for field in
                     result['signals'][0]['arguments'][0]['fields']],
                    ['expected_revision','feedback'])
                self.assertIn('--model-id llama3.2-local',result['worker'])
                self.assertIn('start --mode live',result['start'])
                path=self.root/implementation/'main.py'
                tree=ast.parse(path.read_text())
                body=[n for n in tree.body if
                      isinstance(n,ast.FunctionDef) and n.name=='parser' or
                      isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name)
                      and n.targets[0].id.startswith('DEFAULT_')]
                scope=dict(argparse=argparse,Path=Path,__file__=str(path),
                           __doc__='CLI validation')
                exec(compile(ast.Module(body=body,type_ignores=[]),
                             str(path),'exec'),scope)
                for command in (result['worker'],result['start']):
                    tokens=shlex.split(command)
                    scope['parser']().parse_args(
                        tokens[tokens.index('./run.sh')+1:])

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
            args=scope['parser']().parse_args(tokens[tokens.index('./run.sh')+1:])
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
                                self.assertEqual(tokens[3],'./run.sh')
                                args=parser.parse_args(tokens[4:]);parsed.append(args);commands+=1
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
            self.assertEqual(tokens[:4],['cd',str((self.root/implementation).resolve()),'&&','./run.sh'])
            args=scope['parser']().parse_args(tokens[4:])
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
                        cli=tokens[tokens.index('./run.sh')+1:]
                        actual_action=next(item for item in cli
                                           if item in {'worker','start'})
                        mode_flag='-mode' if language == 'go' else '--mode'
                        actual_mode=cli[cli.index(mode_flag)+1]
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
                    self.assertEqual(
                        result['setup'],
                        'cd '+shlex.quote(str((self.root/implementation).resolve()))+
                        ' && ./run.sh setup')
                    self.assertRegex(result['launcher_revision'],r'^[0-9a-f]{64}$')
                    self.assertEqual(result['preparation']['state'],'not prepared')

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

    def test_ticket_routing_uses_generic_go_commands(self):
        result=self.ticket(mode='mock')
        self.assertIn('worker -mode mock',result['worker'])
        self.assertIn('start -mode mock',result['client'])
        self.assertNotIn('-classifier-id',result['worker'])
        self.assertNotIn('-text',result['client'])
        for classifier in ('jev-default','laya-local'):
            result=self.ticket(mode='live',classifier=classifier)
            self.assertTrue(result['live'])
            self.assertIn('-classifier-id '+classifier,result['worker'])
            self.assertIn('-confirm-live',result['worker'])
            self.assertNotIn('-classifier-id',result['client'])
            self.assertNotIn('-confirm-live',result['client'])
        for classifier in ('kev-local','von-local','reflex-local'):
            with self.assertRaises(InvalidSelection):
                self.ticket(mode='live',classifier=classifier)
        for mode in ('demo','batch','start-ticket','acknowledge','live-batch'):
            with self.assertRaises(InvalidSelection):self.ticket(mode=mode)

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

    def test_revision_bound_preparation_and_readiness_fail_closed(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.fake_preparation_launcher(implementation)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        resolved=self.explorer.resolve(request)
        self.assertRegex(resolved['launcher_revision'],r'^[0-9a-f]{64}$')
        self.assertEqual(resolved['preparation']['state'],'not prepared')
        with self.assertRaisesRegex(InvalidSelection,'revision'):
            self.explorer.prepare(request)
        with self.assertRaisesRegex(InvalidSelection,'only the current selection'):
            self.explorer.prepare({
                **request,'launcher_revision':resolved['launcher_revision'],
                'command':'malicious'})
        with self.assertRaisesRegex(InvalidSelection,'launcher changed'):
            self.explorer.prepare({**request,'launcher_revision':'0'*64})

        state=launcher.parent/'.run-state'
        state.mkdir()
        marker=state/'ready.v1'
        marker.write_text('version=1\n')
        self.assertEqual(self.explorer.preparation_state(launcher.parent)['state'],
                         'not prepared')
        marker.write_text('x'*4097)
        self.assertEqual(self.explorer.preparation_state(launcher.parent)['state'],
                         'not prepared')
        marker.unlink()
        outside=self.root/'outside-marker';outside.write_text('version=1\n')
        marker.symlink_to(outside)
        self.assertEqual(self.explorer.preparation_state(launcher.parent)['state'],
                         'not prepared')
        marker.unlink()

        prepared=self.explorer.prepare({
            **request,'launcher_revision':resolved['launcher_revision']})
        self.assertEqual(prepared['state'],'prepared')
        self.assertEqual(self.explorer.resolve(request)['preparation']['state'],
                         'prepared')
        pyproject=launcher.parent/'pyproject.toml'
        pyproject.write_text(pyproject.read_text()+'\n# stale\n')
        self.assertEqual(self.explorer.resolve(request)['preparation']['state'],'stale')

    def test_go_and_java_cache_readiness_fails_closed_and_detects_loss(self):
        go=self.explorer.root/'recipes/ticket-routing/go/systemone'
        java=self.explorer.root/'java-cache-fixture'
        java.mkdir()
        shutil.copy(
            ROOT/'templates/profiles/java/bare/recipe/java/bare/run.sh',
            java/'run.sh')
        (java/'gradlew').write_text('#!/bin/sh\n')
        (java/'build.gradle.kts').write_text('plugins { application }\n')
        (java/'settings.gradle.kts').write_text('rootProject.name = "fixture"\n')

        cases=(
            (go, go/'.run-state/go',
             ('gopath','gopath/pkg','gopath/pkg/mod','build'),
             'version=1\ncache=go\n'),
            (java, java/'.run-state/gradle-home',
             ('caches','wrapper','wrapper/dists'),
             'version=1\ncache=gradle\n'),
        )
        for implementation, cache, required, evidence_text in cases:
            with self.subTest(implementation=implementation.name):
                self.write_current_readiness(implementation)

                def populate():
                    cache.mkdir(parents=True,exist_ok=True)
                    for name in required:
                        (cache/name).mkdir(parents=True,exist_ok=True)
                    (cache/'prepared.v1').write_text(evidence_text)

                populate()
                self.assertEqual(
                    self.explorer.preparation_state(implementation)['state'],
                    'prepared')
                shutil.rmtree(cache)
                self.assertEqual(
                    self.explorer.preparation_state(implementation)['state'],
                    'stale')
                populate()
                for name in required:
                    shutil.rmtree(cache)
                    populate()
                    shutil.rmtree(cache/name)
                    self.assertEqual(
                        self.explorer.preparation_state(implementation)['state'],
                        'stale')
                shutil.rmtree(cache)
                populate()
                evidence=cache/'prepared.v1'
                for content in ('malformed\n','x'*129):
                    evidence.write_text(content)
                    self.assertEqual(
                        self.explorer.preparation_state(implementation)['state'],
                        'not prepared')
                evidence.unlink()
                outside=self.root/f'{implementation.name}-outside-evidence'
                outside.write_text(evidence_text)
                evidence.symlink_to(outside)
                self.assertEqual(
                    self.explorer.preparation_state(implementation)['state'],
                    'not prepared')
                evidence.unlink()
                evidence.write_text(evidence_text)
                unsafe=cache/required[0]
                shutil.rmtree(unsafe)
                external=self.root/f'{implementation.name}-outside-cache'
                external.mkdir()
                unsafe.symlink_to(external,target_is_directory=True)
                self.assertEqual(
                    self.explorer.preparation_state(implementation)['state'],
                    'not prepared')

    def test_launcher_revision_is_rechecked_immediately_before_execution(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.fake_preparation_launcher(implementation)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        revision=self.explorer.resolve(request)['launcher_revision']
        original=self.explorer.launcher
        calls=0

        def race(path):
            nonlocal calls
            result=original(path);calls+=1
            if calls==1:
                launcher.write_text(launcher.read_text()+'\n# changed\n')
            return result

        with patch.object(self.explorer,'launcher',side_effect=race):
            with self.assertRaisesRegex(InvalidSelection,'immediately before'):
                self.explorer.prepare({**request,'launcher_revision':revision})

    def test_launcher_replacement_after_descriptor_open_is_rejected(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.fake_preparation_launcher(implementation)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        revision=self.explorer.resolve(request)['launcher_revision']
        original=self.explorer.open_launcher
        calls=0
        executed=self.root/'unverified-executed'

        def replace_after_open(path):
            nonlocal calls
            descriptor,current_revision=original(path);calls+=1
            if calls==3:
                replacement=launcher.with_suffix('.replacement')
                replacement.write_text(
                    f'#!/bin/sh\ntouch {shlex.quote(str(executed))}\n')
                replacement.chmod(0o755)
                replacement.replace(launcher)
            return descriptor,current_revision

        with patch.object(self.explorer,'open_launcher',side_effect=replace_after_open):
            result=self.explorer.prepare({
                **request,'launcher_revision':revision})
        self.assertEqual(result['state'],'stale')
        self.assertFalse(executed.exists())
        self.assertFalse((launcher.parent/'.run-state/ready.v1').exists())

    def test_launcher_replacement_during_final_acceptance_is_rejected(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.fake_preparation_launcher(implementation)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        revision=self.explorer.resolve(request)['launcher_revision']
        original_state=self.explorer.preparation_state
        replaced=False

        def replace_after_prepared(path):
            nonlocal replaced
            state=original_state(path)
            if state['state']=='prepared' and not replaced:
                replaced=True
                replacement=launcher.with_suffix('.final-replacement')
                replacement.write_text('#!/bin/sh\nexit 0\n')
                replacement.chmod(0o755)
                replacement.replace(launcher)
            return state

        with patch.object(
                self.explorer,'preparation_state',
                side_effect=replace_after_prepared):
            result=self.explorer.prepare({
                **request,'launcher_revision':revision})
        self.assertEqual(result['state'],'stale')
        self.assertFalse((launcher.parent/'.run-state/ready.v1').exists())

    def test_preparation_environment_excludes_secrets_and_redacts_diagnostics(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.root/implementation/'run.sh'
        package_bin=self.root/'package-bin';package_bin.mkdir()
        package_tool=package_bin/'pip'
        package_tool.write_text(
            f"#!{sys.executable}\n"
            "import os\n"
            "for key, value in os.environ.items(): print(f'{key}={value}')\n"
            "print('Authorization: Bearer printed-authorization')\n"
            "print('MODEL_TOKEN=printed value with spaces')\n"
            "print('noise-' * 180000)\n"
            "print('HUGE_TOKEN=' + 'secret-tail-' * 100000)\n"
            "print('LONG_TOKEN=' + 'x' * 5000)\n"
            "print('final package failure')\n"
            "print('retry with the documented setup command')\n"
            "raise SystemExit(17)\n")
        package_tool.chmod(0o755)
        launcher.write_text('#!/bin/sh\nexec pip install\n')
        launcher.chmod(0o755)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        environment={
            'PATH':f'{package_bin}:/usr/bin:/bin',
            'HOME':'/sensitive/home-sentinel',
            'GRADLE_USER_HOME':'/sensitive/gradle-home-sentinel',
            'PIP_INDEX_URL':'https://pypi.org/simple',
            'MODEL_API_KEY':'inherited-model-sentinel',
            'CLASSIFIER_TOKEN':'inherited-classifier-sentinel',
            'UNRELATED_VALUE':'inherited-unrelated-sentinel',
            'AUTHORIZATION':'Bearer inherited-authorization',
        }
        with patch.dict(os.environ,environment,clear=True):
            revision=self.explorer.resolve(request)['launcher_revision']
            result=self.explorer.prepare({
                **request,'launcher_revision':revision})
        self.assertEqual(result['state'],'failed')
        diagnostic=result['diagnostic']
        for secret in (
                'inherited-model-sentinel','inherited-classifier-sentinel',
                'inherited-unrelated-sentinel','inherited-authorization',
                'printed-authorization','printed value with spaces',
                '/sensitive/home-sentinel','/sensitive/gradle-home-sentinel'):
            self.assertNotIn(secret,diagnostic)
        self.assertNotIn('x'*100,diagnostic)
        self.assertNotIn('secret-tail-',diagnostic)
        self.assertEqual(
            self.explorer.sanitize_preparation_output(
                b'Authorization: Bearer printed-authorization'),
            'Authorization: [redacted]')
        self.assertEqual(
            self.explorer.sanitize_preparation_output(
                b'MODEL_TOKEN=printed value with spaces'),
            'MODEL_TOKEN=[redacted]')
        self.assertEqual(
            self.explorer.sanitize_preparation_output(
                b'  MODEL_TOKEN=indented-secret\n'
                b'+ export MODEL_TOKEN=traced-secret\n'
                b'++ MODEL_TOKEN=nested-trace-secret'),
            'MODEL_TOKEN=[redacted]\n'
            '+ export MODEL_TOKEN=[redacted]\n'
            '++ MODEL_TOKEN=[redacted]')
        self.assertIn('final package failure',diagnostic)
        self.assertIn('retry with the documented setup command',diagnostic)
        self.assertEqual(
            self.explorer.sanitize_preparation_output(
                b'Bearer standalone-secret'),
            'Bearer [redacted]')

    def test_preparation_rejects_package_urls_with_embedded_credentials(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.fake_preparation_launcher(implementation)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        revision=self.explorer.resolve(request)['launcher_revision']
        for invalid_url in (
                'https://user:password@example.test/simple',
                'http://exa mple.com/path',
                'http://example.com:bad/path'):
            with self.subTest(url=invalid_url), patch.dict(
                    os.environ,{'PIP_INDEX_URL':invalid_url}):
                with self.assertRaisesRegex(
                        InvalidSelection,'without credentials|invalid'):
                    self.explorer.prepare({
                        **request,'launcher_revision':revision})
        self.assertFalse((launcher.parent/'.run-state/ready.v1').exists())

    def test_missing_nonexecutable_and_symlinked_launchers_fail_closed(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.root/implementation/'run.sh'
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        launcher.unlink()
        with self.assertRaises(Unsupported):
            self.explorer.resolve(request)
        shutil.copy(ROOT/implementation/'run.sh',launcher)
        launcher.chmod(0o644)
        with self.assertRaises(Unsupported):
            self.explorer.resolve(request)
        launcher.unlink()
        outside=self.root/'outside-run.sh'
        shutil.copy(ROOT/implementation/'run.sh',outside)
        outside.chmod(0o755)
        launcher.symlink_to(outside)
        with self.assertRaises(Unsupported):
            self.explorer.resolve(request)

    def test_preparation_timeout_kills_descendants_and_bounds_output(self):
        launcher=self.root/'slow-launcher'
        child_pid=self.root/'child.pid'
        launcher.write_text(f"""#!/bin/sh
trap '' TERM
mkdir -p .run-state/setup.lock
echo $$ >.run-state/setup.lock/pid
echo 'token=raw-secret' >.run-state/setup.log.tmp
touch .run-state/ready.tmp.$$
(trap '' TERM; while :; do sleep 1; done) &
echo $! >{shlex.quote(str(child_pid))}
while :; do printf 'TOKEN=secret-%04096d\\n' 1; done
""")
        launcher.chmod(0o755)
        explorer=Explorer(self.root,prepare_timeout=.05)
        returncode,timed_out,excerpt=explorer.run_preparation(launcher.resolve())
        self.assertTrue(timed_out)
        self.assertNotEqual(returncode,0)
        self.assertLessEqual(len(excerpt.encode()),4096)
        self.assertNotIn('TOKEN=secret-',excerpt)
        self.assertFalse((self.root/'.run-state/setup.lock').exists())
        self.assertFalse((self.root/'.run-state/setup.log.tmp').exists())
        self.assertEqual(list((self.root/'.run-state').glob('ready.tmp.*')),[])
        invalid=explorer.sanitize_preparation_output(b'\\xff'*4096)
        self.assertLessEqual(len(invalid.encode()),4096)
        deadline=time.time()+2
        while child_pid.exists() and time.time()<deadline:
            try:
                os.kill(int(child_pid.read_text()),0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        if child_pid.exists():
            with self.assertRaises(ProcessLookupError):
                os.kill(int(child_pid.read_text()),0)

    def test_server_shutdown_stops_active_preparation_descendants(self):
        implementation='recipes/agent-handoffs/python/google-adk'
        launcher=self.root/implementation/'run.sh'
        child_pid=self.root/'shutdown-child.pid'
        launcher.write_text(f"""#!/bin/sh
trap '' TERM
(trap '' TERM; while :; do sleep 1; done) &
echo $! >{shlex.quote(str(child_pid))}
while :; do sleep 1; done
""")
        launcher.chmod(0o755)
        explorer=Explorer(self.root,prepare_timeout=30)
        request=dict(sample='agent-handoffs',implementation=implementation,
            mode='mock',model='gemini-flash-lite')
        request['launcher_revision']=explorer.resolve(request)['launcher_revision']
        server=ExplorerHTTPServer(('127.0.0.1',0),explorer)
        server_thread=threading.Thread(target=server.serve_forever,daemon=True)
        server_thread.start()
        response={}

        def post_prepare():
            connection=http.client.HTTPConnection(
                '127.0.0.1',server.server_port,timeout=10)
            try:
                body=json.dumps(request)
                connection.request('POST','/api/prepare',body=body,headers={
                    'Content-Type':'application/json',
                    'Origin':f'http://127.0.0.1:{server.server_port}',
                    'Host':f'127.0.0.1:{server.server_port}',
                })
                reply=connection.getresponse()
                response['status']=reply.status
                response['body']=reply.read()
            finally:
                connection.close()

        request_thread=threading.Thread(target=post_prepare)
        request_thread.start()
        deadline=time.time()+3
        while not child_pid.exists() and time.time()<deadline:
            time.sleep(.01)
        self.assertTrue(child_pid.exists())
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)
        request_thread.join(timeout=5)
        self.assertFalse(request_thread.is_alive())
        pid=int(child_pid.read_text())
        deadline=time.time()+3
        while time.time()<deadline:
            try:
                os.kill(pid,0)
            except ProcessLookupError:
                break
            time.sleep(.02)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid,0)

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
        shutil.copy(watch_source/'run.sh',generic/'run.sh')
        shutil.copy(watch_source/'pyproject.toml',generic/'pyproject.toml')
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
                implementation='recipes/ticket-routing/go/systemone',mode='mock')
            WorkflowStub.run_id='run-ticket'
            selected=self.explorer.workflow_run(ticket)
            self.assertEqual((selected['state'],selected['workflow_id']),
                ('found','ticket-routing-go-systemone-mock-demo'))
            self.assertTrue(WorkflowStub.paths[-1].endswith(
                '/workflows/ticket-routing-go-systemone-mock-demo'))

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
            description='Exercise one bounded object.',intent='confirm',
            arguments=[dict(
                name='request',label='Request',type='object',required=True,
                constraints={},help='Enter the request.',
                fields=[
                    dict(name='text',label='Text',type='string',required=True,
                         constraints=dict(min_length=1,max_length=12),
                         help='Enter text.'),
                    dict(name='count',label='Count',type='integer',
                         required=False,constraints=dict(minimum=1,maximum=5),
                         help='Enter a count.'),
                    dict(name='ratio',label='Ratio',type='number',
                         required=False,constraints=dict(minimum=0,maximum=1),
                         help='Enter a ratio.'),
                    dict(name='enabled',label='Enabled',type='boolean',
                         required=False,constraints={},help='Choose a state.'),
                ])])])
        controls=validate_metadata(valid)
        self.assertEqual(controls[0]['arguments'][0]['type'],'object')
        self.assertEqual(
            [field['type'] for field in controls[0]['arguments'][0]['fields']],
                         ['string','integer','number','boolean'])
        revision=metadata_revision(controls)
        self.assertEqual(len(revision),64)
        self.assertEqual(revision,metadata_revision(validate_metadata(
            json.loads(json.dumps(valid)))))
        changed_controls=json.loads(json.dumps(controls))
        changed_controls[0]['intent']='destructive'
        self.assertNotEqual(revision,metadata_revision(changed_controls))
        values=validate_arguments(
            controls[0],dict(request=dict(
                text='safe',count=2,ratio=.5,enabled=True)))
        self.assertEqual(values,[dict(
            text='safe',count=2,ratio=.5,enabled=True)])
        self.assertEqual(
            serialize_arguments(values),
            '{"text":"safe","count":2,"ratio":0.5,"enabled":true}')
        self.assertIsNone(serialize_arguments([]))
        with self.assertRaisesRegex(
                SignalMetadataError,'at most one top-level JSON value'):
            serialize_arguments([1,2])
        self.assertEqual(validate_metadata(dict(version=1)),[])
        scalar=json.loads(json.dumps(valid))
        scalar_argument=dict(
            name='revision',label='Revision',type='integer',required=True,
            constraints=dict(minimum=0,maximum=100),help='Enter revision.')
        scalar['signals'][0]['arguments']=[scalar_argument]
        scalar_control=validate_metadata(scalar)[0]
        self.assertEqual(validate_arguments(
            scalar_control,dict(revision=3)),[3])
        self.assertEqual(serialize_arguments([3]),'3')

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
        too_many_arguments=json.loads(json.dumps(valid))
        too_many_arguments['signals'][0]['arguments'].append(scalar_argument)
        invalid.append(('multiple top-level arguments',too_many_arguments))
        bad_type=json.loads(json.dumps(valid))
        bad_type['signals'][0]['arguments'][0]['fields'][0]['type']='array'
        invalid.append(('unsupported type',bad_type))
        unhashable_type=json.loads(json.dumps(valid))
        unhashable_type['signals'][0]['arguments'][0]['type']=['object']
        invalid.append(('non-string type',unhashable_type))
        bad_constraint=json.loads(json.dumps(valid))
        bad_constraint['signals'][0]['arguments'][0]['constraints']['minimum']=1
        invalid.append(('type constraint',bad_constraint))
        bad_limits=json.loads(json.dumps(valid))
        bad_limits['signals'][0]['arguments'][0]['fields'][0]['constraints']={
            'min_length':10,'max_length':1}
        invalid.append(('reversed limits',bad_limits))
        no_fields=json.loads(json.dumps(valid))
        no_fields['signals'][0]['arguments'][0]['fields']=[]
        invalid.append(('empty object',no_fields))
        too_many_fields=json.loads(json.dumps(valid))
        too_many_fields['signals'][0]['arguments'][0]['fields']=[
            {**scalar_argument,'name':f'field_{index}'}
            for index in range(MAX_OBJECT_FIELDS+1)]
        invalid.append(('too many object fields',too_many_fields))
        duplicate_field=json.loads(json.dumps(valid))
        duplicate_field['signals'][0]['arguments'][0]['fields'][1]['name']='text'
        invalid.append(('duplicate object fields',duplicate_field))
        nested=json.loads(json.dumps(valid))
        nested_field=nested['signals'][0]['arguments'][0]['fields'][0]
        nested_field.update(type='object',constraints={},fields=[
            scalar_argument])
        invalid.append(('nested object',nested))
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
        unknown_argument['signals'][0]['arguments'][0]['fields'][0]['extra']=True
        invalid.append(('unknown argument field',unknown_argument))
        for label,document in invalid:
            with self.subTest(label=label):
                with self.assertRaises(SignalMetadataError):
                    validate_metadata(document)

        for supplied in (
                {},dict(request='not-an-object'),
                dict(request={}),dict(request=dict(text='')),
                dict(request=dict(text='too-long-for-limit')),
                dict(request=dict(text='safe',count=0)),
                dict(request=dict(text='safe',count=10**1000)),
                dict(request=dict(text='safe',ratio=2)),
                dict(request=dict(text='safe',enabled='true')),
                dict(request=dict(text='safe',unexpected='private-value')),
                dict(request=dict(text='safe'),unexpected='private-value')):
            with self.subTest(supplied=list(supplied)):
                with self.assertRaises(SignalMetadataError) as caught:
                    validate_arguments(controls[0],supplied)
                self.assertNotIn('private-value',str(caught.exception))
        self.assertEqual(validate_arguments(
            controls[0],dict(request=dict(text='safe',enabled=True))),
            [dict(text='safe',enabled=True)])

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
        auto=self.explorer.resolve(dict(
            sample='auto-research',
            implementation='recipes/auto-research/python/openai-agents',
            mode='mock',model='llama3.2-local'))
        self.assertEqual(
            [signal['id'] for signal in auto['signals']],
            ['revise-report','expand-report','approve-report'])
        self.assertEqual(
            [argument['name'] for argument in
             auto['signals'][0]['arguments']],
            ['request'])
        request={'expected_revision':2,'feedback':'Shorten it.'}
        self.assertEqual(
            serialize_arguments(validate_arguments(
                auto['signals'][0],
                {'request':request})),
            json.dumps(request,separators=(',',':')))
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
                implementation='recipes/ticket-routing/go/systemone',mode='mock')
            resolved_ticket=self.explorer.resolve(ticket)
            self.assertEqual(resolved_ticket['signals'],[])
            path_count=len(WorkflowStub.paths)
            post_count=len(WorkflowStub.posts)
            with self.assertRaises(InvalidSelection):
                self.explorer.workflow_signal({
                    **ticket,
                    'signal_metadata_revision':None,
                    'run_id':'run-current',
                    'signal_id':'acknowledge-assignment',
                    'arguments':{}})
            self.assertEqual(len(WorkflowStub.paths),path_count)
            self.assertEqual(len(WorkflowStub.posts),post_count)

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

            auto={**request,'sample':'auto-research',
                'implementation':'recipes/auto-research/python/openai-agents',
                'model':'llama3.2-local'}
            auto['signal_metadata_revision']=self.explorer.resolve(
                auto)['signal_metadata_revision']
            result=self.explorer.workflow_signal({
                **auto,'signal_id':'revise-report',
                'arguments':{'request':{
                    'expected_revision':2,
                    'feedback':'Shorten it.'}}})
            self.assertEqual(result['state'],'accepted')
            path,body=WorkflowStub.posts[-1]
            self.assertTrue(path.endswith('/run-current/signal'))
            self.assertEqual(json.loads(body),dict(
                signalName='revise_report',
                signalInput=json.dumps(dict(
                    expected_revision=2,feedback='Shorten it.'),
                    separators=(',',':'))))

            metadata=self.root/'recipes/human-in-the-loop/explorer.yaml'
            original=metadata.read_text()
            structured=dict(version=1,signals=[dict(
                id='revise',name='revise',label='Revise',
                description='Revise one version.',intent='confirm',
                arguments=[dict(
                    name='request',label='Request',type='object',
                    required=True,constraints={},help='Enter revision details.',
                    fields=[
                        dict(name='expected_revision',label='Expected revision',
                             type='integer',required=True,
                             constraints=dict(minimum=0,maximum=1000),
                             help='Enter the current revision.'),
                        dict(name='detail',label='Detail',type='string',
                             required=True,
                             constraints=dict(min_length=1,max_length=255),
                             help='Enter revision detail.'),
                    ])])])
            metadata.write_text(yaml.safe_dump(structured,sort_keys=False))
            structured_hitl={**hitl,
                'signal_metadata_revision':self.explorer.resolve(
                    hitl)['signal_metadata_revision']}
            structured_value=dict(
                expected_revision=2,detail='Keep this value private.')
            result=self.explorer.workflow_signal({
                **structured_hitl,'signal_id':'revise',
                'arguments':{'request':structured_value}})
            self.assertEqual(result['state'],'accepted')
            _,body=WorkflowStub.posts[-1]
            structured_body=json.loads(body)
            self.assertEqual(structured_body['signalName'],'revise')
            self.assertEqual(
                json.loads(structured_body['signalInput']),structured_value)
            self.assertNotIn('} {',structured_body['signalInput'])
            with self.assertRaises(InvalidSelection) as caught:
                self.explorer.workflow_signal({
                    **structured_hitl,'signal_id':'revise',
                    'arguments':{'request':{
                        **structured_value,'unknown':'private-value'}}})
            self.assertNotIn('private-value',str(caught.exception))
            metadata.write_text(original)

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
            connection.request('GET','/theme.js');response=connection.getresponse()
            self.assertEqual(response.status,200)
            self.assertEqual(response.getheader('Content-Type'),'text/javascript; charset=utf-8')
            self.assertIn(b'window.ExplorerTheme',response.read())
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
            prepare_selection=dict(sample='agent-handoffs',
                implementation='recipes/agent-handoffs/python/google-adk',
                mode='mock',model='gemini-flash-lite')
            self.fake_preparation_launcher(prepare_selection['implementation'])
            revision=self.explorer.resolve(prepare_selection)['launcher_revision']
            prepare_headers={'Content-Type':'application/json',
                'Origin':f'http://127.0.0.1:{server.server_port}'}
            connection.request('POST','/api/prepare',body=json.dumps({
                **prepare_selection,'launcher_revision':revision,'path':'/tmp/evil'}),
                headers=prepare_headers)
            response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            connection.request('POST','/api/prepare',body=json.dumps({
                **prepare_selection,'launcher_revision':revision}),
                headers=prepare_headers)
            response=connection.getresponse();self.assertEqual(response.status,200)
            self.assertEqual(json.loads(response.read())['state'],'prepared')
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
            self.assertIn('./run.sh',terminal.commands[0])
            self.assertNotIn('malicious command',terminal.commands[0])
            terminal_request['action']='start';body=json.dumps(terminal_request)
            connection.request('POST','/api/terminal',body=body,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,200);response.read()
            self.assertIn(' start --mode mock',terminal.commands[-1])
            terminal_request['action']='status';body=json.dumps(terminal_request)
            connection.request('POST','/api/terminal',body=body,headers=headers)
            response=connection.getresponse();self.assertEqual(response.status,422);response.read()
            self.assertEqual(len(terminal.commands),2)
            unavailable=dict(sample='ticket-routing',
                implementation='recipes/ticket-routing/go/systemone',
                mode='demo',action='start')
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
