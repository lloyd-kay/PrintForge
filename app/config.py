import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv('PRINTFORGE_DATA_DIR', './data')))
    mode: str = field(default_factory=lambda: os.getenv('PRINTFORGE_MODE', 'demo'))
    token: str = field(default_factory=lambda: os.getenv('PRINTFORGE_TOKEN', 'printforge-demo'))
    spoolman_url: str = field(default_factory=lambda: os.getenv('SPOOLMAN_URL', ''))
    poll_seconds: float = field(default_factory=lambda: float(os.getenv('PRINTFORGE_POLL_SECONDS', '5')))
    start_scheduler: bool = True

    def validate(self):
        if self.mode not in ('demo', 'live'):
            raise ValueError('PRINTFORGE_MODE must be demo or live')
        if self.mode == 'live' and (len(self.token) < 24 or self.token == 'printforge-demo'):
            raise ValueError('Live mode requires PRINTFORGE_TOKEN of at least 24 characters')
        if not self.token or self.poll_seconds < 1:
            raise ValueError('A token and poll interval of at least 1 second are required')
        self.data_dir = Path(self.data_dir)
