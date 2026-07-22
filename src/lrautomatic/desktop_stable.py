from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .automation_control import read_control
from .desktop import STATUS_PT
from .desktop_selective_cleanup import SelectiveCleanupDesktopApp


class StableDesktopApp(SelectiveCleanupDesktopApp):
    """Monitor legível, responsivo e econômico em acessos ao disco."""

    # O usuário não precisa de telemetria instantânea por foto. Dois segundos deixam
    # a interface estável e reduzem bastante enumerações e stats na pasta de jobs.
    MONITOR_INTERVAL_MS = 2000
    CONTROL_REFRESH_SECONDS = 6.0
    STALE_WARNING_SECONDS = 120

    def __init__(self, config_path: str = "config.json") -> None:
        # Precisam existir antes do __init__ pai, pois ele chama métodos sobrescritos.
        self._monitor_refresh_inflight = False
        self._monitor_generation = 0
        self._last_jobs_snapshot: list[Any] = []
        self._last_control_snapshot: dict[str, Any] | None = None
        self._render_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._row_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._display_percent: dict[str, int] = {}
        self._monitor_last_success = 0.0
        self._last_control_read = 0.0
        super().__init__(config_path)
        self.title("LRAutomatic V5.1")
        self._configure_progress_column()

    def _configure_progress_column(self) -> None:
        if not hasattr(self, "jobs_tree"):
            return
        columns = ("created", "status", "progress", "counts", "summary")
        if tuple(self.jobs_tree.cget("columns")) != columns:
            self.jobs_tree.configure(columns=columns)
        definitions = (
            ("created", "Criada em", 120),
            ("status", "Situação", 120),
            ("progress", "O que está acontecendo", 315),
            ("counts", "Quantidade", 245),
            ("summary", "Resultado", 235),
        )
        for key, title, width in definitions:
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(
                key,
                width=width,
                anchor="w",
                stretch=key in {"progress", "counts", "summary"},
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
        return str(value or "").lower() in {"failed", "error", "failed_after_retries"}

    @staticmethod
    def _requested(value: Any) -> bool:
        return str(value or "").lower() not in {"", "none", "not_requested"}

    @staticmethod
    def _source_name(job: Any) -> str:
        value = str(getattr(job, "current_source", "") or "").rstrip("/\\")
        return Path(value).name if value else ""

    def _raw_stage_and_percent(self, job: Any) -> tuple[str, int]:
        """Calcula progresso em fases fixas; a porcentagem exibida é estabilizada depois."""
        status = str(job.status)
        if status == "queued":
            return "Aguardando o Lightroom", 0
        if status in {"completed", "partial"}:
            return "Concluída", 100
        if status == "failed":
            return "Falhou", 100
        if status == "cancelled":
            return "Cancelada", 100
        if status == "interrupted":
            return "Interrompida", 100

        discovered = max(0, int(job.total_discovered or 0))
        imported = max(0, int(job.total_imported or 0))
        skipped = max(0, int(job.total_skipped or 0))
        failed = max(0, int(job.total_failed or 0))
        processed = imported + skipped + failed
        source_name = self._source_name(job)

        # Fases fixas: localizar 0–5, importar 5–75, preset 75–83,
        # preview padrão 83–91, Smart Preview 91–98 e finalização 99.
        if discovered <= 0:
            return "Procurando fotos nas pastas", 2

        if processed < discovered:
            ratio = min(1.0, processed / max(discovered, 1))
            label = f"Importando fotos — {processed} de {discovered}"
            if source_name:
                label += f" • {source_name}"
            return label, 5 + int(70 * ratio)

        imported_target = max(imported, 1)
        preset_status = getattr(job, "preset_status", "not_requested")
        if self._requested(preset_status) and not self._status_done(preset_status):
            done = max(0, int(getattr(job, "preset_applied_count", 0) or 0))
            if self._status_failed(preset_status):
                return "Preset concluído com falhas", 82
            return f"Aplicando preset — {min(done, imported_target)} de {imported_target}", 75 + int(8 * min(1.0, done / imported_target))

        standard_status = getattr(job, "standard_previews_status", "not_requested")
        if self._requested(standard_status) and not self._status_done(standard_status):
            done = max(0, int(getattr(job, "standard_previews_created", 0) or 0))
            if self._status_failed(standard_status):
                return "Visualizações padrão concluídas com falhas", 90
            return f"Criando visualizações padrão — {min(done, imported_target)} de {imported_target}", 83 + int(8 * min(1.0, done / imported_target))

        smart_status = getattr(job, "smart_previews_status", "not_requested")
        if self._requested(smart_status) and not self._status_done(smart_status):
            done = max(0, int(getattr(job, "smart_previews_created", 0) or 0))
            done += max(0, int(getattr(job, "smart_previews_existed", 0) or 0))
            if self._status_failed(smart_status):
                return "Smart Previews concluídos com falhas", 97
            return f"Criando Smart Previews — {min(done, imported_target)} de {imported_target}", 91 + int(7 * min(1.0, done / imported_target))

        return "Finalizando e salvando o resultado", 99

    def _job_stage_and_percent(self, job: Any) -> tuple[str, int]:
        stage, raw_percent = self._raw_stage_and_percent(job)
        job_id = str(job.job_id)
        status = str(job.status)
        if status == "queued":
            stable = 0
        elif status in {"completed", "partial", "failed", "cancelled", "interrupted"}:
            stable = 100
        else:
            # Nunca deixa a porcentagem voltar, mesmo que o JSON revele uma etapa nova.
            stable = max(self._display_percent.get(job_id, 0), min(raw_percent, 99))
        self._display_percent[job_id] = stable
        return stage, stable

    def _progress_label(self, job: Any) -> str:
        stage, percent = self._job_stage_and_percent(job)
        if str(job.status) != "running":
            return f"{stage} • {percent}%"

        updated = self._parse_timestamp(job.updated_at)
        if updated is not None:
            age = max(0, int(time.time() - updated))
            if age >= self.STALE_WARNING_SECONDS:
                minutes = max(1, age // 60)
                return f"⚠ {stage} • {percent}% • sem avanço há {minutes} min"
        return f"{stage} • {percent}%"

    @staticmethod
    def _counts_label(job: Any) -> str:
        discovered = max(0, int(job.total_discovered or 0))
        imported = max(0, int(job.total_imported or 0))
        skipped = max(0, int(job.total_skipped or 0))
        failed = max(0, int(job.total_failed or 0))
        processed = imported + skipped + failed
        if discovered <= 0:
            return "Total ainda sendo contado"
        parts = [f"{min(processed, discovered)}/{discovered} processadas", f"{imported} novas"]
        if skipped:
            parts.append(f"{skipped} já estavam no catálogo")
        if failed:
            parts.append(f"{failed} falharam")
        return " • ".join(parts)

    @staticmethod
    def _job_fingerprint(job: Any) -> tuple[Any, ...]:
        # updated_at/heartbeat não entram: não redesenha detalhes só porque o relógio mudou.
        return (
            str(job.status),
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
            self._counts_label(job),
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
            self._display_percent.pop(item_id, None)

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
        self.monitor_state.set("Atualização leve • a cada 2 s")
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
