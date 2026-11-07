import json
import math
import re

from .protocol import GatewayError


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate JSON property')
        value[key] = item
    return value


def reject_constant(_value):
    raise ValueError('Non-finite JSON number')


def finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('Non-finite JSON number')
    return result


TOOL_JSON = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=reject_constant,
                            parse_float=finite_float)
MAX_ARGUMENT_JSON_DECODES = 3
FINAL_TEXT_PREFIX = 'GATEWAY_FINAL\n'


def multiline_final(raw):
    """Accept literal line whitespace only in a complete final-text envelope.

    Never apply this compatibility decoder to tool calls or their arguments.
    Duplicate fields, extra members, trailing data and broken quotes still fail.
    """
    if any(ord(char) < 32 and char not in '\n\r\t' for char in raw):
        return None
    try:
        value = json.JSONDecoder(strict=False, object_pairs_hook=unique_object,
                                 parse_constant=reject_constant, parse_float=finite_float).decode(raw)
        if (not isinstance(value, dict) or set(value) != {'type', 'content'}
                or value['type'] != 'final' or not isinstance(value['content'], str)):
            return None
        value['content'].encode('utf8')
        return value
    except (ValueError, UnicodeError, RecursionError):
        return None


def decode_arguments(value):
    """Remove bounded JSON string wrappers, preserving the argument values.

    Only the complete argument envelope is decoded; strings inside an object
    (commands, paths, JSON data, etc.) are never decoded or coerced.
    """
    for _ in range(MAX_ARGUMENT_JSON_DECODES):
        if not isinstance(value, str):
            break
        try:
            value = TOOL_JSON.decode(value)
        except (ValueError, RecursionError):
            raise GatewayError(502, 'tool_arguments_invalid', 'Tool arguments contain malformed JSON encoding, duplicate fields or non-finite numbers. No tool call was emitted.') from None
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        message = 'Tool arguments remain a string after the maximum of three JSON decoding layers. Return an object with the declared fields. No tool call was emitted.'
    elif isinstance(value, list):
        message = 'Tool arguments are an array; this tool requires a JSON object with named fields. No tool call was emitted.'
    elif value is None:
        message = 'Tool arguments are null; provide a JSON object with the declared fields. No tool call was emitted.'
    else:
        message = 'Tool arguments are a scalar; provide a JSON object with named fields rather than a number or boolean. No tool call was emitted.'
    raise GatewayError(502, 'tool_arguments_invalid', message)


class FinalTextStream:
    """Stream only the canonical final-answer string, never tool arguments.

    Other envelope layouts retain the buffered compatibility decoder. Final
    validation still runs before success; a malformed tail fails the stream.
    """
    prefix = re.compile(r'^\s*(?:```(?:json)?\s*)?\{\s*"type"\s*:\s*"final"\s*,\s*"content"\s*:\s*"')

    def __init__(self):
        self.pending = ''
        self.literal = False
        self.started = False
        self.closed = False
        self.disabled = False
        self.emitted = []

    def feed(self, text):
        if self.literal:
            self.emitted.append(text)
            return text
        if self.disabled or self.closed:
            return ''
        self.pending += text
        if not self.started:
            candidate = self.pending.lstrip()
            if FINAL_TEXT_PREFIX.startswith(candidate):
                return ''
            if candidate.startswith(FINAL_TEXT_PREFIX):
                self.literal = self.started = True
                result = candidate[len(FINAL_TEXT_PREFIX):]
                self.pending = ''
                self.emitted.append(result)
                return result
            match = self.prefix.match(self.pending)
            if not match:
                if len(self.pending) > 256:
                    self.disabled = True
                    self.pending = ''
                return ''
            self.started = True
            self.pending = self.pending[match.end():]
        pos, pieces = 0, []
        while pos < len(self.pending):
            char = self.pending[pos]
            if char == '"':
                self.closed = True
                pos += 1
                break
            if char != '\\':
                if (ord(char) < 32 and char not in '\n\r\t') or 0xD800 <= ord(char) <= 0xDFFF:
                    self.disabled = True
                    break
                pieces.append(char)
                pos += 1
                continue
            end = pos + 2
            if end > len(self.pending):
                break
            if self.pending[pos + 1] == 'u':
                end = pos + 6
                if end > len(self.pending):
                    break
                # Keep surrogate pairs together across arbitrary SSE splits.
                try:
                    code = int(self.pending[pos + 2:end], 16)
                except ValueError:
                    self.disabled = True
                    break
                if 0xD800 <= code <= 0xDBFF:
                    end += 6
                    if end > len(self.pending):
                        break
            try:
                decoded = json.loads('"' + self.pending[pos:end] + '"')
                if any(0xD800 <= ord(c) <= 0xDFFF for c in decoded):
                    raise ValueError()
            except ValueError:
                self.disabled = True
                break
            pieces.append(decoded)
            pos = end
        self.pending = self.pending[pos:]
        if self.closed or self.disabled:
            self.pending = ''
        result = ''.join(pieces)
        self.emitted.append(result)
        return result


TAGGED_ARGUMENT_TYPES = ('string', 'number', 'integer', 'boolean', 'object', 'array', 'null')


