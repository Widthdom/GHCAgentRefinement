import json


def read_jsonl_objects(path):
    with open(path, encoding='utf-8-sig') as file:
        for line_number, line in enumerate(file, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except ValueError as exception:
                raise ValueError(f'Invalid JSON at line {line_number}: {exception}') from exception
            if not isinstance(value, dict):
                raise ValueError(f'Expected a JSON object at line {line_number}.')
            yield value
