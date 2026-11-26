"""AIHawk's folder: the settings and everything it remembers about one person's search."""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

CONFIG_FILE = "aihawk.yaml"
KEY_FILE = "key"
# A key kept on disk is sealed with Windows' own protection, which only this Windows user on this computer can open.
KEY_SEALED = "dpapi:"
DEFAULT_BASE_URL = "https://api.orcarouter.ai/v1"
# Any OpenAI-compatible gateway may serve instead: `base_url` in aihawk.yaml, or AIHAWK_BASE_URL.
BASE_URL_ENV = "AIHAWK_BASE_URL"
# The key for the gateway named above, in the environment, whatever its provider.
API_KEY_ENV = "AIHAWK_API_KEY"
# The variable that holds the model key, unless the settings name another. The browser is given no variable that holds a key.
KEY_ENV = "ORCAROUTER_API_KEY"
# Where a person gets a key. A referral link, as the README says: AIHawk gets a share of what is spent through it.
KEY_URL = "https://www.orcarouter.ai/ref/ref_accd17afb78fc95ac4d7"
# Fills the forms and reads their screenshots. Chosen over z-ai/glm-5.3-flash, which was slower and dearer on the same forms.
DEFAULT_MODEL = "deepseek/deepseek-v4.1-flash"
# For the plan and the other short calls of a search. deepseek-v4.1-flash did not keep the plan's nested schema, and
# z-ai/glm-5.3-flash was too slow for a search.
DEFAULT_QUICK_MODEL = "google/gemini-3.5-flash"
# Answers, for each posting, whether it is in line with the candidate, and how sure it is: that is the posting's score.
DEFAULT_DECISION_MODEL = "typesafe/jev-1.14"
# The Hugging Face dataset the pipeline publishes to. It was GitHub's releases until 2026-11-08: see pipeline/publish.py.
# Set in the environment, an application is a trial: the form is filled and the send control named, and nothing is sent, nor
# counted. Read in one place, so what is sent and what is counted can never disagree about it.
DRY_RUN_ENV = "AIHAWK_DRY_RUN"
DEFAULT_DATABASE = "https://huggingface.co/datasets/feder-cr/jobs/resolve/main/library/manifest.json"
# The manifest of the layout before 2026-11-09, which a settings file may still name: it means the default now.
OLD_DATABASE = "https://huggingface.co/datasets/feder-cr/jobs/resolve/main/manifest.json"
# A search looks back this many days by default: the owner's decision of 2026-11-09. The database holds 60.
DEFAULT_MAX_AGE_DAYS = 15


class ConfigError(Exception):
    """aihawk.yaml or a file it needs says something that cannot be used."""


# The gateways AIHawk knows by name, and the variable that holds the key of each in the environment.
PROVIDERS = (
    ("orcarouter", "OrcaRouter", "ORCAROUTER_API_KEY", "orcarouter.ai"),
    ("deepseek", "DeepSeek", "DEEPSEEK_API_KEY", "deepseek.com"),
    ("openai.com", "OpenAI", "OPENAI_API_KEY", "platform.openai.com"),
    ("openrouter", "OpenRouter", "OPENROUTER_API_KEY", "openrouter.ai"),
    ("anthropic", "Anthropic", "ANTHROPIC_API_KEY", "console.anthropic.com"),
)


def _provider(base_url: str) -> tuple[str, str, str]:
    """The gateway's name, the variable its key is kept in, and where a key is made; the address's own words for one not known."""
    host = (urlsplit(base_url).hostname or "").lower()
    for fragment, name, variable, where in PROVIDERS:
        if fragment in host:
            return name, variable, where
    return host or "the AI service", "AIHAWK_API_KEY", host


def provider_name(base_url: str) -> str:
    return _provider(base_url)[0]


def provider_key_env(base_url: str) -> str:
    return _provider(base_url)[1]


def provider_site(base_url: str) -> str:
    return _provider(base_url)[2]


def key_variables() -> set[str]:
    """Every variable a model key can be kept in: none of them is given to the browser."""
    return {API_KEY_ENV, *(variable for _, _, variable, _ in PROVIDERS)}


# The gateway this process talks to, set when the client is made: the words on the screen follow the settings, not the default.
CURRENT = {"name": "OrcaRouter", "site": "orcarouter.ai"}


def use_gateway(base_url: str) -> None:
    name, _, site = _provider(base_url)
    CURRENT["name"], CURRENT["site"] = name, site


@dataclass(frozen=True)
class Form:
    """One wording a title may use for a node. The weight says how surely that exact wording means the job."""

    text: str
    weight: float = 1.0


@dataclass(frozen=True)
class Node:
    """One facet of the job, and the wordings that stand for it. The weight says how telling the facet is; 0 only conditions arcs."""

    id: str
    weight: float
    forms: tuple[Form, ...]


