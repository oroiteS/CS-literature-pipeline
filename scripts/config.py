"""配置读取与校验：不依赖第三方包，明确拒绝不支持的 YAML 语法。"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path


class ConfigError(ValueError):
    """配置语法或内容错误。"""


def _split(text: str, delimiter: str) -> list[str]:
    parts, start, quote, escaped, depth = [], 0, None, False, 0
    for i, c in enumerate(text):
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and c == '\\':
                escaped = True
            elif c == quote:
                # 单引号转义在解析时处理；连续引号开关两次不改变状态。
                quote = None
        elif c in "\"'":
            quote = c
        elif c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth < 0:
                raise ConfigError('意外的 ]')
        elif c == delimiter and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    if quote or depth:
        raise ConfigError('引号或方括号未闭合')
    parts.append(text[start:].strip())
    return parts


def _comment(line: str) -> str:
    quote, escaped = None, False
    for i, c in enumerate(line):
        if quote:
            if quote == '"' and escaped:
                escaped = False
            elif quote == '"' and c == '\\':
                escaped = True
            elif c == quote:
                quote = None
        elif c in "\"'":
            quote = c
        elif c == '#' and (i == 0 or line[i - 1].isspace()):
            return line[:i].rstrip()
    return line.rstrip()


def _scalar(value: str):
    if not value:
        raise ConfigError('缺少值')
    if value.startswith('['):
        if not value.endswith(']'):
            raise ConfigError('内联列表未闭合')
        inside = value[1:-1].strip()
        if not inside:
            return []
        items = _split(inside, ',')
        if any(not item for item in items):
            raise ConfigError('列表存在空项')
        return [_scalar(item) for item in items]
    if value[0] == '"':
        try:
            result = json.loads(value)
        except (ValueError, TypeError) as e:
            raise ConfigError(f'无效双引号字符串: {e}') from e
        if not isinstance(result, str):
            raise ConfigError('无效字符串')
        return result
    if value[0] == "'":
        if len(value) < 2 or value[-1] != "'":
            raise ConfigError('单引号未闭合')
        inner = value[1:-1]
        if "'" in inner.replace("''", ''):
            raise ConfigError('无效单引号字符串')
        return inner.replace("''", "'")
    if any(c in value for c in "[]{}\"'") or value[0] in '&*!|>@`%' or value in ('---', '...'):
        raise ConfigError(f'不支持的 YAML 值: {value}')
    if ': ' in value or value.endswith(':'):
        raise ConfigError(f'不支持的映射值: {value}')
    if re.fullmatch(r'[-+]?(?:0|[1-9][0-9]*)', value):
        return int(value)
    if re.fullmatch(r'[-+]?(?:[0-9]+\.[0-9]*|[0-9]*\.[0-9]+)(?:[eE][-+]?[0-9]+)?', value):
        return float(value)
    if value.lower() in ('true', 'false'):
        return value.lower() == 'true'
    if value.lower() in ('null', '~'):
        return None
    # 拒绝 YAML 的隐式类型及结构标记，避免与标准解析器产生分歧。
    if re.match(r'^[-?](?:\s|$)', value) or value.lower() in ('yes', 'no', 'on', 'off', '.nan', '.inf', '-.inf', '+.inf'):
        raise ConfigError(f'不支持的标量: {value}')
    return value


def parse_yaml(text: str) -> dict:
    """解析受限 YAML：缩进映射/序列、标量和内联序列。"""
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if '\t' in raw[:len(raw) - len(raw.lstrip())]:
            raise ConfigError(f'第 {number} 行: 缩进不能含制表符')
        line = _comment(raw)
        if line.strip():
            lines.append((len(line) - len(line.lstrip(' ')), line.lstrip(' '), number))
    if not lines:
        return {}
    if lines[0][0] != 0:
        raise ConfigError('首行不能缩进')

    def mapping_entry(content, number):
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_.-]*):(?:\s+(.*))?', content)
        if not match:
            raise ConfigError(f'第 {number} 行: 不支持的映射语法')
        return match.group(1), match.group(2)

    def block(i, indent):
        seq = lines[i][1] == '-' or lines[i][1].startswith('- ')
        result = [] if seq else {}
        while i < len(lines) and lines[i][0] == indent:
            _, content, number = lines[i]
            is_item = content == '-' or content.startswith('- ')
            if is_item != seq:
                raise ConfigError(f'第 {number} 行: 映射和序列混用')
            if seq:
                rest = content[1:].strip()
                i += 1
                if not rest:
                    if i >= len(lines) or lines[i][0] <= indent:
                        raise ConfigError(f'第 {number} 行: 空序列项')
                    value, i = block(i, lines[i][0])
                elif re.match(r'^[A-Za-z_][A-Za-z0-9_.-]*:(?:\s|$)', rest):
                    key, raw_value = mapping_entry(rest, number)
                    value = {}
                    if raw_value is not None:
                        value[key] = _scalar(raw_value)
                    else:
                        if i >= len(lines) or lines[i][0] <= indent + 2:
                            raise ConfigError(f'第 {number} 行: 缺少嵌套值')
                        value[key], i = block(i, lines[i][0])
                    if i < len(lines) and lines[i][0] > indent:
                        extra, i = block(i, lines[i][0])
                        if not isinstance(extra, dict) or key in extra:
                            raise ConfigError(f'第 {number} 行: 无效或重复映射项')
                        value.update(extra)
                else:
                    value = _scalar(rest)
                result.append(value)
            else:
                key, raw_value = mapping_entry(content, number)
                if key in result:
                    raise ConfigError(f'第 {number} 行: 重复键 {key}')
                i += 1
                if raw_value is None:
                    if i >= len(lines) or lines[i][0] <= indent:
                        raise ConfigError(f'第 {number} 行: 缺少嵌套值')
                    value, i = block(i, lines[i][0])
                else:
                    value = _scalar(raw_value)
                result[key] = value
            if i < len(lines) and lines[i][0] > indent:
                raise ConfigError(f'第 {lines[i][2]} 行: 意外缩进')
        return result, i

    parsed, end = block(0, 0)
    if end != len(lines) or not isinstance(parsed, dict):
        raise ConfigError('顶层必须是映射，且缩进须一致')
    return parsed


def load_yaml(path: str | Path) -> dict:
    path = Path(path)
    try:
        return parse_yaml(path.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ConfigError) as e:
        raise ConfigError(f'{path}: {e}') from e


def validate_config(profile: dict, venues: dict) -> list[str]:
    """返回全部配置问题；空列表表示可运行。"""
    errors = []
    if not isinstance(profile, dict) or not isinstance(venues, dict):
        return ['profile 和 venues 顶层必须是映射']
    direction = profile.get('direction', {})
    if not isinstance(direction, dict):
        errors.append('direction 必须是映射')
        direction = {}
    for key in ('name', 'description'):
        if key in direction and not isinstance(direction[key], str):
            errors.append(f'direction.{key} 必须是字符串')
    def strings(value, label, required=False):
        if not isinstance(value, list) or (required and not value) or any(not isinstance(x, str) or not x.strip() for x in value):
            errors.append(f'{label} 必须是{"非空" if required else ""}字符串列表')
    keyword_fields = [(label, value) for label, value in (
        ('keywords', profile.get('keywords')),
        ('direction.keywords', profile.get('direction.keywords')),
        ('direction.keywords', direction.get('keywords')),
    ) if value is not None and value != []]
    if not keyword_fields:
        errors.append('direction.keywords 或 keywords 必须是非空字符串列表')
    else:
        for label, value in keyword_fields:
            strings(value, label, True)
        if any(value != keyword_fields[0][1] for _, value in keyword_fields[1:]):
            errors.append('多个关键词配置不一致，请只保留一份或使其一致')
    strings(profile.get('arxiv_categories'), 'arxiv_categories', True)
    strings(profile.get('exclude', []), 'exclude')
    for label, years in (('conference_years', profile.get('conference_years', [])), ('venues.years', venues.get('years', []))):
        if not isinstance(years, list) or any(type(y) is not int or y < 1900 or y > 9999 for y in years):
            errors.append(f'{label} 必须是年份整数列表')
    for key in ('window_days', 'max_papers'):
        if key in profile and (type(profile[key]) is not int or profile[key] <= 0):
            errors.append(f'{key} 必须是正整数')
    def number(value, label, low, high=None):
        if type(value) not in (int, float) or not math.isfinite(value) or value < low or (high is not None and value > high):
            errors.append(f'{label} 必须是 {low} 到 {high if high is not None else "无上限"} 的数字')
    if 'min_relevance' in profile:
        number(profile['min_relevance'], 'min_relevance', 0, 10)
    if 'data_dir' in profile and (not isinstance(profile['data_dir'], str) or not profile['data_dir'].strip()):
        errors.append('data_dir 必须是非空字符串')
    for key in ('default_score', 'preprint_score'):
        if key in venues:
            number(venues[key], key, 0)
    entries = venues.get('venues')
    if not isinstance(entries, list) or not entries:
        errors.append('venues 必须是非空场所列表')
    else:
        for index, entry in enumerate(entries):
            label = f'venues[{index}]'
            if isinstance(entry, str):
                if not entry.strip():
                    errors.append(f'{label} 必须是非空场所名称')
                continue
            if not isinstance(entry, dict):
                errors.append(f'{label} 必须是场所名称或映射')
                continue
            if not isinstance(entry.get('name'), str) or not entry['name'].strip():
                errors.append(f'{label}.name 必须是非空字符串')
            strings(entry.get('aliases', []), f'{label}.aliases')
            if 'score' in entry:
                number(entry['score'], f'{label}.score', 0)
            if 'note' in entry and not isinstance(entry['note'], str):
                errors.append(f'{label}.note 必须是字符串')
    return errors


def load_config(profile_path: str | Path, venues_path: str | Path) -> tuple[dict, dict]:
    profile, venues = load_yaml(profile_path), load_yaml(venues_path)
    errors = validate_config(profile, venues)
    if errors:
        raise ConfigError('配置无效：\n- ' + '\n- '.join(errors))
    direction = profile.setdefault('direction', {})
    keywords = profile.get('keywords') or profile.get('direction.keywords') or direction['keywords']
    direction['keywords'] = keywords
    venues['venues'] = [{'name': entry} if isinstance(entry, str) else entry for entry in venues['venues']]
    return profile, venues
