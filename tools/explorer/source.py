"""Restricted source readers. Never import or execute recipe modules.

These readers accept the repository's literal argparse and Go flag interfaces.
An unfamiliar expression raises Unsupported instead of guessing a capability.
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
        if isinstance(node.func, ast.Attribute) and node.func.attr in {'rstrip', 'strip'}:
            owner = value(node.func.value, env)
            if isinstance(owner, str):
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
        if any(s.get('required') for s in scope.values()):
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
    if 'live' in modes and '--confirm-live' not in commands['worker']:
        raise Unsupported('Live mode has no explicit Worker confirmation flag.')
    return dict(kind='python', root=root, commands=commands, modes=modes)


def python_guard(path, selection):
    tree = ast.parse(path.read_text())
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'validate_live'), None)
    if fn is None:
        raise Unsupported('No readable validate_live() found; live combinations cannot be verified.')
    env = {**constants(tree), 'selection': selection, 'urlsplit': urlsplit}

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


def go_cli(directory):
    source = go_clean('\n'.join(p.read_text() for p in sorted(directory.glob('*.go')) if not p.name.endswith('_test.go')))
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
    switch = re.search(r'switch \*mode\s*\{', main)
    if not switch or '-mode' not in flags or '-domain' not in flags or '-task-list' not in flags:
        raise Unsupported('Go adapter requires a literal mode switch, domain, and task list flags.')
    modes = re.findall(r'case "([^"]+)":', block(main, switch.end() - 1)[0])
    if 'worker' not in modes:
        raise Unsupported('Go CLI does not expose a Worker mode.')
    classifier_flow = go_function(source, 'configuredClassifier')
    live = re.search(r'if\s*\(([^{}]*?)\)\s*&&\s*!live\s*\{', classifier_flow, re.S)
    mock = re.search(r'if\s*\(([^{}]*?)\)\s*&&\s*live\s*\{', classifier_flow, re.S)
    if not live or not mock:
        raise Unsupported('Go classifier mode binding is unfamiliar; see the README.')
    live_modes = re.findall(r'mode == "([^"]+)"', live[1])
    mock_modes = re.findall(r'mode == "([^"]+)"', mock[1])
    if set(live_modes + mock_modes) - set(modes):
        raise Unsupported('Go mode switch and classifier binding disagree.')
    return dict(kind='go', root=flags, modes=modes, live_modes=live_modes, mock_modes=mock_modes,
                env=env, source=source)


def go_classifier(cli, config):
    namespace = SimpleNamespace(ID=config['id'], Provider=config['provider'], Model=config['model'], Endpoint=config['endpoint'])
    env = {**cli['env'], 'config': namespace, 'trim': str.strip,
           'isLoopbackHost': lambda host: host in {'localhost', '127.0.0.1', '::1'}}
    go_validate(go_function(cli['source'], 'validateClassifier'), env)
