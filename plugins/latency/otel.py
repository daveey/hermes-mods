"""Dependency-free, privacy-allowlisted OpenTelemetry JSON bridge.

The legacy validator remains the admission authority for identifiers and timing.
No arbitrary resource/span attributes or status messages are retained or exported.
"""
import hashlib
import re

HEX = re.compile(r'[0-9a-f]+\Z')
ATTRS = {'hermes.session_id': 'session', 'hermes.card_id': 'card',
         'hermes.pr': 'pr', 'hermes.run_id': 'run', 'hermes.phase': 'phase'}
FIELDS = {'trace_id', 'span_id', 'parent_span_id', 'name',
          'start_time_unix_nano', 'end_time_unix_nano', 'attributes'}


def identifier(value, length):
    if not isinstance(value, str) or len(value) != length or not HEX.fullmatch(value) or not int(value, 16):
        raise ValueError('invalid OpenTelemetry identifier')
    return value


def nanos(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdigit():
        raise ValueError('unix nano time must be an unsigned integer')
    result = int(value)
    if not 0 < result < 2**64:
        raise ValueError('unix nano time outside uint64')
    return result


def to_legacy(value):
    if not isinstance(value, dict) or set(value) - FIELDS:
        raise ValueError('unsupported OpenTelemetry span fields')
    trace = identifier(value.get('trace_id'), 32)
    span = identifier(value.get('span_id'), 16)
    parent = value.get('parent_span_id')
    if parent is not None:
        identifier(parent, 16)
    start, end = nanos(value.get('start_time_unix_nano')), nanos(value.get('end_time_unix_nano'))
    if end < start:
        raise ValueError('span end precedes start')
    attrs = value.get('attributes', {})
    if not isinstance(attrs, dict) or set(attrs) - (ATTRS.keys() | {'host.name', 'service.name', 'hermes.gateway'}):
        raise ValueError('unsupported OpenTelemetry attributes')
    if attrs.get('service.name') not in ('hermes-desktop', 'hermes-gateway'):
        raise ValueError('unsupported service.name')
    result = dict(id=f'{trace}:{span}', metric=value.get('name'), start=start / 1e9,
                  dur_ms=(end-start) / 1e6, host=attrs.get('host.name'),
                  gateway=attrs.get('hermes.gateway', attrs.get('host.name')),
                  attrs={short: attrs[key] for key, short in ATTRS.items() if key in attrs},
                  trace_id=trace, span_id=span, start_time_unix_nano=str(start),
                  end_time_unix_nano=str(end), service_name=attrs['service.name'])
    if parent is not None:
        result['parent_span_id'] = parent
    return result


def with_ids(sample):
    """Legacy retries derive stable IDs; explicit trace context is never replaced."""
    result = dict(sample)
    digest = hashlib.sha256(sample['id'].encode()).hexdigest()
    result.setdefault('trace_id', digest[:32])
    result.setdefault('span_id', digest[32:48])
    result.setdefault('start_time_unix_nano', str(round(sample['start'] * 1e9)))
    result.setdefault('end_time_unix_nano', str(int(result['start_time_unix_nano']) + round(sample['dur_ms'] * 1e6)))
    result.setdefault('service_name', 'hermes-gateway')
    return result


def kvs(values):
    if not isinstance(values, list) or len(values) > 20:
        raise ValueError('attributes must be a short key/value list')
    result = {}
    for item in values:
        if not isinstance(item, dict) or set(item) != {'key', 'value'} or not isinstance(item['key'], str):
            raise ValueError('invalid OTLP attribute')
        v = item['value']
        if item['key'] in result or not isinstance(v, dict) or set(v) != {'stringValue'}:
            raise ValueError('only unique string attributes are supported')
        result[item['key']] = v['stringValue']
    return result


def from_otlp(body):
    if not isinstance(body, dict) or set(body) != {'resourceSpans'} or not isinstance(body['resourceSpans'], list):
        raise ValueError('expected OTLP resourceSpans')
    result = []
    for resource in body['resourceSpans']:
        if not isinstance(resource, dict) or set(resource) - {'resource', 'scopeSpans', 'schemaUrl'}:
            raise ValueError('invalid OTLP resource')
        resource_body = resource.get('resource', {})
        if not isinstance(resource_body, dict) or set(resource_body) - {'attributes', 'droppedAttributesCount'}:
            raise ValueError('invalid OTLP resource attributes')
        attrs = kvs(resource_body.get('attributes', []))
        scopes = resource.get('scopeSpans', [])
        if not isinstance(scopes, list):
            raise ValueError('invalid scopeSpans')
        for scope in scopes:
            if not isinstance(scope, dict) or set(scope) - {'scope', 'spans', 'schemaUrl'} or not isinstance(scope.get('spans'), list):
                raise ValueError('invalid OTLP scope')
            for span in scope['spans']:
                if not isinstance(span, dict) or set(span) - {'traceId', 'spanId', 'parentSpanId', 'name', 'startTimeUnixNano', 'endTimeUnixNano', 'attributes', 'kind', 'flags'}:
                    raise ValueError('unsupported OTLP span fields')
                merged = {**attrs, **kvs(span.get('attributes', []))}
                sample = dict(trace_id=span.get('traceId'), span_id=span.get('spanId'), name=span.get('name'),
                              start_time_unix_nano=span.get('startTimeUnixNano'),
                              end_time_unix_nano=span.get('endTimeUnixNano'), attributes=merged)
                if span.get('parentSpanId'):
                    sample['parent_span_id'] = span['parentSpanId']
                result.append(sample)
                if len(result) > 200:
                    raise ValueError('too many OTLP spans')
    if not result:
        raise ValueError('empty OTLP batch')
    return result


def to_otlp(samples):
    resources = []
    for original in samples:
        sample = with_ids(original)
        attrs = {'host.name': sample['host'], 'service.name': sample['service_name'], 'hermes.gateway': sample['gateway']}
        attrs.update({key: sample['attrs'][short] for key, short in ATTRS.items() if short in sample['attrs']})
        span = dict(traceId=sample['trace_id'], spanId=sample['span_id'], name=sample['metric'],
                    startTimeUnixNano=sample['start_time_unix_nano'], endTimeUnixNano=sample['end_time_unix_nano'])
        if sample.get('parent_span_id'):
            span['parentSpanId'] = sample['parent_span_id']
        resources.append({'resource': {'attributes': [{'key': key, 'value': {'stringValue': value}} for key, value in attrs.items()]},
                          'scopeSpans': [{'scope': {'name': 'hermes-latency'}, 'spans': [span]}]})
    return {'resourceSpans': resources}