@dataclass(frozen=True)
class Arc:
    """When or unless a node (or a phrase) is in the title, scale the nodes named in `then`; "*" scales the whole title."""

    then: tuple[tuple[str, float], ...]
    when: str = ""
    unless: str = ""


@dataclass(frozen=True)
class Plan:
    nodes: tuple[Node, ...] = ()
    arcs: tuple[Arc, ...] = ()


@dataclass(frozen=True)
class Filters:
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    locations: tuple[str, ...] = ()
    cities: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    # The years the person has, as words the scoring reads: "fresher", "0-2 years", "5+ years". Empty counts as no limit.
    experience: str = ""
    plan: Plan = field(default_factory=Plan)

    def titles(self) -> str:
        """What a person reads as the job titles searched: the first wording of each of the plan's first nodes, not their ids."""
        nodes = [node for node in self.plan.nodes if node.forms][:3]
        return ", ".join(node.forms[0].text for node in nodes) or ", ".join(self.include[:3])


@dataclass(frozen=True)
class Config:
    workspace: Path
    goals: str = ""
    base_url: str = DEFAULT_BASE_URL
    database: str = DEFAULT_DATABASE
    max_age_days: int = DEFAULT_MAX_AGE_DAYS
    model: str = DEFAULT_MODEL
    quick_model: str = DEFAULT_QUICK_MODEL
    decision_model: str = DEFAULT_DECISION_MODEL
    # off: the CV goes as it is. auto: rewritten per posting when that helps, which the model judges. always: every time.
    tailor_resume: str = "auto"
    # What one tool answer may add to the conversation: the smaller, the fewer tokens an application spends.
    tool_text_chars: int = 8000
    filters: Filters = field(default_factory=Filters)

    @property
    def cv(self) -> Path:
        return self.workspace / "cv.md"

    @property
    def answers_file(self) -> Path:
        """Where the answers were kept before they moved into the store: read once, to import them."""
        return self.workspace / "answers.yaml"

    @property
    def store_file(self) -> Path:
        return self.data_dir / "jobs.db"

    @property
    def data_dir(self) -> Path:
        return self.workspace / "data"

    @property
    def key_file(self) -> Path:
        return self.workspace / KEY_FILE

    @property
    def browser_profile(self) -> Path:
        """The one Firefox profile for everything the browser does: the person's sign-ins and the applications."""
        return self.workspace / "browser-profile"

    def api_key(self) -> str:
        """The model's key: from the environment, or the one the person typed in."""
        for name in (API_KEY_ENV, provider_key_env(self.base_url)):
            from_environment = os.environ.get(name, "").strip()
            if from_environment:
                return from_environment
        return unseal_key(self.key_file.read_text(encoding="utf-8-sig").strip()) if self.key_file.is_file() else ""

    def key_env(self) -> str:
        """The variable this gateway's key is kept in: AIHAWK_API_KEY, or the provider's own name for it."""
        return API_KEY_ENV if os.environ.get(API_KEY_ENV, "").strip() else provider_key_env(self.base_url)

    def key_from_environment(self) -> bool:
        return bool(os.environ.get(API_KEY_ENV, "").strip() or os.environ.get(provider_key_env(self.base_url), "").strip())

    def save_key(self, key: str) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.key_file.touch(mode=0o600, exist_ok=True)
        self.key_file.write_text(seal_key(key.strip()), encoding="utf-8")


def seal_key(key: str) -> str:
    """The key as it is written to disk: sealed with Windows' DPAPI, or as it stands where that is not available."""
    try:
        import win32crypt

        sealed = win32crypt.CryptProtectData(key.encode("utf-8"), "Rainy model key", None, None, None, 0)
        if isinstance(sealed, tuple):  # older pywin32 returned (blob, description)
            sealed = sealed[0]
        return KEY_SEALED + base64.b64encode(sealed).decode("ascii")
    except Exception:  # noqa: BLE001  no Windows, no pywin32: the key is kept as it was
        return key


def unseal_key(stored: str) -> str:
    """The key from its file: one sealed with DPAPI opened, a plain one (an older file) kept as it is."""
    if not stored.startswith(KEY_SEALED):
        return stored
    try:
        import win32crypt

        opened = win32crypt.CryptUnprotectData(base64.b64decode(stored[len(KEY_SEALED):]), None, None, None, 0)
        if isinstance(opened, tuple):  # older pywin32 returned (description, blob)
            opened = opened[1]
        return opened.decode("utf-8")
    except Exception:  # noqa: BLE001  another Windows user's file, or a damaged one: no key
        return ""


def dry_run() -> bool:
    """Whether this run is a trial that sends nothing."""
    return bool(os.environ.get(DRY_RUN_ENV, "").strip())


