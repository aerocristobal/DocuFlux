"""Characterization of worker/tasks/maintenance.py::_job_retention_decision.

These tests pin current behaviour, including behaviour that may be undesirable. If you
change the behaviour deliberately, change the test in the same commit and say why.

This function decides what gets deleted from a user's storage, so its edge cases are
worth freezing: deleting too early loses a document the user was about to download,
deleting too late fills the disk. It is pure, which makes the whole truth table cheap
to pin.
"""
import pytest

import tasks

pytestmark = [pytest.mark.characterization, pytest.mark.storage]

# The constants cleanup_old_files() passes in.
FAILURE = 300           # 5 min
DOWNLOADED = 600        # 10 min
NO_DOWNLOAD = 3600      # 1 h
ORPHAN = 3600           # 1 h
STALE = 7200            # 2 h, hardcoded inside the function

NOW = 1_700_000_000.0


def decide(meta, now=NOW, emergency=False, upload_dir='/nonexistent', output_dir='/nonexistent'):
    return tasks._job_retention_decision(
        'job-1', meta, now, upload_dir, output_dir,
        FAILURE, DOWNLOADED, NO_DOWNLOAD, ORPHAN, emergency,
    )


class TestFailedJobs:

    def test_failed_job_survives_before_five_minutes(self):
        should, _reason, _prio = decide({'status': 'FAILURE', 'completed_at': str(NOW - 299)})
        assert should is False

    def test_failed_job_is_deleted_after_five_minutes(self):
        should, reason, prio = decide({'status': 'FAILURE', 'completed_at': str(NOW - 301)})
        assert should is True
        assert reason == 'Failed job expired (5m)'
        assert prio == 10

    def test_failed_job_falls_back_to_created_at(self):
        """A job that failed before the worker set completed_at still ages out."""
        should, reason, _prio = decide({'status': 'FAILURE', 'created_at': str(NOW - 400)})
        assert should is True
        assert reason == 'Failed job expired (5m)'

    def test_completed_at_wins_over_created_at(self):
        """Old created_at but recent completed_at: not yet expired."""
        should, _reason, _prio = decide({
            'status': 'FAILURE',
            'created_at': str(NOW - 100_000),
            'completed_at': str(NOW - 10),
        })
        assert should is False

    def test_failed_job_with_no_timestamps_is_kept(self):
        """Nothing to age against, so the job is left alone rather than deleted."""
        should, _reason, _prio = decide({'status': 'FAILURE'})
        assert should is False


class TestSuccessfulJobs:

    def test_downloaded_job_survives_before_ten_minutes(self):
        should, _reason, _prio = decide({
            'status': 'SUCCESS', 'completed_at': str(NOW - 1000),
            'downloaded_at': str(NOW - 599),
        })
        assert should is False

    def test_downloaded_job_is_deleted_ten_minutes_after_access(self):
        should, reason, prio = decide({
            'status': 'SUCCESS', 'completed_at': str(NOW - 5000),
            'downloaded_at': str(NOW - 601),
        })
        assert should is True
        assert 'Downloaded/viewed job expired' in reason
        assert prio == 5

    def test_last_viewed_takes_precedence_over_downloaded_at(self):
        """Viewing refreshes the clock, so a recently-viewed old download survives."""
        should, _reason, _prio = decide({
            'status': 'SUCCESS', 'completed_at': str(NOW - 10_000),
            'downloaded_at': str(NOW - 5000), 'last_viewed': str(NOW - 10),
        })
        assert should is False

    def test_last_viewed_can_also_expire_the_job(self):
        should, _reason, prio = decide({
            'status': 'SUCCESS', 'completed_at': str(NOW - 10_000),
            'last_viewed': str(NOW - 700),
        })
        assert should is True
        assert prio == 5

    def test_never_downloaded_job_survives_the_ten_minute_window(self):
        """The 10-minute rule applies only once something has accessed the job."""
        should, _reason, _prio = decide({
            'status': 'SUCCESS', 'completed_at': str(NOW - 1200),
        })
        assert should is False

    def test_never_downloaded_job_is_deleted_after_an_hour(self):
        should, reason, prio = decide({
            'status': 'SUCCESS', 'completed_at': str(NOW - 3601),
        })
        assert should is True
        assert 'not downloaded' in reason
        assert prio == 3

    def test_successful_job_with_no_completed_at_is_kept(self):
        should, _reason, _prio = decide({'status': 'SUCCESS'})
        assert should is False


