"""Runtime configuration, read from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path = Path(os.environ.get("GEO_DATA_DIR", "./data"))
    # Hard cap on the size of an uploaded file (bytes).
    max_upload_bytes: int = int(os.environ.get("GEO_MAX_UPLOAD_BYTES", 50 * 1024 * 1024))
    # Hard cap on the total uncompressed size of the shapefile parts in a zip
    # (protects against zip bombs).
    max_uncompressed_bytes: int = int(
        os.environ.get("GEO_MAX_UNCOMPRESSED_BYTES", 250 * 1024 * 1024)
    )
    # Hard cap on the number of features accepted from one file.
    max_features: int = int(os.environ.get("GEO_MAX_FEATURES", 200_000))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "geo.sqlite3"
