from dataclasses import dataclass


@dataclass(frozen=True)
class DatabaseResult:
    path: str


class MaintenanceService:
    def __init__(self, store):
        self.store = store

    def init(self) -> DatabaseResult:
        return DatabaseResult(str(self.store.path))

    def reset(self) -> DatabaseResult:
        self.store.reset()
        return DatabaseResult(str(self.store.path))

    def clear(self) -> DatabaseResult:
        self.store.clear()
        return DatabaseResult(str(self.store.path))
