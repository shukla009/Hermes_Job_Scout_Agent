from datetime import datetime, timedelta, timezone
import scout_agent


def test_open_feed_does_not_make_old_job_new():
    post = {'source_type':'direct_apply','active_listing':True,
            'date_posted':(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()}
    assert not scout_agent._structured_freshness(post)[0]


def test_future_deadline_does_not_make_old_job_new():
    post = {'source_type':'direct_apply','active_listing':True,
            'valid_through':(datetime.now(timezone.utc)+timedelta(days=10)).isoformat(),
            'date_posted':(datetime.now(timezone.utc)-timedelta(days=30)).isoformat()}
    assert not scout_agent._structured_freshness(post)[0]


def test_unknown_date_is_not_labelled_latest():
    ok, reason = scout_agent._structured_freshness({'source_type':'direct_apply','active_listing':True})
    assert not ok and 'could not be verified' in reason


def test_yesterdays_post_is_fresh():
    post = {'source_type':'direct_apply','date_posted':
            (datetime.now(timezone.utc)-timedelta(hours=20)).isoformat()}
    assert scout_agent._structured_freshness(post)[0]
