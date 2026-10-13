"""Load and validate bounded recipe-owned Explorer Signal metadata."""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

import yaml


MAX_METADATA_BYTES = 16 * 1024
MAX_SIGNALS = 16
MAX_ARGUMENTS = 1
MAX_OBJECT_FIELDS = 8
MAX_ID_LENGTH = 64
MAX_NAME_LENGTH = 128
MAX_LABEL_LENGTH = 80
MAX_DESCRIPTION_LENGTH = 240
MAX_HELP_LENGTH = 240
MAX_STRING_LENGTH = 4096
MAX_ABS_NUMBER = 1_000_000_000_000

ID_PATTERN = re.compile(r'^[a-z][a-z0-9-]*$')
ARGUMENT_PATTERN = re.compile(r'^[a-z][a-z0-9_]*$')
INTENTS = {'normal', 'confirm', 'destructive'}
SCALAR_TYPES = {'string', 'integer', 'number', 'boolean'}
TYPES = SCALAR_TYPES | {'object'}


class SignalMetadataError(ValueError):
    """Metadata is present but cannot be trusted."""


def _object(value, location):
    if not isinstance(value, dict):
        raise SignalMetadataError(f'{location} must be an object.')
    return value


def _fields(value, allowed, required, location):
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise SignalMetadataError(f'{location} has unknown fields.')
    if missing:
        raise SignalMetadataError(
            f'{location} is missing field {sorted(missing)[0]}.')


def _text(value, location, maximum, pattern=None):
    if (not isinstance(value, str) or not value.strip()
            or value != value.strip()
            or len(value) > maximum
            or any(ord(character) < 32 or ord(character) == 127
                   for character in value)
            or '<' in value or '>' in value):
        raise SignalMetadataError(f'{location} must be a nonempty bounded string.')
    if pattern and not pattern.fullmatch(value):
        raise SignalMetadataError(f'{location} has an unsupported format.')
    return value


def _number(value, location, integer=False):
    valid = (isinstance(value, int) and not isinstance(value, bool)
             if integer else
             isinstance(value, (int, float)) and not isinstance(value, bool))
    finite = not isinstance(value, float) or math.isfinite(value)
    if not valid or not finite or abs(value) > MAX_ABS_NUMBER:
        kind = 'integer' if integer else 'number'
        raise SignalMetadataError(f'{location} must be a bounded {kind}.')
    return value


def _constraints(value, argument_type, location):
    value = _object(value, location)
    allowed = ({
        'string': {'min_length', 'max_length'},
        'integer': {'minimum', 'maximum'},
        'number': {'minimum', 'maximum'},
        'boolean': set(),
    })[argument_type]
    _fields(value, allowed, set(), location)
    result = {}
    if argument_type == 'string':
        for name in value:
            result[name] = _number(
                value[name], f'{location}.{name}', integer=True)
            if result[name] < 0 or result[name] > MAX_STRING_LENGTH:
                raise SignalMetadataError(
                    f'{location}.{name} must be between 0 and '
                    f'{MAX_STRING_LENGTH}.')
        minimum = result.get('min_length', 0)
        maximum = result.get('max_length', MAX_STRING_LENGTH)
    elif argument_type in {'integer', 'number'}:
        for name in value:
            result[name] = _number(
                value[name], f'{location}.{name}',
                integer=argument_type == 'integer')
        minimum = result.get('minimum', -MAX_ABS_NUMBER)
        maximum = result.get('maximum', MAX_ABS_NUMBER)
    else:
        return result
    if minimum > maximum:
        raise SignalMetadataError(
            f'{location} minimum cannot exceed maximum.')
    return result


