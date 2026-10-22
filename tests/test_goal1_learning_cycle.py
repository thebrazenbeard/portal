"""PORTAL Goal 1: synthetic-only source boundary regression tests.

No actual QVR, model, production state or independent evaluator is used.
"""
import hashlib
import sys
import tempfile
import types
import unittest
from enum import Enum
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
if (ROOT / 'scripts' / 'research').exists():
    ROOT = ROOT / 'scripts' / 'research'
sys.path.insert(0, str(ROOT))
from goal1_learning_cycle import Observation, learn_once, reuse_after_restart


class MemoryClass(Enum):
    WORKING_PROJECT = 'WORKING_PROJECT'


class AdmissionRequest:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class Memory:
    project_id = 'goal1-synthetic-lab'
    identity_id = 'goal1-synthetic-runtime'

    def __init__(self, root):
        self.path = root / 'synthetic-memory.sqlite3'
        self.path.touch()
        self.records = {}
        self.current_head = 'genesis'

    def admit(self, record, *, expected_head):
        assert expected_head == self.current_head
        assert record.record_id not in self.records
        self.records[record.record_id] = record
        self.current_head = 'next'
        return {'record_id': record.record_id}

    def read(self, record_id):
        record = self.records[record_id]
        return types.SimpleNamespace(**record.__dict__, status='CURRENT')


class Runtime:
    def __init__(self, memory, answers):
        self.lifecycle = types.SimpleNamespace(memory=memory)
        self.answers = list(answers)
        self.calls = []

    def consume_local_cognition(self, prompt, *, permit, invocation_id, max_tokens):
        self.calls.append((prompt, invocation_id))
        response = self.answers.pop(0)
        return types.SimpleNamespace(text=response, response_sha256=hashlib.sha256(response.encode()).hexdigest())


class Boundaries(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vera-goal1-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.memory = Memory(self.root)
        module = types.ModuleType('vera_memory')
        module.AdmissionRequest = AdmissionRequest
        module.MemoryClass = MemoryClass
        fixture = patch.dict(sys.modules, {'vera_memory': module})
        fixture.start()
        self.addCleanup(fixture.stop)

    def write_candidate(self):
        return learn_once(Runtime(self.memory, ['LEFT', 'RIGHT']), sandbox_root=self.root,
                          permit='test', trial_id='x', problem='synthetic first',
                          evaluate=lambda _: Observation(False, 'RIGHT suggested', 'truth:first'))

    def test_feedback_not_preexposed_to_initial_model(self):
        worker = Runtime(self.memory, ['LEFT', 'RIGHT'])
        self.assertEqual(learn_once(worker, sandbox_root=self.root, permit='test',
                                    trial_id='x', problem='task',
                                    evaluate=lambda _: Observation(False, 'RIGHT wins', 'truth:first')).candidate_action, 'RIGHT')
        self.assertNotIn('RIGHT wins', worker.calls[0][0])
        self.assertIn('RIGHT wins', worker.calls[1][0])

    def test_distinct_later_cases_have_distinct_invocation_ids(self):
        self.write_candidate()
        worker = Runtime(self.memory, ['LEFT', 'RIGHT'])
        for case in ('heldout-1', 'heldout-2'):
            reuse_after_restart(worker, sandbox_root=self.root, case_id=case,
                                permit='test', trial_id='x', new_problem=case,
                                evaluate=lambda action: Observation(action == 'RIGHT', 'withheld', 'truth:' + case))
        self.assertEqual(len({call[1] for call in worker.calls}), 2)
        self.assertIn('heldout-1', worker.calls[0][1])
        self.assertIn('heldout-2', worker.calls[1][1])

    def test_wrong_directory_rejected_before_model_call(self):
        worker = Runtime(self.memory, ['LEFT'])
        with self.assertRaises(RuntimeError):
            learn_once(worker, sandbox_root=self.root.parent, permit='test', trial_id='x',
                       problem='test', evaluate=lambda _: Observation(False, 'no', 'truth:bad'))
        self.assertEqual(worker.calls, [])

    def test_spoofed_identity_cannot_mask_foreign_memory_path(self):
        self.memory.path = Path('/not-goal1/canonical.sqlite3')
        worker = Runtime(self.memory, ['LEFT'])
        with self.assertRaises(RuntimeError):
            learn_once(worker, sandbox_root=self.root, permit='test', trial_id='x',
                       problem='test', evaluate=lambda _: Observation(False, 'no', 'truth:bad'))
        self.assertEqual(worker.calls, [])

    def test_changed_memory_binding_blocks_admission(self):
        other = Memory(self.root)
        worker = Runtime(self.memory, ['LEFT', 'RIGHT'])
        original = worker.consume_local_cognition
        def swapped(prompt, *, permit, invocation_id, max_tokens):
            result = original(prompt, permit=permit, invocation_id=invocation_id, max_tokens=max_tokens)
            worker.lifecycle.memory = other
            return result
        worker.consume_local_cognition = swapped
        with self.assertRaises(RuntimeError):
            learn_once(worker, sandbox_root=self.root, permit='test', trial_id='x',
                       problem='test', evaluate=lambda _: Observation(False, 'RIGHT wins', 'truth:bad'))
        self.assertEqual(self.memory.records, {})
        self.assertEqual(other.records, {})

    def test_abstain_never_evaluates_or_writes(self):
        worker = Runtime(self.memory, ['ABSTAIN'])
        def fail(_):
            self.fail('abstention must not evaluate')
        result = learn_once(worker, sandbox_root=self.root, permit='test',
                            trial_id='x', problem='test', evaluate=fail)
        self.assertIsNone(result.candidate_action)
        self.assertEqual(self.memory.records, {})


if __name__ == '__main__':
    unittest.main()