def tagged_value(kind, text):
    """Typed tags describe serialization, not permission to coerce values."""
    if kind == 'string':
        return text
    try:
        value = TOOL_JSON.decode(text)
    except (ValueError, RecursionError):
        raise GatewayError(502, 'tool_arguments_invalid', 'A typed tagged argument contains invalid JSON, duplicate fields or non-finite numbers. No tool call was emitted.') from None
    matches = {
        'number': type(value) in (int, float),
        'integer': type(value) is int or type(value) is float and value.is_integer(),
        'boolean': type(value) is bool,
        'object': isinstance(value, dict),
        'array': isinstance(value, list),
        'null': value is None,
    }
    if not matches.get(kind, False):
        raise GatewayError(502, 'tool_arguments_invalid', 'A tagged argument value does not match its declared type. Quoted numbers and booleans are not coerced. No tool call was emitted.')
    return value


def tagged_tools(raw):
    """Decode complete typed tool tags, then use the shared schema validator.

    The recovered failing response declares timeoutMs as type="number".
    String contents remain literal; only explicitly JSON-typed values parse.
    Structural whitespace and attribute order do not affect the meaning.
    """
    def malformed(message):
        return GatewayError(502, 'tool_bridge_invalid_output', message)
    start = raw.find('<|open|>tools<|sep|>')
    preface = raw[:start] if start >= 0 else ''
    if start < 0:
        raise malformed('The tagged tools opening marker is incomplete or unsupported. No tool call was emitted.')
    if re.search(r'<\|[^|]*\|>', preface) or preface.lstrip().startswith(('{', '[', '```')):
        raise malformed('The tagged tool envelope has an ambiguous prefix or another enclosing format. No tool call was emitted.')
    pos = start

    def whitespace():
        nonlocal pos
        while pos < len(raw) and raw[pos].isspace():
            pos += 1

    def take(token, message):
        nonlocal pos
        whitespace()
        if not raw.startswith(token, pos):
            raise malformed(message)
        pos += len(token)

    def header(kind, expected):
        nonlocal pos
        whitespace()
        prefix = '<|open|>' + kind
        end = raw.find('<|sep|>', pos)
        if not raw.startswith(prefix, pos) or end < 0 or end-pos > 1024:
            raise malformed('A tagged tool header is malformed or exceeds the header limit. No tool call was emitted.')
        text = raw[pos+len(prefix):end]
        attrs, offset = {}, 0
        while text[offset:].strip():
            match = re.compile(r'''\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(["'])([^<>]*?)\2''').match(text, offset)
            if not match or match[1] not in expected or match[1] in attrs:
                raise malformed('A tagged tool header has invalid, duplicate or unsupported attributes. No tool call was emitted.')
            attrs[match[1]] = match[3]
            offset = match.end()
        if set(attrs) != expected:
            raise malformed('A tagged tool header is missing required attributes. No tool call was emitted.')
        pos = end + len('<|sep|>')
        return attrs

    take('<|open|>tools<|sep|>', 'The tagged tools opening marker is incomplete. No tool call was emitted.')
    calls, indices = [], set()
    whitespace()
    while re.compile(r'<\|open\|>call(?=\s)').match(raw, pos):
        call = header('call', {'tool', 'index'})
        if (not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', call['tool'])
                or not re.fullmatch(r'[0-9]{1,4}', call['index'])
                or len(calls) >= 8 or int(call['index']) in indices):
            raise malformed('The tagged call has an invalid name, duplicate index or too many calls. No tool call was emitted.')
        name = call['tool']
        indices.add(int(call['index']))
        args = {}
        whitespace()
        while re.compile(r'<\|open\|>argument(?=\s)').match(raw, pos):
            argument = header('argument', {'key', 'type'})
            key, kind = argument['key'], argument['type']
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', key) or key in args or len(args) >= 128:
                raise malformed('The tagged call has invalid, duplicate or too many argument keys. No tool call was emitted.')
            if kind not in TAGGED_ARGUMENT_TYPES:
                raise malformed('The tagged argument type is unsupported. Use a declared JSON type. No tool call was emitted.')
            end = raw.find('<|close|>argument<|sep|>', pos)
            if end < 0 or re.search(r'<\|[^|]*\|>', raw[pos:end]):
                raise malformed('The tagged argument closing marker is missing or its body contains conflicting tags. No tool call was emitted.')
            args[key] = tagged_value(kind, raw[pos:end])
            pos = end
            take('<|close|>argument<|sep|>', 'The tagged argument closing marker is missing. No tool call was emitted.')
            whitespace()
        take('<|close|>call<|sep|>', 'The tagged call closing marker is missing. No tool call was emitted.')
        calls.append({'name': name, 'arguments': args})
        whitespace()
    take('<|close|>tools<|sep|>', 'The tagged tools closing marker is missing or contains unsupported entries. No tool call was emitted.')
    whitespace()
    if raw.startswith('<|close|>message<|sep|>', pos):
        take('<|close|>message<|sep|>', 'The tagged message closing marker is invalid. No tool call was emitted.')
    whitespace()
    if pos != len(raw) or not calls:
        raise malformed('The tagged tool envelope has trailing content or contains no calls. No tool call was emitted.')
    return {'type': 'tool_calls', 'tool_calls': calls, 'preface': preface}
