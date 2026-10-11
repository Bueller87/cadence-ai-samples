"""Restricted source readers. Never import, build, or execute recipe modules.

These readers accept the repository's literal argparse, Go flag, and generated
Java Command interfaces. An unfamiliar expression raises Unsupported instead of
guessing a capability.
"""
from __future__ import annotations

import ast
import operator
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit


class Unsupported(ValueError):
    pass


class InvalidSelection(ValueError):
    pass


OPS = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.In: lambda a, b: a in b,
       ast.NotIn: lambda a, b: a not in b, ast.Lt: operator.lt, ast.LtE: operator.le,
       ast.Gt: operator.gt, ast.GtE: operator.ge, ast.Is: operator.is_, ast.IsNot: operator.is_not}


def value(node, env):
    """Evaluate only configuration expressions, not arbitrary Python code."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id in env:
        return env[node.id]
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        items = [value(n, env) for n in node.elts]
        return tuple(items) if isinstance(node, ast.Tuple) else items
    if isinstance(node, ast.Dict):
        return {value(key, env): value(item, env)
                for key, item in zip(node.keys, node.values)}
    if isinstance(node, ast.Attribute) and not node.attr.startswith('_'):
        owner = value(node.value, env)
        if isinstance(owner, (SimpleNamespace, type(urlsplit('http://localhost')))):
            return getattr(owner, node.attr)
    if isinstance(node, ast.BoolOp):
        for child in node.values:
            result = value(child, env)
            if isinstance(node.op, ast.And) and not result:
                return result
            if isinstance(node.op, ast.Or) and result:
                return result
        return result
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not value(node.operand, env)
    if isinstance(node, ast.Compare):
        left = value(node.left, env)
        for op, right_node in zip(node.ops, node.comparators):
            right = value(right_node, env)
            if type(op) not in OPS:
                raise Unsupported('Unrecognized comparison operator.')
            if not OPS[type(op)](left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mult, ast.Add)):
        left, right = value(node.left, env), value(node.right, env)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return (operator.mul if isinstance(node.op, ast.Mult) else operator.add)(left, right)
    if isinstance(node, ast.Call) and not node.keywords:
        args = [value(a, env) for a in node.args]
        if isinstance(node.func, ast.Name) and node.func.id in {'urlsplit', 'isLoopbackHost', 'trim'}:
            return env[node.func.id](*args)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {'rstrip', 'strip', 'get'}:
            owner = value(node.func.value, env)
            if isinstance(owner, str) and node.func.attr in {'rstrip', 'strip'}:
                return getattr(owner, node.func.attr)(*args)
            if isinstance(owner, dict) and node.func.attr == 'get':
                return getattr(owner, node.func.attr)(*args)
    raise Unsupported(f'Unrecognized source expression: {ast.unparse(node)}')


def constants(tree):
    env = {}
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            try:
                env[statement.targets[0].id] = value(statement.value, env)
            except (Unsupported, AttributeError, TypeError):
                pass
    return env


def python_cli(path):
    tree = ast.parse(path.read_text())
    env = constants(tree)
    parser = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'parser'), None)
    if parser is None:
        raise Unsupported('No literal parser() interface found in main.py.')
    scopes, root, commands = {}, {}, {}
    for statement in parser.body:
        if isinstance(statement,ast.Return):
            if not isinstance(statement.value,ast.Name) or statement.value.id not in scopes:
                raise Unsupported('parser() does not return a recognized parser.')
            continue
        if isinstance(statement,ast.Expr) and isinstance(statement.value,ast.Constant):
            continue
        if isinstance(statement,ast.Assign) and not isinstance(statement.value,ast.Call):
            if len(statement.targets)!=1 or not isinstance(statement.targets[0],ast.Name):
                raise Unsupported('Unrecognized parser assignment.')
            env[statement.targets[0].id]=value(statement.value,env)
            continue
        if not isinstance(statement,(ast.Assign,ast.Expr)):
            raise Unsupported('parser() uses a dynamic statement; see its source.')
        if isinstance(statement, ast.Assign) and isinstance(statement.value, ast.Call):
            call = statement.value
            if not isinstance(call.func, ast.Attribute) or len(statement.targets) != 1:
                raise Unsupported('Unrecognized parser construction.')
            name = statement.targets[0].id
            if call.func.attr == 'ArgumentParser':
                scopes[name] = root
            elif call.func.attr == 'add_parser':
                command = value(call.args[0], env)
                commands[command] = {}
                scopes[name] = commands[command]
            elif call.func.attr != 'add_subparsers':
                raise Unsupported('Unrecognized parser construction.')
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
            call = statement.value
            if isinstance(call.func, ast.Attribute) and call.func.attr == 'add_parser':
                commands[value(call.args[0], env)] = {}
                continue
            if not isinstance(call.func, ast.Attribute) or call.func.attr != 'add_argument':
                raise Unsupported('parser() uses an unfamiliar call; see its source.')
            receiver = call.func.value
            if not isinstance(receiver, ast.Name) or receiver.id not in scopes:
                raise Unsupported('Unrecognized argparse scope.')
            flags = [value(arg, env) for arg in call.args]
            flag = next((f for f in flags if f.startswith('--')), None)
            if flag is None:
                raise Unsupported('Positional CLI inputs require an explorer adapter.')
            spec = {}
            for kw in call.keywords:
                if kw.arg in {'choices', 'required', 'action', 'help'}:
                    spec[kw.arg] = value(kw.value, env)
                    if kw.arg=='action' and spec[kw.arg] not in {'store','store_true','store_false'}:
                        raise Unsupported('CLI uses an unfamiliar argument action.')
                elif kw.arg == 'default':
                    try:
                        spec['default'] = value(kw.value, env)
                    except Unsupported:
                        if flag != '--catalog-dir':
                            raise
                elif kw.arg=='type':
                    if not isinstance(kw.value,ast.Name) or kw.value.id not in {'str','int','float','Path','_positive'}:
                        raise Unsupported('CLI uses an unfamiliar argument validator.')
                    spec['arg_type']=kw.value.id
                elif kw.arg not in {'dest','metavar'}:
                    raise Unsupported(f'CLI uses an unfamiliar argument setting: {kw.arg}.')
            scopes[receiver.id][flag] = spec
    if not {'worker', 'start'} <= commands.keys():
        raise Unsupported('The Python adapter requires worker and start subcommands.')
    for required in ('--domain', '--task-list', '--workflow-id'):
        if required not in root:
            raise Unsupported(f'CLI lacks {required}; no matching Worker/client commands can be generated.')
    for scope in [root, commands['worker'], commands['start']]:
        if any(spec.get('required') and flag != '--mode'
               for flag, spec in scope.items()):
            raise Unsupported('CLI has required inputs beyond the current explorer flow; see the README.')
    mode_specs = [commands[c].get('--mode') for c in ('worker', 'start')]
    if any(mode_specs):
        if not all(mode_specs) or mode_specs[0].get('choices') != mode_specs[1].get('choices'):
            raise Unsupported('Worker and start modes are not the same literal choices.')
        modes = list(mode_specs[0].get('choices', []))
        if not modes or set(modes) - {'mock', 'live'}:
            raise Unsupported('Unrecognized inference modes; see the README.')
    else:
        modes = []
    inference_flags = set(root) | set(commands['worker'])
    if ('live' in modes
            and {'--model-id', '--classifier-id'} & inference_flags
            and '--confirm-live' not in commands['worker']):
        raise Unsupported('Live mode has no explicit Worker confirmation flag.')
    generic = set(commands) == {'worker', 'start'}
    if generic and set(modes) != {'mock', 'live'}:
        raise Unsupported('Generic Python profiles require explicit mock and live modes.')
    return dict(kind='python', root=root, commands=commands, modes=modes,
                actions=list(commands), generic=generic)


def python_guard(path, selection, function='validate_live'):
    tree = ast.parse(path.read_text())
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function), None)
    if fn is None:
        raise Unsupported(f'No readable {function}() found; live combinations cannot be verified.')
    env = {**constants(tree), 'selection': selection, 'urlsplit': urlsplit}
    if fn.args.args:
        env[fn.args.args[0].arg] = selection

    def execute(statements):
        for statement in statements:
            if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
                continue  # docstring
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                env[statement.targets[0].id] = value(statement.value, env)
            elif isinstance(statement, ast.If):
                execute(statement.body if value(statement.test, env) else statement.orelse)
            elif isinstance(statement, ast.Raise) and isinstance(statement.exc, ast.Call):
                message = value(statement.exc.args[0], env)
                raise InvalidSelection(message)
            elif isinstance(statement, ast.Return) and (statement.value is None or isinstance(statement.value, ast.Constant)):
                return
            else:
                raise Unsupported('validate_live() uses an unfamiliar statement; see its source.')
    execute(fn.body)


def java_cli(directory):
    """Read only the generated Java profile's fixed RecipeApplication CLI."""
    path = directory / 'src/main/java/com/example/cadence/recipe/RecipeApplication.java'
    if not path.is_file():
        raise Unsupported('No generated Java RecipeApplication CLI found.')
    source = path.read_text()
    if ('package com.example.cadence.recipe;' not in source
            or 'public final class RecipeApplication' not in source
            or 'record Command(' not in source
            or 'static Command parse(String[] args)' not in source):
        raise Unsupported('Java CLI does not match the generated profile contract.')

    constants = dict(re.findall(
        r'static final String (DEFAULT_[A-Z_]+) = "([^"]*)";', source))
    default_names = {
        '--address': 'DEFAULT_ADDRESS',
        '--domain': 'DEFAULT_DOMAIN',
        '--task-list': 'DEFAULT_TASK_LIST',
        '--workflow-id': 'DEFAULT_WORKFLOW_ID',
        '--text': 'DEFAULT_TEXT',
        '--catalog-dir': 'DEFAULT_CATALOG_DIR',
    }
    flags = set(re.findall(r'flag\.equals\("(--[a-z-]+)"\)', source))
    flags.update(re.findall(r'"(--[a-z-]+)"\.equals\(flag\)', source))
    required = {'--mode', '--address', '--domain', '--task-list',
                '--workflow-id', '--text'}
    if not required <= set(flags):
        raise Unsupported('Java adapter requires the generated mode, address, domain, task-list, workflow-id, and text flags.')
    if flags - (required | {'--classifier-id', '--catalog-dir', '--confirm-live'}):
        raise Unsupported('Java CLI exposes flags outside the generated profile contract.')

    root = {}
    for flag in flags:
        spec = {'type': 'Bool' if flag == '--confirm-live' else 'String'}
        if flag in default_names and default_names[flag] in constants:
            spec['default'] = constants[default_names[flag]]
        root[flag] = spec
    if '--classifier-id' in flags:
        mock = directory / 'src/main/java/com/example/cadence/recipe/MockClassifier.java'
        if not mock.is_file():
            raise Unsupported('Java SystemOne CLI has no readable mock classifier.')
        match = re.search(r'static final String ID = "([^"]+)";', mock.read_text())
        if not match:
            raise Unsupported('Java SystemOne mock classifier ID is not literal.')
        root['--classifier-id']['default'] = match[1]

    actions = re.findall(r'!"([^"]+)"\.equals\(action\)', source)
    modes = re.findall(r'!"([^"]+)"\.equals\(mode\)', source)
    if set(actions) != {'worker', 'start'} or len(actions) != 2:
        raise Unsupported('Generic Java profiles require exactly worker and start actions.')
    if set(modes) != {'mock', 'live'} or len(modes) != 2:
        raise Unsupported('Generic Java profiles require exactly mock and live modes.')
    classifier = '--classifier-id' in flags
    if classifier:
        if '--confirm-live' not in flags:
            raise Unsupported('Java SystemOne CLI has no explicit live Worker confirmation.')
        catalog_path = directory / 'src/main/java/com/example/cadence/recipe/ClassifierCatalog.java'
        if not catalog_path.is_file():
            raise Unsupported('Java SystemOne CLI has no readable classifier catalog contract.')
        catalog_source = catalog_path.read_text()
        providers = set(re.findall(
            r'static final String (?:TYPESAFE|LAYA) = "([^"]+)";', catalog_source))
        required_checks = ('endpoint.getRawUserInfo() != null',
                           'endpoint.getRawQuery() != null',
                           'endpoint.getRawFragment() != null',
                           'TYPESAFE.equals(config.provider()) && !"https".equals(scheme)',
                           'LAYA.equals(config.provider()) && !isLoopback(endpoint.getHost())')
        if providers != {'typesafe', 'laya'} or any(check not in catalog_source for check in required_checks):
            raise Unsupported('Java classifier validation no longer matches the bounded adapter.')
    else:
        providers = set()
    return dict(kind='java', root=root, modes=modes, actions=actions,
                generic=True, source=source, providers=providers)


