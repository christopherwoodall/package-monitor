"""Local filesystem notifier — writes a markdown report and records to DB."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scm.models import Alert, Verdict
from scm.notifiers import Notifier

log = logging.getLogger(__name__)

# local.py is at src/scm/notifiers/local.py
# parents[0]=notifiers/  parents[1]=scm/  parents[2]=src/  parents[3]=project root
REPORTS_ROOT: Path = Path(__file__).resolve().parents[3] / "reports"


class LocalNotifier(Notifier):
    name = "local"

    def notify(self, verdict: Verdict, conn: sqlite3.Connection) -> Alert:
        """Write a markdown report to reports/{ecosystem}/{package}/{version}.md."""
        release = verdict.release
        now = datetime.now(timezone.utc)

        report_dir = REPORTS_ROOT / release.ecosystem / release.package
        report_path = report_dir / f"{release.version}.md"

        try:
            report_dir.mkdir(parents=True, exist_ok=True)

            content = (
                f"# Supply Chain Analysis: {release.package} {release.version}\n\n"
                f"**Ecosystem:** {release.ecosystem}  **Rank:** #{release.rank}\n"
                f"**Verdict:** {verdict.result.upper()}  **Confidence:** {verdict.confidence}\n"
                f"**Analyzed:** {verdict.analyzed_at.isoformat()}\n"
                f"**Old SHA-256:** `{verdict.old_artifact.sha256}`\n"
                f"**New SHA-256:** `{verdict.new_artifact.sha256}`\n\n"
                f"## Summary\n\n{verdict.summary}\n\n"
                f"## Full Analysis\n\n{verdict.analysis}\n"
            )
            report_path.write_text(content, encoding="utf-8")
            log.info("report written  %s", report_path)
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=True,
                detail=str(report_path),
            )
        except Exception as exc:
            log.exception(
                "LocalNotifier failed for %s@%s: %s",
                release.package,
                release.version,
                exc,
            )
            return Alert(
                verdict=verdict,
                notifier=self.name,
                sent_at=now,
                success=False,
                detail=str(exc),
            )
