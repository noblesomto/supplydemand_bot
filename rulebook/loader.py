import yaml

from rulebook.models import Rulebook


def load_rulebook(path: str, *, variant: str | None = None, profile: str | None = None) -> Rulebook:
    with open(path) as f:
        raw = yaml.safe_load(f)

    base = dict(raw.get("baseline", {}))
    if profile:
        base["profile"] = profile

    if variant:
        variants = raw.get("variants", {})
        if variant not in variants:
            raise KeyError(f"unknown variant {variant!r}; available: {sorted(variants)}")
        base = _deep_merge(base, variants[variant])

    return Rulebook.model_validate(base)


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result