class TestStaleJobs:

    def test_processing_job_without_completed_at_expires_after_two_hours(self):
        should, reason, prio = decide({
            'status': 'PROCESSING', 'started_at': str(NOW - STALE - 1),
        })
        assert should is True
        assert reason == 'Stale processing job (2h)'
        assert prio == 8

    def test_processing_job_survives_under_two_hours(self):
        should, _reason, _prio = decide({
            'status': 'PROCESSING', 'started_at': str(NOW - 100),
        })
        assert should is False

    def test_pending_job_expires_two_hours_after_creation(self):
        should, reason, prio = decide({
            'status': 'PENDING', 'created_at': str(NOW - STALE - 1),
        })
        assert should is True
        assert reason == 'Stale PENDING job (2h)'
        assert prio == 8

    def test_pending_branch_needs_started_at_absent(self):
        """The stale-processing branch is checked first and wins when started_at is set."""
        should, reason, _prio = decide({
            'status': 'PENDING',
            'created_at': str(NOW - STALE - 1),
            'started_at': str(NOW - STALE - 1),
        })
        assert should is True
        assert reason == 'Stale processing job (2h)'


class TestStaleBranchShadowing:
    """The stale checks run *after* the status checks and overwrite their result.

    They are not part of the status if/elif chain, so a decision already made for a
    FAILURE or SUCCESS job can be replaced — including having its priority lowered.
    This is surprising, and worth freezing so a refactor cannot change it silently.
    """

    def test_stale_branch_overwrites_a_failure_decision_and_lowers_priority(self):
        """A long-running job that failed without completed_at is reported as stale
        processing (priority 8), not as a failed job (priority 10)."""
        should, reason, prio = decide({
            'status': 'FAILURE',
            'created_at': str(NOW - STALE - 1),
            'started_at': str(NOW - STALE - 1),
        })
        assert should is True
        assert reason == 'Stale processing job (2h)', "the FAILURE reason is overwritten"
        assert prio == 8, "priority is lowered from 10 to 8"

    def test_stale_branch_does_not_fire_when_completed_at_is_set(self):
        """completed_at gates the stale-processing branch, so finished jobs keep theirs."""
        should, reason, prio = decide({
            'status': 'FAILURE',
            'completed_at': str(NOW - 400),
            'started_at': str(NOW - STALE - 1),
        })
        assert should is True
        assert reason == 'Failed job expired (5m)'
        assert prio == 10

    def test_stale_branch_never_un_deletes_a_job(self):
        """It can only set should_delete True; a prior True is never reverted."""
        should, _reason, _prio = decide({
            'status': 'SUCCESS',
            'completed_at': str(NOW - 100_000),
            'started_at': str(NOW - 10),
        })
        assert should is True


class TestOrphans:

    def test_orphan_with_no_files_on_disk_is_not_deleted(self):
        should, _reason, _prio = decide(None)
        assert should is False

    def test_orphan_in_upload_dir_expires_after_an_hour(self, tmp_path):
        job_dir = tmp_path / 'uploads' / 'job-1'
        job_dir.mkdir(parents=True)
        os_utime_old(job_dir, NOW - ORPHAN - 1)

        should, reason, prio = decide(
            None, upload_dir=str(tmp_path / 'uploads'), output_dir=str(tmp_path / 'outputs'),
        )
        assert should is True
        assert 'Orphaned job expired' in reason
        assert prio == 7

    def test_recent_orphan_is_kept(self, tmp_path):
        job_dir = tmp_path / 'uploads' / 'job-1'
        job_dir.mkdir(parents=True)
        os_utime_old(job_dir, NOW - 10)

        should, _reason, _prio = decide(
            None, upload_dir=str(tmp_path / 'uploads'), output_dir=str(tmp_path / 'outputs'),
        )
        assert should is False

    def test_orphan_falls_back_to_the_output_dir(self, tmp_path):
        """Outputs outlive uploads, so a job may exist only under outputs/."""
        job_dir = tmp_path / 'outputs' / 'job-1'
        job_dir.mkdir(parents=True)
        os_utime_old(job_dir, NOW - ORPHAN - 1)

        should, _reason, prio = decide(
            None, upload_dir=str(tmp_path / 'uploads'), output_dir=str(tmp_path / 'outputs'),
        )
        assert should is True
        assert prio == 7


class TestEmergencyCleanup:

    def test_emergency_deletes_a_job_that_would_otherwise_be_kept(self):
        should, reason, prio = decide({'status': 'SUCCESS', 'completed_at': str(NOW)},
                                      emergency=True)
        assert should is True
        assert reason == 'EMERGENCY: Disk >95% full'
        assert prio == 15

    def test_emergency_prefixes_an_existing_reason(self):
        should, reason, prio = decide({'status': 'FAILURE', 'completed_at': str(NOW - 400)},
                                      emergency=True)
        assert should is True
        assert reason == 'EMERGENCY: Failed job expired (5m)'
        assert prio == 15

    def test_emergency_outranks_every_other_priority(self):
        _should, _reason, prio = decide({'status': 'PENDING', 'created_at': str(NOW - STALE - 1)},
                                        emergency=True)
        assert prio == 15

    def test_emergency_deletes_orphans_with_no_metadata(self):
        should, _reason, prio = decide(None, emergency=True)
        assert should is True
        assert prio == 15


def os_utime_old(path, when):
    """Backdate a path's mtime so orphan ageing can be exercised deterministically."""
    import os
    os.utime(path, (when, when))
