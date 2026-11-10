import re
from dataclasses import dataclass

import requests
from langchain_core.tools import tool

from inex.agent.chat import build_llm
from inex.errors import InexError

PRICES_URL = "https://openrouter.ai/api/v1/models"
PRICES_TIMEOUT_SECONDS = 30
CHARS_PER_ESTIMATED_TOKEN = 4
CALLS_PER_TURN = 3
OUTPUT_TOKENS_PER_CALL = 300
KEY_NAMES = {
    "openrouter": "OPENROUTER_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}
PROBE_MESSAGE = "Reply with OK."
KEY_WORDS = re.compile(r"api[ _-]?key|credential", re.IGNORECASE)
PING_DESCRIPTION = "Returns pong. Used only to check that the model accepts tools."


@dataclass(frozen=True)
class Price:
    prompt: float
    completion: float


@dataclass(frozen=True)
class Preflight:
    llm: object | None
    price: Price | None
    problem: str | None


@dataclass(frozen=True)
class Estimate:
    input_tokens: int
    output_tokens: int
    cost: float | None


def parse_prices(payload) -> dict[str, Price]:
    prices = {}
    for entry in payload.get("data") or []:
        pricing = entry.get("pricing") or {}
        try:
            prices[entry["id"]] = Price(float(pricing["prompt"]), float(pricing["completion"]))
        except (KeyError, TypeError, ValueError):
            continue
    return prices


def fetch_prices(get=requests.get) -> dict[str, Price]:
    response = get(PRICES_URL, timeout=PRICES_TIMEOUT_SECONDS)
    response.raise_for_status()
    return parse_prices(response.json())


def _provider(model):
    return model.partition(":")[0]


def is_openrouter(model) -> bool:
    return _provider(model) == "openrouter"


def is_ollama(model) -> bool:
    return _provider(model) == "ollama"


def model_price(model, prices) -> Price | None:
    if not is_openrouter(model) or prices is None:
        return None
    return prices.get(model.partition(":")[2])


def cost_of(input_tokens, output_tokens, price) -> float | None:
    if price is None:
        return None
    return input_tokens * price.prompt + output_tokens * price.completion


def estimate(prompt_chars, scenarios, runs, price) -> Estimate:
    calls = CALLS_PER_TURN * runs * sum(len(s.turns) for s in scenarios)
    input_tokens = calls * (prompt_chars // CHARS_PER_ESTIMATED_TOKEN)
    output_tokens = calls * OUTPUT_TOKENS_PER_CALL
    return Estimate(input_tokens, output_tokens, cost_of(input_tokens, output_tokens, price))


def reproject(spent, done, total) -> float:
    if not done:
        return 0.0
    return spent / done * total


def _status(error):
    status = getattr(error, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(error, "code", None)
    if isinstance(code, int) and not isinstance(code, bool):
        return code
    status = getattr(getattr(error, "response", None), "status_code", None)
    return status if isinstance(status, int) else None


def _key_hint(provider):
    return f"set {KEY_NAMES.get(provider, provider.upper() + '_API_KEY')} in .env"


def _build_problem(model, error):
    if KEY_WORDS.search(str(error)):
        return f"the provider needs a key; {_key_hint(_provider(model))}"
    return f"the model could not be built: {type(error).__name__}: {error}"


def _probe_problem(model, error):
    provider, _, name = model.partition(":")
    status = _status(error)
    if status in (401, 403):
        return f"the provider rejected the key; {_key_hint(provider)}"
    if status == 404:
        if provider == "ollama":
            return f"the model was not found; run: ollama pull {name}"
        return "the model was not found; check the model id"
    if provider == "ollama" and (
        isinstance(error, ConnectionError) or "connect" in type(error).__name__.lower()
    ):
        return "Ollama is not reachable; start Ollama, or set OLLAMA_BASE"
    return f"preflight call failed: {type(error).__name__}: {error}"


def _refused(problem):
    return Preflight(None, None, problem)


def preflight(settings, prices, build=build_llm) -> Preflight:
    model = settings.orchestrator
    price = None
    if is_openrouter(model):
        if prices is None:
            return _refused("prices unavailable; the budget cannot be enforced")
        price = model_price(model, prices)
        if price is None:
            name = model.partition(":")[2]
            return _refused(f"{name} is not in the OpenRouter price list; check the model id")
    try:
        llm = build(settings)
    except InexError as error:
        return _refused(error.message)
    except Exception as error:
        return _refused(_build_problem(model, error))

    @tool("ping", description=PING_DESCRIPTION)
    def ping() -> str:
        return "pong"

    try:
        llm.bind_tools([ping]).invoke(PROBE_MESSAGE)
    except Exception as error:
        return _refused(_probe_problem(model, error))
    return Preflight(llm, price, None)
