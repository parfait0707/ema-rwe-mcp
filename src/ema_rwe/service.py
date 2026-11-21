"""The service facade: one object holding the settings, storage and clients; its methods live in the
catalogue, screening, protocols, extraction and evidence modules."""

import asyncio

from .archive import ProtocolArchive
from .catalogue import (  # noqa: F401  (imported from here by the CLI)
    CatalogueMixin,
    source_type_from_filename,
)
from .comparison import Comparisons
from .config import Settings
from .evidence import EvidenceMixin
from .exploration import Explorer
from .extraction import ExtractionMixin
from .http import EMAClient
from .protocols import ProtocolsMixin
from .screening import ScreeningMixin
from .storage import Repository


class Service(CatalogueMixin, ScreeningMixin, ProtocolsMixin, ExtractionMixin, EvidenceMixin):
    def __init__(self, settings: Settings | None = None, client=None, repo=None):
        self.settings = settings or Settings()
        self.repo = repo or Repository(self.settings.db_path)
        self.client = client or EMAClient(self.settings)
        self.archive = ProtocolArchive(
            self.settings.protocol_dir or self.settings.db_path.parent / "protocols"
        )
        self.explorer = Explorer(self.settings, self.archive, self.repo)
        self.comparisons = Comparisons(self)
        # Running provider extractions of this process. A restart loses the tasks, not their work: finished
        # batches are in the database, and the next analyze_protocol asks only for the rest (spec v1.14).
        self._extractions: dict[tuple[str, str], tuple[asyncio.Task, dict]] = {}  # (study, fingerprint)

    @property
    def unmatched_log_path(self):
        return self.settings.unmatched_log_path or self.settings.db_path.parent / "terminology_unmatched.json"

    async def close(self):
        await self.client.close()

    async def get_protocol_comparison(
        self, comparison_id: str, selected_study_ids: list[str] | None = None, detail: str = "compact"
    ):
        return await asyncio.to_thread(self.comparisons.collect, comparison_id, selected_study_ids, detail)