def _argument(raw_argument, location, allow_object):
    argument = _object(raw_argument, location)
    argument_type = argument.get('type')
    is_object = argument_type == 'object'
    fields = {
        'name', 'label', 'type', 'required', 'constraints', 'help'}
    if is_object:
        fields.add('fields')
    _fields(argument, fields, fields, location)
    argument_name = _text(
        argument['name'], f'{location}.name', MAX_ID_LENGTH, ARGUMENT_PATTERN)
    if (not isinstance(argument_type, str)
            or argument_type not in TYPES
            or (is_object and not allow_object)):
        raise SignalMetadataError(f'{location}.type is unsupported.')
    required = argument['required']
    if not isinstance(required, bool):
        raise SignalMetadataError(f'{location}.required must be true or false.')
    constraints = argument['constraints']
    if is_object:
        constraints = _object(constraints, f'{location}.constraints')
        _fields(constraints, set(), set(), f'{location}.constraints')
        raw_fields = argument['fields']
        if (not isinstance(raw_fields, list) or not raw_fields
                or len(raw_fields) > MAX_OBJECT_FIELDS):
            raise SignalMetadataError(
                f'{location}.fields must contain 1 to '
                f'{MAX_OBJECT_FIELDS} scalar fields.')
        seen, validated_fields = set(), []
        for field_index, raw_field in enumerate(raw_fields):
            field_location = f'{location}.fields[{field_index}]'
            field = _argument(raw_field, field_location, allow_object=False)
            if field['name'] in seen:
                raise SignalMetadataError(
                    f'{location} has duplicate field name {field["name"]}.')
            seen.add(field['name'])
            validated_fields.append(field)
    else:
        constraints = _constraints(
            constraints, argument_type, f'{location}.constraints')
        validated_fields = None
    result = {
        'name': argument_name,
        'label': _text(
            argument['label'], f'{location}.label', MAX_LABEL_LENGTH),
        'type': argument_type,
        'required': required,
        'constraints': constraints,
        'help': _text(
            argument['help'], f'{location}.help', MAX_HELP_LENGTH),
    }
    if validated_fields is not None:
        result['fields'] = validated_fields
    return result


def validate_metadata(document):
    document = _object(document, 'explorer.yaml')
    _fields(document, {'version', 'signals'}, {'version'}, 'explorer.yaml')
    if (not isinstance(document['version'], int)
            or isinstance(document['version'], bool)
            or document['version'] != 1):
        raise SignalMetadataError('explorer.yaml version must be 1.')
    signals = document.get('signals', [])
    if not isinstance(signals, list) or len(signals) > MAX_SIGNALS:
        raise SignalMetadataError(
            f'explorer.yaml signals must be a list of at most {MAX_SIGNALS}.')
    seen_ids, seen_names, result = set(), set(), []
    for signal_index, raw_signal in enumerate(signals):
        location = f'explorer.yaml signals[{signal_index}]'
        signal = _object(raw_signal, location)
        fields = {'id', 'name', 'label', 'description', 'intent', 'arguments'}
        _fields(signal, fields, fields, location)
        signal_id = _text(
            signal['id'], f'{location}.id', MAX_ID_LENGTH, ID_PATTERN)
        name = _text(signal['name'], f'{location}.name', MAX_NAME_LENGTH)
        if signal_id in seen_ids:
            raise SignalMetadataError(
                f'explorer.yaml has duplicate Signal ID {signal_id}.')
        if name in seen_names:
            raise SignalMetadataError(
                f'explorer.yaml has duplicate Signal name {name}.')
        seen_ids.add(signal_id)
        seen_names.add(name)
        intent = signal['intent']
        if not isinstance(intent, str) or intent not in INTENTS:
            raise SignalMetadataError(
                f'{location}.intent must be normal, confirm, or destructive.')
        arguments = signal['arguments']
        if not isinstance(arguments, list) or len(arguments) > MAX_ARGUMENTS:
            raise SignalMetadataError(
                f'{location}.arguments must contain at most one top-level '
                'argument because Cadence Web accepts one JSON document.')
        validated_arguments = []
        for argument_index, raw_argument in enumerate(arguments):
            argument_location = (
                f'{location}.arguments[{argument_index}]')
            validated_arguments.append(
                _argument(raw_argument, argument_location, allow_object=True))
        result.append({
            'id': signal_id,
            'name': name,
            'label': _text(
                signal['label'], f'{location}.label', MAX_LABEL_LENGTH),
            'description': _text(
                signal['description'], f'{location}.description',
                MAX_DESCRIPTION_LENGTH),
            'intent': intent,
            'arguments': validated_arguments,
        })
    return result


