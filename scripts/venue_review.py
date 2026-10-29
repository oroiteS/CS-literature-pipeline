"""会议白名单指纹及受其影响的台账复核策略。

调用方可在一批去重开始时计算一次 venue_fingerprint()，然后把结果传给
venue_recheck_required(row, fingerprint)。旧 CSV 没有指纹时保守地重评。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

try:
    from .config import parse_yaml
except ImportError:
    from config import parse_yaml

VENUES = Path(__file__).resolve().parent.parent / "references" / "venues.yaml"


def _canonical(value):
    """消除 YAML 格式、映射顺序与名单顺序的差异。"""
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        items = [_canonical(v) for v in value]
        return sorted(items, key=lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False, default=str))
    return value


def venue_fingerprint(path: Path | None = None) -> str:
    """返回内容指纹；配置解析器不支持或解析失败时回退原始字节哈希。"""
    path = Path(path) if path is not None else VENUES
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return "missing"
    try:
        parsed = parse_yaml(raw.decode("utf-8"))
        content = json.dumps(_canonical(parsed), sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), default=str).encode("utf-8")
        prefix = "yaml:"
    except (ImportError, ValueError, UnicodeError, TypeError):
        content, prefix = raw, "raw:"
    except Exception:  # 无效 YAML 也必须可靠地产生指纹
        content, prefix = raw, "raw:"
    return prefix + hashlib.sha256(content).hexdigest()


def venue_recheck_required(row: dict, fingerprint: str | None = None) -> bool:
    """仅对白名单淘汰，旧行、缺失指纹或白名单变化时立即复核。"""
    if row.get("reason") != "venue_not_allowed":
        return False
    stored = (row.get("venue_fingerprint") or "").strip()
    current = fingerprint if fingerprint is not None else venue_fingerprint()
    return not stored or stored != current
