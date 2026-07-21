from __future__ import annotations

import threading
import time
from typing import Any

from .automation_control import read_control
from .desktop import STATUS_PT
from .desktop_enhanced import EnhancedDesktopApp


class StableDesktopApp(EnhancedDesktopApp):
    """Monitor responsivo: leitura em background e atualização incremental da tabela."""

    MONITOR_INTERVAL_MS = 1000

    def __init__(self, config_path: str = "config.json") -> None:
        # Estes atributos precisam existir antes do __init__ pai, pois ele chama os
        # métodos de atualização que esta classe sobrescreve.
        self._monitor_refresh_inflight = False
        self._monitor_generation = 0
        self._last_jobs_snapshot: list[Any] = []
        self._last_control_snapshot: dict[str, Any] | None = None
        self._render_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._row_fingerprints: dict[str, tuple[Any, ...]] = {}
        self._monitor_last_success = 0.0
        super().__init__(config_path)
        self.title("LRAutomatic V4.8")

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
                control = read_control(self.settings)
            except Exception as exc:
                self.after(0, lambda error=exc, gen=generation: self._monitor_failed(gen, error))
                return
            self.after(
                0,
                lambda values=jobs, state=control, gen=generation, quiet=silent: self._apply_monitor_snapshot(
                    gen, values, state, quiet
                ),
            )

        threading.Thread(target=worker, daemon=True, name="MonitorRefresh").start()

    def _apply_monitor_snapshot(
        self,
        generation: int,
        jobs: list[Any],
        control: dict[str, Any],
        silent: bool,
    ) -> None:
        if generation != self._monitor_generation:
            return
        self._monitor_refresh_inflight = False
        self._last_jobs_snapshot = jobs
        self._last_control_snapshot = control
        self._monitor_last_success = time.monotonic()
        self.jobs_by_id = {job.job_id: job for job in jobs}

        selected = self.selected_job_id
        visible = [job for job in jobs if self._matches(job)]
        desired_ids = [job.job_id for job in visible]
        desired_set = set(desired_ids)
        current_ids = set(self.jobs_tree.get_children())

        # Remove somente linhas que realmente deixaram de existir. Não limpa a tabela.
        for item_id in current_ids - desired_set:
            self.jobs_tree.delete(item_id)
            self._row_fingerprints.pop(item_id, None)
            self._render_fingerprints.pop(item_id, None)

        # Insere ou altera apenas o que mudou e reposiciona sem piscar.
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
        # Reutiliza o snapshot já lido pelo monitor para não enumerar os jobs duas vezes.
        if self._last_control_snapshot is not None:
            self._apply_control_snapshot(self._last_control_snapshot, self._last_jobs_snapshot)
            return
        try:
            control = read_control(self.settings)
        except Exception:
            return
        self._last_control_snapshot = control
        self._apply_control_snapshot(control, self._last_jobs_snapshot)

    def _monitor_failed(self, generation: int, exc: Exception) -> None:
        if generation != self._monitor_generation:
            return
        self._monitor_refresh_inflight = False
        # Falhas transitórias do Drive não apagam linhas nem transformam jobs em falha.
        # O último estado válido permanece visível até a próxima leitura bem-sucedida.
        label = type(exc).__name__
        self.monitor_state.set(f"Drive ocupado ({label}); mantendo último estado")

    def _auto_refresh(self) -> None:
        self._refresh_jobs(True)
        self.after(self.MONITOR_INTERVAL_MS, self._auto_refresh)


def main() -> None:
    StableDesktopApp().mainloop()


if __name__ == "__main__":
    main()