def java_classifier(cli, config):
    if config['provider'] not in cli['providers']:
        raise InvalidSelection('The generated Java profile does not implement this classifier provider.')
    endpoint = urlsplit(config['endpoint'])
    try:
        endpoint.port
    except ValueError as error:
        raise InvalidSelection('Classifier endpoint has an invalid URL or port.') from error
    if (endpoint.scheme not in {'http', 'https'} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
        raise InvalidSelection('Classifier endpoint must be an HTTP URL without credentials, query, or fragment.')
    if config['provider'] == 'typesafe' and endpoint.scheme != 'https':
        raise InvalidSelection('Remote credential-bearing classifiers require an HTTPS endpoint.')
    if (config['provider'] == 'laya'
            and endpoint.hostname not in {'localhost', '127.0.0.1', '::1'}):
        raise InvalidSelection('Laya requires a loopback endpoint.')


def go_clean(source):
    """Strip comments while preserving quoted strings, including their braces."""
    pattern = r'"(?:\\.|[^"\\])*"|`[^`]*`|//[^\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: ' ' if m[0].startswith(('//', '/*')) else m[0], source)


def block(source, start):
    depth, quote, escaped = 0, None, False
    for index in range(start, len(source)):
        char = source[index]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\' and quote != '`':
                escaped = True
            elif char == quote:
                quote = None
        elif char in {'"', '`'}:
            quote = char
        elif char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return source[start + 1:index], index + 1
    raise Unsupported('Unclosed Go source block.')


def go_function(source, name, receiver=None):
    receiver_part = rf'\([^)]*\b{receiver}\b[^)]*\)\s*' if receiver else ''
    match = re.search(rf'func\s+{receiver_part}{name}\s*\([^\n]*\)[^{{\n]*\{{', source)
    if not match:
        raise Unsupported(f'No readable Go {name} function.')
    return block(source, match.end() - 1)[0]


def go_expr(text, env):
    text = text.replace('\n', ' ')
    text = text.replace('&&', ' and ').replace('||', ' or ')
    text = re.sub(r'!(?!=)', ' not ', text)
    text = re.sub(r'\bnil\b', 'None', text)
    text = text.replace('strings.TrimSpace', 'trim')
    text = text.replace('endpoint.Hostname()', 'endpoint.hostname')
    try:
        return value(ast.parse(text.strip(), mode='eval').body, env)
    except (SyntaxError, AttributeError, TypeError) as error:
        raise Unsupported('Unrecognized Go validation expression.') from error


def go_constants(source):
    env = {'time': SimpleNamespace(Second=1, Minute=60), 'false': False, 'true': True}
    for match in re.finditer(r'^\s*([A-Za-z]\w*)\s*=\s*([^\n]+)', source, re.M):
        try:
            env[match[1]] = go_expr(match[2], env)
        except Unsupported:
            pass
    return env


def go_validate(body, env):
    """Read simple if/return validators; unknown code fails closed."""
    cursor = 0
    while cursor < len(body):
        tail = body[cursor:].lstrip()
        cursor = len(body) - len(tail)
        if not tail or tail.startswith('return nil'):
            return
        if tail.startswith('endpoint, err := url.Parse(config.Endpoint)'):
            try:
                endpoint = urlsplit(env['config'].Endpoint)
                endpoint.port  # Reject invalid ports, as Go's url.Parse does.
            except ValueError as error:
                raise InvalidSelection('Classifier endpoint has an invalid URL or port.') from error
            env['endpoint'] = SimpleNamespace(Scheme=endpoint.scheme, hostname=endpoint.hostname or '',
                User=True if '@' in endpoint.netloc else None,
                RawQuery=endpoint.query, Fragment=endpoint.fragment)
            env['err'] = None
            cursor += len('endpoint, err := url.Parse(config.Endpoint)')
            continue
        if tail.startswith('return fmt.Errorf('):
            match = re.match(r'return fmt.Errorf\("((?:\\.|[^"\\])*)"', tail)
            message=match[1] if match else 'Recipe validation rejected this selection.'
            if match and re.search(r'%[qds]',message):
                args=re.match(r'\s*,\s*([^\n]+)\)',tail[match.end():])
                try:
                    values=iter(go_expr(arg.strip(),env) for arg in args[1].split(',')) if args else iter(())
                    message=re.sub(r'%[qds]',lambda token: repr(next(values)) if token[0]=='%q' else str(next(values)),message)
                except (Unsupported,StopIteration):
                    pass
            raise InvalidSelection(message)
        if not tail.startswith('if '):
            raise Unsupported('Go validator uses an unfamiliar statement; see its source.')
        opening = body.find('{', cursor)
        if opening < 0:
            raise Unsupported('Unreadable Go validation guard.')
        condition = body[cursor + 3:opening].strip()
        inner, cursor = block(body, opening)
        if ';' in condition:
            assignment, condition = condition.split(';', 1)
            name, expression = assignment.split(':=', 1)
            env[name.strip()] = go_expr(expression, env)
        if go_expr(condition, env):
            go_validate(inner, env)


def generic_go_cli(source):
    parse = go_function(source, 'parseCommand')
    validate = go_function(source, 'Validate', receiver='Config')
    action_switch = re.search(r'switch\s+config\.Action\s*\{', validate)
    mode_switch = re.search(r'switch\s+config\.Mode\s*\{', validate)
    if not action_switch or not mode_switch:
        raise Unsupported('Generic Go CLI requires literal action and mode switches.')
    actions = re.findall(
        r'"([^"]+)"', block(validate, action_switch.end() - 1)[0].split('default:', 1)[0])
    modes = re.findall(
        r'"([^"]+)"', block(validate, mode_switch.end() - 1)[0].split('default:', 1)[0])
    if set(actions) != {'worker', 'start'} or len(actions) != 2:
        raise Unsupported('Generic Go profiles require exactly worker and start actions.')
    if set(modes) != {'mock', 'live'} or len(modes) != 2:
        raise Unsupported('Generic Go profiles require exactly mock and live modes.')
    if 'config := Config{Action: args[0]}' not in parse:
        raise Unsupported('Generic Go actions must be the first positional argument.')

    flags = {}
    env = go_constants(source)
    pattern = (r'flags\.(String|Bool|Int|Float64|Duration)Var\('
               r'&config\.(\w+),\s*"([^"]+)",\s*([^,]+),\s*'
               r'"((?:\\.|[^"\\])*)"\)')
    for match in re.finditer(pattern, parse):
        kind, field, name, default, help_text = match.groups()
        try:
            default_value = go_expr(default, env)
        except Unsupported:
            default_value = None
        flags['-' + name] = dict(
            type=kind, default=default_value, help=help_text, field=field)
    required = {'-mode', '-address', '-domain', '-task-list'}
    if not required <= set(flags):
        raise Unsupported('Generic Go adapter requires mode, address, domain, and task-list flags.')
    allowed = required | {'-workflow-id', '-text', '-classifier-id',
                          '-catalog-dir', '-confirm-live'}
    if set(flags) - allowed:
        raise Unsupported('Generic Go CLI exposes flags outside the profile contract.')
    if '-classifier-id' in flags and '-confirm-live' not in flags:
        raise Unsupported('Generic Go SystemOne CLI has no explicit live Worker confirmation.')
    return dict(kind='go', root=flags, modes=modes, actions=actions,
                generic=True, env=env, source=source)


def go_cli(directory):
    source = go_clean('\n'.join(p.read_text() for p in sorted(directory.glob('*.go')) if not p.name.endswith('_test.go')))
    if 'flag.NewFlagSet' in source and 'func parseCommand(' in source:
        return generic_go_cli(source)
    main = go_function(source, 'main')
    flags = {}
    env = go_constants(source)
    for match in re.finditer(r'flag\.(String|Bool|Int|Float64|Duration)\("([^"]+)",\s*([^,]+),\s*"((?:\\.|[^"\\])*)"\)', main):
        kind, name, default, help_text = match.groups()
        try:
            default_value = go_expr(default, env)
        except Unsupported:
            default_value = None
        flags['-' + name] = dict(type=kind, default=default_value, help=help_text)
    for match in re.finditer(r'flag\.(String|Bool|Int|Float64|Duration)Var\([^,]+,\s*"([^"]+)",\s*([^,]+),\s*"((?:\\.|[^"\\])*)"\)', main):
        kind, name, default, help_text = match.groups()
        try:
            default_value = go_expr(default, env)
        except Unsupported:
            default_value = None
        flags['-' + name] = dict(type=kind, default=default_value, help=help_text)
    switch = re.search(r'switch \*mode\s*\{', main)
    if not switch or '-mode' not in flags or '-domain' not in flags or '-task-list' not in flags:
        raise Unsupported('Go adapter requires a literal mode switch, domain, and task list flags.')
    modes = re.findall(r'case "([^"]+)":', block(main, switch.end() - 1)[0])
    if 'worker' not in modes:
        raise Unsupported('Go CLI does not expose a Worker mode.')
    if '-classifier-id' in flags:
        classifier_flow = go_function(source, 'configuredClassifier')
        live = re.search(r'if\s*\(([^{}]*?)\)\s*&&\s*!live\s*\{', classifier_flow, re.S)
        mock = re.search(r'if\s*\(([^{}]*?)\)\s*&&\s*live\s*\{', classifier_flow, re.S)
        if not live or not mock:
            raise Unsupported('Go classifier mode binding is unfamiliar; see the README.')
        live_modes = re.findall(r'mode == "([^"]+)"', live[1])
        mock_modes = re.findall(r'mode == "([^"]+)"', mock[1])
        if set(live_modes + mock_modes) - set(modes):
            raise Unsupported('Go mode switch and classifier binding disagree.')
    else:
        if set(modes) != {'worker', 'start'}:
            raise Unsupported(
                'Classifier-free Go CLIs must expose only worker and start modes.'
            )
        live_modes = []
        mock_modes = ['start']
    return dict(kind='go', root=flags, modes=modes, actions=modes,
                live_modes=live_modes, mock_modes=mock_modes, generic=False,
                env=env, source=source)


def go_classifier(cli, config):
    namespace = SimpleNamespace(ID=config['id'], Provider=config['provider'], Model=config['model'], Endpoint=config['endpoint'])
    env = {**cli['env'], 'config': namespace, 'trim': str.strip,
           'isLoopbackHost': lambda host: host in {'localhost', '127.0.0.1', '::1'}}
    go_validate(go_function(cli['source'], 'validateClassifier'), env)
