"""
SDD — Spec: speed and time remaining in the progress

CONTRACT
  The `progress` event published by the progress hook carries, besides the
  percentage and the message, `speed` (bytes/s) and `eta` (seconds) when
  yt-dlp reports them, and `None` when it does not. `transfer_text(speed, eta)`
  turns both into the bar's text ("4.2 MB/s · faltam 0:12").

WHY IT EXISTS
  A bar stuck at 37% does not say whether the download froze or the connection
  is slow. Speed and time remaining answer that — it is what every app of the
  same kind shows next to the bar.

BUSINESS RULES
  - What yt-dlp does not report is not invented: without speed, there is no
    text.
  - Cutting a section through FFmpeg does not report a total: the percentage
    stays at 0 and the speed still appears if it comes.
"""

import queue

from media_downloader.downloader import DownloadManager, transfer_text
from media_downloader.models import DownloadSummary


def _published(data):
    event_queue = queue.Queue()
    hook = DownloadManager(event_queue)._make_progress_hook(DownloadSummary(total_items=1))
    hook(data)
    return event_queue.get_nowait()


class TestSpeedInProgress:
    def test_should_publish_speed_and_time_remaining_when_yt_dlp_reports_them(self):
        event = _published({"status": "downloading", "downloaded_bytes": 50,
                            "total_bytes": 100, "speed": 2_000_000.0, "eta": 12,
                            "info_dict": {"title": "Clipe"}})
        assert event["speed"] == 2_000_000.0
        assert event["eta"] == 12
        assert event["progress"] == 50

    def test_should_not_invent_values_when_yt_dlp_did_not_report_them(self):
        event = _published({"status": "downloading", "downloaded_bytes": 50,
                            "info_dict": {"title": "Clipe"}})
        assert event["speed"] is None
        assert event["eta"] is None


class TestTransferText:
    def test_should_join_speed_and_time_remaining_when_both_are_known(self):
        assert transfer_text(4.2 * 1024 * 1024, 12) == "4.2 MB/s · faltam 0:12"

    def test_should_use_kb_when_the_connection_is_slow(self):
        assert transfer_text(512_000, None) == "500 KB/s"

    def test_should_have_no_text_when_there_is_no_speed(self):
        assert transfer_text(None, 30) == ""
