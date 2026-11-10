import json
import os
import time
from unittest import mock

from helpers import BrainTest

from jarvis import fleet, phone, sync, twoway, work


class FleetTest(BrainTest):
    """T121: the laptop takes the phone by itself when the PC goes quiet, hands it back by a clear rule, never
    answers alongside it, and the two machines share durable workers without ever running one twice."""

    def setUp(self):
        super().setUp()
        self.init()
        os.environ.pop("JARVIS_ROLE", None)  # the shared note decides
        self.env = mock.patch.dict(os.environ, {"JARVIS_MACHINE": "marley"})
        self.env.start()
        self.patches = [mock.patch.object(fleet, "_can_answer", return_value=True),
                        mock.patch.object(fleet, "dashboard_up", return_value=True),
                        mock.patch.object(fleet, "_notify")]
        self.notify = [p.start() for p in self.patches][2]
        self.now = time.time()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        os.environ["JARVIS_ROLE"] = "primary"
        super().tearDown()

    def as_machine(self, name):
        os.environ["JARVIS_MACHINE"] = name
        # each machine has its own local state next to its role file
        os.environ["LOCALAPPDATA"] = str(self.local_dir / ("" if name == "marley" else name))

    def note(self, **data):
        (self.sync_dir / phone.NOTE).write_text(json.dumps({"machines": ["marley", "Marleyslaptop"], **data}),
                                                encoding="utf-8")

    def beat_of(self, name, ago=0, answering=True, app=True, up_for=3600, role="primary", workers=0):
        ts = self.now - ago
        hb = {"machine": name, "ts": ts, "role": role, "app": app, "answering": answering, "can_answer": True,
              "workers": workers, "up_since": ts - up_for, "app_since": ts - up_for if app else None,
              "answering_ts": ts if answering else ts - up_for}
        fleet.fleet_dir().mkdir(parents=True, exist_ok=True)
        fleet.heartbeat_path(name).write_text(json.dumps(hb), encoding="utf-8")

    def step_failover(self, bridge=None):
        starts = []
        with mock.patch.object(sync, "pull", return_value={"note": "pulled"}) as pull:
            did = fleet.failover(self.conn, bridge, self.now, start_app=lambda: starts.append(1))
        return did, pull, starts

    # ------------------------------------------------------------ heartbeats

    def test_heartbeat_says_whether_this_machine_answers(self):
        bridge = mock.Mock(running=True, error=None)
        hb = fleet.beat(bridge, self.now)
        self.assertTrue(hb["answering"] and hb["app"])
        self.assertEqual(fleet.heartbeat_of("marley", self.now)["machine"], "marley")
        # The routines tick doesn't overwrite the app's fresher heartbeat a moment later.
        self.assertIsNone(fleet.beat(None, self.now + 10))
        hb2 = fleet.beat(None, self.now + 120)
        self.assertFalse(hb2["answering"])
        self.assertEqual(hb2["answering_ts"], self.now)  # when it last answered is kept
        self.assertEqual(hb2["up_since"], hb["up_since"])

    def test_the_primary_names_itself_when_no_one_is_named(self):
        self.assertIn("named", fleet.failover(self.conn, None, self.now))
        self.assertEqual(phone.holder(), "marley")

    # ------------------------------------------------------------ taking over

    def test_laptop_takes_the_phone_when_the_pc_goes_quiet(self):
        self.note(holder="marley")
        self.beat_of("marley", ago=400)
        self.as_machine("Marleyslaptop")
        with mock.patch.object(fleet, "dashboard_up", return_value=False):
            did, pull, starts = self.step_failover()
        self.assertIn("taken your phone", did)
        pull.assert_called_once()
        self.assertEqual(starts, [1])  # the app was shut, so it was opened for Telegram
        st = phone.state()
        self.assertEqual((st["holder"], st["auto"], st["home"]), ("Marleyslaptop", True, "marley"))
        self.notify.assert_called_once()

    def test_no_takeover_while_the_pc_still_answers_or_never_reported(self):
        self.note(holder="marley")
        self.as_machine("Marleyslaptop")
        self.assertIsNone(self.step_failover()[0])  # no heartbeat ever: older JARVIS there, don't guess
        self.beat_of("marley", ago=60)
        self.assertIsNone(self.step_failover()[0])
        self.assertEqual(phone.holder(), "marley")

    def test_pc_up_with_its_app_shut_counts_as_not_answering(self):
        self.note(holder="marley")
        self.beat_of("marley", ago=10, answering=False, app=False, up_for=600)
        self.as_machine("Marleyslaptop")
        self.assertIn("taken your phone", self.step_failover()[0])

    def test_failover_can_be_switched_off(self):
        (self.home / "config.json").write_text(json.dumps({"failover": {"auto": False}}), encoding="utf-8")
        self.note(holder="marley")
        self.beat_of("marley", ago=4000)
        self.as_machine("Marleyslaptop")
        self.assertIsNone(self.step_failover()[0])

    def test_only_one_machine_takes_over(self):
        self.note(holder="marley", machines=["marley", "Marleyslaptop", "Atlas"])
        self.beat_of("marley", ago=400)
        self.beat_of("Atlas", ago=5, role="standby")
        self.as_machine("Marleyslaptop")
        self.assertIsNone(self.step_failover()[0])  # Atlas comes first by name
        self.as_machine("Atlas")
        self.assertIn("taken", self.step_failover()[0])

    # ------------------------------------------------------------ handing back

    def test_hands_back_once_the_pc_has_been_back_five_minutes(self):
        self.note(holder="Marleyslaptop", auto=True, home="marley")
        self.as_machine("Marleyslaptop")
        self.beat_of("marley", ago=5, answering=False, up_for=120, role="standby")
        self.assertIsNone(self.step_failover()[0])
        self.beat_of("marley", ago=5, answering=False, up_for=400, role="standby")
        self.assertIn("handed your phone back", self.step_failover()[0])
        self.assertEqual(phone.holder(), "marley")
        self.assertFalse(phone.state()["auto"])
        self.assertTrue((self.sync_dir / sync.BRAIN_ZIP).exists())  # its brain went first

    def test_an_owner_chosen_machine_is_never_handed_away(self):
        self.note(holder="Marleyslaptop", by="marley")  # the owner texted /laptop
        self.as_machine("Marleyslaptop")
        self.beat_of("marley", ago=5, up_for=4000, role="standby")
        self.assertIsNone(self.step_failover()[0])
        self.assertEqual(phone.holder(), "Marleyslaptop")

    def test_handback_can_wait_for_the_owner(self):
        (self.home / "config.json").write_text(json.dumps({"failover": {"handback": "ask"}}), encoding="utf-8")
        self.note(holder="Marleyslaptop", auto=True, home="marley")
        self.as_machine("Marleyslaptop")
        self.beat_of("marley", ago=5, up_for=4000, role="standby")
        self.assertIsNone(self.step_failover()[0])

    def test_steps_back_when_telegram_says_both_are_polling(self):
        self.note(holder="Marleyslaptop", auto=True, home="marley")
        self.as_machine("Marleyslaptop")
        bridge = mock.Mock(running=True, error="Conflict: terminated by other getUpdates request")
        self.assertIsNone(self.step_failover(bridge)[0])
        self.assertIsNone(self.step_failover(bridge)[0])
        self.assertIn("stepped back", self.step_failover(bridge)[0])
        self.assertEqual(phone.holder(), "marley")
        # and it doesn't grab the phone straight back while the PC's heartbeat catches up
        self.beat_of("marley", ago=400)
        self.assertIsNone(self.step_failover()[0])

    # ------------------------------------------------------------ the returning machine keeps its work

    def test_returning_machine_uses_its_own_last_push_as_baseline(self):
        self.note(holder="marley")
        sync.push(self.conn, force=True)
        self.assertTrue(fleet.keep_own_push())
        self.assertFalse(fleet.keep_own_push())  # same push: nothing to copy
        self.note(holder="Marleyslaptop", auto=True, home="marley")  # taken over while away
        self.assertTrue(fleet.baseline_from_own_push())
        base = json.loads(twoway._baseline_json().read_text(encoding="utf-8"))
        self.assertTrue(base["exact"])
        self.assertEqual(base["stamp"], sync.manifest()["stamp"])
        self.assertFalse(fleet.baseline_from_own_push())  # already the baseline

    # ------------------------------------------------------------ shared jobs

    def test_shared_job_goes_to_the_least_busy_machine_and_runs_once(self):
        self.note(holder="marley")
        self.beat_of("marley", workers=3)
        self.beat_of("Marleyslaptop", workers=0, role="standby")
        job = fleet.share("research the knee brace", task="T5", agent="product-research")
        self.assertEqual(fleet.dispatch(self.now), [f"{job['id']} → Marleyslaptop"])
        started = []
        start = lambda brief, **kw: started.append(brief) or {"id": 77}
        self.assertEqual(fleet.run_mine(self.now, start=start), [])  # the PC doesn't run the laptop's job
        self.as_machine("Marleyslaptop")
        self.assertIn("started W77", fleet.run_mine(self.now, start=start)[0])
        self.assertEqual(fleet.lock_of(job["id"])["machine"], "Marleyslaptop")
        with mock.patch.object(work, "get", return_value={"id": 77}), \
                mock.patch.object(work, "refresh", return_value={"id": 77, "status": "running"}):
            fleet.run_mine(self.now + 30, start=start)
        self.assertEqual(len(started), 1)
        self.assertEqual(fleet.get_job(job["id"])["status"], "running")
        with mock.patch.object(work, "get", return_value={"id": 77}), \
                mock.patch.object(work, "refresh", return_value={"id": 77, "status": "done"}):
            self.assertIn("done", fleet.run_mine(self.now + 60, start=start)[0])
        self.assertFalse(fleet._lock_path(job["id"]).exists())

    def test_job_moves_off_a_quiet_machine_and_its_old_copy_stops(self):
        self.note(holder="marley")
        self.beat_of("marley", workers=2)
        self.beat_of("Marleyslaptop", role="standby")
        job = fleet.share("write the copy", task="T6")
        fleet.dispatch(self.now)
        self.as_machine("Marleyslaptop")
        fleet.run_mine(self.now, start=lambda brief, **kw: {"id": 8})
        # The laptop goes quiet for 20 minutes; the PC, answering the phone, moves the job to itself.
        self.as_machine("marley")
        self.beat_of("Marleyslaptop", ago=1200, role="standby")
        later = self.now + 1200
        self.assertEqual(fleet.dispatch(later), [f"{job['id']} → marley"])
        briefs = []
        self.assertIn("started W9", fleet.run_mine(later, start=lambda brief, **kw: briefs.append(brief) or {"id": 9})[0])
        self.assertIn("went quiet", briefs[0])  # told to carry on from what was saved
        # The laptop wakes still running W8: it sees the job is no longer its own and stops it.
        self.as_machine("Marleyslaptop")
        with mock.patch.object(work, "get", return_value={"id": 8}), \
                mock.patch.object(work, "refresh", return_value={"id": 8, "status": "running"}), \
                mock.patch.object(work, "stop") as stop:
            notes = fleet.run_mine(later + 30)
        stop.assert_called_once_with("W8")
        self.assertIn("moved to marley", notes[0])

    def test_cli_status_and_work_anywhere(self):
        self.note(holder="marley")
        self.beat_of("marley")
        out = self.run_cli("work", "start", "--anywhere", "--brief", "check the store", "--task", "T7").stdout
        self.assertIn("queued", out)
        status = self.run_cli("fleet").stdout
        self.assertIn("Failover: on", status)
        self.assertIn("[queued]", status)
        jid = fleet.jobs()[0]["id"]
        self.assertIn("cancelled", self.run_cli("fleet", "cancel", jid).stdout)