def load_metadata(recipe_path, inside):
    path = Path(recipe_path) / 'explorer.yaml'
    if path.is_symlink() or (path.exists() and not inside(path)):
        raise SignalMetadataError(
            'explorer.yaml must stay inside the checkout and must not be a symlink.')
    if not path.exists():
        return []
    if not path.is_file():
        raise SignalMetadataError('explorer.yaml must be a regular file.')
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise SignalMetadataError('explorer.yaml could not be read.') from error
    if len(raw) > MAX_METADATA_BYTES:
        raise SignalMetadataError(
            f'explorer.yaml must not exceed {MAX_METADATA_BYTES} bytes.')
    try:
        document = yaml.safe_load(raw.decode('utf-8'))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        raise SignalMetadataError('explorer.yaml is not valid safe YAML.') from error
    return validate_metadata(document)


def metadata_revision(signals):
    canonical = json.dumps(
        signals, ensure_ascii=False, allow_nan=False, sort_keys=True,
        separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(canonical).hexdigest()


def _validate_value(definition, value, kind='argument'):
    name = definition['name']
    value_type = definition['type']
    finite_number = not isinstance(value, float) or math.isfinite(value)
    valid = {
        'string': isinstance(value, str),
        'integer': isinstance(value, int) and not isinstance(value, bool),
        'number': (isinstance(value, (int, float))
                   and not isinstance(value, bool)
                   and finite_number),
        'boolean': isinstance(value, bool),
    }[value_type]
    if not valid:
        raise SignalMetadataError(
            f'Signal {kind} {name} must match its declared type.')
    constraints = definition['constraints']
    if value_type == 'string':
        minimum = constraints.get('min_length', 0)
        maximum = constraints.get('max_length', MAX_STRING_LENGTH)
        valid = minimum <= len(value) <= maximum
    elif value_type in {'integer', 'number'}:
        minimum = constraints.get('minimum', -MAX_ABS_NUMBER)
        maximum = constraints.get('maximum', MAX_ABS_NUMBER)
        valid = minimum <= value <= maximum
    if not valid:
        raise SignalMetadataError(
            f'Signal {kind} {name} does not satisfy its declared constraints.')
    return value


def _validate_object(argument, supplied):
    if not isinstance(supplied, dict):
        raise SignalMetadataError(
            f'Signal argument {argument["name"]} must match its declared type.')
    definitions = argument['fields']
    names = {field['name'] for field in definitions}
    if set(supplied) - names:
        raise SignalMetadataError(
            f'Signal object {argument["name"]} received an unknown field.')
    result = {}
    for field in definitions:
        name = field['name']
        if name not in supplied:
            if field['required']:
                raise SignalMetadataError(
                    f'Signal object {argument["name"]} requires field {name}.')
            continue
        result[name] = _validate_value(
            field, supplied[name], kind='object field')
    return result


def validate_arguments(signal, supplied):
    if not isinstance(supplied, dict):
        raise SignalMetadataError('Signal arguments must be an object.')
    definitions = signal['arguments']
    names = {argument['name'] for argument in definitions}
    unknown = set(supplied) - names
    if unknown:
        raise SignalMetadataError(
            f'Signal {signal["id"]} received an unknown argument.')
    values = []
    for argument in definitions:
        name = argument['name']
        if name not in supplied:
            if argument['required']:
                raise SignalMetadataError(
                    f'Signal {signal["id"]} requires argument {name}.')
            continue
        value = supplied[name]
        values.append(
            _validate_object(argument, value)
            if argument['type'] == 'object' else
            _validate_value(argument, value))
    return values


def serialize_arguments(values):
    if not values:
        return None
    if len(values) != 1:
        raise SignalMetadataError(
            'A Signal may contain at most one top-level JSON value.')
    return json.dumps(
        values[0], ensure_ascii=False, allow_nan=False, separators=(',', ':'))
