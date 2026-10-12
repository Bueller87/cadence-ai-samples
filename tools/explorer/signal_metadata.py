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
MAX_ARGUMENTS = 8
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
TYPES = {'string', 'integer', 'number', 'boolean'}


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
                f'{location}.arguments must be a list of at most '
                f'{MAX_ARGUMENTS}.')
        argument_names, validated_arguments = set(), []
        optional_seen = False
        for argument_index, raw_argument in enumerate(arguments):
            argument_location = (
                f'{location}.arguments[{argument_index}]')
            argument = _object(raw_argument, argument_location)
            argument_fields = {
                'name', 'label', 'type', 'required', 'constraints', 'help'}
            _fields(
                argument, argument_fields, argument_fields, argument_location)
            argument_name = _text(
                argument['name'], f'{argument_location}.name',
                MAX_ID_LENGTH, ARGUMENT_PATTERN)
            if argument_name in argument_names:
                raise SignalMetadataError(
                    f'{location} has duplicate argument name {argument_name}.')
            argument_names.add(argument_name)
            argument_type = argument['type']
            if not isinstance(argument_type, str) or argument_type not in TYPES:
                raise SignalMetadataError(
                    f'{argument_location}.type is unsupported.')
            required = argument['required']
            if not isinstance(required, bool):
                raise SignalMetadataError(
                    f'{argument_location}.required must be true or false.')
            if optional_seen and required:
                raise SignalMetadataError(
                    f'{location} required arguments cannot follow optional ones.')
            optional_seen = optional_seen or not required
            validated_arguments.append({
                'name': argument_name,
                'label': _text(
                    argument['label'], f'{argument_location}.label',
                    MAX_LABEL_LENGTH),
                'type': argument_type,
                'required': required,
                'constraints': _constraints(
                    argument['constraints'], argument_type,
                    f'{argument_location}.constraints'),
                'help': _text(
                    argument['help'], f'{argument_location}.help',
                    MAX_HELP_LENGTH),
            })
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


def validate_arguments(signal, supplied):
    if not isinstance(supplied, dict):
        raise SignalMetadataError('Signal arguments must be an object.')
    definitions = signal['arguments']
    names = {argument['name'] for argument in definitions}
    unknown = set(supplied) - names
    if unknown:
        raise SignalMetadataError(
            f'Signal {signal["id"]} received an unknown argument.')
    values, gap = [], False
    for argument in definitions:
        name = argument['name']
        if name not in supplied:
            if argument['required']:
                raise SignalMetadataError(
                    f'Signal {signal["id"]} requires argument {name}.')
            gap = True
            continue
        if gap:
            raise SignalMetadataError(
                f'Signal {signal["id"]} optional arguments must be supplied in order.')
        value = supplied[name]
        argument_type = argument['type']
        finite_number = (not isinstance(value, float)
                         or math.isfinite(value))
        valid = {
            'string': isinstance(value, str),
            'integer': isinstance(value, int) and not isinstance(value, bool),
            'number': (isinstance(value, (int, float))
                       and not isinstance(value, bool)
                       and finite_number),
            'boolean': isinstance(value, bool),
        }[argument_type]
        if not valid:
            raise SignalMetadataError(
                f'Signal argument {name} must match its declared type.')
        constraints = argument['constraints']
        if argument_type == 'string':
            minimum = constraints.get('min_length', 0)
            maximum = constraints.get('max_length', MAX_STRING_LENGTH)
            valid = minimum <= len(value) <= maximum
        elif argument_type in {'integer', 'number'}:
            minimum = constraints.get('minimum', -MAX_ABS_NUMBER)
            maximum = constraints.get('maximum', MAX_ABS_NUMBER)
            valid = minimum <= value <= maximum
        if not valid:
            raise SignalMetadataError(
                f'Signal argument {name} does not satisfy its declared constraints.')
        values.append(value)
    return values


def serialize_arguments(values):
    if not values:
        return None
    return ' '.join(json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        for value in values)
