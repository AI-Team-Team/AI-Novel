from .common import *


class ContinuousLoopResumeTests(unittest.TestCase):
    class _MemoryStub:
        def __init__(self, commits_by_chapter=None):
            self.commits_by_chapter = commits_by_chapter or {}
            self.purged = []

        def get_chapter_commits(self, chapter_num: int, source: str = None, limit: int = 50):
            return list(self.commits_by_chapter.get(chapter_num, []))[:limit]

        def purge_incomplete_chapter_commits(self, chapter_num: int, source: str = None):
            self.purged.append((chapter_num, source))
            self.commits_by_chapter[chapter_num] = [
                row for row in self.commits_by_chapter.get(chapter_num, [])
                if str(row[4]) not in {"STARTED", "FAILED"}
            ]
            return 0

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="auto_resume_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _build_workflow_stub(self, memory):
        wf = WorkflowManager.__new__(WorkflowManager)
        wf.logger = logging.getLogger("auto-resume-test")
        wf.memory = memory
        wf.world_dir = os.path.join(self.tmpdir, "frame", "world")
        wf.plot_dir = os.path.join(self.tmpdir, "frame", "plot")
        wf.facts_dir = os.path.join(self.tmpdir, "process", "facts")
        wf.guides_dir = os.path.join(self.tmpdir, "frame", "chapter_guides")
        wf.archives_dir = os.path.join(self.tmpdir, "frame", "archives")
        wf.chapters_dir = os.path.join(self.tmpdir, "main_text", "chapters")
        wf.discussions_dir = os.path.join(self.tmpdir, "process", "discussions")
        wf.reviews_dir = os.path.join(self.tmpdir, "process", "reviews")
        wf.revisions_dir = os.path.join(self.tmpdir, "process", "revisions")
        wf.critiques_dir = os.path.join(self.tmpdir, "process", "critiques")
        wf.discussion_log_dir = os.path.join(self.tmpdir, "Discussion_Log")
        os.makedirs(wf.world_dir, exist_ok=True)
        os.makedirs(wf.plot_dir, exist_ok=True)
        os.makedirs(wf.facts_dir, exist_ok=True)
        os.makedirs(wf.guides_dir, exist_ok=True)
        os.makedirs(wf.archives_dir, exist_ok=True)
        os.makedirs(wf.chapters_dir, exist_ok=True)
        os.makedirs(wf.discussions_dir, exist_ok=True)
        os.makedirs(wf.reviews_dir, exist_ok=True)
        os.makedirs(wf.revisions_dir, exist_ok=True)
        os.makedirs(wf.critiques_dir, exist_ok=True)
        os.makedirs(wf.discussion_log_dir, exist_ok=True)
        wf._get_system_prompts = lambda: {"critic": "c", "writer": "w"}
        return wf

    def test_auto_loop_skips_completed_chapters_and_continues(self):
        payload = {
            "new_characters": [],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [{"event_name": "DoneEvent"}],
            "details": [],
        }
        commit_row = ("c1", 1, "scan_chapter", json.dumps(payload), "COMPLETED", 0, "", 0, None, "2026-01-01")
        memory = self._MemoryStub(commits_by_chapter={1: [commit_row], 2: []})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.chapters_dir, "chapter_001.md"), "w", encoding="utf-8") as f:
            f.write("chapter 1 text")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts_summary.md"), "w", encoding="utf-8") as f:
            f.write("Summary for Chapter 1: done")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts.json"), "w", encoding="utf-8") as f:
            json.dump(payload, f)

        calls = []
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: (
            calls.append(("plan", chapter_num, previous_summary)) or f"guide-{chapter_num}"
        )
        wf.write_chapter = lambda chapter_num, guide_content: (
            calls.append(("write", chapter_num, guide_content)) or f"chapter-{chapter_num}"
        )
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (
            calls.append(("review", chapter_num)) or (chapter_text, "ok")
        )
        wf.scan_chapter = lambda chapter_num: (
            calls.append(("scan", chapter_num)) or f"Summary for Chapter {chapter_num}: done"
        )

        with mock.patch("workflow.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 2)

        planned = [x for x in calls if x[0] == "plan"]
        written = [x for x in calls if x[0] == "write"]
        scanned = [x for x in calls if x[0] == "scan"]
        self.assertEqual(len(planned), 1)
        self.assertEqual(planned[0][1], 2)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0][1], 2)
        self.assertEqual(len(scanned), 1)
        self.assertEqual(scanned[0][1], 2)

    def test_auto_loop_discards_incomplete_chapter_and_regenerates(self):
        memory = self._MemoryStub(commits_by_chapter={1: [("c1", 1, "scan_chapter", "{}", "STARTED", 0, "", 0, None, "2026-01-01")]})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.guides_dir, "chapter_001_guide.md"), "w", encoding="utf-8") as f:
            f.write("Existing guide")
        with open(os.path.join(wf.chapters_dir, "chapter_001.md"), "w", encoding="utf-8") as f:
            f.write("Existing chapter draft")

        calls = []
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: (
            calls.append(("plan", chapter_num, previous_summary)) or "new-guide"
        )
        wf.write_chapter = lambda chapter_num, guide_content: (
            calls.append(("write", chapter_num, guide_content)) or "new-chapter"
        )
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (
            calls.append(("review", chapter_num, guide, chapter_text)) or (chapter_text, "ok")
        )
        wf.scan_chapter = lambda chapter_num: (
            calls.append(("scan", chapter_num)) or f"Summary for Chapter {chapter_num}: done"
        )

        with mock.patch("workflow.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 1)

        self.assertTrue(any(x[0] == "plan" for x in calls))
        self.assertTrue(any(x[0] == "write" for x in calls))
        self.assertEqual(memory.purged, [(1, "scan_chapter")])
        self.assertFalse(os.path.exists(os.path.join(wf.guides_dir, "chapter_001_guide.md")))
        self.assertFalse(os.path.exists(os.path.join(wf.chapters_dir, "chapter_001.md")))

    def test_auto_loop_discards_chapter_when_facts_json_corrupted(self):
        payload = {
            "new_characters": [],
            "updated_characters": [],
            "new_rules": [],
            "relationships": [],
            "events": [{"event_name": "DoneEvent"}],
            "details": [],
        }
        commit_row = ("c1", 1, "scan_chapter", json.dumps(payload), "COMPLETED", 0, "", 0, None, "2026-01-01")
        memory = self._MemoryStub(commits_by_chapter={1: [commit_row]})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.guides_dir, "chapter_001_guide.md"), "w", encoding="utf-8") as f:
            f.write("Existing guide")
        with open(os.path.join(wf.chapters_dir, "chapter_001.md"), "w", encoding="utf-8") as f:
            f.write("Existing chapter text")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts_summary.md"), "w", encoding="utf-8") as f:
            f.write("summary")
        with open(os.path.join(wf.facts_dir, "chapter_001_facts.json"), "w", encoding="utf-8") as f:
            f.write("{ invalid json")

        calls = []
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: (
            calls.append(("plan", chapter_num, previous_summary)) or "new-guide"
        )
        wf.write_chapter = lambda chapter_num, guide_content: (
            calls.append(("write", chapter_num, guide_content)) or "new-chapter"
        )
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (
            calls.append(("review", chapter_num, guide, chapter_text)) or (chapter_text, "ok")
        )
        wf.scan_chapter = lambda chapter_num: (
            calls.append(("scan", chapter_num)) or f"Summary for Chapter {chapter_num}: done"
        )

        with mock.patch("workflow.time.sleep", return_value=None):
            wf.run_continuous_loop(1, 1)

        self.assertTrue(any(x[0] == "plan" for x in calls))
        self.assertTrue(any(x[0] == "write" for x in calls))
        self.assertEqual(memory.purged, [(1, "scan_chapter")])

    def test_auto_loop_blocks_on_corrupted_world_bible(self):
        memory = self._MemoryStub(commits_by_chapter={})
        wf = self._build_workflow_stub(memory)
        with open(os.path.join(wf.world_dir, "world_bible.md"), "w", encoding="utf-8") as f:
            f.write("")
        wf.generate_chapter_guide = lambda chapter_num, previous_summary=None: "x"
        wf.write_chapter = lambda chapter_num, guide_content: "y"
        wf._review_and_revise_chapter = lambda chapter_num, guide, chapter_text, prompts: (chapter_text, "ok")
        wf.scan_chapter = lambda chapter_num: "z"

        with mock.patch("workflow.time.sleep", return_value=None):
            with self.assertRaises(RuntimeError):
                wf.run_continuous_loop(1, 1)
