import json
import os
from pathlib import Path

import requests

from bbva_gastos.categories import criteria_for, match_rule
from bbva_gastos.client import DATA, SECRETS

API_URL = "https://openrouter.ai/api/v1/systemone"
MODEL = "typesafe/jev-1.14"
CACHE_PATH = DATA / "clasificacion_cache.json"
BATCH = 12


class JevError(RuntimeError):
    pass


def load_api_key(key_file=None):
    if key_file:
        return Path(key_file).read_text(encoding="utf-8").strip()
    env = os.environ.get("OPENROUTER_API_KEY", "").strip() or os.environ.get("TYPESAFE_API_KEY", "").strip()
    if env:
        return env
    for name in ("openrouter.key", "typesafe.key"):
        file = SECRETS / name
        if file.exists():
            return file.read_text(encoding="utf-8").strip()
    raise SystemExit(
        "Falta la clave de OpenRouter. Guárdala en secrets/openrouter.key "
        "o pásala con --key-file. No la pegues en el chat."
    )


def cache_key(movement):
    concepto = " ".join(movement.get("concepto", "").casefold().split())
    detalle = " ".join(movement.get("detalle", "").casefold().split())
    return f"{movement['direccion']}|{concepto}|{detalle}"


def _label(category, confidence, origin):
    return {
        "categoria": category,
        "confianza": round(float(confidence), 4),
        "origen": origin,
        "revisar": origin == "jev" and float(confidence) < 0.45,
    }


def _load_cache():
    if not CACHE_PATH.exists():
        return {}
    return json.loads(CACHE_PATH.read_text(encoding="utf-8"))


def _save_cache(cache):
    DATA.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def _ask(api_key, movements):
    state = {
        "movimientos": [
            {
                "concepto": movement["concepto"],
                "detalle": movement.get("detalle") or "",
                "importe": movement["importe"],
                "direccion": movement["direccion"],
            }
            for movement in movements
        ]
    }
    questions = {}
    for index, movement in enumerate(movements):
        questions[f"m{index}"] = {
            "type": "choice",
            "instructions": (
                f"Elige la única categoría que mejor describe `movimientos[{index}]`. "
                "Usa el concepto, el detalle y el signo del importe."
            ),
            "criteria": criteria_for(movement["direccion"]),
        }
    response = requests.post(
        API_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": MODEL, "state": state, "questions": questions},
        timeout=90,
    )
    if response.status_code >= 400:
        raise JevError(f"Jev respondió {response.status_code}: {response.text[:500]}")
    answers = response.json().get("answers") or {}
    labels = []
    for index, movement in enumerate(movements):
        answer = answers.get(f"m{index}") or {}
        allowed = criteria_for(movement["direccion"])
        choice = answer.get("choice")
        probabilities = answer.get("probabilities") or {}
        confidence = answer.get("confidence")
        if confidence is None and probabilities:
            confidence = max(probabilities.values())
        if choice not in allowed:
            choice = "otros" if movement["direccion"] == "gasto" else "otros_ingresos"
            confidence = 0
        labels.append(_label(choice, confidence or 0, "jev"))
    usage = (response.json().get("usage") or {}).get("input_tokens", 0)
    return labels, usage


def classify(movements, api_key, *, refresh=False):
    cache = _load_cache()
    output = [None] * len(movements)
    pending = []
    for index, movement in enumerate(movements):
        rule = match_rule(movement)
        if rule:
            output[index] = {**movement, **_label(rule, 1, "regla")}
            continue
        key = cache_key(movement)
        cached = None if refresh else cache.get(key)
        if cached:
            output[index] = {**movement, **cached}
            continue
        pending.append(index)

    tokens = 0
    calls = 0
    seen = {}
    for index in pending:
        key = cache_key(movements[index])
        seen.setdefault(key, []).append(index)
    unique_keys = list(seen)
    for start in range(0, len(unique_keys), BATCH):
        chunk_keys = unique_keys[start : start + BATCH]
        sample = [movements[seen[key][0]] for key in chunk_keys]
        labels, used = _ask(api_key, sample)
        tokens += used
        calls += 1
        for key, label in zip(chunk_keys, labels):
            cache[key] = label
            for index in seen[key]:
                output[index] = {**movements[index], **label}
        _save_cache(cache)
        print(f"Jev: {min(start + BATCH, len(unique_keys))}/{len(unique_keys)} conceptos")
    if any(row is None for row in output):
        raise JevError("Quedó algún movimiento sin categoría")
    return output, {"llamadas": calls, "tokens_entrada": tokens, "conceptos": len(unique_keys)}
