from dataclasses import dataclass

from inex.clients.npm import NpmClient
from inex.config import Settings
from inex.services.classification import ClassificationService
from inex.services.collection import CollectionService
from inex.services.imports import ImportService
from inex.services.linkage import LinkageService
from inex.services.maintenance import MaintenanceService
from inex.services.profiles import Profile, load_profile
from inex.services.queries import QueryService
from inex.store.database import Store


@dataclass
class App:
    settings: Settings
    profile: Profile
    collection: CollectionService
    classification: ClassificationService
    imports: ImportService
    linkage: LinkageService
    queries: QueryService
    maintenance: MaintenanceService

    def close(self) -> None:
        self.queries.store.close()


def build_app(settings: Settings) -> App:
    profile = load_profile(settings.profile)
    store = Store(settings.database)
    collection = CollectionService(store, settings.github_token)
    classification = ClassificationService(
        store,
        profile,
        settings.classifier,
        settings.temperature,
        workers=settings.classify_workers,
        retries=settings.classify_retries,
        ollama_base=settings.ollama_base,
        judge=settings.judge,
        thinking=settings.thinking,
        judge_thinking=settings.judge_thinking,
    )
    return App(
        settings=settings,
        profile=profile,
        collection=collection,
        classification=classification,
        imports=ImportService(store, collection, classification),
        linkage=LinkageService(store, NpmClient(settings.npm_cache_dir)),
        queries=QueryService(store, settings.exports_dir),
        maintenance=MaintenanceService(store),
    )