def default_home() -> Path:
    """The folder this account keeps: AIHAWK_HOME when it is set, which is how two people share one computer."""
    override = os.environ.get("AIHAWK_HOME", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".aihawk"


def _words(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{name} must be a list of words")
    return tuple(value)


def _items(value: object, name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ConfigError(f"{name} must be a list")
    return value


def _chars(value: object) -> int:
    """How many characters a tool answer may add: at least a page's worth, whatever the settings say."""
    try:
        return max(1000, int(value))
    except (TypeError, ValueError):
        return 8000


def _days(value: object, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"{name} must be a whole number of days, not {value!r}") from None


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a word or phrase, not {value!r}")
    return value


def _weight(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ConfigError(f"{name} must be a number, 0 or more, not {value!r}")
    return float(value)


def _node(entry: object) -> Node:
    if not isinstance(entry, dict):
        raise ConfigError(f"a node needs id, weight and forms: {entry!r}")
    forms = tuple(_form(item) for item in _items(entry.get("forms"), "a node's forms"))
    return Node(_text(entry.get("id"), "a node's id"), _weight(entry.get("weight", 1.0), "a node's weight"), forms)


def _form(entry: object) -> Form:
    if not isinstance(entry, dict):
        raise ConfigError(f"a form needs text and weight: {entry!r}")
    return Form(_text(entry.get("text"), "a form's text"), _weight(entry.get("weight", 1.0), "a form's weight"))


def _arc(entry: object) -> Arc:
    if not isinstance(entry, dict):
        raise ConfigError(f"an arc needs when or unless, and then: {entry!r}")
    when = str(entry.get("when") or "")
    unless = str(entry.get("unless") or "")
    # Exactly one condition: an arc with both, or with neither, would not say what it means.
    if bool(when) == bool(unless):
        raise ConfigError(f"an arc needs exactly one of when or unless: {entry!r}")
    then = tuple(_target(item) for item in _items(entry.get("then"), "an arc's then"))
    return Arc(then, when, unless)


def _target(entry: object) -> tuple[str, float]:
    if not isinstance(entry, dict):
        raise ConfigError(f"a target needs target and multiplier: {entry!r}")
    multiplier = entry.get("multiplier")
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        raise ConfigError(f"a multiplier must be a number, not {multiplier!r}")
    return _text(entry.get("target"), "a target"), float(multiplier)


def _plan(value: object) -> Plan:
    if value is None:
        return Plan()
    if not isinstance(value, dict):
        raise ConfigError("filters.plan must be a mapping with nodes and arcs")
    nodes = tuple(_node(entry) for entry in _items(value.get("nodes"), "filters.plan.nodes"))
    arcs = tuple(_arc(entry) for entry in _items(value.get("arcs"), "filters.plan.arcs"))
    ids = {node.id for node in nodes}
    for arc in arcs:
        for target, _ in arc.then:
            if target != "*" and target not in ids:
                raise ConfigError(f"an arc targets {target!r}, which is no node in filters.plan")
    return Plan(nodes, arcs)


def load_config(workspace: Path) -> Config:
    """The settings in the folder. Nothing there yet is a new search, not an error."""
    path = workspace / CONFIG_FILE
    if not path.is_file():
        return Config(workspace=workspace)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
    except yaml.YAMLError as error:
        raise ConfigError(f"{path} is not valid YAML: {error}") from error
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a mapping of settings")

    filters = raw.get("filters") or {}
    if not isinstance(filters, dict):
        raise ConfigError("filters must be a mapping with include, exclude, locations, cities, countries and plan")
    return Config(
        workspace=workspace,
        goals=str(raw.get("goals") or ""),
        base_url=str(raw.get("base_url") or os.environ.get(BASE_URL_ENV, "") or DEFAULT_BASE_URL).rstrip("/"),
        database=DEFAULT_DATABASE if str(raw.get("database") or OLD_DATABASE) == OLD_DATABASE else str(raw["database"]),
        max_age_days=_days(raw.get("max_age_days", DEFAULT_MAX_AGE_DAYS), "max_age_days"),
        model=str(raw.get("model") or DEFAULT_MODEL),
        quick_model=str(raw.get("quick_model") or DEFAULT_QUICK_MODEL),
        decision_model=str(raw.get("decision_model") or DEFAULT_DECISION_MODEL),
        tailor_resume=str(raw.get("tailor_resume") or "auto").strip().lower(),
        tool_text_chars=_chars(raw.get("tool_text_chars", 8000)),
        filters=Filters(
            _words(filters.get("include"), "filters.include"),
            _words(filters.get("exclude"), "filters.exclude"),
            _words(filters.get("locations"), "filters.locations"),
            _words(filters.get("cities"), "filters.cities"),
            _words(filters.get("countries"), "filters.countries"),
            str(filters.get("experience") or "").strip(),
            _plan(filters.get("plan")),
        ),
    )
