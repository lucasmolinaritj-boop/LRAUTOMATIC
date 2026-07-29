from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lrautomatic.models import ImportJob, ImportJobRequest


PLUGIN = ROOT / "lightroom_plugin" / "LRAutomatic.lrplugin"
ENTRY = (PLUGIN / "JobRunner.lua").read_text(encoding="utf-8")
CORE = (PLUGIN / "JobRunnerCore.lua").read_text(encoding="utf-8")


class RunnerArchitectureTests(unittest.TestCase):
    def test_entrypoint_uses_only_canonical_core(self) -> None:
        self.assertIn("require 'JobRunnerCore'", ENTRY)
        self.assertNotRegex(ENTRY, r"require 'JobRunner(?:4[8-9]|5[0-7])'")
        self.assertFalse(list(PLUGIN.glob("JobRunner[4-5][0-9].lua")))

    def test_runtime_has_no_textual_patch_chain(self) -> None:
        self.assertNotRegex(CORE, r"(?m)^[^-\n]*\breplaceOnce\s*\(")
        self.assertNotRegex(CORE, r"(?m)^[^-\n]*\bloadstring\s*\(")
        self.assertNotRegex(CORE, r"(?m)^[^-\n]*\bxpcall\s*\(")

    def test_catalog_write_gate_is_not_protected_by_pcall(self) -> None:
        self.assertIn("catalog:withWriteAccessDo", CORE)
        self.assertIn("imported=catalog:addPhoto(path)", CORE)
        self.assertNotIn("pcall(function() return catalog:addPhoto", CORE)
        self.assertNotIn("callbackOk,callbackError=pcall(fn", CORE)
        self.assertNotIn("gateOk,statusOrError=pcall", CORE)

    def test_count_is_persisted_before_import_loop(self) -> None:
        process_source = CORE.index("local function processSource")
        scan_complete = CORE.index("progress.scan_completed=true", process_source)
        counted_stage = CORE.index("job.current_stage='counted'", scan_complete)
        persisted = CORE.index("safeWriteJob(jobPath,job)", counted_stage)
        import_loop = CORE.index("for _,path in ipairs(files) do", persisted)
        self.assertLess(scan_complete, counted_stage)
        self.assertLess(counted_stage, persisted)
        self.assertLess(persisted, import_loop)

    def test_inventory_paths_are_external_to_job_json(self) -> None:
        self.assertIn("local function inventoryPath", CORE)
        self.assertIn("progress.inventory_manifest=manifestPath", CORE)
        self.assertIn("progress.discovered_files=nil", CORE)
        self.assertEqual(CORE.count("progress.discovered_files"), 1)

    def test_import_retries_are_classified_and_bounded(self) -> None:
        self.assertIn("local function classifyImportError", CORE)
        self.assertIn("IMPORT_DEFAULT_DELAYS = { 2, 5, 15 }", CORE)
        self.assertIn("IMPORT_CLOUD_DELAYS = { 5, 15, 30, 60 }", CORE)
        self.assertNotIn("MAX_ATTEMPTS", CORE)
        self.assertNotIn("RETRY_DELAY_SECONDS", CORE)

    def test_progress_writes_are_batched(self) -> None:
        self.assertIn("progressSinceWrite>=10", CORE)
        self.assertIn("now-lastProgressWrite>=2", CORE)

    def test_new_job_schema_accepts_partial_sources_and_legacy_bad_files(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            request = ImportJobRequest(sources=[{"path": folder}])
            job = ImportJob(request=request)
            payload = job.model_dump(mode="json")
            payload["progress"] = [
                {
                    "path": folder,
                    "status": "partial",
                    "discovered": 3,
                    "imported": 2,
                    "failed": 1,
                    "inventory_manifest": "manifest.json",
                    "next_index": 4,
                }
            ]
            payload["bad_files"] = [
                {"path": "broken.cr3", "error": "legacy record", "at": job.created_at}
            ]
            restored = ImportJob.model_validate(payload)
            self.assertEqual(restored.progress[0].status, "partial")
            self.assertEqual(restored.progress[0].next_index, 4)
            self.assertEqual(restored.bad_files[0].error, "legacy record")


if __name__ == "__main__":
    unittest.main()
