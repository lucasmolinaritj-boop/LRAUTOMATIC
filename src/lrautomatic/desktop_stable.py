from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from .automation_control import read_control
from .desktop import STATUS_PT
from .desktop_selective_cleanup import SelectiveCleanupDesktopApp


class StableDesktopApp(SelectiveCleanupDesktopApp):
    """Monitor responsivo com leitura em background e atualização incremental."""

    MONITOR_INTERVAL_MS = 750
    CONTROL_REFRESH_SECONDS = 3.0
    STALE_WARNING_SECONDS = 120

    def __init__(self, config_path: str = "config.json") -> None:
        # Precisam existir antes do __init__ pai, pois ele chama métodos sobrescritos.
        self._monitor_refresh_inflight = False
        self._monitor_generation = 0
        self._last_jobs_snapshot: list[Any] = []
        self._last_control_snapshot: dict[str, Any] | None = None
        self._render_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._row_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._monitor_last_success = 0.0
        self._last_control_read = 0.0
        super().__init__(config_path)
        self.title("LRAutomatic V4.9")
        self._configure_progress_column()

    def _configure_progress_column(self) -> None:
        if not hasattr(self, "jobs_tree"):
            return
        columns = ("created", "status", "progress", "folders", "imported", "summary")
        if tuple(self.jobs_tree.cget("columns")) != columns:
            self.jobs_tree.configure(columns=columns)
        definitions = (
            ("created", "Criada em", 125),
            ("status", "Status", 115),
            ("progress", "Etapa e progresso", 285),
            ("folders", "Pastas", 60),
            ("imported", "Fotos", 70),
            ("summary", "Resultado", 260),
        )
        for key, title, width in definitions:
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(
                key,
                width=width,
                anchor="w",
                stretch=key in {"progress", "summary"},
            )

    @staticmethod
    def _parse_timestamp(value: str | None) -> float | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return None

    @staticmethod
    def _status_done(value: Any) -> bool:
        return str(value or "").lower() in {"completed", "complete", "done", "success", "succeeded"}

    @staticmethod
    def _status_failed(value: Any) -> bool:
        return str(value or "").lower() in {"failed", "error"}

    def _job_stage_and_percent(self, job: Any) -> tuple[str, int]:
        status = str(job.status)
        if status == "queued":
            return "Aguardando início", 0
        if status in {"completed", "partial"}:
            return "Concluída", 100
        if status == "failed":
            return "Falhou", 100
        if status == "cancelled":
            return "Cancelada", 100

        discovered = max(0, int(job.total_discovered or 0))
        imported = max(0, int(job.total_imported or 0))
        skipped = max(0, int(job.total_skipped or 0))
        failed = max(0, int(job.total_failed or 0))
        processed = imported + skipped + failed

        # Pesos dinâmicos: só reserva espaço para etapas realmente solicitadas.
        requested_steps: list[tuple[str, str, int, int]] = []
        if str(job.preset_status) != "not_requested":
            requested_steps.append(("preset", "Aplicando preset", int(job.preset_applied_count or 0), max(imported, 1)))
        if str(job.standard_previews_status) != "not_requested":
            requested_steps.append((
                "standard",
                "Criando visualizações padrão",
                int(job.standard_previews_created or 0),
                max(imported, 1),
            ))
        if str(job.smart_previews_status) != "not_requested":
            requested_steps.append((
                "smart",
                "Criando Smart Previews",
                int(job.smart_previews_created or 0) + int(job.smart_previews_existed or 0),
                max(imported, 1),
            ))

        post_weight = 10 * len(requested_steps)
        import_weight = max(55, 90 - post_weight)
        discovery_weight = 10

        if discovered <= 0:
            return "Descobrindo arquivos", 3

        if processed < discovered:
            ratio = min(1.0, processed / max(discovered, 1))
            percent = discovery_weight + int(import_weight * ratio)
            current_name = ""
            if job.current_source:
                current_name = str(job.current_source).rstrip("/\\").split("\\")[-1].split("/")[-1]
            detail = f"Importando {processed}/{discovered}"
            if current_name:
                detail += f" • {current_name}"
            return detail, min(percent, 94)

        base = discovery_weight + import_weight
        for index, (key, label, done, target) in enumerate(requested_steps):
            if key == "preset":
                state = job.preset_status
            elif key == "standard":
                state = job.standard_previews_status
            else:
                state = job.smart_previews_status

            if self._status_failed(state):
                return f"{label} • com falhas", min(99, base + index * 10)
            if self._status_done(state):
                base += 10
                continue

            ratio = min(1.0, done / max(target, 1))
            percent = min(99, base + int(10 * ratio))
            counter = f" {done}/{target}" if done else ""
            return f"{label}{counter}", percent

        # O Lightroom pode já ter terminado as etapas, mas ainda não ter gravado o
        # status terminal no JSON. Nunca apresenta 100% antes da confirmação final.
        return "Finalizando e confirmando resultado", 99

    def _progress_label(self, job: Any) -> str:
        stage, percent = self._job_stage_and_percent(job)
        if str(job.status) != "running":
            return f"{stage} • {percent}%"

        updated = self._parse_timestamp(job.updated_at)
        if updated is not None:
            age = max(0, int(time.time() - updated))
            if age >= self.STALE_WARNING_SECONDS:
                minutes, seconds = divmod(age, 60)
                return f"⚠ {stage} • {percent}% • sem atualização há {minutes}m{seconds:02d}s"
        return f"{stage} • {percent}%"

    @staticmethod
    def _job_fingerprint(job: Any) -> tuple[Any, ...]:
        return (
            str(job.status),
            job.updated_at,
            job.total_discovered,
            job.total_imported,
            job.total_skipped,
            job.total_failed,
            job.error,
            job.current_source,
            job.preset_status,
            job.preset_applied_count,
            job.standard_previews_status,
            job.standard_previews_created,
            job.standard_previews_failed,
            job.smart_previews_status,
            job.smart_previews_created,
            job.smart_previews_existed,
            job.smart_previews_failed,
            len(job.events),
            tuple(
                (
                    source.path,
                    str(source.status),
                    source.discovered,
                    source.imported,
                    source.skipped,
                    source.failed,
                    source.error,
                )
                for source in job.progress
            ),
        )

    def _row_values(self, job: Any) -> tuple[Any, ...]:
        return (
            self._dt(job.created_at),
            STATUS_PT.get(str(job.status), str(job.status)),
            self._progress_label(job),
            len(job.progress),
            job.total_imported,
            self._result(job),
        )

    def _refresh_jobs(self, silent: bool = False) -> None:
        if not hasattr(self, "jobs_tree") or self._monitor_refresh_inflight:
            return
        self._monitor_refresh_inflight = True
        self._monitor_generation += 1
        generation = self._monitor_generation

        def worker() -> None:
            try:
                jobs = self.store.list()
                now = time.monotonic()
                if self._last_control_snapshot is None or now - self._last_control_read >= self.CONTROL_REFRESH_SECONDS:
                    control = read_control(self.settings)
                    control_read = now
                else:
                    control = self._last_control_snapshot
                    control_read = self._last_control_read
            except Exception as exc:
                self.after(0, lambda error=exc, gen=generation: self._monitor_failed(gen, error))
                return
            self.after(
                0,
                lambda values=jobs, state=control, read_at=control_read, gen=generation, quiet=silent: self._apply_monitor_snapshot(
                    gen, values, state, read_at, quiet
                ),
            )

        threading.Thread(target=worker, daemon=True, name="MonitorRefresh").start()

    def _apply_monitor_snapshot(
        self,
        generation: int,
        jobs: list[Any],
        control: dict[str, Any],
        control_read_at: float,
        silent: bool,
    ) -> None:
        if generation != self._monitor_generation:
            return
        self._monitor_refresh_inflight = False
        self._last_jobs_snapshot = jobs
        self._last_control_snapshot = control
        self._last_control_read = control_read_at
        self._monitor_last_success = time.monotonic()
        self.jobs_by_id = {job.job_id: job for job in jobs}
        self._configure_progress_column()

        selected = self.selected_job_id
        visible = [job for job in jobs if self._matches(job)]
        desired_ids = [job.job_id for job in visible]
        desired_set = set(desired_ids)
        current_ids = set(self.jobs_tree.get_children())

        for item_id in current_ids - desired_set:
            self.jobs_tree.delete(item_id)
            self._row_fingerprints.pop(item_id, None)
            self._render_fingerprints.pop(item_id, None)

        for index, job in enumerate(visible):
            item_id = job.job_id
            values = self._row_values(job)
            row_fingerprint = (values, str(job.status), index % 2)
            tags = (str(job.status), "even" if index % 2 == 0 else "odd")
            if not self.jobs_tree.exists(item_id):
                self.jobs_tree.insert("", index, iid=item_id, values=values, tags=tags)
            else:
                if self._row_fingerprints.get(item_id) != row_fingerprint:
                    self.jobs_tree.item(item_id, values=values, tags=tags)
                if self.jobs_tree.index(item_id) != index:
                    self.jobs_tree.move(item_id, "", index)
            self._row_fingerprints[item_id] = row_fingerprint

        self.jobs_tree.tag_configure("even", background="#FFF")
        self.jobs_tree.tag_configure("odd", background="#F8FAFC")
        self.jobs_tree.tag_configure("failed", foreground=self.DANGER)
        self.jobs_tree.tag_configure("running", foreground=self.ACCENT)
        self.jobs_tree.tag_configure("completed", foreground=self.SUCCESS)
        self.jobs_tree.tag_configure("partial", foreground=self.WARNING)

        self.metric_vars["active"].set(sum(str(job.status) in {"queued", "running"} for job in jobs))
        self.metric_vars["done"].set(sum(str(job.status) in {"completed", "partial"} for job in jobs))
        self.metric_vars["photos"].set(sum(job.total_imported for job in jobs))
        self.metric_vars["failed"].set(sum(str(job.status) == "failed" for job in jobs))

        target = selected if selected in desired_set else (desired_ids[0] if desired_ids else None)
        if target and self.jobs_tree.exists(target):
            if self.jobs_tree.selection() != (target,):
                self.jobs_tree.selection_set(target)
            self.selected_job_id = target
            job = self.jobs_by_id[target]
            fingerprint = self._job_fingerprint(job)
            if self._render_fingerprints.get(target) != fingerprint:
                self._render(job)
                self._render_fingerprints[target] = fingerprint

        self._apply_control_snapshot(control, jobs)
        self.monitor_state.set("Atualizado em tempo real")
        if not silent:
            self.status.set(f"Histórico atualizado: {len(jobs)} tarefa(s).")

    def _apply_control_snapshot(self, control: dict[str, Any], jobs: list[Any]) -> None:
        paused = bool(control.get("paused"))
        force_pending = bool(control.get("force_next_requested"))
        running = any(str(job.status) == "running" for job in jobs)
        if paused:
            self.automation_state.set(
                "AUTOMAÇÃO PAUSADA" + (" — tarefa atual terminará normalmente" if running else "")
            )
            self.pause_button.configure(text="RETOMAR AUTOMAÇÃO", style="Primary.TButton")
        else:
            self.automation_state.set("AUTOMAÇÃO ATIVA")
            self.pause_button.configure(text="PAUSAR AUTOMAÇÃO", style="Danger.TButton")
        if force_pending:
            self.force_state.set(
                "Próximo job solicitado; aguardando a tarefa atual terminar."
                if running
                else "Próximo job solicitado ao agente."
            )
        else:
            self.force_state.set(str(control.get("message") or ""))

    def _refresh_control_state(self) -> None:
        if self._last_control_snapshot is not None:
            self._apply_control_snapshot(self._last_control_snapshot, self._last_jobs_snapshot)
            return
        try:
            control = read_control(self.settings)
        except Exception:
            return
        self._last_control_snapshot = control
        self._last_control_read = time.monotonic()
        self._apply_control_snapshot(control, self._last_jobs_snapshot)

    def _monitor_failed(self, generation: int, exc: Exception) -> None:
        if generation != self._monitor_generation:
            return
        self._monitor_refresh_inflight = False
        label = type(exc).__name__
        self.monitor_state.set(f"Drive ocupado ({label}); mantendo último estado")

    def _auto_refresh(self) -> None:
        self._refresh_jobs(True)
        self.after(self.MONITOR_INTERVAL_MS, self._auto_refresh)


def main() -> None:
    StableDesktopApp().mainloop()


if __name__ == "__main__":
    main()
